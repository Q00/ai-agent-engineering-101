"""Retry read-only requests; never wrap a purchase POST."""
import time
from urllib.error import URLError, HTTPError
import httpx


def retry_read(operation, attempts=3, sleep=time.sleep, report=None):
    failures = 0
    while True:
        try:
            result = operation()
            if failures and report:
                report(event='read_recovered', failures=failures)
            return result
        except (URLError, TimeoutError, ConnectionError, httpx.TransportError, httpx.HTTPStatusError) as exc:
            status = exc.code if isinstance(exc, HTTPError) else getattr(getattr(exc, 'response', None), 'status_code', None)
            if status is not None and status != 429 and status < 500:
                raise
            failures += 1
            if attempts is not None and failures >= attempts:
                raise
            delay = min(60, 2 ** min(failures, 6))
            if report:
                report(event='read_retry', error=type(exc).__name__, status=status, retry_seconds=delay)
            sleep(delay)
