"""Dashboard-first RFQ research: pull RFQ lines from the order portal, search the history API, push results back.

  python portal_case.py queue
  python portal_case.py pull  <order-folder> --case CASE_DIR [--claim --by NAME]
  python portal_case.py search --case CASE_DIR [--limit 6]
  python portal_case.py find TERM [TERM ...] [--all] [--stock | --special] [--limit 15]
  python portal_case.py find --code "RAW PRODUCT"
  python research.py build --review CASE_DIR/review.json --out CASE_DIR/output      (history API by default)
  python portal_case.py push --case CASE_DIR [--by NAME] [--dry-run]

Portal URLs default to the Tailscale hosts; override with PUD_ORDER_PORTAL / PUD_HISTORY_URL.
Nothing here sends RFQs, orders goods, or sets prices. `push` only writes recommendations and the report PDF.
"""
import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from history_source import DEFAULT_URL as HISTORY_URL, http_json, norm

ORDER_PORTAL = os.environ.get('PUD_ORDER_PORTAL', 'https://ctr3.tail946e94.ts.net:8443').rstrip('/')
PRICE_MEANING = 'Historical PUD-to-Faber Last Net Price; original price unit may be unknown'
POST_HEADERS = {'X-Portal': '1'}


def portal_get(path):
    return http_json(ORDER_PORTAL + path)


def portal_post(path, body=None, raw=None, content_type='application/json'):
    data = raw if raw is not None else json.dumps(body or {}).encode()
    req = urllib.request.Request(ORDER_PORTAL + path, data=data, method='POST',
                                 headers={'Content-Type': content_type, **POST_HEADERS})
    try:
        with urllib.request.urlopen(req, timeout=120, context=ssl.create_default_context()) as r:
            return json.load(r)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f'Portal {exc.code} on {path}: {exc.read().decode(errors="replace")[:400]}')


def clip(text, n):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    return text if len(text) <= n else text[:n - 1] + '…'


def as_qty(v):
    return int(v) if isinstance(v, float) and v.is_integer() else v


def clean_part(part):
    return re.sub(r'^[#\s]+|[\s,.;:]+$', '', part or '')


# ------------------------------------------------------------------ queue
def cmd_queue(args):
    q = portal_get('/api/research/queue')
    orders = q.get('orders') or []
    if not orders:
        print('No orders need research.')
        return
    for o in orders:
        print(f"{o['folder']} | {clip(o.get('title'), 60)} | status {o.get('status')} | RFQ received {o.get('rfq_received')} | "
              f"{len(o.get('unresearched_lines') or [])} line(s) unresearched")


# ------------------------------------------------------------------ pull
def extraction_warnings(order, lines):
    warn = []
    docs = [d for d in order.get('documents', []) if d.get('type') in ('rfq', 'rfq_addon')]
    parsed_docs = {l.get('source_file') for l in lines if l.get('source_file')}
    for d in docs:
        if d['file'].lower().endswith('.pdf') and d['file'] not in parsed_docs:
            warn.append(f"RFQ document {d['file']} has no parsed lines; check it or add lines in the RFQ tab.")
    if not lines:
        warn.append('Order has no RFQ lines.')
    for l in lines:
        tag = f"line {l.get('line')} ({l.get('item_id') or 'no item ID'})"
        if not l.get('item_id'):
            warn.append(f'{tag}: missing PUD item ID.')
        if l.get('qty') in (None, 0):
            warn.append(f'{tag}: missing quantity.')
        if not (l.get('description') or '').strip():
            warn.append(f'{tag}: missing description.')
        if not l.get('options') and not (l.get('details') or '').strip():
            warn.append(f'{tag}: no manufacturer/catalog options parsed (confirm the RFQ lists none).')
    ids = [l.get('item_id') for l in lines if l.get('item_id')]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        warn.append(f"Duplicate item IDs across RFQ lines: {', '.join(dupes)}.")
    po_ids = {p.get('item_id') for p in order.get('line_items') or [] if p.get('item_id') and not p.get('removed')}
    if po_ids:
        missing = sorted(po_ids - set(ids))
        if missing:
            warn.append(f"PO has item IDs not in the RFQ lines: {', '.join(missing)}.")
    return warn


