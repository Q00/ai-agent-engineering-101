"""A2_OPERATOR_EFFORT overrides the harness route's effort for operator model calls (2026-11-10, usage cut)."""

from kernel import operator as op


class _Terra:
    def complete(self, prompt, *, agent, timeout_s, effort="medium"):
        self.effort = effort
        return ""


def _ask(route):
    o = op.Operator.__new__(op.Operator)
    o.terra = _Terra()
    o._ask("p", route)
    return o.terra.effort


def test_route_effort_without_env(monkeypatch):
    monkeypatch.delenv("A2_OPERATOR_EFFORT", raising=False)
    assert _ask({"effort": "medium"}) == "medium"


def test_env_overrides_route(monkeypatch):
    monkeypatch.setenv("A2_OPERATOR_EFFORT", "low")
    assert _ask({"effort": "medium"}) == "low"
