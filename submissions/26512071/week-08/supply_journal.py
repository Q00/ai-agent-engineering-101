"""Exact supply-result evidence for restock-pending rows, read from the kit log (scripts/market_kit.py restock()).

The kit logs the restock hook's stdout as '[buyer] <line>' and then exactly one '[supply]' line per buy entry, in
order, and starts the next restock only after that thread ends. So the '[supply]' lines after a hook's buy line,
before the next hook output, are that invocation's results, one per entry. Anything that does not match exactly
(SKU, qty, catalog name, count, no kit restart in between) stays unknown: absence is never failure."""
import hashlib
import json
import re
import time
import uuid
from pathlib import Path

STAMP = re.compile(r'^(\d\d):(\d\d):(\d\d) ')
SUPPLIED = re.compile(r'^\[supply\] (\d+) x (.*) for (\d+(?:\.\d+)?), balance (-?\d+(?:\.\d+)?)$')
REFUSED = re.compile(r"^\[supply\] \{'sku': '([^']*)', 'qty': (\d+)\} refused: (\d+) ?(.*)$")
ANCHOR_WINDOW = 30  # s between the row's plan time and the logged hook output line
OPENS_WINDOW = 600  # s: a 429 'closed' body's opens_at must fall in (at, at+600] (plan waits <=90 s, kit <=3x30 s)


def scan(path, start=0):
    """Lines of the kit log with byte offset, 1-based line number (from start), length and sha256 of the raw bytes."""
    data = path.read_bytes()[start:]
    out, pos = [], 0
    for n, raw in enumerate(data.split(b'\n'), 1):
        if raw == b'' and pos == len(data): break
        text = raw.rstrip(b'\r').decode('utf-8', errors='replace')
        m = STAMP.match(text)
        out.append({'file':str(path), 'offset':start+pos, 'length':len(raw), 'line':n, 'text':text,
                    'sha256':hashlib.sha256(raw).hexdigest(),
                    'sod':int(m[1])*3600+int(m[2])*60+int(m[3]) if m else None})
        pos += len(raw)+1
    return out


def _body(line):
    return line['text'][9:] if line['sod'] is not None else line['text']


def _buy(line):
    b = _body(line)
    if not b.startswith('[buyer] {"buy"'): return None
    try:
        j = json.loads(b[len('[buyer] '):])
    except ValueError:
        return None
    return j['buy'] if isinstance(j, dict) and isinstance(j.get('buy'), list) else None


def _ends_segment(line):
    b = _body(line)
    return b.startswith('[buyer] {"buy"') or b.startswith('[buyer] [restock]') or b.startswith('[kit] market_kit')


def _evidence(line):
    return {k:line[k] for k in ('file', 'offset', 'length', 'line', 'sha256', 'text')}


def results(lines, i, buy):
    """Per buy entry (kit order, buy[:10], dicts only): the exact '[supply]' result line or None."""
    entries = [b for b in buy[:10] if isinstance(b, dict)]
    found = []
    for line in lines[i+1:]:
        if _ends_segment(line) or len(found) == len(entries): break
        if _body(line).startswith('[supply] '): found.append(line)
    return [(e, found[k] if k < len(found) else None) for k, e in enumerate(entries)]


def classify(entry, line, catalog):
    """(status, http_status, total) for one entry; only an exact SKU/qty(/catalog name) match counts."""
    if line is None: return 'unknown', None, None
    m = SUPPLIED.match(_body(line))
    if m:
        name = (catalog.get(entry.get('sku')) or {}).get('name')
        if int(m[1]) == entry.get('qty') and isinstance(name, str) and m[2] == name:
            return 'supplied', 200, float(m[3]) if '.' in m[3] else int(m[3])
        return 'unknown', None, None
    m = REFUSED.match(_body(line))
    if m:
        if m[1] != entry.get('sku') or int(m[2]) != entry.get('qty'): return 'unknown', None, None
        status = int(m[3])
        try:
            body = json.loads(m[4])
        except ValueError:
            body = None
        # Market-originated 4xx JSON error: the request was refused and created no unit.
        if 400 <= status < 500 and isinstance(body, dict) and isinstance(body.get('error'), str):
            return 'refused', status, None
        return 'unknown', status, None
    return 'unknown', None, None


def _opens_at(line):
    m = REFUSED.match(_body(line)) if line is not None else None
    try:
        body = json.loads(m[4]) if m else None
    except ValueError:
        return None
    v = body.get('opens_at') if isinstance(body, dict) else None
    return v if type(v) in (int, float) else None


def correlate_legacy(rows, lines, catalog, sod=None):
    """Evidence per row (same order). Rows of one plan share 'at'; the anchor is the unique hook buy line logged
    within ANCHOR_WINDOW s after 'at' whose entries cover those rows."""
    sod = sod or (lambda t: (lambda s:s.tm_hour*3600+s.tm_min*60+s.tm_sec)(time.localtime(t)))
    out = []
    for r in rows:
        start = sod(r['at'])
        want = sum(1 for x in rows if x['at'] == r['at'] and x['sku'] == r['sku'])
        cands = []
        for i, line in enumerate(lines):
            buy = _buy(line) if line['sod'] is not None and (line['sod']-start) % 86400 <= ANCHOR_WINDOW else None
            if buy and any(isinstance(b, dict) and b.get('sku') == r['sku'] and b.get('qty', 0) >= want for b in buy):
                cands.append((i, buy))
        if len(cands) != 1:
            out.append({'status':'unknown', 'reason':f'{len(cands)} anchor candidates'})
            continue
        i, buy = cands[0]
        hits = [(e, l) for e, l in results(lines, i, buy) if e.get('sku') == r['sku']]
        if len(hits) != 1:
            out.append({'status':'unknown', 'reason':f'{len(hits)} entries for sku', 'anchor':_evidence(lines[i])})
            continue
        entry, line = hits[0]
        status, http, total = classify(entry, line, catalog)
        opens = _opens_at(line)
        if status == 'refused' and opens is not None and not r['at'] < opens <= r['at']+OPENS_WINDOW:
            status = 'unknown'  # the absolute window time contradicts this plan's date: another day's line
        ev ={'status':status, 'anchor':_evidence(lines[i]), 'entry':entry, 'http_status':http, 'total':total}
        if line is not None: ev['result'] = _evidence(line)
        out.append(ev)
    return out


