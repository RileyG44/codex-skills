"""Purchase-history backends: the local dashboard folder or the read-only history API (Tailscale :8444).

Both return the same row shapes so research.py builds identical reports either way.
"""
import hashlib
import json
import os
import re
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_FOLDER = 'C:/Users/CTR3/Documents/PUDSearchv2'
DEFAULT_URL = os.environ.get('PUD_HISTORY_URL', 'https://ctr3.tail946e94.ts.net:8444')


def norm(value):
    return re.sub('[^A-Z0-9]', '', str(value).upper())


def http_json(url, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method='POST' if data is not None else 'GET',
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return json.load(r)


def open_history(spec=None):
    spec = spec or os.environ.get('PUD_HISTORY') or DEFAULT_URL
    if str(spec).startswith(('http://', 'https://')):
        return ApiHistory(str(spec))
    return LocalHistory(Path(spec))


class LocalHistory:
    kind = 'local-folder'

    def __init__(self, folder):
        self.folder = folder
        self.html_path = folder / 'PUD-Purchase-Dashboard.html'
        html = self.html_path.read_text(encoding='utf-8')

        def embedded(name):
            match = re.search(r'<script id="' + name + r'" type="application/json">(.*?)</script>', html, re.S)
            if not match:
                raise ValueError(f'Dashboard schema not recognized: {name}')
            return json.loads(match.group(1))
        self.data, self.vendors = embedded('purchase-data'), embedded('vendor-data')
        self.indexed = [(r, ' '.join(str(r.get(k, '')) for k in ['product', 'description', 'extra'])) for r in self.data]
        self.readers, self.used = {}, {self.html_path.resolve()}
        vendor_source = folder / 'vendorlist2026rotated.pdf'
        if vendor_source.exists():
            self.used.add(vendor_source.resolve())

    @property
    def label(self):
        return str(self.folder)

    def meta(self):
        return {'records': len(self.data), 'accounts': sorted({r['account'] for r in self.data}),
                'date_min': min(r['date'] for r in self.data), 'date_max': max(r['date'] for r in self.data)}

    def candidates(self, items, limit=None):
        out = []
        for item in items:
            hits = []
            for row, hay in self.indexed:
                queries = [q for q in item['search_terms'] if q.strip() and (q.upper() in hay.upper() or (norm(q) and norm(q) in norm(hay)))]
                if queries:
                    hits.append({**row, 'matched_queries': queries})
            out.append({'line': item['line'], 'records': sorted(hits, key=lambda r: r['date'], reverse=True)})
        return out

    def enrich(self, row):
        from pypdf import PdfReader
        row = dict(row)
        match = re.match(r'^\*\s*(\d+)\s+(.+)$', row['product'])
        vendor = self.vendors.get(match[1], {}) if match else {}
        row['supplier'] = ('Stocked item' if not row['special'] else (f"{vendor.get('name') or 'Name unavailable'} (#{match[1]})" if match else 'Special order; supplier not identified'))
        row['vendor_page'] = vendor.get('page')
        source = self.folder / row['source']
        row['verification'] = 'Unverified: source file missing'
        if source.exists():
            self.used.add(source.resolve())
            try:
                if source not in self.readers:
                    self.readers[source] = PdfReader(source)
                text = self.readers[source].pages[row['page'] - 1].extract_text() or ''
                date = datetime.strptime(row['date'], '%Y-%m-%d').strftime('%m/%d/%y')
                matches = [line for line in text.splitlines() if row['product'] in line and date in line and row['price'] in line]
                row['verification'] = 'Source text verified' if matches else 'Unverified: inspect source page visually'
                row['source_line'] = matches[0] if matches else None
            except Exception as exc:
                row['verification'] = f'Unverified: {type(exc).__name__}; inspect source page'
        return row

    def products(self, codes):
        out = {}
        for code in codes:
            rows = [r for r in self.data if r['product'] == code]
            if not rows:
                out[code] = {'found': False, 'latest': None, 'tied_records': []}
                continue
            date = max(r['date'] for r in rows)
            tied = [r for r in rows if r['date'] == date]
            out[code] = {'found': True, 'latest': self.enrich(tied[0]),
                         'tied_records': [self.enrich(r) for r in tied[1:]] if len({r['price'] for r in tied}) > 1 else []}
        return out

    def source_hashes(self):
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.used}


class ApiHistory:
    kind = 'history-api'
    ROW_KEYS = ('product', 'description', 'extra', 'date', 'price', 'special', 'page', 'account', 'source',
                'supplier', 'vendor_page', 'verification', 'source_line', 'matched_queries')

    def __init__(self, base):
        self.base = base.rstrip('/')
        self._meta = http_json(self.base + '/api/meta')

    @property
    def label(self):
        return self.base

    def meta(self):
        return self._meta

    def _row(self, row):
        return {k: row.get(k) for k in self.ROW_KEYS if k in row}

    def candidates(self, items, limit=50):
        lines = [{'id': str(i['line']), 'terms': i['search_terms'], 'match': i.get('search_match', 'any')} for i in items]
        res = http_json(self.base + '/api/search/batch', {'limit': limit, 'compact': False, 'lines': lines})['results']
        out = []
        for item, r in zip(items, res):
            rows = sorted((r.get('stocked') or []) + (r.get('special') or []), key=lambda x: x['date'], reverse=True)
            out.append({'line': item['line'], 'total': r.get('total'), 'stocked_total': r.get('stocked_total'),
                        'special_total': r.get('special_total'), 'error': r.get('error'),
                        'records': [self._row(x) for x in rows]})
        return out

    def products(self, codes):
        if not codes:
            return {}
        res = http_json(self.base + '/api/products', {'codes': list(codes), 'verify': True})
        return {c: {'found': v['found'], 'latest': self._row(v['latest']) if v.get('latest') else None,
                    'tied_records': [self._row(t) for t in v.get('tied_records') or []]} for c, v in res.items()}

    def source_hashes(self):
        try:
            hashes = http_json(self.base + '/api/meta?hashes=1').get('sha256') or {}
        except Exception as exc:  # hashing is provenance, never a blocker
            hashes = {'error': f'{type(exc).__name__}: {exc}'}
        return {f'{self.base}/{k}': v for k, v in hashes.items()}
