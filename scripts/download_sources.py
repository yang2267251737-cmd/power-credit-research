"""Download registered public PDFs locally; raw documents are not versioned."""
import csv
import hashlib
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]

def main():
    with (ROOT / 'data' / 'sources.csv').open(encoding='utf-8-sig', newline='') as f:
        sources = list(csv.DictReader(f))
    with (ROOT / 'data' / 'source_checksums.csv').open(encoding='utf-8', newline='') as f:
        expected = {r['source_id']: r['sha256'] for r in csv.DictReader(f)}
    target = ROOT / 'data' / 'raw'
    target.mkdir(parents=True, exist_ok=True)
    for source in sources:
        path = target / (source['source_id'] + '.pdf')
        if not path.exists():
            req = Request(source['url'], headers={'User-Agent': 'Mozilla/5.0'})
            with urlopen(req, timeout=60) as response:
                payload = response.read()
            if not payload.startswith(b'%PDF-'):
                raise ValueError('Not a PDF: ' + source['source_id'])
            path.write_bytes(payload)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != expected[source['source_id']]:
            raise ValueError('Document changed; review before importing: ' + source['source_id'])
        print(source['source_id'], path.stat().st_size, digest)

if __name__ == '__main__':
    main()
