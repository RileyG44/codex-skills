"""Candidate retrieval and deterministic reporting; product decisions belong to the agent.

--history accepts the local dashboard folder or the read-only history API URL
(default: $PUD_HISTORY, else https://ctr3.tail946e94.ts.net:8444).
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from history_source import open_history
from pdf_report import write_pdf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['search', 'build'])
    parser.add_argument('--history', help='history folder or API URL')
    parser.add_argument('--review', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    review = json.loads(args.review.read_text(encoding='utf-8'))
    history = open_history(args.history)
    items = review['items']
    candidates = history.candidates(items)
    if args.mode == 'search':
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(candidates, indent=2), encoding='utf-8')
        print(f'Candidate groups: {len(candidates)}. No matches automatically approved.')
        return
    portal = review.get('extraction_source') == 'order-portal'
    if not review.get('extraction_verified') and not (portal and not review.get('extraction_warnings')):
        raise ValueError('Extraction not established: review the RFQ document (or resolve extraction_warnings from the order portal) and set extraction_verified true.')
    if len(items) != review['expected_item_count'] or len({i['line'] for i in items}) != len(items):
        raise ValueError('Item count or duplicate research line index failed.')
    codes = sorted({c for i in items for c in ([i['selected']] if i.get('selected') else []) + list(i.get('alternatives', []))})
    found = history.products(codes)
    missing = [c for c in codes if not found.get(c, {}).get('found')]
    if missing:
        raise ValueError(f'Selected code is not in dashboard: {", ".join(missing)}')
    attest = review.get('visual_verifications', [])

    def apply_attestations(row):
        for attestation in attest:
            if all(attestation.get(k) == row[k] for k in ['product', 'source', 'page', 'date', 'price']) and attestation.get('note'):
                row['verification'] = 'Agent visual review: ' + attestation['note']
        return row

    def latest(code):
        result = apply_attestations(dict(found[code]['latest']))
        result['tied_records'] = [apply_attestations(dict(t)) for t in found[code]['tied_records']]
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
    meta = history.meta()
    counts = Counter(i['status'] for i in items)
    heading = f"{review['title']} - purchase-history review" if review.get('title') else f"{review.get('rfq_date') or Path(str(review['rfq'])).stem} RFQ - purchase-history review"
    lines = [f"# {heading}", '',
             f"Reviewed {review['review_date']}. {len(items)} RFQ items; {meta['records']:,} history records; accounts {', '.join(meta['accounts'])}. Supplied snapshot dates: {meta['date_min']} through {meta['date_max']}.", '',
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
        if i.get('pages'):
            where = f"RFQ page(s): {', '.join(map(str, i['pages']))}."
        else:
            where = f"RFQ source: order portal, printed line {i.get('printed_line') or i['line']} ({i.get('source_document') or 'manual entry'})."
        lines += [f"### Line {i['line']} - {i.get('item_id') or 'Item ID not provided'}", '',
                  f"{where} Listed options: {'; '.join(i['acceptable']) or 'None provided'}.", '',
                  f"RFQ notes: {(i.get('rfq_notes') or 'None').rstrip('.')}. Quantity: {safe(i.get('quantity'))} {i.get('unit') or 'unit not provided'}.", '',
                  detail(r) if r else '**No established history match.**', '', i['assessment'], '']
        if r and r['special']:
            lines += ["Why special order instead of stock: " + i['stock_selection_justification'], '']
        if r:
            lines += [f"History description: {r['description']} {r.get('extra', '')}", '']
            if r['vendor_page']:
                lines += [f"Vendor reference: vendorlist2026rotated.pdf, p. {r['vendor_page']} (dashboard mapping).", '']
            if r['tied_records']:
                lines += ['**Same-date account price conflict: no unique latest price is established.**', '']
                lines += [detail(t) for t in r['tied_records']] + ['']
        if 'historical_price_basis' in i:
            lines += [f"Historical price basis: {i['historical_price_basis']}. Pack size: {safe(i.get('pack_size'))}; interpreted individual quantity: {safe(i.get('interpreted_each_quantity'))}; order packs: {safe(i.get('order_pack_quantity'))}. {i.get('quantity_status', '')}. Basis evidence: {safe(i.get('price_basis_evidence'))}.", '']
        if i['alternative_records']:
            lines += ['Other relevant records (not automatically approved equivalents):', '']
            lines += ['- ' + detail(a) for a in i['alternative_records']] + ['']
    if review.get('workflow_notes'):
        lines += ['## Friction observed and repeatable workflow', ''] + [safe(n) for n in review['workflow_notes']]
    args.out.mkdir(parents=True, exist_ok=True)
    markdown = '\n'.join(lines)
    (args.out / 'report.md').write_text(markdown, encoding='utf-8')
    hashes = history.source_hashes()
    rfq_path = Path(str(review['rfq']))
    if not str(review['rfq']).startswith(('http://', 'https://')) and rfq_path.is_file():
        hashes[str(rfq_path.resolve())] = hashlib.sha256(rfq_path.read_bytes()).hexdigest()
    hashes[str(args.review.resolve())] = hashlib.sha256(args.review.read_bytes()).hexdigest()
    audit = {'history': {'kind': history.kind, 'location': history.label, **meta}, 'source_sha256': hashes,
             'review': review, 'items': results, 'candidates': candidates}
    (args.out / 'evidence.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
    write_pdf(markdown, args.out / 'report.pdf', results)
    print(f"Built {args.out / 'report.pdf'}; {len(results)} lines. Render and visually inspect before delivery.")


if __name__ == '__main__':
    main()
