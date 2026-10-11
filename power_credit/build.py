"""python -m power_credit.build: validate evidence, build SQLite and offline outputs."""
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from .model import METRICS, DERIVED, normalize, derive, stress
from .cash_bridge import derive_cash_bridge

ROOT = Path(__file__).resolve().parents[1]

def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))

def validate(issuers, sources, facts):
    issuer_map = {item['issuer_id']: item for item in issuers}
    source_map = {item['source_id']: item for item in sources}
    if len(issuer_map) != len(issuers) or len(source_map) != len(sources):
        raise ValueError('Duplicate issuer or source identifier')
    for source in sources:
        if source['issuer_id'] not in issuer_map or not source['url'].startswith('https://'):
            raise ValueError('Invalid source registry')
        date.fromisoformat(source['publication_date'])
        date.fromisoformat(source['accessed_date'])
    seen = set()
    for fact in facts:
        key = tuple(fact[k] for k in ['issuer_id', 'period_end', 'scope', 'report_vintage', 'metric'])
        if key in seen:
            raise ValueError('Duplicate fact: ' + repr(key))
        seen.add(key)
        source = source_map.get(fact['source_id'])
        if fact['issuer_id'] not in issuer_map or source is None or source['issuer_id'] != fact['issuer_id']:
            raise ValueError('Fact/source issuer mismatch')
        if fact['scope'] not in ['consolidated', 'parent'] or fact['currency'] != 'CNY':
            raise ValueError('This build supports consolidated/parent CNY facts only')
        if fact['report_vintage'] != fact['source_id'].split('_')[1]:
            raise ValueError('Report vintage/source mismatch')
        if fact['metric'] not in METRICS or fact['review_status'] not in ['agent_verified', 'user_verified']:
            raise ValueError('Unknown metric or unreviewed fact')
        if int(fact['pdf_page']) < 1 or int(fact['report_page']) < 1 or not fact['section'] or not fact['basis']:
            raise ValueError('Missing evidence location/basis')
        date.fromisoformat(fact['period_end'])
        if fact['period_end'] > source['publication_date']:
            raise ValueError('Reporting period cannot be after publication')
        amount = normalize(fact['value'], fact['unit'])
        if fact['metric'] != 'operating_cash_flow' and amount < 0:
            raise ValueError('Unexpected negative balance or cash outlay')
    groups = defaultdict(dict)
    for fact in facts:
        groups[(fact['issuer_id'], fact['period_end'], fact['scope'], fact['report_vintage'])][fact['metric']] = normalize(fact['value'], fact['unit'])
    for key, values in groups.items():
        for small, large in [('restricted_monetary_funds', 'monetary_funds'),
                             ('finance_company_deposits','monetary_funds'),
                             ('short_term_interest_bearing_debt', 'interest_bearing_debt'),
                             ('current_assets', 'total_assets'),
                             ('current_liabilities', 'total_liabilities'),
                             ('total_liabilities', 'total_assets')]:
            if small in values and large in values and values[small] > values[large]:
                raise ValueError(f'Inconsistent balances at {key}: {small} > {large}')
    return groups

def write_database(path, issuers, sources, facts):
    with sqlite3.connect(path) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.executescript('''
          DROP TABLE IF EXISTS facts;
          DROP TABLE IF EXISTS sources;
          DROP TABLE IF EXISTS issuers;
          CREATE TABLE issuers(issuer_id TEXT PRIMARY KEY, short_name TEXT, legal_name TEXT, stock_code TEXT, sample_note TEXT);
          CREATE TABLE sources(source_id TEXT PRIMARY KEY, issuer_id TEXT REFERENCES issuers, title TEXT, url TEXT, publication_date TEXT, accessed_date TEXT, document_type TEXT);
          CREATE TABLE facts(issuer_id TEXT REFERENCES issuers, period_end TEXT, scope TEXT, metric TEXT,
            raw_value TEXT, raw_unit TEXT, currency TEXT, source_id TEXT REFERENCES sources, pdf_page INTEGER,
            report_page INTEGER, section TEXT, basis TEXT, review_status TEXT, note TEXT, amount_cny_1000 TEXT,
            report_vintage TEXT,
            PRIMARY KEY(issuer_id,period_end,scope,report_vintage,metric));
        ''')
        connection.executemany('INSERT INTO issuers VALUES (?,?,?,?,?)',
            [[item[k] for k in ['issuer_id','short_name','legal_name','stock_code','sample_note']] for item in issuers])
        connection.executemany('INSERT INTO sources VALUES (?,?,?,?,?,?,?)',
            [[item[k] for k in ['source_id','issuer_id','title','url','publication_date','accessed_date','document_type']] for item in sources])
        keys = ['issuer_id','period_end','scope','metric','value','unit','currency','source_id',
                'pdf_page','report_page','section','basis','review_status','note']
        connection.executemany('INSERT INTO facts VALUES (' + ','.join('?' for _ in range(16)) + ')',
            [[item[k] for k in keys] + [str(normalize(item['value'], item['unit'])),item['report_vintage']] for item in facts])

