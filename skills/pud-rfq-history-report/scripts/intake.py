"""Preserve provenance, extract text, and render complete RFQ pages for agent review."""
import hashlib
import json
import sys
from pathlib import Path
import pypdfium2 as pdfium
from pypdf import PdfReader

source, case = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
case.mkdir(parents=True, exist_ok=True)
pages = case / 'pages'
pages.mkdir(exist_ok=True)
reader = PdfReader(source)
doc = pdfium.PdfDocument(source)
text = []
for n, page in enumerate(doc, 1):
    page.render(scale=2).to_pil().save(pages / f'page-{n}.png')
    text.append(f'PAGE {n}\n{reader.pages[n-1].extract_text() or "[Image-only page: inspect/OCR rendered image]"}')
(case / 'extracted-text.txt').write_text('\n\n'.join(text), encoding='utf-8')
(case / 'intake.json').write_text(json.dumps({'source': str(source), 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'page_count': len(doc), 'extraction_verified': False}, indent=2), encoding='utf-8')
print(f'Rendered {len(doc)} complete pages to {pages}. Agent review is required.')