def cmd_pull(args):
    folder = args.order
    order = portal_get(f'/api/orders/{urllib.parse.quote(folder)}?compact=1')
    lines = order.get('rfq_lines') or []
    case = Path(args.case)
    case.mkdir(parents=True, exist_ok=True)
    review_path = case / 'review.json'
    if review_path.exists() and not args.force:
        raise SystemExit(f'{review_path} exists; pass --force to overwrite agent work.')
    docs = [{'file': d['file'], 'type': d['type'], 'date': d.get('date'),
             'url': ORDER_PORTAL + '/files/' + urllib.parse.quote(folder) + '/' + urllib.parse.quote(d['file'])}
            for d in order.get('documents', []) if d.get('type') in ('rfq', 'rfq_addon')]
    warnings = extraction_warnings(order, lines)
    items = []
    for n, l in enumerate(lines, 1):
        options = l.get('options') or []
        parts = [clean_part(o.get('part')) for o in options]
        terms = []
        for p in parts:
            if len(norm(p)) >= 4 and p not in terms:
                terms.append(p)
        for o in options:  # manufacturer only when no usable part number was listed
            if not clean_part(o.get('part')) and o.get('manufacturer') and o['manufacturer'] not in terms:
                terms.append(o['manufacturer'])
        items.append({
            'line': n, 'printed_line': str(l.get('line')) if l.get('line') is not None else None,
            'portal_key': l['key'], 'source_document': l.get('source_file') or 'manual entry',
            'item_id': l.get('item_id'), 'quantity': as_qty(l.get('qty_requested') if l.get('qty_adjusted') else l.get('qty')), 'unit': l.get('uom'), 'pages': [],
            'description': l.get('description') or '',
            'acceptable': [' '.join(filter(None, [o.get('manufacturer'), clean_part(o.get('part'))])) + (f" ({o['description']})" if o.get('description') else '') for o in options],
            'rfq_notes': ' '.join(filter(None, [(l.get('details') or '').strip(),
                                                f"Riley is quoting {as_qty(l['qty'])} {l.get('uom') or ''}".strip() + '.' if l.get('qty_adjusted') else ''])) or None,
            'search_terms': terms, 'search_match': 'any',
            'selected': None, 'status': '', 'alternatives': [], 'assessment': '',
        })
    rfq_dates = sorted(d.get('date') or '' for d in docs if d.get('date'))
    title_bits = [order.get('po') and (order['po'].get('number') if isinstance(order['po'], dict) else order['po']),
                  order.get('rfq') and (order['rfq'].get('number') if isinstance(order['rfq'], dict) else order['rfq'])]
    review = {
        'rfq': f'{ORDER_PORTAL}/api/orders/{folder}', 'rfq_source': 'order-portal',
        'portal': {'base': ORDER_PORTAL, 'folder': folder, 'order_id': order.get('order_id'), 'documents': docs},
        'title': ' / '.join(str(b) for b in title_bits if b) or folder,
        'rfq_date': (rfq_dates[0][:10] if rfq_dates else None), 'review_date': dt.date.today().isoformat(),
        'expected_item_count': len(items), 'extraction_source': 'order-portal', 'extraction_warnings': warnings,
        'extraction_verified': False, 'price_meaning': PRICE_MEANING, 'workflow_notes': [], 'items': items,
    }
    review_path.write_text(json.dumps(review, indent=2), encoding='utf-8')
    (case / 'order-snapshot.json').write_text(json.dumps({'pulled_at': dt.datetime.now().isoformat(timespec='seconds'),
                                                         'order': {k: order.get(k) for k in ('order_id', 'folder', 'title', 'status', 'rfq', 'po', 'documents', 'research', 'rfq_lines', 'line_items')}},
                                                        indent=2), encoding='utf-8')
    if args.claim:
        portal_post(f'/api/orders/{urllib.parse.quote(folder)}/research', {'status': 'in_progress', 'by': args.by, 'notes': f'Research started by {args.by}'})
    print(f"{folder}: {order.get('title')} | {len(items)} RFQ lines -> {review_path}")
    for i in items:
        opts = '; '.join(i['acceptable']) or '-'
        qty = '?' if i['quantity'] is None else f"{i['quantity']:g}"
        print(f"  {i['line']:>2} [p{i['printed_line']}] {i['item_id']} | {qty} {i['unit'] or ''} | {clip(i['description'], 70)}"
              + (f" | notes: {clip(i['rfq_notes'], 60)}" if i['rfq_notes'] else '') + f" | options: {clip(opts, 110)}")
    if warnings:
        print('EXTRACTION WARNINGS (open the RFQ document for these, then set extraction_verified true):')
        for w in warnings:
            print('  - ' + w)
    else:
        print('No extraction warnings: work from these lines; the RFQ images are not needed.')
    print('Next: add descriptive search terms per line (stock is found by description), then `portal_case.py search`.')


