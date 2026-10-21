"""Sync this shop's published low-rating authors into its local access policy.

This does not label misconduct, change reviews, identify anonymous requests or
make trades. Existing sellers reload the access JSON on every turn, so all
seller generations see updates without restarting the tunnel or active tasks.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import stat
import tempfile
import threading
import urllib.parse
import urllib.request

from tracing import Trace

ROOT = Path(__file__).resolve().parent
SOURCE = 'published_low_review'


def review_rule(policy):
    rule = policy.get('review_restriction') or {}
    if not isinstance(rule, dict) or type(rule.get('enabled', False)) is not bool:
        raise ValueError('Review access enabled must be a boolean')
    if not rule.get('enabled', False):
        return None
    if rule.get('market_url') != 'https://market.ourolabs.ai':
        raise ValueError('Review access requires the configured official market origin')
    if not isinstance(rule.get('shop'), str) or not rule['shop'].strip():
        raise ValueError('Review access requires our exact shop registry name')
    for field, lower, upper in (('round', 0, 10000), ('max_stars', 1, 3), ('poll_seconds', 5, 3600)):
        value = rule.get(field)
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f'Invalid review access {field}')
    return rule


def denied_authors(reviews, rule):
    """Use official author/star fields only; review prose has no policy authority."""
    if not isinstance(reviews, list):
        raise ValueError('Official shop reviews must be a list')
    authors = {}
    for row in reviews:
        if not isinstance(row, dict):
            raise ValueError('Malformed published review')
        stars = row.get('stars')
        if isinstance(stars, bool) or not isinstance(stars, (int, float)) or not math.isfinite(stars) \
                or stars != int(stars) or not 1 <= stars <= 5:
            raise ValueError('Malformed published review stars')
        if row.get('round', rule['round']) != rule['round'] or stars > rule['max_stars']:
            continue
        author = row.get('author')
        if not isinstance(author, str) or not author.strip():
            # A missing author cannot identify a buyer; never manufacture one.
            continue
        author = author.strip()
        if author.casefold() not in authors:
            owner = row.get('owner')
            login = owner.get('login') if isinstance(owner, dict) else None
            authors[author.casefold()] = {'registry_name': author,
                'owner_login': login.strip() if isinstance(login, str) and login.strip() else None,
                'stars': int(stars), 'review_ts': row.get('ts')}
    return authors


def apply_reviews(policy, reviews, *, observed_at):
    """Accumulate published low-rating authors, preserving explicit manual blocks."""
    updated = deepcopy(policy)
    rule = review_rule(policy)
    # Operator disabling/changing this rule removes only incompatible automatic entries.
    retained, removed = [], []
    for entry in updated['blocked_buyers']:
        evidence = entry.get('review_evidence') or {}
        compatible = (rule and evidence.get('shop') == rule['shop']
                      and evidence.get('round') == rule['round']
                      and type(evidence.get('stars')) is int
                      and 1 <= evidence['stars'] <= rule['max_stars'])
        if entry.get('source') == SOURCE and not compatible:
            removed.append(entry['registry_name'])
        else:
            retained.append(entry)
    observed = denied_authors(reviews, rule) if rule else {}
    existing = {entry['registry_name'].casefold() for entry in retained}
    added = []
    for key, author in observed.items():
        if key in existing:
            continue
        evidence = {'market_url': rule['market_url'], 'shop': rule['shop'], 'round': rule['round'],
                    'stars': author['stars'], 'review_ts': author['review_ts'], 'observed_at': observed_at}
        entry = {'registry_name': author['registry_name'], 'source': SOURCE,
                 'reason': 'Local participation rule: published 1-3-star review of this shop; not a misconduct finding.',
                 'review_evidence': evidence, 'scope': 'This shop only; identified buyers only.'}
        if author['owner_login']:
            entry['owner_login'] = author['owner_login']
        retained.append(entry)
        existing.add(key)
        added.append(author['registry_name'])
    updated['blocked_buyers'] = retained
    return updated, {'added': added, 'removed': removed, 'observed_low_rating_authors':
                     sorted(author['registry_name'] for author in observed.values()),
                     'review_rows': len(reviews), 'total_blocked_buyers': len(retained),
                     'anonymous_requests_remain_allowed': True, 'enabled': bool(rule)}


class ReviewAccessSync:
    def __init__(self, policy_path=None, *, state_dir=None, fetch_reviews=None, trace=None):
        self.path = Path(policy_path or ROOT / 'data/buyer-access.json')
        self.state = Path(state_dir or ROOT / '.market/review-access')
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.fetch_reviews = fetch_reviews or self._fetch_reviews
        self.trace = trace or Trace('review-access', ROOT / 'logs/runtime')

    def _fetch_reviews(self, rule):
        query = urllib.parse.urlencode({'shop': rule['shop'], 'round': rule['round']})
        url = rule['market_url'] + '/api/shop-reviews?' + query
        with urllib.request.urlopen(url, timeout=4) as response:
            return json.load(response)

    def sync(self):
        with (self.state / 'update.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            original = self.path.read_bytes()
            policy = json.loads(original)
            rule = review_rule(policy)
            reviews = self.fetch_reviews(rule) if rule else []
            now = datetime.now(timezone.utc).isoformat()
            updated, result = apply_reviews(policy, reviews, observed_at=now)
            # Validate the complete next policy before replacing the file that
            # existing seller processes read on every request and before publication.
            for entry in updated['blocked_buyers']:
                if not isinstance(entry.get('registry_name'), str) or not entry['registry_name'].strip():
                    raise ValueError('Invalid blocked buyer registry name')
                if 'owner_login' in entry and (not isinstance(entry['owner_login'], str) or not entry['owner_login'].strip()):
                    raise ValueError('Invalid blocked buyer owner login')
            changed = updated != policy
            if changed:
                if self.path.read_bytes() != original:
                    raise RuntimeError('Access policy changed during review fetch; retry without overwriting it')
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile('w', dir=self.path.parent, prefix='.review-access-',
                                                     delete=False, encoding='utf-8') as stream:
                        temporary = Path(stream.name)
                        json.dump(updated, stream, ensure_ascii=False, indent=2)
                        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
                    temporary.chmod(stat.S_IMODE(self.path.stat().st_mode))
                    os.replace(temporary, self.path)
                finally:
                    if temporary:
                        temporary.unlink(missing_ok=True)
            result.update(observed_at=now, policy_changed=changed,
                          shop=rule['shop'] if rule else None, round=rule['round'] if rule else None)
            # Status is replaced atomically; historical observations remain append-only in Trace.
            status_path = self.state / 'status.json'
            temporary_status = status_path.with_suffix('.tmp')
            temporary_status.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            os.replace(temporary_status,status_path)
            self.trace.write('review_access_synced', **result)
            return result

    def watch(self):
        # A second watcher fails rather than doubling API polling or overwriting state.
        with (self.state / 'watcher.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('A review access watcher is already running') from None
            (self.state / 'pid').write_text(str(os.getpid()))
            stopped = threading.Event()
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: stopped.set())
            while not stopped.is_set():
                interval = 60
                try:
                    self.sync()
                    rule = review_rule(json.loads(self.path.read_text()))
                    interval = rule['poll_seconds'] if rule else interval
                except Exception as exc:
                    # Failed or malformed fetches never erase known restrictions.
                    self.trace.write('review_access_sync_failed', kind=type(exc).__name__, detail=str(exc)[:300])
                stopped.wait(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['once', 'watch'])
    parser.add_argument('--config')
    parser.add_argument('--state-dir')
    args = parser.parse_args()
    sync = ReviewAccessSync(args.config, state_dir=args.state_dir)
    if args.command == 'once':
        print(json.dumps(sync.sync(),ensure_ascii=False))
    else:
        sync.watch()


if __name__ == '__main__':
    main()
