"""Default OFF, fresh seller study: .97 M versus 1.00 M on 300k+ goods.

SKU x grade is the randomization unit; tasks are observations, not independent
samples. SQLite commits assignment and intended price before the caller uses it.
No inventory, payment, model, network, legacy controller or pricing writes.
"""
from contextlib import closing
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

import profit_price
import hard_limits
import sol_source_load

class StudyError(RuntimeError):
    """No public quote may consume an uncommitted or conflicting assignment."""

class BaselineChanged(StudyError):
    """Final-gate pause; the caller must journal the unknown outcome off-loop."""

VERSION = 2
COMMON_ADOPTED = {'seller_late_concession', 'at_floor_exact_trial', 'exact_counter_trial', 'exact_accept_trial'}
SOURCE_FILES = ('sol_source_load.py', 'sol_seller_margin.py', 'seller.py', 'profit_price.py', 'hard_limits.py',
                'shop_stock.py', 'shop_memory.py', 'seller_opening_default.py',
                'seller_concession_trial.py', 'seller_reader_trial.py', 'seller_late_concession.py',
                'at_floor_exact_trial.py', 'exact_counter_trial.py', 'exact_accept_trial.py',
                'seller_opening_cue.py', 'seller_late_speed.py', 'seller_jev_transport.py',
                'shelf_life_reply.py', 'seller_grade_preference.py', 'food_opening_trial.py',
                'deep_lowball_trial.py', 'quote_hold.py', 'negotiate.py', 'fast_classify.py',
                'seller_warranty_trial.py', 'shelf_jev_skip.py', 'shelf_fact.py',
                'warranty_fact.py', 'grade_read.py')
# Factorial peer: the grade warranty study (unit hash, own salt/namespace) may
# run in the same epoch. Its source is attested above; its controller is not in
# the baseline, so its ON/OFF never pauses this study. Each quote records the
# warranty cell instead (warranty_cell), so the 2x2 cell is attributable.
SOURCE_FILES += ('market_ref_read.py',)   # the seller's market-reference line read (지시23 11/10)
WARRANTY_CELL_KEYS = ('study', 'version', 'epoch', 'eligible', 'assigned_arm', 'arm', 'reason', 'offered_hours')


def warranty_cell(deal):
    """JSON-safe warranty factor of one talk; never raises, arm None when OFF."""
    try:
        row = deal.get('warranty_trial')
        row = row if isinstance(row, dict) else {}
        def plain(v):
            return v is None or type(v) in (str, bool) or number(v)
        cell = {k: row.get(k) for k in WARRANTY_CELL_KEYS if plain(row.get(k))}
        hours = deal.get('warranty')
        cell['promised_hours'] = hours if number(hours) else None
        return cell
    except Exception:
        return dict(arm=None, promised_hours=None, reason='cell_unavailable')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_manifest():
    if any((Path(__file__).parent/name).is_symlink() for name in SOURCE_FILES):
        raise StudyError('disk snapshot symlink')
    return {name: hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in SOURCE_FILES}


DISK_SNAPSHOT = source_manifest()
# Import-time disk snapshot only, not proof of loaded Python bytecode. Activation
# also requires a fresh seller and source immutability across imports/verification.
DISK_SNAPSHOT_SHA256 = digest(DISK_SNAPSHOT)


def baseline_snapshot(pricing, others=()):
    effective = dict(pricing or {})
    effective['price'] = profit_price.cfg_of(pricing)
    effective['warranty'] = hard_limits.clamp_cfg('warranty', {
        **hard_limits.SAFE['warranty'], **(effective.get('warranty') or {})})
    effective['markdown'] = hard_limits.clamp_cfg('markdown', effective.get('markdown') or {})
    policies = {}
    for peer in others:
        name = type(peer).__module__+'.'+type(peer).__name__
        policies[name] = dict(adopted=(peer.root/'adopted').is_file(),
                              disabled=(peer.root/'disabled').exists(),
                              version='adopted-v1',
                              opening_disabled=(peer.root/'seller_opening_default.disabled').exists())
    return dict(pricing=effective, policies=policies, disk_snapshot=DISK_SNAPSHOT)


