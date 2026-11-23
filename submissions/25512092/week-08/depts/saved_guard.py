"""Saved guard. 11/12 ~18:30 (person, after agent.md patch 3.6 changed saved from ~0.4 to ~0.8): every fixed threshold
(0.395 / 0.390 / 0.383, the 4th-best rule) and the student-2nd insurance of ~16:20 are removed - why() / back() never
switch anything now (older tools still import them). One rule instead, read every 30 min from the board by
tools/saved_guard_watch.py: against the student buyer just below me in saved (b["student_below"]):
  my saved < that + A_BELOW (0.008)   -> grade_rule.json a_as_bc on: A-grade deals use B / C's moves (0.65 / 1.00)
  my saved >= that + A_BACK (0.015)   -> a_as_bc off: A back to 0.75 / 1.05
Each switch: urgent Telegram and a decision. Nothing else is switched on or off by saved.
"""
A_BELOW, A_BACK = 0.008, 0.015


def why(feature, b):
    """Removed 11/12 ~18:30 (no fixed thresholds): never a reason to switch a feature off."""
    return None


def back(feature, b):
    """Removed 11/12 ~18:30: never a reason to switch a feature on again."""
    return None


def text(feature, b=None):
    return "고정 기준 삭제 (11/12)"


def a_switch(b, a_as_bc):
    """(new a_as_bc, why) when the A rule should switch for board summary b, else None."""
    me, below = (b or {}).get("saved"), (b or {}).get("student_below")
    if me is None or below is None:
        return None
    if not a_as_bc and me < below + A_BELOW:
        return True, f"saved {me} < 학생 바로 아래 {below} + {A_BELOW} = {below + A_BELOW:.4f}"
    if a_as_bc and me >= below + A_BACK:
        return False, f"saved {me} >= 학생 바로 아래 {below} + {A_BACK} = {below + A_BACK:.4f}"
    return None
