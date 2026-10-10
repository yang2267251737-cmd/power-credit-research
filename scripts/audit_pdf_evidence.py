"""Optional pypdf check: every recorded value occurs on its registered page.

This does not prove the column, label or financial interpretation; review the
original table as well. No third-party PDFs are copied into the repository.
"""
import csv
import hashlib
import re
from decimal import Decimal
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]

def main():
    with (ROOT/'data/source_checksums.csv').open(encoding='utf-8',newline='') as f:
        checksums=list(csv.DictReader(f))
    readers={}
    for item in checksums:
        path=ROOT/'data/raw'/f"{item['source_id']}.pdf"
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Source changed: '+item['source_id'])
        reader=PdfReader(path)
        if len(reader.pages)!=int(item['pdf_pages']):
            raise ValueError('Page count changed: '+item['source_id'])
        readers[item['source_id']]=reader
    cache={}
    with (ROOT/'data/facts.csv').open(encoding='utf-8',newline='') as f:
        facts=list(csv.DictReader(f))
    for fact in facts:
        key=(fact['source_id'],int(fact['pdf_page']))
        if key not in cache:
            text=readers[key[0]].pages[key[1]-1].extract_text() or ''
            tokens=re.findall(r'(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?',text)
            cache[key]={Decimal(token.replace(',','')) for token in tokens}
        if Decimal(fact['value']) not in cache[key]:
            raise ValueError('Value absent from page: '+repr(fact))
    print(f'Checked source hashes, page counts and {len(facts)} value/page references')

if __name__=='__main__':
    main()
