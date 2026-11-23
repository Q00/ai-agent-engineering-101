"""11/12 ~16:20 insurance (soft_neg accept / first by the student 2nd) was removed at ~18:30 (person, saved rescaled by
agent.md patch 3.6): the watcher has no insure() any more and its check() switches nothing; the only saved rule is
the A-grade switch (tests/test_grade_rule.py). student_second() is still in the board summary.
    python tests/test_saved_insure.py"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _isolate import policy  # noqa: E402

policy()
import saved_guard_watch as W  # noqa: E402


def test_removed():
    assert not hasattr(W, "insure") and hasattr(W, "a_guard")


def test_student_second():
    j = {"players": [{"student": True, "buyer": {"name": "choo-chu"}}, {"student": True, "buyer": {"name": "dragonash"}},
                     {"buyer": {"name": "junghoonwoo-agent-lab"}}],
         "buyers": [{"name": "choo-chu", "saved": 0.406}, {"name": "dragonash", "saved": 0.381},
                    {"name": "junghoonwoo-agent-lab", "saved": 0.439}]}
    assert W.student_second(j) == 0.381


if __name__ == "__main__":
    test_removed()
    test_student_second()
    print("ok")
