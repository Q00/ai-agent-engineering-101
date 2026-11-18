import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))  # the market's own round-end times are quoted in KST
DAILY_RETENTION_DAYS = 30  # older day-buckets are pruned automatically so this file never grows unbounded


class TeamMemory:
    """Minimal per-department state: counters and per-key post timestamps, persisted to its own
    JSON file. Every forum/sales sub-team (ad/credit/loan/purchase/pricing/loyalty) gets its own
    instance so each department's memory is genuinely separate -- not one shared blob every team
    reads and writes, which is how `replied_posts.json`/`payments_seen.json` used to work before
    the department split. Replaces the repeated ad-hoc "read a last_post_at.txt, compare, write it
    back" pattern duplicated across hegemony_engine.py and loan_engine.py with one shared helper.

    Every counter is ALSO mirrored into a KST-midnight day-bucket (data["daily"][YYYY-MM-DD]) with
    no extra call needed at any call site -- a plain `bump()` already gives every team day-by-day
    history (see today_summary/yesterday_summary), which is what makes "관리를 매일, 자정 기준으로"
    possible without touching every bump() call across every engine individually."""

    def __init__(self, name: str, base_dir: Path):
        self.path = base_dir / f"{name}_memory.json"
        self.data = self._load()

    def _load(self):
        if self.path.exists():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"counters": {}, "last_post_at": {}, "daily": {}, "updated_at": 0}

    def save(self):
        self.data["updated_at"] = time.time()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @staticmethod
    def _today():
        return datetime.now(KST).strftime("%Y-%m-%d")

    def _prune_daily(self, daily):
        if len(daily) <= DAILY_RETENTION_DAYS:
            return
        cutoff = (datetime.now(KST) - timedelta(days=DAILY_RETENTION_DAYS)).strftime("%Y-%m-%d")
        for day in [d for d in daily if d < cutoff]:
            del daily[day]

    def bump(self, counter: str, by: int = 1):
        counters = self.data.setdefault("counters", {})
        counters[counter] = counters.get(counter, 0) + by
        daily = self.data.setdefault("daily", {})
        today = daily.setdefault(self._today(), {})
        today[counter] = today.get(counter, 0) + by
        self._prune_daily(daily)
        self.save()

    def today_summary(self) -> dict:
        """This KST calendar day's counters only -- resets automatically at midnight since it is
        keyed by the current date string, no explicit rollover job required."""
        return dict(self.data.get("daily", {}).get(self._today(), {}))

    def yesterday_summary(self) -> dict:
        y = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        return dict(self.data.get("daily", {}).get(y, {}))

    def is_new_day(self, key: str) -> bool:
        """True the first time this is called on a given KST calendar day for this key -- lets a
        caller post one "오늘의 요약" exactly once per day instead of every cycle. Marks the day as
        seen as a side effect (call only when actually about to act on the result)."""
        last = self.data.get("last_day_seen", {}).get(key)
        today = self._today()
        if last == today:
            return False
        self.data.setdefault("last_day_seen", {})[key] = today
        self.save()
        return True


    def ready(self, key: str, min_interval: float) -> bool:
        """True once min_interval seconds have passed since the last post tagged `key`."""
        last = self.data.get("last_post_at", {}).get(key, 0)
        return time.time() - last >= min_interval

    def mark_posted(self, key: str):
        self.data.setdefault("last_post_at", {})[key] = time.time()
        self.save()

    def seen(self, bucket: str) -> set:
        return set(self.data.get(bucket, []))

    def add_seen(self, bucket: str, ids) -> None:
        current = set(self.data.get(bucket, []))
        current.update(ids)
        self.data[bucket] = sorted(current, key=str)
        self.save()
