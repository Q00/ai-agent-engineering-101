"""Default-off, unit-randomized grade warranty experiment. No market calls.

Configuration metadata is checked for each quote; unchanged bytes are cached.
The async seller runs the whole terms transaction in a cancellation-safe worker.
While enabled, talks retain their arm;
a later request or an OFF configuration restores the normal warranty path.
"""
import asyncio
import hashlib
import json
import math
import os
import re
from pathlib import Path
import threading
import time
import unicodedata


_LOCK = threading.Lock()
_CONFIG_LOCK = threading.Lock()
_CONFIG_CACHE = None  # One bounded entry, keyed by path and file metadata.
_CONFIG = Path(__file__).with_suffix(".json")
_MAX_CONFIG_BYTES = 64 * 1024
_MAX_RECORD_BYTES = 64 * 1024  # excludes the terminating newline
_SEPARATORS = "-‐‑‒–—−./"
_STEMS = ("warrant", "guarant", "cover", "refund", "moneyback", "return", "replace",
          "repair", "break", "broke", "defect", "fault", "insur", "protect", "assur", "hr", "hour",
          "보증", "보장", "환불", "반품", "교환", "고장", "파손", "불량", "시간")
_AFTER_SALES = re.compile(r"(?<!\w)a[\s/\.\-‐‑‒–—−]*s(?!\w)")


def requested(text):
    """Ambiguous refund, breakage or duration language keeps the normal terms."""
    if len(text or "") > 8000:
        return True
    text = unicodedata.normalize("NFKC", text or "").lower()
    # Compact once, then scan fixed stems and a single digit/unit pair. This
    # absorbs spelling separators without repeated scans of digit/space runs.
    compact = "".join(char for char in text if not char.isspace() and char not in _SEPARATORS)
    return (any(stem in compact for stem in _STEMS)
            or bool(_AFTER_SALES.search(text))
            or bool(re.search(r"\dh", compact)))


def _number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


