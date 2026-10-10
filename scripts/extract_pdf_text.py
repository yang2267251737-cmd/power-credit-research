"""Optional: pip install pypdf. Extract locally for research, not publication."""
from pathlib import Path
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
target = ROOT / 'tmp' / 'pdfs'
target.mkdir(parents=True, exist_ok=True)
for path in sorted((ROOT / 'data' / 'raw').glob('*.pdf')):
    reader = PdfReader(path)
    pages = [f'\n=== PDF PAGE {i} ===\n' + (p.extract_text() or '')
             for i, p in enumerate(reader.pages, 1)]
    (target / (path.stem + '.txt')).write_text('\n'.join(pages), encoding='utf-8')
    print(path.stem, len(reader.pages))
