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
        key = tuple(fact[k] for k in ['issuer_id', 'period_end', 'scope', 'metric'])
        if key in seen:
            raise ValueError('Duplicate fact: ' + repr(key))
        seen.add(key)
        source = source_map.get(fact['source_id'])
        if fact['issuer_id'] not in issuer_map or source is None or source['issuer_id'] != fact['issuer_id']:
            raise ValueError('Fact/source issuer mismatch')
        if fact['scope'] != 'consolidated' or fact['currency'] != 'CNY':
            raise ValueError('This build supports consolidated CNY facts only')
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
        groups[(fact['issuer_id'], fact['period_end'])][fact['metric']] = normalize(fact['value'], fact['unit'])
    for key, values in groups.items():
        for small, large in [('restricted_monetary_funds', 'monetary_funds'),
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
            PRIMARY KEY(issuer_id,period_end,scope,metric));
        ''')
        connection.executemany('INSERT INTO issuers VALUES (?,?,?,?,?)',
            [[item[k] for k in ['issuer_id','short_name','legal_name','stock_code','sample_note']] for item in issuers])
        connection.executemany('INSERT INTO sources VALUES (?,?,?,?,?,?,?)',
            [[item[k] for k in ['source_id','issuer_id','title','url','publication_date','accessed_date','document_type']] for item in sources])
        keys = ['issuer_id','period_end','scope','metric','value','unit','currency','source_id',
                'pdf_page','report_page','section','basis','review_status','note']
        connection.executemany('INSERT INTO facts VALUES (' + ','.join('?' for _ in range(15)) + ')',
            [[item[k] for k in keys] + [str(normalize(item['value'], item['unit']))] for item in facts])

def build(root=ROOT):
    issuers = read_csv(root/'data/issuers.csv')
    sources = read_csv(root/'data/sources.csv')
    facts = read_csv(root/'data/facts.csv')
    groups = validate(issuers, sources, facts)
    result = []
    for (issuer, period), values in sorted(groups.items()):
        derived = derive(values)
        derived['stress_ocf_after_cash_capex'] = stress(values)
        result.append({'issuer_id': issuer, 'period_end': period,
                       'metrics': {k: None if v is None else str(v) for k,v in derived.items()},
                       'missing_metrics': [k for k in METRICS if k not in values]})
    output = root/'data/processed'
    output.mkdir(parents=True, exist_ok=True)
    write_database(output/'credit.sqlite', issuers, sources, facts)
    with (output/'comparison.csv').open('w',encoding='utf-8',newline='') as handle:
        names = ['issuer_id','period_end'] + list(result[0]['metrics'])
        writer = csv.DictWriter(handle,fieldnames=names)
        writer.writeheader()
        for item in result:
            writer.writerow({'issuer_id':item['issuer_id'],'period_end':item['period_end'],**item['metrics']})
    payload = {'as_of': '2026-10-10', 'financial_snapshot': '2025年度及2024年比较数',
               'issuers':issuers,'sources':sources,'facts':facts,'comparison':result,
               'metric_labels':METRICS,'derived_labels':DERIVED,
               'stress': {'ocf_decline': 0.2, 'capex_increase': 0.1}}
    serialized = json.dumps(payload,ensure_ascii=False,indent=2)
    (output/'research.json').write_text(serialized,encoding='utf-8')
    template = (root/'dashboard/template.html').read_text(encoding='utf-8')
    (root/'dashboard/index.html').write_text(template.replace('__DATA__',serialized.replace('</','<\\/')),encoding='utf-8')
    print(f'Built {len(issuers)} issuers, {len(facts)} facts, {len(result)} issuer-period rows')

if __name__ == '__main__':
    build()
