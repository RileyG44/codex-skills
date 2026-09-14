"""Candidate retrieval and deterministic reporting; product decisions belong to the agent."""
import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from pypdf import PdfReader
from pdf_report import write_pdf

def norm(value):
    return re.sub('[^A-Z0-9]', '', value.upper())

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['search', 'build'])
    parser.add_argument('--history', required=True, type=Path)
    parser.add_argument('--review', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding='utf-8'))
    html_path = args.history / 'PUD-Purchase-Dashboard.html'
    html = html_path.read_text(encoding='utf-8')
    def embedded(name):
        match = re.search(r'<script id="' + name + r'" type="application/json">(.*?)</script>', html, re.S)
        if not match:
            raise ValueError(f'Dashboard schema not recognized: {name}')
        return json.loads(match.group(1))
    data, vendors = embedded('purchase-data'), embedded('vendor-data')
    indexed = [(r, ' '.join(str(r.get(k, '')) for k in ['product', 'description', 'extra'])) for r in data]
    candidates = []
    for item in review['items']:
        hits = []
        for row, hay in indexed:
            queries = [q for q in item['search_terms'] if q.strip() and (q.upper() in hay.upper() or (norm(q) and norm(q) in norm(hay)))]
            if queries:
                hits.append({**row, 'matched_queries': queries})
        candidates.append({'line': item['line'], 'records': sorted(hits, key=lambda r:r['date'], reverse=True)})
    if args.mode == 'search':
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(candidates, indent=2), encoding='utf-8')
        print(f'Candidate groups: {len(candidates)}. No matches automatically approved.')
        return
    items = review['items']
    if not review.get('extraction_verified'):
        raise ValueError('Complete visual extraction review before building; set extraction_verified true.')
    if len(items) != review['expected_item_count'] or len({i['line'] for i in items}) != len(items):
        raise ValueError('Item count or duplicate research line index failed.')
    readers, used_sources = {}, {html_path.resolve(), args.review.resolve(), Path(review['rfq']).resolve()}
    vendor_source = args.history / 'vendorlist2026rotated.pdf'
    if vendor_source.exists(): used_sources.add(vendor_source.resolve())
    def enrich(row):
        row = dict(row)
        match = re.match(r'^\*\s*(\d+)\s+(.+)$', row['product'])
        vendor = vendors.get(match[1], {}) if match else {}
        row['supplier'] = ('Stocked item' if not row['special'] else (f"{vendor.get('name') or 'Name unavailable'} (#{match[1]})" if match else 'Special order; supplier not identified'))
        row['vendor_page'] = vendor.get('page')
        source = args.history / row['source']
        row['verification'] = 'Unverified: source file missing'
        if source.exists():
            used_sources.add(source.resolve())
            try:
                if source not in readers: readers[source] = PdfReader(source)
                text = readers[source].pages[row['page']-1].extract_text() or ''
                date = datetime.strptime(row['date'], '%Y-%m-%d').strftime('%m/%d/%y')
                matches = [line for line in text.splitlines() if row['product'] in line and date in line and row['price'] in line]
                row['verification'] = 'Source text verified' if matches else 'Unverified: inspect source page visually'
                row['source_line'] = matches[0] if matches else None
            except Exception as exc:
                row['verification'] = f'Unverified: {type(exc).__name__}; inspect source page'
        for attestation in review.get('visual_verifications', []):
            if all(attestation.get(k) == row[k] for k in ['product', 'source', 'page', 'date', 'price']) and attestation.get('note'):
                row['verification'] = 'Agent visual review: ' + attestation['note']
        return row
    def latest(code):
        rows = [r for r in data if r['product'] == code]
        if not rows: raise ValueError(f'Selected code is not in dashboard: {code}')
        date = max(r['date'] for r in rows)
        tied = [r for r in rows if r['date'] == date]
        result = enrich(tied[0])
        result['tied_records'] = [enrich(r) for r in tied[1:]] if len({r['price'] for r in tied}) > 1 else []
        return result
    results = [{**i, 'selected_record': latest(i['selected']) if i.get('selected') else None,
                'alternative_records': [latest(c) for c in i.get('alternatives', [])]} for i in items]
    for item in results:
        if item['selected_record'] and item['selected_record']['special'] and not item.get('stock_selection_justification', '').strip():
            raise ValueError(f"Line {item['line']}: special-order selection requires stock_selection_justification after reviewing stock candidates.")
    def safe(value):
        return str(value if value is not None else 'Not provided').replace('|', '/').replace('\n', ' ')
    def detail(row):
        return f"`{row['product']}` - {row['supplier']}; {row['date']}; **${float(row['price']):.2f}** (raw `{row['price']}`); account {row['account']}; {row['source']}, p. {row['page']}. {row['verification']}."
    counts = Counter(i['status'] for i in items)
    lines = [f"# {review.get('rfq_date') or Path(review['rfq']).stem} RFQ - purchase-history review", '',
             f"Reviewed {review['review_date']}. {len(items)} RFQ items; {len(data):,} history records; accounts {', '.join(sorted({r['account'] for r in data}))}. Supplied snapshot dates: {min(r['date'] for r in data)} through {max(r['date'] for r in data)}.", '',
             'Last Net Price is historical PUD-to-Faber price, not supplier cost or a new quote. Last date belongs to the PUD account history. Historical pricing units may be unknown; no totals are calculated from uncertain units.', '',
             '; '.join(f'{n} {status}' for status, n in counts.items()) + '.', '',
             '## Primary findings', '',
             '| Line | PUD Item ID | Request | Historical code | Supplier / type | Last date | Last Net Price | Match |',
             '|---|---|---|---|---|---|---:|---|']
    for i in results:
        r = i['selected_record']
        values = [i['line'], i.get('item_id'), f"{safe(i.get('quantity'))} {i.get('unit') or 'unit unknown'}: {i['description']}",
                  r['product'] if r else 'No match', r['supplier'] if r else 'Not established', r['date'] if r else '-',
                  f"${float(r['price']):.2f}" if r else '-', i['status'] + ('; price tie' if r and r['tied_records'] else '')]
        lines.append('| ' + ' | '.join(safe(v) for v in values) + ' |')
    lines += ['', 'Stocked means history classification, not current availability. Supplier names may be truncated in the supplied vendor master. See per-item verification and uncertainty notes.', '', '## Evidence and decisions', '']
    for i in results:
        r = i['selected_record']
        lines += [f"### Line {i['line']} - {i.get('item_id') or 'Item ID not provided'}", '',
                  f"RFQ page(s): {', '.join(map(str,i['pages']))}. Listed options: {'; '.join(i['acceptable']) or 'None provided'}.", '',
                  f"RFQ notes: {i.get('rfq_notes') or 'None'}. Quantity: {safe(i.get('quantity'))} {i.get('unit') or 'unit not provided'}.", '',
                  detail(r) if r else '**No established history match.**', '', i['assessment'], '']
        if r and r['special']:
            lines += ["Why special order instead of stock: " + i['stock_selection_justification'], '']
        if r:
            lines += [f"History description: {r['description']} {r.get('extra','')}", '']
            if r['vendor_page']: lines += [f"Vendor reference: vendorlist2026rotated.pdf, p. {r['vendor_page']} (dashboard mapping).", '']
            if r['tied_records']:
                lines += ['**Same-date account price conflict: no unique latest price is established.**', '']
                lines += [detail(t) for t in r['tied_records']] + ['']
        if 'historical_price_basis' in i:
            lines += [f"Historical price basis: {i['historical_price_basis']}. Pack size: {safe(i.get('pack_size'))}; interpreted individual quantity: {safe(i.get('interpreted_each_quantity'))}; order packs: {safe(i.get('order_pack_quantity'))}. {i.get('quantity_status','')}. Basis evidence: {safe(i.get('price_basis_evidence'))}.", '']
        if i['alternative_records']:
            lines += ['Other relevant records (not automatically approved equivalents):', '']
            lines += ['- ' + detail(a) for a in i['alternative_records']] + ['']
    if review.get('workflow_notes'):
        lines += ['## Friction observed and repeatable workflow', ''] + [safe(n) for n in review['workflow_notes']]
    args.out.mkdir(parents=True, exist_ok=True)
    markdown = '\n'.join(lines)
    (args.out / 'report.md').write_text(markdown, encoding='utf-8')
    audit = {'source_sha256': {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in used_sources},
             'review':review, 'items':results, 'candidates':candidates}
    (args.out / 'evidence.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    write_pdf(markdown, args.out / 'report.pdf', results)
    print(f"Built {args.out / 'report.pdf'}; {len(results)} lines. Render and visually inspect before delivery.")

if __name__ == '__main__': main()