def reconcile_versions(facts):
    """Compare disclosures without silently replacing or blending them."""
    index={tuple(f[k] for k in ['issuer_id','period_end','scope','report_vintage','metric']):f for f in facts}
    result=[]
    for key, old in sorted(index.items()):
        issuer,period,scope,vintage,metric=key
        if vintage!='2024' or period!='2024-12-31':
            continue
        new=index.get((issuer,period,scope,'2025',metric))
        comparison_type='same_metric'
        if new is None and metric=='interest_bearing_debt':
            new=index.get((issuer,period,scope,'2025','debt_disclosed_opening'))
            comparison_type='year_end_vs_next_report_opening'
        if new is None:
            continue
        before=normalize(old['value'],old['unit'])
        after=normalize(new['value'],new['unit'])
        result.append({'issuer_id':issuer,'period_end':period,'scope':scope,'metric':metric,
            'comparison_type':comparison_type,'old_cny_1000':str(before),'new_cny_1000':str(after),
            'delta_cny_1000':str(after-before),'old_source_id':old['source_id'],
            'new_source_id':new['source_id'],'old_pdf_page':old['pdf_page'],'new_pdf_page':new['pdf_page']})
    return result

def validate_debt_breakdown(rows, facts, exceptions=()):
    """Reconcile complete columns to reported totals with rounding tolerance."""
    fact_index={tuple(f[k] for k in ['issuer_id','period_end','scope','report_vintage','metric']):f for f in facts}
    groups=defaultdict(list)
    seen=set()
    known={tuple(r[k] for k in ['issuer_id','period_end','scope','report_vintage','check','category','field']):r for r in exceptions}
    if len(known)!=len(exceptions):
        raise ValueError('Duplicate evidence exception')
    matched=set()
    def check_delta(key,kind,category,field,delta,source):
        issue_key=key+(kind,category,field)
        issue=known.get(issue_key)
        if issue is not None:
            if Decimal(issue['expected_delta'])!=delta or issue['source_id']!=source or issue['status']!='unresolved' or issue['unit']!='CNY_100M':
                raise ValueError('Evidence exception no longer matches source values')
            matched.add(issue_key)
        elif abs(delta)>Decimal('0.03'):
            raise ValueError('Debt category sum exceeds rounding tolerance')
    for row in rows:
        key=tuple(row[k] for k in ['issuer_id','period_end','scope','report_vintage'])
        category_key=key+(row['category'],)
        if category_key in seen:
            raise ValueError('Duplicate debt category')
        seen.add(category_key)
        fact=fact_index.get(key+('interest_bearing_debt',))
        if fact is None or row['source_id']!=fact['source_id'] or row['unit']!='CNY_100M':
            raise ValueError('Debt category/source mismatch')
        if row['review_status'] not in ['agent_verified','user_verified'] or int(row['pdf_page'])<1 or int(row['report_page'])<1:
            raise ValueError('Unreviewed debt evidence')
        for field in ['total','within_one_year','over_one_year']:
            if row[field] and normalize(row[field],row['unit'])<0:
                raise ValueError('Negative debt category')
        groups[key].append(row)
        if row['within_one_year'] and row['over_one_year'] and row['total']:
            check_delta(key,'category_row_sum',row['category'],'total',
                        Decimal(row['within_one_year'])+Decimal(row['over_one_year'])-Decimal(row['total']),row['source_id'])
    for key,items in groups.items():
        totals=[r for r in items if r['category']=='合计']
        if len(totals)!=1:
            raise ValueError('Missing debt total')
        total=totals[0]
        for field,metric in [('total','interest_bearing_debt'),('within_one_year','short_term_interest_bearing_debt')]:
            fact=fact_index[key+(metric,)]
            if normalize(total[field],total['unit'])!=normalize(fact['value'],fact['unit']):
                raise ValueError('Debt detail/fact total mismatch')
        for field in ['total','within_one_year','over_one_year']:
            detail=[r for r in items if r['category']!='合计']
            if all(r[field] for r in detail):
                delta=sum(Decimal(r[field]) for r in detail)-Decimal(total[field])
                check_delta(key,'category_column_sum','合计',field,delta,total['source_id'])
    if set(known)!=matched:
        raise ValueError('Stale evidence exception')

