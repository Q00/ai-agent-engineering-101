# RLPO config. M1/M2 values (DESIGN_v1 + EL decisions 15:36) and M2r changes (CR cr_rlpo_M2 REVIEW fc1a0b26).
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
V120C = Path(os.environ.get("RLPO_V120C") or HERE.parent / "v120c")          # read-only dependency (harness, experiments, policy)
V120C_FROM_ENV = os.environ.get("RLPO_V120C")  # N3: path is pinned in deps_lock.json (deps.verify refuses another path)
if str(V120C) not in sys.path:
    sys.path.insert(0, str(V120C))

PERIOD_S = 3600                        # 60 min, configurable
T_END = 1794927540                     # 2026-11-17 23:59 KST (CoS 15:45 전달), configurable
T_START = 1793702924                   # [ASSUMED] first week-09 metrics row 2026-11-03 19:48 KST; round start not in box docs
T_TOTAL_S = T_END - T_START
SUPPLY_STOP_S = 24 * 3600              # no stock_target / qty_per_buy increase in the last 24 h (STRATEGY_v119 rule)
BOARD_REFRESH_S = None                 # board copy cadence: unknown, configurable
MIN_REPLAY_N = 30                      # same as B/work2/llm/harness2_propose.py
SEED = 20261103
N_SEEDS = 20                           # legacy (m2_run / m2b_run reports only); NOT used by guard.wins (C7)
SEED_WIN_MIN = 18                      # legacy, NOT used by guard.wins: no-op because lo = min over seeds (CR 2)
MONEY_TOL = -0.01                      # judge money metric may not fall more than 1 %
CUM_FRAC = 0.40                        # per key, vs the anchor policy, as a fraction of the bounds range
MAX_APPLY_PER_WINDOW = 2
APPLY_WINDOW_S = 6 * 3600
COOLDOWN_S = 6 * 3600                  # a path rolled back is blocked for this long
WATCH_S = 2 * 3600                     # C3: no next apply before the watch window of the last apply has closed
MAX_CONSEC_ROLLBACKS = 2
ENDGAME_S = SUPPLY_STOP_S              # alias kept for M1 code
GAMMA_MIN, GAMMA_MAX = 0.3, 0.95        # SUPERSEDED (L bundle, 성수님 16:34 decision, CoS 전달): no code reads these any more
# ---- L bundle RL constants (DESIGN_v3 sec 3). gamma_t = 1 - GAMMA_DT_S / T_rem, T_rem = GAMMA_T_END - t, floor 0.
GAMMA_DT_S = PERIOD_S                  # one decision cycle (3600 s)
GAMMA_FLOOR = 0.0                      # gamma never below 0 near the end (T_rem <= dt -> 0)
GAMMA_T_END = T_END                    # horizon end = round end 2026-11-17 23:59 KST (final grade time)
RL_CONST_SHA = "b1cb52b53456ad552b92bae2a474d8e78b182ea5c2f250a6a70cbe2fe6455589"
#                # reward.const_sha() pin; reward.check_constants refuses a change
EXCLUDED_KEY_SUBSTR = ("warranty",)    # EL 15:36: warranty keys excluded

# ---- M2r (C7) win rule: the 18/20 seed rule is dropped (no-op, CR 2). Bonferroni over the cycles of one day,
# one-sided lower bound = min(Student-t on hour-block sums, block bootstrap quantile), plus a block-count floor.
ALPHA_DAY = 0.05
CYCLES_PER_DAY = 24
ALPHA_ADJ = ALPHA_DAY / CYCLES_PER_DAY # 0.00208 one-sided per cycle
N_BOOT_JUDGE = 20000
MIN_BLOCKS = 24                        # >= 24 hour blocks in the judged window (C7 example: >= 24 h)

# ---- M2r (C6) proposal allowlist: only these shop keys pass the guard; buyer.* never; categories must exist.
PROPOSE_KEYS = frozenset({"price_mult"})
REPLAY_KEYS = frozenset({"price_mult"})          # the M2 replay adapter judges only these; anything else -> n = 0
# ---- price_mult cannot be judged by replay (CR 3a): apply is blocked in code on every path.
APPLY_BLOCKED_KEYS = frozenset({"price_mult"})

# ---- M2r operating mode. 성수님 15:59 결정 (CoS 전달, CoS는 성수님 직접 확인이라고 보고): proposal-only, the automatic apply
# path stays OFF permanently, approver = CoS, proposals go to the CoS one at a time.
# B5: the approver part is REPLACED by 성수님 16:28 decision (CoS 전달 16:29): approver = LLM inside the harness
# (rlpo.approver, L mode, default OFF). The CoS path below stays for M2r mode (L_APPROVER_ENABLED False).
MODE = "propose_only"
AUTO_APPLY = False                     # OFF. 15:59 (CoS 전달) said permanent OFF, approver CoS; replaced by 성수님 16:28
#                                        decision (CoS 전달 16:29): approver = LLM inside the harness (rlpo.approver), still OFF
#                                        by default until the L CR gate (cr_L_pass). Code paths refuse auto apply while False.
LIVE_OBS_READY = True  # v121c gate step (cr_L_pass source cos_override): CoS 결정 18:29 (CoS 전달): LIVE_OBS_READY=True, cr_L_pass=True (CR L=REVISE, 관측모드 한정, source cos_override), APPLY_KEYS=[], L_APPLY_RANGES={}
APPROVER = "CoS"                       # 성수님 15:59 결정 (CoS 전달): the approver is 머큐리 (CoS)
APPROVER_ID = "8decbdb4-9f91-4a11-b76c-c0db67d60d39"
APPROVAL_CHANNELS = frozenset({"cos"})
# start gates (성수님 15:59 결정, CoS 전달): B r3f restart + 1 h observation passed, and the C1~C5 bundle passed CR.
START_GATES = ("b_r3f_restart_obs_1h", "cr_m2r_c1_c5_pass")

