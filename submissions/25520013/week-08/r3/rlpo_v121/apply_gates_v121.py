#!/usr/bin/env python3
# v121b RLPO gate step (go script step 4). Prepared only; run by call_v121_go.sh on labpc after CR pass.
# Turns ON exactly the decided values of LIVE_OBS_READY, APPLY_KEYS, L_APPLY_RANGES (rlpo/config.py lines) and the
# cr_L_pass state gate (rstate.Store.set_gate, by=CoS, ref=<CR report path>#<sha256 prefix>, checked against the file).
# Values come ONLY from a gates file whose full sha256 the CR names (--gates-sha). No default values exist here:
# status != "DECIDED", a missing decision ref, or a value outside the code invariants -> exit 2, nothing written.
#   --mode check : validate + build the new config.py / l_lock.json in a temp copy, print shas, write nothing
#   --mode apply : the same, then .bak + atomic write, re-check (import L-C6 + l_lock.violations()==[]), set cr_L_pass
# Exit 0 ok, 2 refused (nothing written), 5 failed after write (restored from .bak).
# v121c (CR v121b R4 + CoS 결정 18:48, CoS 전달): --expect-config-sha is mandatory (full sha256 named by the CR);
#   control characters in decision_ref refused; the gates file must carry "cr_L_pass_source": "cos_override" and the
#   store gets an extra event {ev: gate_source, gate: cr_L_pass, source: cos_override, ...} (cr_L_pass = a CoS
#   override of a CR REVISE verdict for observation mode only, not a CR pass);
#   N1: the store open_watch is read; with --clear-open-watch a remaining open_watch is cleared (.bak + sha before /
#   after printed) before anything else, else refused; a missing store is created with --init-store-anchor
#   <policy.json> (fresh state: OFF, open_watch None, verified [None] = no rollback target -> any watch rollback alarms).
import unicodedata
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LINES = {"LIVE_OBS_READY": r"^LIVE_OBS_READY = .*$", "APPLY_KEYS": r"^APPLY_KEYS = .*$",
         "L_APPLY_RANGES": r"^L_APPLY_RANGES = .*$"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def refuse(msg):
    print("GATES refused: " + msg, flush=True)
    sys.exit(2)


def load_gates(path, want_sha):
    if not re.fullmatch(r"[0-9a-f]{64}", want_sha or ""):
        refuse("--gates-sha must be the full sha256 named by the CR")
    if sha(path) != want_sha:
        refuse("gates file sha differs from --gates-sha")
    g = json.loads(Path(path).read_text(encoding="utf-8"))
    if g.get("status") != "DECIDED":
        refuse("gates file status is not DECIDED (values not decided)")
    ref = str(g.get("decision_ref") or "").strip()
    if any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") for c in str(g.get("decision_ref") or "")):
        refuse("decision_ref has a control / format character")
    if not ref or len(ref) > 160 or not ("성수님" in ref or "CoS" in ref):
        refuse("decision_ref missing (needs the 성수님 / CoS decision record)")
    if g.get("cr_L_pass_source") != "cos_override":
        refuse("cr_L_pass_source must be cos_override (CoS override of the CR L verdict, observation mode only)")
    v = g.get("values") or {}
    if set(v) != {"LIVE_OBS_READY", "APPLY_KEYS", "L_APPLY_RANGES"}:
        refuse("values must hold exactly LIVE_OBS_READY, APPLY_KEYS, L_APPLY_RANGES")
    if not isinstance(v["LIVE_OBS_READY"], bool):
        refuse("LIVE_OBS_READY must be true/false")
    ks = v["APPLY_KEYS"]
    if not (isinstance(ks, list) and all(isinstance(k, str) and re.fullmatch(r"[a-z_]{1,40}", k) for k in ks)):
        refuse("APPLY_KEYS must be a list of key names")
    rg = v["L_APPLY_RANGES"]
    if not isinstance(rg, dict):
        refuse("L_APPLY_RANGES must be an object key -> [lo, hi]")
    for k, b in rg.items():
        if not (isinstance(b, list) and len(b) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                            for x in b) and b[0] < b[1]):
            refuse(f"L_APPLY_RANGES[{k}] must be [lo, hi] numbers, lo < hi")
    return g, ref, v