def validate_income_evidence(rows, facts, sources):
    """Keep accrual income separate from cash receipts; no assumed cash bridge."""
    source_map = {s['source_id']: s for s in sources}
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        key = tuple(row[k] for k in ['issuer_id', 'period_end', 'scope', 'report_vintage'])
        record_key = key + (row['component'],)
        if record_key in seen:
            raise ValueError('Duplicate investment income evidence')
        seen.add(record_key)
        source = source_map.get(row['source_id'])
        if source is None or source['issuer_id'] != row['issuer_id'] or row['report_vintage'] != row['source_id'].split('_')[1]:
            raise ValueError('Investment income source mismatch')
        if row['scope'] != 'parent' or row['currency'] != 'CNY' or row['review_status'] not in ['agent_verified', 'user_verified']:
            raise ValueError('Invalid investment income evidence')
        if int(row['pdf_page']) < 1 or int(row['report_page']) < 1:
            raise ValueError('Missing investment income page')
        date.fromisoformat(row['period_end'])
        if row['period_end'] > source['publication_date']:
            raise ValueError('Investment income period after publication')
        if row['measurement_basis'] not in ['accounting_income', 'cash_flow'] or row['role'] not in ['component', 'total']:
            raise ValueError('Invalid investment income basis/role')
        groups[key].append(row)
    result = []
    for key, items in groups.items():
        accounting = [r for r in items if r['measurement_basis'] == 'accounting_income']
        totals = [r for r in accounting if r['role'] == 'total']
        cash = [r for r in items if r['measurement_basis'] == 'cash_flow']
        if len(totals) != 1 or len(cash) != 1 or cash[0]['role'] != 'total':
            raise ValueError('Income table requires separate accounting and cash totals')
        total = normalize(totals[0]['value'], totals[0]['unit'])
        components = sum((normalize(r['value'], r['unit']) for r in accounting if r['role'] == 'component'), Decimal(0))
        if total != components:
            raise ValueError('Accounting income components do not reconcile')
        fact = next((f for f in facts if tuple(f[k] for k in ['issuer_id', 'period_end', 'scope', 'report_vintage']) == key and f['metric'] == 'investment_income_cash'), None)
        receipts = normalize(cash[0]['value'], cash[0]['unit'])
        if fact is None or fact['source_id'] != cash[0]['source_id'] or normalize(fact['value'], fact['unit']) != receipts:
            raise ValueError('Investment cash evidence/fact mismatch')
        result.append(dict(zip(['issuer_id', 'period_end', 'scope', 'report_vintage'], key),
            accounting_income_cny_1000=str(total), investment_cash_cny_1000=str(receipts),
            cash_minus_accounting_cny_1000=str(receipts-total), bridge_status='cash_bridge_pending'))
    return result

def validate_review_log(reviews, facts, income, issues):
    """A user-reviewed label needs a matching human feedback record."""
    index = {}
    key_fields = ['issuer_id', 'period_end', 'scope', 'report_vintage']
    for kind, records, id_field, value_field in [('fact', facts, 'metric', 'value'),
            ('income', income, 'component', 'value'), ('issue', issues, 'issue_id', 'expected_delta')]:
        for row in records:
            index[(kind, row[id_field], *(row[k] for k in key_fields), value_field)] = row
    seen, confirmed = set(), set()
    for review in reviews:
        if review['review_id'] in seen:
            raise ValueError('Duplicate review identifier')
        seen.add(review['review_id'])
        date.fromisoformat(review['review_date'])
        key = (review['record_type'], review['target_id'], *(review[k] for k in key_fields), review['field'])
        row = index.get(key)
        if row is None or review['source_id'] != row['source_id'] or Decimal(review['confirmed_value']) != Decimal(row[review['field']]):
            raise ValueError('User review target/value mismatch')
        if review['reviewer'] != 'user' or review['review_status'] != 'user_verified' or not review['note']:
            raise ValueError('Missing human review provenance')
        if any(review[k] != row[k] for k in ['pdf_page', 'report_page']):
            raise ValueError('User review source page mismatch')
        if len(review['attachment_sha256']) != 64 or any(c not in '0123456789abcdef' for c in review['attachment_sha256']):
            raise ValueError('Invalid attachment checksum')
        if min(int(review['attachment_pdf_page']), int(review['attachment_report_page'])) < 1:
            raise ValueError('Missing attachment page mapping')
        confirmed.add(key)
    if any(r['review_status'] == 'user_verified' and key not in confirmed for key, r in index.items()):
        raise ValueError('User-verified record has no review log')

