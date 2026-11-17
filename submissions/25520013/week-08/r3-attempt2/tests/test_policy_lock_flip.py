"""11-17: (A) policy.json writers hold an flock on policy.json.lock across their read-modify-write;
(B) the card's [키 왕복] fact lists policy keys whose value changed direction 2+ times in the last 2 hours."""

import fcntl
import json
import os
import threading
import time
from types import SimpleNamespace

from kernel import brief
from kernel import policy_edit as pe
from tests.test_policy_edit import BED, _pol, _traj, root  # noqa: F401  (root is a fixture)

NOW = 1_794_400_000.0


# ------------------------------------------------------------ A. write lock
def _slow(path):
    time.sleep(
        0.2
    )  # a slow check between the read and the rename widens the lost-update window


def test_two_interleaved_writers_keep_both_updates(root):
    traj = _traj(root)
    items = (
        [{"section": BED, "key": "price_mult", "value": 0.7}],
        [{"section": BED, "key": "stock_target", "value": 40}],
    )
    ts = [
        threading.Thread(
            target=pe.apply_many,
            args=(root, it, traj, time.time()),
            kwargs={"validate": _slow},
        )
        for it in items
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert _pol(root)["categories"][BED] == {"stock_target": 40, "price_mult": 0.7}
    assert os.path.exists(os.path.join(root, "policy.json.lock"))


def test_writer_waits_for_the_lock_file(root):
    done = []
    with open(os.path.join(root, "policy.json.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)  # another tool on the box holds the lock
        t = threading.Thread(
            target=lambda: done.append(
                pe.apply(
                    root,
                    [{"section": BED, "key": "price_mult", "value": 0.7}],
                    _traj(root),
                    time.time(),
                    validate=lambda p: None,
                )
            )
        )
        t.start()
        time.sleep(0.3)
        assert not done and _pol(root)["categories"][BED]["price_mult"] == 0.66
        fcntl.flock(f, fcntl.LOCK_UN)
    t.join(5)
    assert (
        done and done[0][0]["ok"] and _pol(root)["categories"][BED]["price_mult"] == 0.7
    )


def test_nested_writers_do_not_deadlock(root):
    traj, ok = _traj(root), []

    def run():
        with pe.write_lock(root):
            ok.append(
                pe.kernel_set(
                    root,
                    BED,
                    "price_mult",
                    0.7,
                    traj,
                    time.time(),
                    "t",
                    validate=lambda p: None,
                )
            )

    t = threading.Thread(target=run)
    t.start()
    t.join(5)
    assert not t.is_alive() and ok[0]["ok"]


# ------------------------------------------------------------ B. [키 왕복]
def _flip(tmp_path, edits, now=NOW):
    os.makedirs(tmp_path / "a2", exist_ok=True)
    with open(tmp_path / "a2/trajectory.jsonl", "w", encoding="utf-8") as f:
        for ts, sec, key, old, new in edits:
            f.write(
                json.dumps(
                    {
                        "ts": ts,
                        "agent": "operator",
                        "kind": "policy_edit",
                        "role": "shop",
                        "section": sec,
                        "key": key,
                        "old": old,
                        "new": new,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        f.write(json.dumps({"ts": now, "kind": "llm", "status": "ok"}) + "\n")
    return brief._flip_flops(SimpleNamespace(root=str(tmp_path), now=now))


def test_flip_none(tmp_path):
    assert _flip(tmp_path, []) == []
    assert (
        brief._flip_flops(SimpleNamespace(root=str(tmp_path / "missing"), now=NOW))
        == []
    )


def test_flip_flop_listed_with_values_and_count(tmp_path):
    e = [
        (NOW - 3000, BED, "price_mult", 0.71, 0.66),
        (NOW - 2000, BED, "price_mult", 0.66, 0.70),
        (NOW - 1000, BED, "price_mult", 0.70, 0.65),
    ]
    assert _flip(tmp_path, e) == [
        "[키 왕복] 최근 2시간 방향을 2번 이상 바꾼 키: 침대/매트리스 price_mult "
        "0.71→0.66→0.7→0.65 (3번 바꿈)"
    ]
    # one of the turns older than 2 hours: only one direction change left in the window
    assert _flip(tmp_path, [(NOW - 8000, *e[0][1:])] + e[1:]) == []


def test_flip_monotonic_not_listed(tmp_path):
    e = [
        (NOW - 3000, BED, "price_mult", 0.71, 0.68),
        (NOW - 2000, BED, "price_mult", 0.68, 0.66),
        (NOW - 1000, BED, "price_mult", 0.66, 0.60),
        (NOW - 900, "defaults.goods", "max_rounds", 4, 5),
    ]
    assert _flip(tmp_path, e) == []


def test_flip_on_the_card(tmp_path):
    e = [
        (NOW - 3000, BED, "price_mult", 0.71, 0.66),
        (NOW - 2000, BED, "price_mult", 0.66, 0.70),
        (NOW - 1000, BED, "price_mult", 0.70, 0.65),
    ]
    _flip(tmp_path, e)
    t = brief.build_compact(str(tmp_path), NOW)
    assert "[키 왕복] 최근 2시간" in t and "만들지 못함" not in t
