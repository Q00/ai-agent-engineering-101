"""Bound supply deadlines to the market's existing inventory response clock.

Only schedule comparisons use this clock. Local file freshness, logs, payment
signatures and history timestamps retain the host clock. No request is added.
"""
from email.utils import parsedate_to_datetime
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

MAX_SAMPLE_AGE = 45
MAX_ROUND_TRIP = 5
CLOCK_JUMP_TOLERANCE = 2
MAX_SAMPLE_BYTES = 4096


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


@lru_cache(maxsize=1)
def _boot_id():
    """Monotonic timestamps are shared between processes, but not boots."""
    try:
        if sys.platform.startswith("linux"):
            return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        if sys.platform == "darwin":
            return subprocess.check_output(
                ["/usr/sbin/sysctl", "-n", "kern.bootsessionuuid"],
                timeout=1, stderr=subprocess.DEVNULL, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _read(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_SAMPLE_BYTES + 1)
    if len(raw) > MAX_SAMPLE_BYTES:
        raise ValueError("Oversize market clock sample")
    return json.loads(raw)


def _write(path, sample):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent,
                prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = stream.name
            json.dump(sample, stream, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def capture_response(md, response, *, round_trip, received_wall, received_monotonic):
    """Save one bounded, uncached inventory response; never save inventory data."""
    if response.status_code != 200:
        return "http_error"
    if (not all(_number(n) for n in (round_trip, received_wall, received_monotonic))
            or not 0 <= round_trip <= MAX_ROUND_TRIP or received_monotonic < 0):
        return "invalid_timing"
    age = response.headers.get("Age")
    if age is not None and (not age.strip().isdigit() or int(age) != 0):
        return "cached_response"
    if response.headers.get("CF-Cache-Status", "").upper() in {"HIT", "STALE", "UPDATING", "REVALIDATED"}:
        return "cached_response"
    try:
        stamp = parsedate_to_datetime(response.headers.get("Date", ""))
        if stamp.tzinfo is None:
            return "invalid_date"
        market_epoch = stamp.timestamp()
        body = response.json()
    except (ValueError, TypeError, OverflowError):
        return "invalid_date"
    if (not _number(market_epoch) or market_epoch <= 0 or not isinstance(body, dict)
            or type(body.get("round")) is not int or body["round"] < 1):
        return "invalid_response"
    # An open schedule must agree with Date within its finite transport error.
    # This avoids trusting an intermediary clock inconsistent with the market.
    window = body.get("restock")
    if isinstance(window, dict) and window.get("open") is True:
        opens, closes = window.get("opens_at"), window.get("closes_at")
        if (not _number(opens) or not _number(closes) or closes <= opens
                or market_epoch >= closes or market_epoch + 1 + round_trip < opens):
            return "date_schedule_mismatch"
    sample = {"version": 1, "round": body["round"], "market_epoch": market_epoch,
              "received_wall": received_wall, "received_monotonic": received_monotonic,
              "round_trip": round_trip, "boot_id": _boot_id()}
    _write(Path(md) / "market-clock.json", sample)
    return "recorded"


def schedule_now(md, round_number, *, local_now=None, monotonic_now=None):
    """Return market time or labelled local fallback; offset is MARKET minus LOCAL."""
    local = time.time() if local_now is None else local_now
    mono = time.monotonic() if monotonic_now is None else monotonic_now
    fallback = {"now": local, "status": "invalid", "offset_seconds": None,
                "uncertainty_seconds": None}
    if not _number(local) or not _number(mono) or type(round_number) is not int:
        return fallback
    try:
        sample = _read(Path(md) / "market-clock.json")
    except FileNotFoundError:
        return {**fallback, "status": "missing"}
    except (OSError, ValueError, TypeError):
        return fallback
    if (not isinstance(sample, dict) or type(sample.get("version")) is not int or sample["version"] != 1
            or type(sample.get("round")) is not int
            or not all(_number(sample.get(k)) for k in
                       ("market_epoch", "received_wall", "received_monotonic", "round_trip"))
            or sample["market_epoch"] <= 0 or sample["received_monotonic"] < 0
            or not 0 <= sample["round_trip"] <= MAX_ROUND_TRIP):
        return fallback
    if sample["round"] != round_number:
        return {**fallback, "status": "round_mismatch"}
    elapsed = mono - sample["received_monotonic"]
    wall_elapsed = local - sample["received_wall"]
    boot = _boot_id()
    if (elapsed < 0 or (sample.get("boot_id") != boot and (sample.get("boot_id") or boot))
            or abs(wall_elapsed - elapsed) > CLOCK_JUMP_TOLERANCE):
        return {**fallback, "status": "clock_changed"}
    if elapsed > MAX_SAMPLE_AGE:
        return {**fallback, "status": "stale"}
    # Date has whole-second resolution. At receipt, real market time is no
    # later than Date + 1 second + RTT; using that upper bound closes early.
    uncertainty = 1 + sample["round_trip"]
    upper = sample["market_epoch"] + uncertainty + elapsed
    midpoint = sample["market_epoch"] + uncertainty / 2 + elapsed
    return {"now": upper, "status": "synced", "offset_seconds": midpoint - local,
            "uncertainty_seconds": uncertainty}


def install_market_clock(kit, md):
    """Observe existing kit calls; return the same response and preserve errors."""
    original = kit.Market.call
    if getattr(original, "_market_clock_installed", False) is True:
        return
    lock = threading.Lock()

    def call(market, method, path, role="buyer", **kwargs):
        capture = method == "GET" and path == "/api/inventory" and role == "shop"
        if not capture:
            return original(market, method, path, role, **kwargs)
        started, wall_started = time.monotonic(), time.time()
        response = original(market, method, path, role, **kwargs)
        finished, wall_finished = time.monotonic(), time.time()
        elapsed = finished - started
        try:
            if abs((wall_finished - wall_started) - elapsed) <= CLOCK_JUMP_TOLERANCE:
                with lock:
                    capture_response(md, response, round_trip=elapsed,
                                     received_wall=wall_finished, received_monotonic=finished)
        except (OSError, ValueError, TypeError, OverflowError):
            # A clock sample is advisory; inventory sync and trading still run.
            pass
        return response

    call._market_clock_installed = True
    kit.Market.call = call
