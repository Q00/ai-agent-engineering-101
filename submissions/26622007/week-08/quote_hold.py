"""Default-OFF quote hold shadow. No runtime treatment or economic attribution."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing

from shop_stock import needs, canonical_task, market_unit


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def classify(text):
    """One exact affirmative grammar; everything else remains unknown.

    This is a reviewed language contract, NOT evidence that live buyers use it.
    No quoted, negative, embedded, multi-instruction, or truncated matches.
    """
    if not isinstance(text, str) or len(text) > 500:
        return 'unknown'
    if any(c in text for c in '\n\r;\"\'`<>'):
        return 'unknown'
    legacy = r'Quote only for G[0-9a-f]{8}\. I do not authorize purchase or payment\.'
    if re.fullmatch(legacy, text):
        return 'explicit_quote_only_no_authorization'
    grammar = (r'quote only, no purchase authorization: ([\w ,/+\-]{1,180}) '
               r'\(G[0-9a-f]{8}\)\. State current asking price and ([A-Za-z]{1,24}) availability\.')
    match = re.fullmatch(grammar, text, re.I)
    if not match or len(re.findall(r'G[0-9a-f]{8}', text, re.I)) != 1:
        return 'unknown'
    # A single availability label cannot introduce a separate purchase instruction.
    if match[2].lower() in {"no", "not", "never", "authorize", "buy", "pay", "sell",
                            "reserve", "accept", "ignore", "execute", "run", "override",
                            "permission", "payment", "purchase", "authorization", "cancel",
                            "transfer", "charge", "order"}:
        return 'unknown'
    # Description is data, never a second instruction or exception to no-authorization.
    if re.search(r'\b(?:no|not|never|except|unless|but|however|also|then|instead|authorize|authorization|purchase|payment|pay|buy|sell|order|execute|run|ignore|override|accept|checkout|reserve|cancel|do|please|must|now|grant|allow|permission|transfer|charge|place|send|ship|process|proceed|confirm|finalize|complete|immediately)\b'
                 r'|구매|결제|승인|실행|취소|하지|아니|제외|보내|송금|허용|무시|해줘|하라|하세요', match[1], re.I):
        return 'unknown'
    return 'explicit_quote_only_no_authorization'


class QuoteHold:
    def __init__(self, root=None):
        self.base = Path(root) if root is not None else Path(os.environ.get('MARKET_DIR', '.market'))
        self.root = self.base / 'quote_hold'
        self.lock = threading.RLock()
        self.errors = 0
        self.revisions = {}
        self.projections = {}
        self.process_epoch = str(uuid.uuid4())

    def config(self, now=None):
        now = time.time() if now is None else now
        try:
            # No mkdir, DB, trace, selection or inventory operation on disabled paths.
            if self.root.is_symlink() or (self.root / 'config.json').is_symlink():
                return None
            if (self.root / 'disabled').exists():
                return None
            path = self.root / 'config.json'
            if path.stat().st_size > 4096:
                return None
            cfg = json.loads(path.read_text())
            if not isinstance(cfg, dict):
                return None
            if cfg.get('enabled') is not True or cfg.get('mode') != 'shadow':
                return None
            if type(cfg.get('owner_pid')) is not int or cfg['owner_pid'] != os.getpid():
                return None
            if not canonical_task(cfg.get('id')):
                return None
            if any(type(cfg.get(k)) not in (int, float) or not math.isfinite(cfg[k])
                   for k in ('created_at', 'start', 'end')):
                return None
            if not cfg['created_at'] < cfg['start'] or not 1800 <= cfg['end']-cfg['start'] <= 3600:
                return None
            return cfg if cfg['start'] <= now < cfg['end'] else None
        except (OSError, ValueError, TypeError):
            return None

    def pre_pick_ok(self, others=(), now=None):
        if not self.config(now):
            return True
        from at_floor_exact_trial import AtFloorExactTrial
        return not any(AtFloorExactTrial._other_active(c, time.time() if now is None else now)
                       for c in others)

    def _db(self):
        path = self.root / 'journal.sqlite3'
        if any(Path(str(path)+suffix).is_symlink() for suffix in ('', '-journal', '-wal', '-shm')):
            raise ValueError('journal_symlink')
        db = sqlite3.connect(path, timeout=0.2)
        db.execute('CREATE TABLE IF NOT EXISTS requests (task TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        return db

    def touch(self, task):
        """Invalidate a pending first projection before a concurrent turn can mutate a deal."""
        with self.lock:
            if task in self.revisions:
                self.revisions[task] += 1
                self.projections.pop(task, None)

    def _health(self, task, window, event):
        """Independent fsynced ingress/gap ledger; never called on the OFF path."""
        path = self.root / 'health.jsonl'
        if path.is_symlink():
            raise ValueError('health_symlink')
        data = (json.dumps(dict(task=task, window=window, event=event, at=time.time()))+'\n').encode()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.write(fd, data) != len(data):
                raise OSError('short_health_write')
            os.fsync(fd)
        finally:
            os.close(fd)

    def _ingress(self, row):
        folder = self.root / 'ingress'
        if folder.is_symlink():
            raise ValueError('ingress_symlink')
        folder.mkdir(exist_ok=True)
        path = folder / (row['task']+'.json')
        if path.is_symlink():
            raise ValueError('ingress_task_symlink')
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        try:
            data = json.dumps(row, allow_nan=False).encode()
            if os.write(fd, data) != len(data):
                raise OSError('short_ingress_write')
            os.fsync(fd)
        finally:
            os.close(fd)
        for directory in (folder, self.root):
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return True

    def begin(self, task, text, stock, *, first=True):
        with self.lock:
            cfg = self.config()
            if not cfg or not canonical_task(task) or not first:
                return None
            self.touch(task)
            # Durable task-wide dedup is independent of SQLite, including failed DB begins.
            if (self.root / 'ingress' / (task+'.json')).exists():
                return None
            health_failed = False
            try:
                self._health(task, cfg['id'], 'ingress_attempt')
            except Exception:
                self.errors += 1
                health_failed = True
            try:
                snapshot = stock.pool_snapshot()
                error = None
            except Exception:
                snapshot, error = None, 'snapshot_error'
                self.errors += 1
            # Unique full task over restarts/windows: re-entry never becomes another first request.
            try:
                row = dict(task=task, window=cfg['id'], at=time.time(), input_sha256=digest(text),
                           classification=classify(text), pool=snapshot, error=error,
                           outcome='pending', competitors=[], competitor_status='unknown',
                           source_sha256=self.source_hash(), config_sha256=digest(cfg),
                           process_epoch=self.process_epoch, carry_over='unknown', economic_effect=None,
                           ingress_health_error=health_failed)
                if not self._ingress(row):
                    return None
                with closing(self._db()) as db, db:
                    changed = db.execute('INSERT OR IGNORE INTO requests VALUES (?, ?)',
                                         (task, json.dumps(row))).rowcount
                if not changed:
                    return None
            except (OSError, ValueError, sqlite3.Error):
                self.errors += 1
                try:
                    self._health(task, cfg['id'], 'begin_error')
                except Exception:
                    self.errors += 1
                return None
            self.revisions[task] = 0
            row['revision'] = 0
            # Structural requirements are necessary; mere SKU coincidence is never qualified.
            try:
                self._competitors(row, text, stock)
            except Exception:
                self.errors += 1
                row['competitor_status'] = 'error'
                row['competitors'] = []
            return row

    @staticmethod
    def source_hash():
        return digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                       for name in ('quote_hold.py', 'shop_stock.py', 'seller.py', 'seller_drain.py',
                                    'hard_limits.py', 'profit_price.py')})

    def _competitors(self, row, text, stock):
        # Only an exact structured checkout policy gives a complete demand contract.
        match = re.fullmatch(r'Required checkout policy: (\{[^{}]{1,400}\})', text or '')
        if not match:
            return
        try:
            policy = json.loads(match[1])
            if set(policy) != {'sku', 'grades', 'min_hours_left', 'cert', 'warranty_hours'}:
                return
            if (not isinstance(policy['sku'], str) or not re.fullmatch(r'G[0-9a-f]{8}', policy['sku'])
                    or not isinstance(policy['grades'], list) or not policy['grades']
                    or any(g not in ('A', 'B', 'C') for g in policy['grades'])
                    or type(policy['cert']) is not bool
                    or any(type(policy[k]) not in (int, float) or not math.isfinite(policy[k])
                           or policy[k] < 0 for k in ('min_hours_left', 'warranty_hours'))):
                return
            row['competitor_status'] = 'inventory_compatible_only_economic_unknown'
            need = needs(text)
            row['requirements_sha256'] = digest(policy)
            for other, projection in list(self.projections.items()):
                if other == row['task']:
                    continue
                token, unit, warranty = projection
                state = stock.quote_competitor(token, need, sku=policy['sku'], warranty=warranty,
                                              required_warranty=policy['warranty_hours'])
                if state['reason'] == 'inventory_compatible_candidate':
                    row['competitors'].append(dict(task=other, unit=unit['unit'],
                                                   generation=token['generation'],
                                                   pool_sha256=state['pool_sha256'],
                                                   pool_count=len(state['snapshot']['units']),
                                                   snapshot=state['snapshot'], economic_status='unknown',
                                                   free_alternatives=state['free_alternatives'],
                                                   selection_blocked=state['selection_blocked']))
        except (ValueError, TypeError, KeyError):
            row['competitor_status'] = 'unknown'

    def finish(self, row, stock, *, deal=None, token=None, outcome='unknown'):
        if row is None:
            return
        with self.lock:
            try:
                cfg = self.config()
                row.update(outcome=outcome, selected_unit=(deal or {}).get('unit'),
                           selected_at=time.time(), projection='unknown', confirmed_eligible=False,
                           would_change=False, qualified_competitor=None,
                           structural_candidate=bool(row['competitors']), economic_status='unknown')
                row['hold_token'] = dict(token) if token else None
                row['policy_sha256'] = (deal or {}).get('reader_trial_policy_sha')
                if not cfg or cfg['id'] != row['window'] or digest(cfg) != row['config_sha256']:
                    row['projection'] = 'config_off'
                elif self.revisions.get(row['task']) != row['revision']:
                    row['projection'] = 'reentry'
                elif row['error']:
                    row['projection'] = 'snapshot_error'
                elif row['classification'] != 'explicit_quote_only_no_authorization':
                    row['projection'] = 'unknown_language'
                elif outcome != 'quote' or not deal or not token:
                    row['projection'] = outcome
                else:
                    rows = [u for u in row['pool']['units'] if u['unit'] == deal['unit']]
                    owned = (len(rows) == 1 and market_unit(deal['unit'])
                             and rows[0]['held_at'] is None and not rows[0]['agreed']
                             and not rows[0]['late'] and not rows[0]['sold']
                             and rows[0]['kind'] in ('food', 'goods')
                             and rows[0]['grade'] in ('A', 'B', 'C')
                             and (rows[0]['expires_at'] is None or rows[0]['expires_at'] > row['at']))
                    if not owned:
                        row['projection'] = 'owned_available_inventory_unconfirmed'
                    else:
                        row['confirmed_eligible'] = True
                        state = stock.compare_and_release_quote(token, rounds=deal['rounds'], shadow=True)
                        row['projection'] = state['reason']
                        row['would_change'] = state['reason'] == 'would_release'
                        row['remaining_seconds_projection'] = state.get('remaining_seconds')
                        if row['would_change']:
                            # Retain actual inventory fields for selection guard; never a reservation.
                            unit = next(u for u in stock.load()['units'] if u['unit'] == deal['unit'])
                            self.projections[row['task']] = (dict(token), dict(unit), deal.get('warranty', 0))
                with closing(self._db()) as db, db:
                    db.execute('UPDATE requests SET payload=? WHERE task=?', (json.dumps(row), row['task']))
            except Exception:
                self.errors += 1
                try:
                    self._health(row['task'], row['window'], 'finish_error')
                except Exception:
                    self.errors += 1
                # Pending row is an explicit unresolved outcome; never a successful quote denominator.

    def report(self, *, window):
        """Explicit-window read-only reconciliation. No mkdir/CREATE/config writes.

        complete stays false: simultaneous failure of both local stores cannot be
        disproved without an independent ingress census. Never certify N=0 as complete.
        """
        if not canonical_task(window):
            raise ValueError('canonical_window_required')
        with self.lock:
            rows, events, failures, db_tasks = {}, [], 0, set()
            health = self.root / 'health.jsonl'
            if health.exists():
                try:
                    for line in health.read_text().splitlines():
                        event = json.loads(line)
                        if event.get('window') == window:
                            events.append(event)
                except (OSError, ValueError):
                    failures += 1
            for event in events:
                task = event.get('task')
                if canonical_task(task):
                    rows[task] = dict(task=task,window=window,outcome='pending',error='ingress_gap')
            for path in (self.root / 'ingress').glob('*.json'):
                try:
                    row = json.loads(path.read_text())
                    if row.get('window') == window:
                        rows[row['task']] = row
                except (OSError, ValueError, KeyError):
                    failures += 1
            path = self.root / 'journal.sqlite3'
            if path.exists():
                try:
                    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=0.2)) as db:
                        for value, in db.execute('SELECT payload FROM requests'):
                            row = json.loads(value)
                            if row.get('window') == window:
                                rows[row['task']] = row
                                db_tasks.add(row['task'])
                except (OSError, ValueError, sqlite3.Error):
                    failures += 1
            result = sorted(rows.values(),key=lambda row:row['task'])
            errors = failures + sum(e.get('event','').endswith('_error') for e in events) + sum(bool(r.get('error')) + bool(r.get('ingress_health_error')) for r in result)
            pending = sum(r.get('outcome') == 'pending' for r in result)
            return dict(window=window,first_requests=len(result),complete=False,
                        completeness_reason='independent_ingress_reconciliation_required',
                        journal_complete=bool(events) and not errors and not pending,
                        confirmed_eligible=sum(r.get('confirmed_eligible',False) for r in result),
                        would_change=sum(r.get('would_change',False) for r in result),
                        structural_candidates=sum(r.get('structural_candidate',False) for r in result),
                        qualified_competitor=None,economic_status='unknown',pending=pending,
                        dropped=sum(r['task'] not in db_tasks for r in result),
                        unknown_ingress=True,
                        unknown=sum(not r.get('confirmed_eligible',False) for r in result),
                        errors=errors,process_errors=self.errors,rows=result)