# ------------------------------------------------------------------ search / find
def row_line(r):
    kind = 'STK ' if r.get('item_type') == 'stocked' or r.get('special') is False else 'SPEC'
    desc = ' | '.join(filter(None, [r.get('description'), r.get('extra')]))
    sup = '' if kind == 'STK ' else f" {clip(r.get('supplier'), 26)}"
    return f"{r['date']} {kind} {r['product']!r:24} ${float(r['price']):>9.2f} acct {r['account']}{sup} | {clip(desc, 60)} | {r['source']} p{r['page']}"


def cmd_search(args):
    case = Path(args.case)
    review = json.loads((case / 'review.json').read_text(encoding='utf-8'))
    items = review['items']
    lines = [{'id': str(i['line']), 'terms': i['search_terms'], 'match': i.get('search_match', 'any')} for i in items]
    res = http_json(HISTORY_URL + '/api/search/batch', {'limit': max(args.limit, 50), 'lines': lines})['results']
    (case / 'candidates.json').write_text(json.dumps({'history': HISTORY_URL, 'searched_at': dt.datetime.now().isoformat(timespec='seconds'),
                                                    'results': res}, indent=2), encoding='utf-8')
    for item, r in zip(items, res):
        print(f"\n== {item['line']} {item.get('item_id')} {clip(item['description'], 60)} | terms: {', '.join(item['search_terms']) or '-'}")
        if r.get('error'):
            print(f"   {r['error']} (add search_terms)")
            continue
        print(f"   {r['total']} hits: {r['stocked_total']} stocked, {r['special_total']} special")
        for row in (r.get('stocked') or [])[:args.limit]:
            print('   ' + row_line(row) + f"  <- {', '.join(row.get('matched_queries') or [])}")
        for row in (r.get('special') or [])[:args.limit]:
            print('   ' + row_line(row) + f"  <- {', '.join(row.get('matched_queries') or [])}")
        if not r['stocked_total']:
            print('   (no stocked hits: search descriptive terms before choosing special order)')
    print(f"\nSaved {case / 'candidates.json'}.")