class Admission:
    """Serialize consumption with the supported controller producer.

    consume() is synchronous on the seller loop, immediately before invoking
    transport. The linearization point is its final current-clock check, not ACK.
    """
    def __init__(self, study, cfg, task, assignment, pricing, loader, peers):
        self.study, self.cfg = study, cfg
        self.task, self.assignment = task, assignment
        self.pricing, self.loader, self.peers = pricing, loader, peers
        self.consumed = False
        self.closed = False

    def __enter__(self): return self
    def __exit__(self, *exc): self.close()
    def close(self):
        self.closed = True

    def consume(self):
        if self.closed or self.consumed:
            raise StudyError('admission already closed or consumed')
        try:
            handle = self.study._controller_lock(wait=False)
        except BlockingIOError as exc:
            raise StudyError('controller transition in progress') from exc
        with handle:
            return self._consume_locked()

    def _consume_locked(self):
        # Intent is already durable from ready() on a worker. No SQLite here,
        # including baseline rejection. OFF writers use this same lock.
        cfg = self.study.config()
        at = time.time()
        if (cfg != self.cfg or not cfg or not cfg['start'] <= at < cfg['end']
                or any(self.study.peer_active(p, at) for p in self.peers)):
            raise StudyError('admission revoked or expired')
        if self.assignment['eligible']:
            pricing = self.loader() if self.loader is not None else self.pricing
            self.study.verify_baseline(self.task, cfg, pricing, self.peers, journal=False)
        # Baseline/source reads also precede the linearization point.
        at = time.time()
        if not cfg['start'] <= at < cfg['end']:
            raise StudyError('admission expired before transport')
        self.consumed = True
        return at


def number(v):
    return type(v) in (int, float) and math.isfinite(v)


