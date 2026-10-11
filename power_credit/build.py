"""python -m power_credit.build: validate evidence, build SQLite and offline outputs."""
import csv
import json
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from .model import METRICS, DERIVED, normalize, derive, stress

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

def build(root=ROOT):
    issuers = read_csv(root/'data/issuers.csv')
    sources = read_csv(root/'data/sources.csv')
    facts = read_csv(root/'data/facts.csv')
    debt_breakdown=read_csv(root/'data/debt_breakdown.csv')
    evidence_issues=read_csv(root/'data/evidence_issues.csv')
    groups = validate(issuers, sources, facts)
    validate_debt_breakdown(debt_breakdown,facts,evidence_issues)
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
              ['issue_id','issuer_id','period_end','scope','report_vintage','check','category','field','expected_delta','unit','source_id','pdf_page','report_page','status','review_status','note'])]:
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
               'metric_labels':METRICS,'derived_labels':DERIVED,
               'stress': {'ocf_decline': 0.2, 'capex_increase': 0.1}}
    serialized = json.dumps(payload,ensure_ascii=False,indent=2)
    (output/'research.json').write_text(serialized,encoding='utf-8')
    template = (root/'dashboard/template.html').read_text(encoding='utf-8')
    (root/'dashboard/index.html').write_text(template.replace('__DATA__',serialized.replace('</','<\\/')),encoding='utf-8')
    print(f'Built {len(issuers)} issuers, {len(facts)} facts, {len(result)} issuer-period rows')

if __name__ == '__main__':
    build()
