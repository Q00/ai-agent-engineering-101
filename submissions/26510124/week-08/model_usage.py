"""Metadata-only CLI usage accounting. Missing usage is unknown, never zero.

Claude input excludes cache reads/writes; Codex input includes cache reads.
Report these separately rather than inventing a cross-provider billing total.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

FIELDS = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens')


def count(value):
    return value if type(value) is int and value >= 0 else None


def extract(profile, stdout):
    result = {key: None for key in FIELDS}
    result['input_includes_cache'] = profile not in ('haiku', 'opus5.5')
    if not isinstance(stdout, str) or len(stdout) > 2_000_000:
        return result
    try:
        if profile in ('haiku', 'opus5.5'):
            envelope = json.loads(stdout)
            usage = envelope.get('usage', {})
            mapping = dict(input_tokens='input_tokens', output_tokens='output_tokens',
                           cache_read_tokens='cache_read_input_tokens', cache_creation_tokens='cache_creation_input_tokens')
            for field, key in mapping.items():
                result[field] = count(usage.get(key))
            # Per-model counters are an alternative, never added to top-level usage.
            models = envelope.get('modelUsage', {})
            aliases = dict(input_tokens='inputTokens', output_tokens='outputTokens',
                           cache_read_tokens='cacheReadInputTokens', cache_creation_tokens='cacheCreationInputTokens')
            for field, key in aliases.items():
                values = [count(row.get(key)) for row in models.values()] if isinstance(models, dict) else []
                if result[field] is None and values and all(v is not None for v in values):
                    result[field] = sum(values)
        else:
            events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            usages = [e['usage'] for e in events if e.get('type') == 'turn.completed' and isinstance(e.get('usage'), dict)]
            for field, key in (('input_tokens', 'input_tokens'), ('output_tokens', 'output_tokens'),
                               ('cache_read_tokens', 'cached_input_tokens')):
                values = [count(row.get(key)) for row in usages]
                if values and all(v is not None for v in values):
                    result[field] = sum(values)
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    return result


def role(prompt_name):
    if prompt_name == 'shop_operations': return 'shop_operations'
    if prompt_name.startswith('seller'): return 'shop'
    if prompt_name == 'sales_advisor.txt': return 'sales_advisor'
    if prompt_name == 'market_select.txt': return 'buyer_selection'
    if prompt_name == 'buyer.txt': return 'buyer_negotiation'
    return 'other'


def record(log, *, profile, prompt_name, prompt, started, stdout, status):
    """Best-effort single immutable file per attempt; concurrent writers never share a file."""
    try:
        name = getattr(log, 'name', None)
        if not isinstance(name, str): return False
        parent = Path(name).absolute().parent
        if parent.is_symlink(): return False
        folder = parent / 'model-usage'
        if folder.is_symlink(): return False
        folder.mkdir(exist_ok=True, mode=0o700)
        now = time.time()
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S')
        call_id = uuid.uuid4().hex
        row = dict(schema=1, time=now, call_id=call_id, log=Path(name).name,
                   role=role(prompt_name), profile=profile, status=status,
                   elapsed_seconds=round(time.monotonic()-started, 3), prompt_bytes=len(prompt.encode()),
                   **extract(profile, stdout))
        raw = (json.dumps(row, allow_nan=False) + '\n').encode()
        temporary = folder / ('.' + call_id + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
        os.replace(temporary, folder / (stamp + '-' + call_id + '.json'))
        return True
    except (OSError, ValueError, TypeError):
        # Telemetry availability must not interrupt an existing trade.
        return False


def report(root, hours=6, now=None):
    now = time.time() if now is None else now
    paths = sorted((Path(root) / 'logs/model-usage').glob('*.json'))
    groups, invalid, rows = {}, 0, 0
    for path in paths[-50000:]:
        try:
            if path.is_symlink() or path.stat().st_size > 8192: raise ValueError('unsafe usage file')
            row = json.loads(path.read_text())
            if not now-hours*3600 <= row['time'] <= now: continue
            key = row['role'] + '/' + row['profile']
            group = groups.setdefault(key, dict(calls=0, cli_errors=0, totals={k:0 for k in FIELDS},
                missing={k:0 for k in FIELDS}, input_includes_cache=row['input_includes_cache']))
            group['calls'] += 1
            group['cli_errors'] += row['status'] != 'process_success'
            for field in FIELDS:
                value = count(row.get(field))
                if value is None: group['missing'][field] += 1
                else: group['totals'][field] += value
            rows += 1
        except (OSError, ValueError, TypeError, KeyError): invalid += 1
    return dict(hours=hours, calls_observed=rows, groups=groups, invalid_files=invalid,
                truncated=len(paths)>50000,
                limits=['Totals cover reported counters only; missing counters are UNKNOWN.',
                        'Claude input excludes cache; Codex input includes cache. Do not add cache twice.',
                        'Process success is not valid decision or confirmed settlement.',
                        'No subscription quota or monetary cost is inferred. prompt_bytes is not tokens.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hours', type=float, default=6)
    args = parser.parse_args()
    if not 0 < args.hours <= 168: parser.error('--hours must be in (0, 168]')
    print(json.dumps(report(Path(__file__).resolve().parent, args.hours), ensure_ascii=False, indent=2))