DEPS_LOCK = HERE / "deps_lock.json"    # C9: pinned v120c dependency shas, verified fail-closed by the loop

# ---- C12: M2b adapter price_mult stays unsupported (price_ok False) regardless of the slope CI. Opening = CR gate.
M2B_PRICE_OPEN = False
# ---- C12 live entry point (rlpo.live_entry): decision time t_d = now, judged window = PAST [t_d - LIVE_LOOKBACK_S, t_d),
# adapter = replay_adapter.CausalAdapter only. Legacy Adapter (look-ahead) and adapter_m2b (future window) are refused.
LIVE_LOOKBACK_S = 24 * 3600
STALE_S = PERIOD_S                      # P2: last recorded row older than one cycle before t_d -> verdict stale
# ---- C11 explicit APPLY allowlist (keys that may ever be applied). Empty in M2r: nothing is applicable.
# open_frac / max_pay_frac / min_margin are NOT opened (would need per-key false-win CI upper bound + CR).
APPLY_KEYS = frozenset()  # v121c gate step (cr_L_pass source cos_override): CoS 결정 18:29 (CoS 전달): LIVE_OBS_READY=True, cr_L_pass=True (CR L=REVISE, 관측모드 한정, source cos_override), APPLY_KEYS=[], L_APPLY_RANGES={}

# ---- P1 (CR REVIEW_M2r), changed by CoS 16:27 relay: 성수님 14:38 decision = no LLM caps (unlimited).
# Daily KST call/token counts are REPORTED (state breaker ledger + experience row replay.llm_day) and a warning line
# is written to the alarm file once per day at LLM_WARN_TOKENS_DAY. No hard cap and no OFF latch for usage;
# the error auto-OFF (breaker.py: quota/auth/model/3x other) is unchanged.
LLM_WARN_TOKENS_DAY = 300_000
LLM_HARD_CAP = None                     # disabled on purpose (성수님 14:38 무제한); not read by any code path

# ---- L bundle: LLM approver inside code guards (DESIGN_v3 sec 7; 성수님 16:28 decision, CoS 전달 16:29). Default OFF.
L_APPROVER_ENABLED = False             # ON only after CR review of CR_BUNDLE_L
APPROVER_LLM = "approver_llm"          # L3: in L mode an approval must come from rlpo.approver (MAC-checked)
APPROVAL_TTL_S = PERIOD_S              # L2: a pending proposal older than this cannot be approved
L_APPLY_RANGES = {}  # v121c gate step (cr_L_pass source cos_override): CoS 결정 18:29 (CoS 전달): LIVE_OBS_READY=True, cr_L_pass=True (CR L=REVISE, 관측모드 한정, source cos_override), APPLY_KEYS=[], L_APPLY_RANGES={}
GATE_SETTERS = frozenset({"CoS", "EL"})  # L4: who may set a start gate (with a ref); cr_* gates: CoS only (B3)
# ---- L2 (CR REVIEW_M2r2_L 485bb16c)
L_START_GATES = ("cr_L_pass",)        # B3: L-only start gate; by=CoS, ref = <CR report path>#<sha256 prefix> (checked)
CR_GATE_SETTER = "CoS"                 # B3: cr_* gates only by the CoS, with a ref matching the CR report file
L_LOCK = HERE / "l_lock.json"          # B3: sha256 of config.py, approver.py, guard.py; checked by approver and Loop (L mode)
L_LOCK_FILES = ("config.py", "approver.py", "guard.py")
VERDICT_TTL_S = 600                    # B2: a verdict older than this is not applied (single-use nonce as well)
L_FREEZE_S = WATCH_S + PERIOD_S        # L-C5: no apply when T_rem < 3 h (watch could not close before the round end)
PROPOSER_MODEL = "gpt-6.1-terra"       # v121 (성수님 결정 CoS 전달 17:40): proposer = rlpo.llm_live role rlpo_prop (live codex path)
APPROVER_MODEL = "gpt-6.1-terra"       # v121b CoS 결정 18:02: same model as the proposer, only inside the code guards
# v121: only one model is verified (gpt-6.1-terra). Independence of the approver is by INPUT (structured numbers made
# by code, no proposer text, no evidence source text; approver._prompt) and by SESSION (codex exec --ephemeral, a fresh
# process per call, separate role rlpo_appr). False = L-C3 as before (approver model must differ). True only with a
# 성수님 결정 (CoS 전달) + CR; then APPROVER_MODEL = "gpt-6.1-terra" is allowed to equal PROPOSER_MODEL.
# v121b: CoS 결정 18:02 -> True (approver.same_model_violations + prompt_violations enforce the guards; CR scope).
APPROVER_SAME_MODEL_OK = True
APPROVER_ROLE = "rlpo_appr"            # v121b: the approver LLM role (own caps counter / breaker; never the proposer)
LLM_WORK = Path(os.environ["RLPO_LLM_WORK"]) if os.environ.get("RLPO_LLM_WORK") else None   # v121: work tree with llm/

# ---- L-C6 invariants (import time): every applicable key is proposable; every L range key is applicable
assert APPLY_KEYS <= PROPOSE_KEYS, "L-C6: APPLY_KEYS must be a subset of PROPOSE_KEYS"
assert set(L_APPLY_RANGES) <= APPLY_KEYS, "L-C6: L_APPLY_RANGES keys must be a subset of APPLY_KEYS"