def render(src, v, ref):
    tag = "  # v121c gate step (cr_L_pass source cos_override): " + ref
    keys = "frozenset({" + ", ".join(json.dumps(k) for k in sorted(v["APPLY_KEYS"])) + "})" if v["APPLY_KEYS"] else "frozenset()"
    rng = "{" + ", ".join(f"{json.dumps(k)}: ({float(b[0])!r}, {float(b[1])!r})" for k, b in sorted(v["L_APPLY_RANGES"].items())) + "}"
    ready = v["LIVE_OBS_READY"]
    new = {"LIVE_OBS_READY": f"LIVE_OBS_READY = {ready!r}" + tag, "APPLY_KEYS": f"APPLY_KEYS = {keys}" + tag,
           "L_APPLY_RANGES": f"L_APPLY_RANGES = {rng}" + tag}
    for k, rx in LINES.items():
        src, n = re.subn(rx, lambda m, k=k: new[k], src, count=0, flags=re.M)
        if n != 1:
            refuse(f"config.py line {k} found {n} times (want 1)")
    return src


def check_pkg(pkg, env):
    code = ("import json; from rlpo import config as C, l_lock; "
            "bad = sorted(set(C.APPLY_KEYS) & set(C.APPLY_BLOCKED_KEYS)); "
            "print(json.dumps({\"violations\": l_lock.violations(), \"blocked\": bad, "
            "\"apply_keys\": sorted(C.APPLY_KEYS), \"ranges\": {k: list(v) for k, v in C.L_APPLY_RANGES.items()}, "
            "\"live_obs_ready\": C.LIVE_OBS_READY}))")
    p = subprocess.run([sys.executable, "-c", code], cwd=str(pkg), env=env, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        return None, (p.stderr.strip().splitlines() or ["import failed"])[-1][:200]
    return json.loads(p.stdout.strip().splitlines()[-1]), None


def build(pkg, v, ref, env):
    cfg = Path(pkg) / "rlpo" / "config.py"
    new_src = render(cfg.read_text(encoding="utf-8"), v, ref)
    tmp = Path(tempfile.mkdtemp(prefix="v121b_gates_"))
    shutil.copytree(Path(pkg) / "rlpo", tmp / "rlpo", ignore=shutil.ignore_patterns("*.bak*", "__pycache__", "tests"))
    (tmp / "rlpo" / "config.py").write_text(new_src, encoding="utf-8")
    pin = ("from rlpo import l_lock; l_lock.pin(note=" + json.dumps("v121b gate step re-pin: " + ref) + ")")
    p = subprocess.run([sys.executable, "-c", pin], cwd=str(tmp), env=env, capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        refuse("re-pin in temp copy failed (L-C6 invariant or import): " +
               ((p.stderr.strip().splitlines() or ["?"])[-1][:200]))
    res, err = check_pkg(tmp, env)
    viol = None if err else res["violations"]
    if err or viol:
        refuse(f"temp copy check failed: {err or viol}")
    blocked = res["blocked"]
    if blocked:
        refuse(f"APPLY_KEYS in APPLY_BLOCKED_KEYS {blocked}: would stay blocked; needs a separate decision + CR")
    return tmp, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", required=True, help="dir that holds rlpo/ (labpc rlpo deploy dir)")
    ap.add_argument("--gates", required=True)
    ap.add_argument("--gates-sha", required=True)
    ap.add_argument("--cr-ref", required=True, help="<CR report path on labpc>#<sha256 prefix >= 8>")
    ap.add_argument("--store", required=True, help="rlpo state file (rstate.Store); must exist and be valid")
    ap.add_argument("--expect-config-sha", required=True, help="config.py sha after the step, as named by the CR")
    ap.add_argument("--clear-open-watch", action="store_true", help="N1 (CoS 결정 18:48): clear a remaining open_watch")
    ap.add_argument("--init-store-anchor", default="", help="policy.json path: create the store when it is missing")
    ap.add_argument("--mode", choices=("check", "apply"), default="check")
    a = ap.parse_args()
    pkg = Path(a.pkg).resolve()
    env = dict(os.environ, PYTHONPATH=str(pkg))
    if not re.fullmatch(r"[0-9a-f]{64}", a.expect_config_sha or ""):
        refuse("--expect-config-sha must be the full sha256 named by the CR")
    g, ref, v = load_gates(a.gates, a.gates_sha)
    sys.path.insert(0, str(pkg))
    from rlpo import rstate
    if not rstate.cr_ref_ok(a.cr_ref):
        refuse("--cr-ref does not match the CR report file (path#sha256 prefix)")
    store = rstate.Store(a.store)
    init = False
    if not Path(a.store).exists() and a.init_store_anchor:
        try:
            anchor = Path(a.init_store_anchor).read_bytes()
            json.loads(anchor.decode("utf-8"))
        except (OSError, ValueError) as e:
            refuse(f"--init-store-anchor unreadable: {type(e).__name__}")
        init = True
        st = rstate.fresh(anchor, None, time.time())
        print("GATES N1 store missing -> will be created (open_watch None) anchor_sha=" + st["anchor_sha"][:12], flush=True)
    else:
        st = store.load()
        if st.get("_bad"):
            refuse("rlpo state store missing / fail-closed: " + str(st.get("off_reason"))[:120])
    ow = st.get("open_watch")
    print("GATES N1 open_watch=" + ("None" if not ow else json.dumps(ow, sort_keys=True)[:200]), flush=True)
    if ow and not a.clear_open_watch:
        refuse("N1 open_watch remains and --clear-open-watch not given")
    tmp, res = build(pkg, v, ref, env)
    new_cfg, new_lock = tmp / "rlpo" / "config.py", tmp / "rlpo" / "l_lock.json"
    print("GATES plan " + json.dumps({"config_sha": sha(new_cfg), "l_lock_sha": sha(new_lock), "values": v,
                                      "decision_ref": ref, "cr_ref": a.cr_ref}, ensure_ascii=False), flush=True)
    if a.expect_config_sha and sha(new_cfg) != a.expect_config_sha:
        refuse("new config.py sha differs from --expect-config-sha")
    if a.mode == "check":
        print("GATES check ok (nothing written)")
        return 0
    cfg, lock = pkg / "rlpo" / "config.py", pkg / "rlpo" / "l_lock.json"
    ts = time.strftime("%Y%m%dT%H%M%S") + f"_{os.getpid()}"
    try:                                                   # N1 store prep first (init or clear), then the config
        if init:
            Path(a.store).parent.mkdir(parents=True, exist_ok=True)
            store.init(Path(a.init_store_anchor).read_bytes(), None, time.time())
            print("GATES N1 store created sha=" + sha(a.store)[:12], flush=True)
        elif ow:
            b0 = sha(a.store)
            bak = Path(a.store).with_name(Path(a.store).name + ".bak_v121c_ow_" + ts)
            shutil.copy2(a.store, bak)
            s2 = store.load()
            s2["open_watch"] = None
            s2.setdefault("events", []).append({"t": time.time(), "ev": "open_watch_cleared", "by": "CoS",
                                                "ref": "CoS 결정 18:48 (CoS 전달)", "was": ow})
            store.save(s2)
            if store.load().get("open_watch") is not None:
                raise RuntimeError("open_watch still set after clear")
            print(f"GATES N1 open_watch cleared sha_before={b0[:12]} sha_after={sha(a.store)[:12]} bak={bak.name}",
                  flush=True)
    except Exception as e:                               # noqa: BLE001
        print(f"GATES refused: N1 store prep failed ({type(e).__name__}: {str(e)[:120]}); config not touched")
        return 2
    for p in (cfg, lock):
        shutil.copy2(p, p.with_name(p.name + ".bak_v121b_gates_" + ts))
    try:
        for src, dst in ((new_cfg, cfg), (new_lock, lock)):
            t = dst.with_name("." + dst.name + ".tmp_" + ts)
            shutil.copy2(src, t)
            os.replace(t, dst)
        res2, err = check_pkg(pkg, env)
        if err or res2["violations"] or res2["blocked"]:
            raise RuntimeError(f"post-write check failed: {err or res2}")
        rstate.Store(a.store).set_gate("cr_L_pass", True, "CoS", a.cr_ref, time.time())
        s3 = rstate.Store(a.store).load()
        s3["events"].append({"t": time.time(), "ev": "gate_source", "gate": "cr_L_pass", "source": "cos_override",
                             "decision_ref": ref, "note": "CR L verdict REVISE; CoS override, observation mode only"})
        rstate.Store(a.store).save(s3)
    except Exception as e:                               # noqa: BLE001
        for p in (cfg, lock):
            shutil.copy2(p.with_name(p.name + ".bak_v121b_gates_" + ts), p)
        print(f"GATES failed after write -> restored .bak_v121b_gates_{ts}: {type(e).__name__}: {str(e)[:200]}")
        return 5
    print("GATES applied " + json.dumps({"config_sha": sha(cfg), "l_lock_sha": sha(lock), "bak": ts,
                                         "cr_L_pass": True, "cr_L_pass_source": "cos_override",
                                         "store_sha": sha(a.store)[:12]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
