import json
import random
import time
from pathlib import Path


MAX_QUEUE_LINES = 40  # backpressure cap: see post_to_outbox


def post_to_outbox(outbox_path: Path, message: dict, retries: int = 1) -> bool:
    """Append one line to outbox.jsonl, writing exactly once -- with backpressure.

    market_kit.py (read-only, not ours to patch) drains outbox.jsonl on its own sync cycle, and the
    live market only accepts one post per 5 minutes per role (shop/buyer). Four engines (credit
    bureau, loan desk, forum agent, futures) all write to the same shop-role queue; if their combined
    write rate ever exceeds that drain rate even briefly, the queue backs up. A backed-up queue makes
    market_kit's own drain cycle (which re-reads the whole file each pass) take far longer than any
    short "is this stuck" timeout could safely assume, so recover_orphaned_taking below must stay very
    conservative. The real fix is here: refuse to add more work once a backlog already exists, so the
    queue self-limits instead of growing without bound.
    """
    recover_orphaned_taking(outbox_path)
    try:
        if outbox_path.exists():
            with open(outbox_path, encoding="utf-8") as f:
                queued = sum(1 for line in f if line.strip())
            if queued >= MAX_QUEUE_LINES:
                return False  # already backed up; don't pile on, let the drain catch up
    except OSError:
        pass
    line = json.dumps(message, ensure_ascii=False)
    try:
        outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with open(outbox_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except OSError:
        return False


def recover_orphaned_taking(outbox_path: Path, min_age_sec: float = 900) -> int:
    """market_kit.py's drain does os.replace(outbox.jsonl, outbox.taking), reads/sends from
    outbox.taking, then unlinks it. A genuinely crashed/killed drain can leave outbox.taking stranded
    forever. But a *slow, still-running* drain (a large queue takes many real HTTP round-trips to
    walk) also leaves outbox.taking on disk for a while -- and stealing that file out from under an
    active drain does not just lose nothing, it actively corrupts state: the drain's own final
    unlink() then fails, and worse, every line we "recover" (including ones the drain already posted
    successfully) gets re-appended to outbox.jsonl, duplicating content. A short timeout here was
    tried (15s) and confirmed to cause exactly that: one stale line got re-queued over 18,000 times
    once the backlog made drain cycles take longer than 15s. min_age_sec is deliberately generous
    (15 min) so this only fires for a drain that is truly gone, not one that is merely busy.
    """
    taking = outbox_path.parent / "outbox.taking"
    if not taking.exists():
        return 0
    try:
        if time.time() - taking.stat().st_mtime < min_age_sec:
            return 0  # could still be an active drain; don't steal it
        content = taking.read_text(encoding="utf-8")
    except OSError:
        return 0
    lines = [l for l in content.splitlines() if l.strip()]
    if lines:
        with open(outbox_path, "a", encoding="utf-8") as f:
            for l in lines:
                f.write(l + "\n")
    try:
        taking.unlink()
    except OSError:
        pass
    return len(lines)