def cmd_find(args):
    if args.code:
        res = http_json(HISTORY_URL + '/api/products', {'codes': args.code, 'verify': True})
        for code, v in res.items():
            if not v['found']:
                print(f'{code!r}: not in history')
                continue
            print(f"{code!r}: {v['count']} row(s); latest {row_line(v['latest'])} | {v['latest']['supplier']} | {v['latest']['verification']}")
            for t in v['tied_records']:
                print('   same-date price tie: ' + row_line(t))
            for row in v['rows'][1:args.limit]:
                print('   ' + row_line(row))
        return
    if not args.terms:
        raise SystemExit('Give search terms or --code')
    q = [('q', t) for t in args.terms] + [('match', 'all' if args.all else 'any'), ('limit', args.limit), ('compact', 1)]
    if args.stock:
        q.append(('type', 'stock'))
    if args.special:
        q.append(('type', 'special'))
    r = http_json(HISTORY_URL + '/api/search?' + urllib.parse.urlencode(q))
    print(f"{r['total']} hits ({r['stocked_total']} stocked, {r['special_total']} special) for {' + '.join(args.terms) if args.all else ' | '.join(args.terms)}")
    for row in r['results']:
        print('  ' + row_line(row))


# ------------------------------------------------------------------ push
def cmd_push(args):
    case = Path(args.case)
    review = json.loads((case / 'review.json').read_text(encoding='utf-8'))
    folder = (review.get('portal') or {}).get('folder') or args.order
    if not folder:
        raise SystemExit('review.json has no portal.folder; pass --order.')
    evidence = json.loads((case / 'output' / 'evidence.json').read_text(encoding='utf-8'))
    pdf = (case / 'output' / 'report.pdf').read_bytes()
    evidence.pop('candidates', None)
    name = re.sub(r'[^A-Za-z0-9._-]+', '-', f"{review.get('title') or folder}").strip('-') + '.pdf'
    date = review.get('review_date') or dt.date.today().isoformat()
    q = f'/api/orders/{urllib.parse.quote(folder)}'
    if args.dry_run:
        print(f'Dry run: would upload research PDF {name} ({len(pdf):,} bytes) and import {len(evidence["items"])} recommendations to {folder}.')
        return
    order = portal_get(q + '?compact=1')
    for d in order.get('documents', []):  # replace an earlier copy of this report instead of stacking duplicates
        if d.get('type') == 'research' and d['file'].endswith('_Research_' + name):
            portal_post(q + '/documents/remove', {'file': d['file']})
    up = portal_post(q + '/documents?' + urllib.parse.urlencode({'type': 'research', 'date': date, 'filename': name}), raw=pdf, content_type='application/pdf')
    doc = (up.get('document') or {}).get('file')
    res = portal_post(q + '/research/import', {'evidence': evidence, 'by': args.by, 'report_file': doc})
    imp = res.get('import') or {}
    order = res.get('order') or res
    lines = order.get('rfq_lines') or []
    done = sum(1 for l in lines if l.get('recommendation'))
    print(f"Uploaded {doc}. Imported {len(imp.get('matched', []))} recommendation(s); unmatched: {imp.get('unmatched') or 'none'}.")
    print(f"Research status: {(order.get('research') or {}).get('status')} ({done}/{len(lines)} lines have recommendations).")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('queue')
    s = sub.add_parser('pull'); s.add_argument('order'); s.add_argument('--case', required=True)
    s.add_argument('--claim', action='store_true', help='mark research in_progress on the portal'); s.add_argument('--by', default='research-agent')
    s.add_argument('--force', action='store_true')
    s = sub.add_parser('search'); s.add_argument('--case', required=True); s.add_argument('--limit', type=int, default=6)
    s = sub.add_parser('find'); s.add_argument('terms', nargs='*'); s.add_argument('--all', action='store_true')
    g = s.add_mutually_exclusive_group(); g.add_argument('--stock', action='store_true'); g.add_argument('--special', action='store_true')
    s.add_argument('--code', action='append'); s.add_argument('--limit', type=int, default=15)
    s = sub.add_parser('push'); s.add_argument('--case', required=True); s.add_argument('--order'); s.add_argument('--by', default='research-agent')
    s.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    {'queue': cmd_queue, 'pull': cmd_pull, 'search': cmd_search, 'find': cmd_find, 'push': cmd_push}[args.cmd](args)


if __name__ == '__main__':
    main()