class MarginStudy:
    def __init__(self, root=None):
        self.observation_unknown = 0
        self.root = Path(root) if root is not None else Path(
            os.environ.get('MARKET_DIR') or Path(__file__).parent/'.market')/'sol_seller_margin_v2'

    def _controller_lock(self, wait=True):
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root/'controller.lock'
        if self.root.is_symlink() or path.is_symlink():
            raise StudyError('controller lock symlink')
        handle = path.open('a+b')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            return handle
        except BaseException:
            handle.close()
            raise

    def write_controller(self, cfg):
        """Only supported ON/OFF producer; serializes with consumption.

        OFF completion fences new admissions. An already admitted send may still
        be pending/delivered/unknown; its original terms remain binding.
        """
        with self._controller_lock():
            path = self.root/'controller.json'
            if path.is_symlink():
                raise StudyError('controller symlink')
            tag = uuid.uuid4().hex
            if path.exists():
                with (self.root/('controller-'+tag+'.backup')).open('xb') as f:
                    f.write(path.read_bytes()); f.flush(); os.fsync(f.fileno())
            pending = self.root/('controller-'+tag+'.pending')
            with pending.open('x') as f:
                json.dump(cfg, f, sort_keys=True, allow_nan=False); f.flush(); os.fsync(f.fileno())
            os.replace(pending, path)
            fd = os.open(self.root, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)

    def config(self, now=None):
        now = time.time() if now is None else now
        path = self.root/'controller.json'
        if not path.exists():
            return None
        try:
            if self.root.is_symlink() or path.is_symlink():
                raise ValueError('symlink')
            c = json.loads(path.read_text())
            if not isinstance(c, dict) or type(c.get('enabled')) is not bool:
                raise ValueError('enabled')
            if c['enabled'] is False:
                return None
            if set(c) != {'enabled', 'version', 'study_id', 'epoch', 'start', 'end', 'salt', 'disk_snapshot_sha256',
                          'baseline', 'baseline_sha256', 'load_generation', 'load_proof_sha256'}:
                raise ValueError('schema')
            if type(c['version']) is not int or c['version'] != VERSION:
                raise ValueError('version')
            if any(not isinstance(c[k], str) or not re.fullmatch(r'[A-Za-z0-9_-]{12,80}', c[k])
                   for k in ('study_id', 'salt')):
                raise ValueError('identity')
            if not all(number(c[k]) for k in ('epoch', 'start', 'end')):
                raise ValueError('time')
            if c['epoch'] != c['start'] or not 9*3600 <= c['end']-c['start'] <= 12*3600:
                raise ValueError('window')
            if c['disk_snapshot_sha256'] != DISK_SNAPSHOT_SHA256:
                raise ValueError('disk_snapshot')
            if not sol_source_load.verifies(Path(__file__).parent, c, DISK_SNAPSHOT):
                raise ValueError('fresh_frozen_load_proof')
            if not isinstance(c['baseline'], dict) or digest(c['baseline']) != c['baseline_sha256']:
                raise ValueError('baseline_signature')
            return c if c['start'] <= now < c['end'] else None
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise StudyError('margin controller invalid') from exc

    @staticmethod
    def peer_active(controller, now):
        if (controller.root/'disabled').exists():
            return False
        if (controller.root/'adopted').is_file():
            return type(controller).__module__ not in COMMON_ADOPTED
        if type(controller).__module__ == 'seller_opening_default':
            return False
        cfg = controller.config()
        if not cfg or cfg.get('stop'):
            return False
        return cfg.get('start', float('-inf')) <= now < cfg.get('end', float('inf'))

    def pre_pick_ok(self, others=(), now=None):
        now = time.time() if now is None else now
        if not self.config(now):
            return True
        return not any(self.peer_active(c, now) for c in others)

    def _db(self):
        if self.root.is_symlink():
            raise StudyError('margin root symlink')
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root/'journal.sqlite3'
        if any(Path(str(path)+s).is_symlink() for s in ('', '-journal', '-wal', '-shm')):
            raise StudyError('margin journal symlink')
        db = sqlite3.connect(path, timeout=2)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS study (id TEXT PRIMARY KEY, config TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS observations (task TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS events (task TEXT, event TEXT, at REAL, data TEXT)')
        return db

    def quote(self, task, unit, median, measured, paid, pricing, now=None, others=()):
        """Real price seam; A retains round(), B changes only high_median_up.factor.

        The caller supplies the same ledger median/risk/history to both policies.
        Missing median, floor/cap clipping and warranty refusals remain in ITT.
        Valid fixed scarce asks exclude the SKU from target ITT in both arms,
        even when that ask is below the median and absent from baseline why.
        """
        now = time.time() if now is None else now
        q = profit_price.quote(unit, median, measured, paid, pricing)
        c = self.config(now)
        if not c:
            return q, None
        try:
            price = (pricing or {}).get('price') or {}
            if ((price.get('high_median_up') or {}).get('factor') != .97
                    or (price.get('high_median_up') or {}).get('min_list') != 300000
                    or (price.get('high_price_trial') or {}).get('share', 0) != 0):
                raise StudyError('legacy price policy conflicts')
            if not isinstance(task, str) or not task or len(task) > 128:
                raise StudyError('invalid task')
            cluster = [unit.get('sku'), unit.get('grade')]
            known_grade = unit.get('grade') in ('A', 'B', 'C')
            scarce_asks = profit_price.cfg_of(pricing).get('scarce_asks')
            scarce = scarce_asks.get(unit.get('sku')) if isinstance(scarce_asks, dict) else None
            scarce_fixed = number(scarce) and scarce > 0
            # Record excluded selections, but compare only non-scarce high goods.
            high_goods = unit.get('kind') == 'goods' and (unit.get('list_price') or 0) >= 300000
            eligible = high_goods and not scarce_fixed and known_grade
            if eligible:
                self.verify_baseline(task, c, pricing, others)
            arm = 'B' if hashlib.sha256(json.dumps(
                [c['salt'], *cluster], separators=(',', ':')).encode()).digest()[0] < 128 else 'A'
            candidate = q
            reason = ('outside_target' if not high_goods else 'scarce_fixed_ask' if scarce_fixed
                      else 'grade_unknown' if not known_grade else 'outside_target')
            if eligible:
                reason = 'no_median'
                if number(median) and median > 0:
                    bprice = dict(price, high_median_up=dict(price.get('high_median_up') or {}, factor=1.0))
                    candidate = profit_price.quote(unit, median, measured, paid, dict(pricing, price=bprice))
                    reason = ('warranty_refusal' if q['refuse'] else 'control' if arm == 'A'
                              else 'floor_or_policy_clipped' if candidate['open'] == q['open'] else 'price_change')
            changed = bool(eligible and reason == 'price_change')
            chosen = candidate if changed else q
            rec = dict(study_id=c['study_id'], version=VERSION, epoch=c['epoch'], salt=c['salt'],
                       disk_snapshot_sha256=DISK_SNAPSHOT_SHA256,
                       load_generation=c['load_generation'], load_proof_sha256=c['load_proof_sha256'],
                       task=task, at=now, cluster=cluster,
                       unit=unit.get('unit'), sku=unit.get('sku'), grade=unit.get('grade'),
                       cost=unit.get('cost'), list_price=unit.get('list_price'), kind=unit.get('kind'),
                       median=median, floor=q['floor'], b=q['b'], eligible=eligible, arm=arm,
                       baseline_sha256=c['baseline_sha256'],
                       baseline=q['open'], candidate=candidate['open'], selected=chosen['open'],
                       prepared_treatment=changed, reason=reason)
            encoded = json.dumps(rec, sort_keys=True, allow_nan=False)
            config = json.dumps(c, sort_keys=True, allow_nan=False)
            with closing(self._db()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                prior = db.execute('SELECT id, config FROM study').fetchall()
                if prior and prior != [(c['study_id'], config)]:
                    raise StudyError('fresh study requires a fresh controller root')
                db.execute('INSERT OR IGNORE INTO study VALUES (?,?)', (c['study_id'], config))
                old = db.execute('SELECT data FROM observations WHERE task=?', (task,)).fetchone()
                if old:
                    # A restarted process cannot know that an earlier prepared quote
                    # was published. Existing public talks stay in Seller.open; a
                    # second first-quote call must never reconsume this task's B.
                    raise StudyError('task already prepared; publication not resumed')
                else:
                    db.execute('INSERT INTO observations VALUES (?,?)', (task, encoded))
            return chosen, rec
        except StudyError:
            raise
        except Exception as exc:
            # No baseline fallback: otherwise failed B assignments disappear from ITT.
            raise StudyError('margin assignment unavailable') from exc

    def selection_miss(self, task, now=None):
        """No selected unit is outside the target ITT, but keep the ingress count."""
        now = time.time() if now is None else now
        c = self.config(now)
        if c:
            self.event(task, dict(study_id=c['study_id'], epoch=c['epoch']), 'selection_miss', now=now)

    def verify_baseline(self, task, cfg, pricing, others, *, journal=True):
        if (not sol_source_load.verifies(Path(__file__).parent, cfg, DISK_SNAPSHOT) or source_manifest() != DISK_SNAPSHOT or
                digest(baseline_snapshot(pricing, others)) != cfg['baseline_sha256']):
            if not journal:
                raise BaselineChanged('baseline changed; paused unknown')
            try:
                self.event(task, cfg, 'paused', reason='baseline_changed', economic_status='unknown')
            except StudyError:
                print('SOL_MARGIN paused unknown journal_unavailable', file=sys.stderr)
            raise StudyError('baseline changed; paused unknown')

    def ready(self, task, assignment, deal, keep, others=(), now=None, pricing=None, pricing_loader=None):
        if not assignment:
            return None
        cfg = self._prepare(task, assignment, deal, keep, others, now, pricing, pricing_loader)
        return Admission(self, cfg, task, assignment, pricing, pricing_loader, others)

    def _prepare(self, task, assignment, deal, keep, others=(), now=None, pricing=None, pricing_loader=None):
        """Durable final price before publication; common markdown/opening stay common."""
        if not assignment:
            return
        now = time.time() if now is None else now
        c = self.config(now)
        if (not c or any(c[k] != assignment[k] for k in ('study_id', 'epoch', 'salt', 'disk_snapshot_sha256',
                                                       'load_generation', 'load_proof_sha256'))
                or not self.pre_pick_ok(others, now)):
            raise StudyError('study changed before quote consumption')
        if assignment['eligible']:
            if pricing_loader is not None:
                pricing = pricing_loader()
            self.verify_baseline(task, c, pricing, others)
            if assignment['baseline_sha256'] != c['baseline_sha256']:
                raise StudyError('assignment baseline changed')
        baseline = assignment['baseline']
        if keep < 1:
            baseline = max(deal['reserve'], round(baseline*keep))
        opening = deal.get('opening_default') or {}
        if opening.get('policy') == 'opening-3pct-v1':
            baseline = max(deal['reserve'], round(baseline*.97))
        actual = bool(assignment['prepared_treatment'] and deal['ask'] != baseline)
        self.event(task, assignment, 'quote_ready', now, selected_unit=deal.get('unit'),
                   baseline_final=baseline, final_price=deal['ask'], reserve=deal['reserve'],
                   warranty=deal.get('warranty'), warranty_cell=warranty_cell(deal), actual_treatment=actual,
                   untreated_reason=None if actual else (
                       'downstream_clipped' if assignment['prepared_treatment'] else assignment['reason']))
        # Durable preparation to attempt admission, never evidence of consumption
        # or publication. Final source/baseline/OFF/clock checks still run under
        # the producer lock immediately before transport on the seller loop.
        self.event(task, assignment, 'consume_intent')
        return c

    def event(self, task, assignment, event, now=None, **data):
        if not assignment:
            return
        now = time.time() if now is None else now
        payload = dict(data, study_id=assignment['study_id'], epoch=assignment['epoch'])
        try:
            with closing(self._db()) as db, db:
                db.execute('INSERT INTO events VALUES (?,?,?,?)',
                           (task, event, now, json.dumps(payload, allow_nan=False, sort_keys=True)))
        except Exception as exc:
            raise StudyError('margin event unavailable') from exc

    def observations(self):
        path = self.root/'journal.sqlite3'
        if not path.exists():
            return []
        with closing(self._read_snapshot(path)) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM observations ORDER BY task')]

    def observe_event(self, task, assignment, event, now=None, **data):
        """Post-publication observation failure never blocks common trade persistence."""
        try:
            self.event(task, assignment, event, now, **data)
            return True
        except Exception:
            self.observation_unknown += 1
            print('SOL_MARGIN observation unknown journal_unavailable', file=sys.stderr)
            return False

    def events(self):
        path = self.root/'journal.sqlite3'
        if not path.exists():
            return []
        with closing(self._read_snapshot(path)) as db:
            return [dict(task=task, event=event, at=at, **json.loads(data))
                    for task,event,at,data in db.execute('SELECT task,event,at,data FROM events ORDER BY rowid')]

    def _read_snapshot(self, path):
        """Read an existing stable journal into a query-only in-memory database.

        No URI interpretation, file creation or writable connection to the journal.
        A moving/incomplete snapshot fails explicitly instead of returning counts.
        """
        if self.root.is_symlink() or path.is_symlink():
            raise StudyError('reader symlink')
        sidecars = [Path(str(path)+suffix) for suffix in ('-journal', '-wal', '-shm')]
        if any(p.exists() or p.is_symlink() for p in sidecars):
            raise StudyError('reader transaction state unknown')
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if (before.st_ino, before.st_mtime_ns, before.st_size) != (after.st_ino, after.st_mtime_ns, after.st_size):
            raise StudyError('reader snapshot changed')
        if any(p.exists() or p.is_symlink() for p in sidecars):
            raise StudyError('reader transaction state unknown')
        db = sqlite3.connect(':memory:')
        try:
            db.deserialize(data)
            db.execute('PRAGMA query_only=ON')
            return db
        except Exception:
            db.close()
            raise


def exact_outcome(task, payments, ledger, shop):
    """Read-only exact join. Local paid/unit disappearance is never confirmed.

    Accept the actual full ledger envelope or the collector's explicit k rows.
    Result.receipt.task joins seller task to order; no fuzzy unit/time matching.
    Missing buyer payment evidence stays unknown, even with a settle row.
    """
    # Identity is trusted caller input, never derived from a buyer message/result.
    # The parent must verify this alias/URL mapping against existing authoritative
    # registration/source evidence and retain that evidence under its digest.
    identity_sha = None
    alias = shop
    if isinstance(shop, dict):
        binding = shop
        shop, alias = binding.get('ledger_url'), binding.get('receipt_alias')
        identity_sha = binding.get('evidence_sha256')
        if (binding.get('provenance') != 'parent-verified-shop-binding'
                or not isinstance(alias, str) or not alias or not isinstance(identity_sha, str)
                or not re.fullmatch('[0-9a-f]{64}', identity_sha)):
            return dict(status='unknown', reason='shop_binding_unverified')
    if not isinstance(shop, str) or not shop or not isinstance(payments, list) or not isinstance(ledger, (list, tuple)):
        return dict(status='unknown', reason='input_schema', valid_rows=0)
    rows, errors, seen = [], 0, set()
    for r in ledger:
        if not isinstance(r, dict):
            errors += 1
            continue
        if 'kind' in r:
            if not isinstance(r.get('data'), dict) or 'seq' not in r or 'ts' not in r:
                errors += 1
                continue
            r = dict(r['data'], k=r['kind'], seq=r['seq'], ts=r['ts'])
        seq = r.get('seq')
        if type(seq) is not int or seq <= 0 or not number(r.get('ts')) or seq in seen:
            errors += 1
            continue
        seen.add(seq)
        if r.get('k') in ('result', 'settle', 'broken') and any(
                not isinstance(r.get(k), str) or not r[k] for k in ('order', 'buyer', 'shop')):
            errors += 1
            continue
        if r.get('k') == 'result' and not isinstance(r.get('receipt'), dict):
            errors += 1
            continue
        rows.append(r)
    if errors:
        # Preserve later valid rows for audit; never certify economics with corrupt coverage.
        return dict(status='unknown', reason='ledger_schema_or_duplicate', valid_rows=len(rows), schema_errors=errors)
    results = [r for r in rows if r.get('k') == 'result' and r.get('shop') == shop
               and isinstance(r.get('receipt'), dict) and r['receipt'].get('task') == task]
    keys = {(r.get('order'), r.get('buyer')) for r in results}
    if len(keys) != 1:
        return dict(status='unknown', reason='result_missing_or_ambiguous')
    order, buyer = next(iter(keys))
    if not isinstance(order, str) or not isinstance(buyer, str):
        return dict(status='unknown', reason='identity_missing')
    receipt = results[0]['receipt']
    official = dict(status='unknown', reason='verified_unique_settle_missing')
    matched = [r for r in rows if r.get('k') == 'settle'
               and all(r.get(k) == v for k, v in dict(order=order, buyer=buyer, shop=shop).items())]
    if (all(r.get('verified') is True and r['receipt'].get('shop') == alias
            and r['receipt'].get('price') == receipt.get('price') for r in results)
            and number(receipt.get('price')) and receipt['price'] > 0 and len(matched) == 1
            and number(matched[0].get('amount')) and matched[0]['amount'] == receipt['price']):
        official = dict(status='observed', seq=matched[0]['seq'], amount=matched[0]['amount'],
                        at=matched[0]['ts'], identity_evidence_sha256=identity_sha)

    def outcome(**fields):
        return dict(fields, official_settle=official)

    if any(not isinstance(p, dict) for p in payments):
        return outcome(status='unknown', reason='payment_schema')
    ps = [p for p in payments if p.get('order') == order]
    p = ps[-1] if ps else {}
    if p.get('paid') is False:
        return outcome(status='refused', order=order)
    seq = p.get('ledger')
    if isinstance(seq, str) and seq.isdigit():
        seq = int(seq)
    settles = [r for r in rows if r.get('k') == 'settle' and type(r.get('seq')) is int and r['seq'] == seq]
    if (p.get('paid') is not True or type(seq) is not int or len(settles) != 1
            or not number(p.get('price')) or p['price'] <= 0):
        return outcome(status='unknown', reason='payment_or_settle_missing')
    s = settles[0]
    if any(r.get('verified') is not True or r['receipt'].get('shop') != alias
           or r['receipt'].get('price') != p['price'] for r in results):
        return outcome(status='unknown', reason='result_receipt_unverified_or_mismatch')
    if not number(s.get('amount')) or any(s.get(k) != v for k,v in dict(order=order, buyer=buyer, shop=shop, amount=p['price']).items()):
        return outcome(status='unknown', reason='settle_mismatch')
    return outcome(status='confirmed', order=order, seq=seq, price=p['price'], at=s.get('ts'),
                identity_evidence_sha256=identity_sha,
                broken=[r['seq'] for r in rows if r.get('k') == 'broken' and r.get('order') == order
                        and r.get('buyer') == buyer and r.get('shop') == shop])