async def finish_before_cancel(fn, *args, **kwargs):
    """Keep the caller's turn lock until terms and journaling have both finished.

    Shield alone is insufficient: it lets a cancelled caller release its lock
    while the worker still mutates the deal. Drain even repeated cancellation
    before propagating it; no public terms or receipt can race that mutation.
    """
    worker = asyncio.create_task(asyncio.to_thread(fn, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            if worker.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError from None
            raise
    if cancelled:
        raise asyncio.CancelledError
    return result


def _read_config(path):
    try:
        # Bound the read itself, including when a file grows after opening.
        with path.open("rb") as stream:
            data = stream.read(_MAX_CONFIG_BYTES + 1)
        if len(data) > _MAX_CONFIG_BYTES:
            return {}, "invalid_config"
        cfg = json.loads(data.decode("utf-8"))
        if not isinstance(cfg, dict):
            return {}, "invalid_config"
        valid = (all(isinstance(cfg.get(k), str) and cfg[k].strip() for k in ("study", "salt"))
                 and type(cfg.get("version")) is int and cfg["version"] > 0
                 and (isinstance(cfg.get("epoch"), str) and bool(cfg["epoch"].strip())
                      or _number(cfg.get("epoch")))
                 and type(cfg.get("enabled")) is bool
                 and _number(cfg.get("share")) and 0 <= cfg["share"] <= 1
                 and all(_number(cfg.get(k)) and cfg[k] > 0 for k in ("start_at", "stop_at"))
                 and cfg["start_at"] < cfg["stop_at"]
                 and isinstance(cfg.get("grades"), list) and bool(cfg["grades"])
                 and all(isinstance(g, str) and g in ("A", "B", "C") for g in cfg["grades"]))
        if not valid:
            return {}, "invalid_config"
        # JSON permits escaped lone surrogates; hash/journal UTF-8 does not.
        for field in ("study", "salt", "epoch"):
            if isinstance(cfg[field], str):
                cfg[field].encode("utf-8")
        return cfg, None
    except Exception:
        return {}, "config_unavailable"


def _config(now):
    global _CONFIG_CACHE
    try:
        path = Path(os.path.abspath(os.environ.get("SELLER_WARRANTY_TRIAL_CONFIG") or _CONFIG))
        with _CONFIG_LOCK:
            stat = path.stat()
            key = (path, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
            if _CONFIG_CACHE is None or _CONFIG_CACHE[0] != key:
                cfg, reason = _read_config(path)
                # Transient read errors must be retried even with unchanged metadata.
                _CONFIG_CACHE = None if reason == "config_unavailable" else (key, cfg, reason)
            else:
                _, cfg, reason = _CONFIG_CACHE
        if reason is not None:
            return cfg, reason
        if not cfg["enabled"]:
            return cfg, "disabled"
        # Evaluate the clock on every quote, even when bytes are unchanged.
        if not cfg["start_at"] <= now < cfg["stop_at"]:
            return cfg, "outside_window"
        return cfg, None
    except Exception:
        return {}, "config_unavailable"


class Trial:
    def _assignment(self, task, deal, unit, requested, now, cfg):
        reason = None
        unit = unit or {}
        if reason is None:
            if deal.get("kind") != "goods" or unit.get("kind") != "goods":
                reason = "not_goods"
            elif (not isinstance(task, str) or not task or not isinstance(deal.get("unit"), str)
                  or not deal["unit"] or unit.get("unit") != deal["unit"]
                  or not isinstance(unit.get("sku"), str) or unit["sku"] != deal.get("id")
                  or not _number(unit.get("list_price")) or unit["list_price"] <= 0):
                reason = "unit_unknown"
            elif unit.get("grade") not in cfg["grades"]:
                reason = "grade_not_selected"
            elif requested:
                reason = "buyer_requested"
            elif "warranty" in deal:
                reason = "existing_warranty"
        eligible = reason is None
        arm = "control"
        if eligible:
            key = f"{cfg['salt']}|{cfg['study']}|{cfg['version']}|{deal['unit']}"
            bucket = int.from_bytes(hashlib.sha256(key.encode()).digest(), "big")
            if bucket < cfg["share"] * (1 << 256):
                arm = "treatment"
        row = dict(study=cfg.get("study"), version=cfg.get("version"), epoch=cfg.get("epoch"),
                   ts=now, task=task, sku=deal.get("id"), unit=deal.get("unit"),
                   grade=unit.get("grade"), list_price=unit.get("list_price"),
                   eligible=eligible, arm=arm, reason=reason or "grade_eligible",
                   randomization_unit="unit")
        if eligible:
            row["offered_hours"] = 0 if arm == "treatment" else 72
        row["assigned_arm"] = arm
        return row

    def quote(self, task, deal, unit, *, text, log, now=None):
        # Assignment, detection and serialization must be as fail-closed as I/O.
        # Never let an experimental failure prevent the normal goods quote.
        try:
            return self._quote(task, deal, unit, text=text, log=log, now=now)
        except Exception:
            previous = deal.get("warranty_trial") or {}
            deal["warranty_trial"] = {**previous, "task": task, "unit": deal.get("unit"),
                                      "arm": "control", "offered_hours": 72,
                                      "reason": "trial_failed"}
            try:
                log("WARRANTY_TRIAL failed; control warranty applies")
            except Exception:
                pass
            return False

    def _quote(self, task, deal, unit, *, text, log, now=None):
        """Journal before returning permission to omit the receipt's warranty.

        Tasks are repeated observations, not independent samples. Randomization
        uses only unit within a fixed study/version/salt, across different tasks.
        """
        now = time.time() if now is None else now
        cfg, reason = _config(now)
        if reason is not None:
            # OFF is the original seller path, including on a later turn. Do not
            # scan input, create directories or append an observational record.
            deal.pop("warranty_trial", None)
            return False
        wants_warranty = requested(text)
        previous = deal.get("warranty_trial") or {}
        if previous and previous.get("task") == task and previous.get("unit") == deal.get("unit"):
            row = {**previous, "ts": now}
            if row["arm"] == "treatment" and (wants_warranty or "warranty" in deal):
                row.update(arm="control", offered_hours=72,
                           reason="followup_warranty_request" if wants_warranty else "existing_warranty")
        else:
            row = self._assignment(task, deal, unit, wants_warranty, now, cfg)
        try:
            path = Path(os.environ.get("MARKET_DIR") or ".market") / "measure" / "seller_warranty_trial.jsonl"
            data = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            with _LOCK:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a+b") as stream:
                    if stream.tell():
                        stream.seek(-1, os.SEEK_END)
                        if stream.read(1) != b"\n":
                            raise OSError("incomplete measurement tail")
                        # Read at most one bounded tail window. The extra byte
                        # distinguishes an exact-limit record from a longer one,
                        # even when all earlier records form a very large file.
                        end = stream.tell() - 1
                        size = min(end, _MAX_RECORD_BYTES + 1)
                        stream.seek(end - size)
                        window = stream.read(size)
                        if len(window) != size:
                            raise OSError("short measurement read")
                        tail = window.rsplit(b"\n", 1)[-1]
                        if len(tail) > _MAX_RECORD_BYTES:
                            raise OSError("measurement tail exceeds byte limit")
                        json.loads(tail)
                        stream.seek(0, os.SEEK_END)
                    if stream.write(data) != len(data):
                        raise OSError("short measurement write")
        except (OSError, ValueError, TypeError):
            log("WARRANTY_TRIAL measurement failed; control warranty applies")
            row.update(arm="control", offered_hours=72, reason="measurement_failed")
        deal["warranty_trial"] = row
        return row["arm"] == "treatment"
