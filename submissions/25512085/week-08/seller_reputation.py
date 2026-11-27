"""Independent, manually requested review snapshot; no trading or state imports."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import urllib.parse
import urllib.request


ENDPOINT = 'https://market.ourolabs.ai/api/shop-reviews'
TIMEOUT_SECONDS = 10
MAX_BYTES = 2 * 1024 * 1024
LOW_STARS = 2
SNIPPET_CHARS = 160
EXAMPLES_PER_TAG = 3
STALE_SECONDS = 3600
# Optional diagnostic vocabulary, not verified defects or automatic policy inputs.
CUE_PATTERNS = {
    'price': r'\b(overpriced|too expensive)\b',
    'quality': r'\b(broken|defective|poor quality)\b',
    'warranty': r'\b(warranty denied|warranty refused)\b',
    'delay': r'\b(late delivery|delivery delayed)\b',
}
NEGATION = re.compile(r"\b(no|not|never|without|unobserved|isn't|wasn't|didn't)\b", re.I)


def finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def timestamp(value):
    """Accept finite epoch seconds or timezone-aware ISO timestamps only."""
    if isinstance(value, bool):
        return None
    try:
        if finite_number(value):
            return datetime.fromtimestamp(value, timezone.utc).timestamp()
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
    except (ValueError, OverflowError, OSError):
        pass
    return None


def utc(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def scope(shop, round_number):
    if not isinstance(shop, str) or not shop.strip():
        raise ValueError('shop must be a non-empty string')
    if type(round_number) is not int or round_number < 1:
        raise ValueError('round must be a positive integer')


def analyze_snapshot(reviews, *, shop, round_number, fetched_at, now, cues=False):
    """Analyze this response only. No previous state or identity inference."""
    scope(shop, round_number)
    fetched, clock = timestamp(fetched_at), timestamp(now)
    if fetched is None or clock is None:
        raise ValueError('valid fetched_at and now required')
    if not isinstance(reviews, list):
        raise ValueError('response must be a JSON array')
    quality = Counter({key: 0 for key in (
        'invalid_record', 'invalid_stars', 'invalid_time', 'invalid_review_text',
        'unknown_account_records', 'unknown_account_valid_ratings',
        'possible_duplicate_records')})
    accounts, ratings, times, seen = {}, [], [], set()
    tags = {tag: {'count': 0, 'examples': []}
            for tag in (*CUE_PATTERNS, 'unknown')}
    verified = 0
    for row in reviews:
        if not isinstance(row, dict):
            quality['invalid_record'] += 1
            continue
        for key, expected in (('shop', shop), ('round', round_number)):
            if key in row and (type(row[key]) is not type(expected) or row[key] != expected):
                raise ValueError(f'explicit {key} mismatch')
        verified += int('shop' in row and 'round' in row)
        fingerprint = json.dumps(row, sort_keys=True, ensure_ascii=True)
        quality['possible_duplicate_records'] += int(fingerprint in seen)
        seen.add(fingerprint)  # diagnostic only: never remove any record
        ts = timestamp(row.get('ts'))
        if ts is None:
            quality['invalid_time'] += 1
        else:
            times.append(ts)
        owner = row.get('owner')
        login = owner.get('login') if isinstance(owner, dict) else None
        login = login.strip().casefold() if isinstance(login, str) else ''
        if not login:
            quality['unknown_account_records'] += 1
        text = row.get('review')
        if not isinstance(text, str):
            quality['invalid_review_text'] += 1
            text = ''
        stars = row.get('stars')
        if not finite_number(stars) or not 1 <= stars <= 5:
            quality['invalid_stars'] += 1
            continue
        ratings.append(stars)
        if login:
            account = accounts.setdefault(login, {'ratings': [], 'times': []})
            account['ratings'].append(stars)
            if ts is not None:
                account['times'].append(ts)
        else:
            quality['unknown_account_valid_ratings'] += 1
        if stars <= LOW_STARS:
            matches = ([tag for tag, pattern in CUE_PATTERNS.items()
                        if re.search(pattern, text, re.I)]
                       if cues and not NEGATION.search(text) else [])
            for tag in matches or ['unknown']:
                tags[tag]['count'] += 1
                if len(tags[tag]['examples']) < EXAMPLES_PER_TAG:
                    tags[tag]['examples'].append(text[:SNIPPET_CHARS])
    summary = {}
    for login, account in sorted(accounts.items()):
        values = account['ratings']
        summary[login] = {
            'n': len(values), 'mean': sum(values) / len(values),
            'low_count': sum(value <= LOW_STARS for value in values),
            'last_ts': utc(max(account['times'])) if account['times'] else None,
        }
    age = clock - fetched
    return {
        'source': {
            'endpoint': ENDPOINT, 'requested_shop': shop, 'requested_round': round_number,
            'source_scope': ('per-record shop and round verified' if reviews and verified == len(reviews)
                             else 'requested endpoint, not per-record verified'),
            'response_count': len(reviews), 'fetched_at': utc(fetched),
            'analyzed_at': utc(clock), 'age_seconds': age,
            'freshness': 'future' if age < 0 else 'stale' if age > STALE_SECONDS else 'fresh',
            'time_range': {'min': utc(min(times)), 'max': utc(max(times))} if times else None,
            'whole_history': False, 'not_official_rank': True,
            'history_gap': 'Unknown coverage/pagination; no merge or deduplication; not a full-history score.',
        },
        'valid_rating_count': len(ratings),
        'raw_review_mean': sum(ratings) / len(ratings) if ratings else None,
        'observed_account_mean': (sum(a['mean'] for a in summary.values()) / len(summary)
                                  if summary else None),
        'accounts': summary, 'data_quality': dict(quality),
        'low_rating_diagnostics': {
            'meaning': 'unverified text cues, not defect facts or established causes',
            'cues_enabled': cues, 'tags': tags,
        },
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('redirects forbidden')


def fetch_reviews(shop, round_number):
    scope(shop, round_number)
    url = ENDPOINT + '?' + urllib.parse.urlencode({'shop': shop, 'round': round_number})
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(url, timeout=TIMEOUT_SECONDS) as response:
        if response.geturl() != url:
            raise ValueError('unexpected response URL')
        payload = response.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError('response too large')
    reviews = json.loads(payload)
    if not isinstance(reviews, list):
        raise ValueError('response must be a JSON array')
    return reviews


def write_exclusive(path, result):
    # Serialize before taking an exclusive creation lock. Never truncate existing files.
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    with Path(path).open('x', encoding='utf-8') as output:
        output.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--fetch', action='store_true', help='explicit manual public GET')
    source.add_argument('--input', type=Path, help='synthetic/offline snapshot envelope')
    parser.add_argument('--shop', required=True)
    parser.add_argument('--round', type=int, required=True)
    parser.add_argument('--cues', action='store_true')
    parser.add_argument('--output', type=Path, help='exclusive new JSON file; default stdout')
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).timestamp()
    if args.fetch:
        reviews = fetch_reviews(args.shop, args.round)
        fetched_at = datetime.now(timezone.utc).timestamp()
        now = fetched_at
        mode = 'explicit manual GET'
    else:
        if args.input.stat().st_size > MAX_BYTES:
            raise ValueError('input too large')
        fixture = json.loads(args.input.read_text(encoding='utf-8'))
        if (not isinstance(fixture, dict) or fixture.get('shop') != args.shop
                or type(fixture.get('round')) is not int or fixture['round'] != args.round):
            raise ValueError('input envelope must match requested shop/round')
        reviews, fetched_at = fixture['reviews'], fixture['fetched_at']
        mode = 'offline input (not independently fetched)'
    result = analyze_snapshot(reviews, shop=args.shop, round_number=args.round,
                              fetched_at=fetched_at, now=now, cues=args.cues)
    result['source']['mode'] = mode
    if args.output:
        write_exclusive(args.output, result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