def build(root=ROOT):
    issuers = read_csv(root/'data/issuers.csv')
    sources = read_csv(root/'data/sources.csv')
    facts = read_csv(root/'data/facts.csv')
    debt_breakdown=read_csv(root/'data/debt_breakdown.csv')
    evidence_issues=read_csv(root/'data/evidence_issues.csv')
    income_evidence=read_csv(root/'data/investment_income_evidence.csv')
    review_log=read_csv(root/'data/review_log.csv')
    cash_bridge_inputs=read_csv(root/'data/cash_bridge_inputs.csv')
    dividend_receivables=read_csv(root/'data/dividend_receivables.csv')
    groups = validate(issuers, sources, facts)
    validate_debt_breakdown(debt_breakdown,facts,evidence_issues)
    income_checks=validate_income_evidence(income_evidence,facts,sources)
    validate_review_log(review_log,facts,income_evidence,evidence_issues)
    cash_bridge=derive_cash_bridge(cash_bridge_inputs,dividend_receivables,income_evidence,sources)
    result = []
    for (issuer, period, scope, vintage), values in sorted(groups.items()):
        derived = derive(values)
        negative_ocf=values.get('operating_cash_flow',Decimal(0))<0
        derived['stress_ocf_after_cash_capex'] = None if negative_ocf else stress(values)
        result.append({'issuer_id': issuer, 'period_end': period,'scope':scope,'report_vintage':vintage,
                       'stress_status':'negative_ocf_not_applicable' if negative_ocf else 'standard_scenario',
                       'metrics': {k: None if v is None else str(v) for k,v in derived.items()},
                       'missing_metrics': [k for k in METRICS if k not in values]})
    output = root/'data/processed'
    output.mkdir(parents=True, exist_ok=True)
    write_database(output/'credit.sqlite', issuers, sources, facts)
    reconciliation=reconcile_versions(facts)
    # Keep audit tables in the reproducible local database as well as CSV/JSON.
    with sqlite3.connect(output/'credit.sqlite') as connection:
        for name,items,fields in [('debt_breakdown',debt_breakdown,
              ['issuer_id','period_end','scope','report_vintage','category','total','within_one_year','over_one_year','unit','source_id','pdf_page','report_page','review_status','note']),
              ('reconciliation',reconciliation,
              ['issuer_id','period_end','scope','metric','comparison_type','old_cny_1000','new_cny_1000','delta_cny_1000','old_source_id','new_source_id','old_pdf_page','new_pdf_page']),
              ('evidence_issues',evidence_issues,
              ['issue_id','issuer_id','period_end','scope','report_vintage','check','category','field','expected_delta','unit','source_id','pdf_page','report_page','status','review_status','note']),
              ('investment_income_evidence',income_evidence,list(income_evidence[0])),
              ('review_log',review_log,list(review_log[0])),
              ('cash_bridge_inputs',cash_bridge_inputs,list(cash_bridge_inputs[0])),
              ('dividend_receivables',dividend_receivables,list(dividend_receivables[0]))]:
            connection.execute(f'DROP TABLE IF EXISTS {name}')
            connection.execute(f'CREATE TABLE {name} ('+', '.join(f'"{field}" TEXT' for field in fields)+')')
            connection.executemany(f'INSERT INTO {name} VALUES ('+','.join('?' for _ in fields)+')',
                                   [[r[field] for field in fields] for r in items])
    with (output/'comparison.csv').open('w',encoding='utf-8',newline='') as handle:
        names = ['issuer_id','period_end','scope','report_vintage','stress_status'] + list(result[0]['metrics'])
        writer = csv.DictWriter(handle,fieldnames=names)
        writer.writeheader()
        for item in result:
            writer.writerow({**{k:item[k] for k in names[:5]},**item['metrics']})
    with (output/'reconciliation.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.DictWriter(handle,fieldnames=list(reconciliation[0]))
        writer.writeheader()
        writer.writerows(reconciliation)
    payload = {'as_of': '2026-10-11', 'financial_snapshot': '2023–2025年，按报告版本与报表口径分组',
               'issuers':issuers,'sources':sources,'facts':facts,'comparison':result,
               'reconciliation':reconciliation,
               'debt_breakdown':debt_breakdown,
               'evidence_issues':evidence_issues,
               'investment_income_evidence':income_evidence,'income_checks':income_checks,
               'review_log':review_log,
               'cash_bridge_inputs':cash_bridge_inputs,'dividend_receivables':dividend_receivables,
               'cash_bridge':cash_bridge,
               'metric_labels':METRICS,'derived_labels':DERIVED,
               'stress': {'ocf_decline': 0.2, 'capex_increase': 0.1}}
    serialized = json.dumps(payload,ensure_ascii=False,indent=2)
    (output/'research.json').write_text(serialized,encoding='utf-8')
    (output/'cash_bridge.json').write_text(json.dumps(cash_bridge,ensure_ascii=False,indent=2),encoding='utf-8')
    template = (root/'dashboard/template.html').read_text(encoding='utf-8')
    (root/'dashboard/index.html').write_text(template.replace('__DATA__',serialized.replace('</','<\\/')),encoding='utf-8')
    print(f'Built {len(issuers)} issuers, {len(facts)} facts, {len(result)} issuer-period rows')

if __name__ == '__main__':
    build()