# --- Future rows: exact per-invocation results. Legacy correlate_legacy() above is dry-run evidence only.
MARKER = '[restock] invocation '
RELEASED = ('refused', 'not_attempted')  # provably no unit created: the reservation may be released


def log_path(state):
    return Path(state).resolve().parent/'logs'/'market-kit.log'


def begin(state, now):
    """A new hook invocation: unique id and the kit log size before the hook prints anything."""
    path = log_path(state)
    try:
        size = path.stat().st_size
    except OSError:
        size = None
    return {'invocation':uuid.uuid4().hex, 'at':now, 'log':str(path), 'log_offset':size}


def marker(inv):
    return MARKER+inv['invocation']


def buy_line(inv, buy):
    return json.dumps({'buy':buy, 'invocation':inv['invocation']})


def request(inv, index, unit, entry):
    """Stored on each pending row: which kit buy entry (index, unit-of-qty) of which invocation it is."""
    return {'invocation':inv['invocation'], 'index':index, 'unit':unit, 'sku':entry['sku'], 'qty':entry['qty'],
            'log':inv['log'], 'log_offset':inv['log_offset']}


def _segments(req):
    """Candidate kit log views from the recorded offset: the live log alone, then the rotated .log.1 from the
    offset followed by the whole live log (the live log may have regrown past the offset after a rotation)."""
    path = Path(req['log'])
    off = req.get('log_offset')
    if not isinstance(off, int) or not path.exists(): return []
    views = [scan(path, off)] if path.stat().st_size >= off else []
    old = path.with_suffix('.log.1')
    if old.exists() and old.stat().st_size >= off: views.append(scan(old, off)+scan(path, 0))
    return views


def _anchored(lines, inv):
    """Index of the unique hook buy line of this invocation (after its unique marker), or None."""
    marks = [i for i, l in enumerate(lines) if _body(l) == '[buyer] '+MARKER+inv]
    anchors = []
    for i, l in enumerate(lines):
        b = _body(l)
        if b.startswith('[buyer] {') and inv in b:
            try:
                j = json.loads(b[len('[buyer] '):])
            except ValueError:
                return None
            if isinstance(j, dict) and j.get('invocation') == inv and isinstance(j.get('buy'), list): anchors.append(i)
    if len(marks) != 1 or len(anchors) != 1 or marks[0] > anchors[0]: return None
    return anchors[0]


def _ends(line):
    b = _body(line)
    return _ends_segment(line) or b.startswith('[buyer] {') and '"invocation"' in b


def invocation_results(lines, i, catalog):
    """[(entry, status, http, total, line)] for the anchored buy, or None while the segment is incomplete."""
    buy = json.loads(_body(lines[i])[len('[buyer] '):])['buy']
    entries = [b for b in buy[:10] if isinstance(b, dict)]
    found, ended = [], False
    for line in lines[i+1:]:
        if _ends(line):
            ended = True
            break
        if _body(line).startswith('[supply] '): found.append(line)
        if len(found) == len(entries): break
    out, stop = [], False
    for k, e in enumerate(entries):
        if stop:
            out.append((e, 'not_attempted', None, None, None))  # the kit breaks its loop after a 402
            continue
        line = found[k] if k < len(found) else None
        if line is None and not ended: return None  # thread may still be running: look again next time
        status, http, total = classify(e, line, catalog)
        out.append((e, status, http, total, line))
        stop = status == 'refused' and http == 402
    return out


def resolve(state, pending, catalog):
    """Set supply_result/supply_evidence on rows with a request and no result yet, from exact evidence only.
    Returns the audit records to persist before the pending journal is rewritten. Unknown rows are untouched."""
    audit, by_inv = [], {}
    for r in pending:
        req = r.get('request')
        if isinstance(req, dict) and isinstance(req.get('invocation'), str) and 'supply_result' not in r:
            by_inv.setdefault(req['invocation'], []).append(r)
    for inv, rows in by_inv.items():
        found = [(v, _anchored(v, inv)) for v in _segments(rows[0]['request'])]
        found = [(v, i) for v, i in found if i is not None]
        lines, i = found[0] if found else (None, None)
        res = invocation_results(lines, i, catalog) if i is not None else None
        if res is None: continue
        for r in rows:
            req = r['request']
            k = req.get('index')
            if not isinstance(k, int) or not 0 <= k < len(res): continue
            entry, status, http, total, line = res[k]
            if entry.get('sku') != r.get('sku') or entry.get('sku') != req.get('sku') or entry.get('qty') != req.get('qty') \
                    or not isinstance(req.get('unit'), int) or not 0 <= req['unit'] < entry.get('qty', 0):
                continue
            if status not in RELEASED and status != 'supplied': continue
            ev = {'anchor':_evidence(lines[i]), 'entry':entry, 'http_status':http, 'total':total}
            if line is not None: ev['result'] = _evidence(line)
            r.update(supply_result=status, supply_evidence=ev)
            audit.append({'invocation':inv, 'supply_result':status, 'row':dict(r)})
    return audit
