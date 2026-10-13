"""Deterministic negotiation state and the shadow/enforce policy switch."""

from __future__ import annotations

from uuid import uuid4

from market.engine_errors import MarketError
from market.metrics import summarize
from market.models import (
    Action,
    Condition,
    EpisodeSummary,
    EventInput,
    EventRecord,
    EventType,
    Grant,
    Move,
    MoveView,
    Negotiation,
    NegotiationId,
    NegotiationView,
    Role,
    Scenario,
    Status,
)
from market.policy import outside_limit, render_move, violation_reason


class MarketStore:
    """Own negotiation snapshots and an append-only evidence ledger."""

    def __init__(self) -> None:
        self._states: dict[NegotiationId, Negotiation] = {}
        self._events: dict[NegotiationId, list[EventRecord]] = {}

    def open(self, scenario: Scenario, condition: Condition) -> NegotiationId:
        """Open a buyer-first negotiation and return its explicit handle."""
        negotiation_id = NegotiationId(uuid4().hex)
        self._states[negotiation_id] = Negotiation(
            negotiation_id=negotiation_id,
            scenario=scenario,
            condition=condition,
        )
        self._events[negotiation_id] = []
        return negotiation_id

    def view(self, grant: Grant, negotiation_id: NegotiationId) -> NegotiationView:
        """Return a role-specific view and inject the fixed notice when required."""
        state = self._authorized(grant, negotiation_id)
        rendered: list[MoveView] = []
        for move in state.moves:
            view, notice_rendered = render_move(state, grant.role, move)
            if notice_rendered:
                self._record(
                    state,
                    EventInput(event=EventType.NOTICE_RENDERED, role=grant.role, action=Action.GET),
                )
            rendered.append(view)
        self._states[negotiation_id] = state.model_copy(
            update={"tool_calls": state.tool_calls + 1}
        )
        return NegotiationView(
            negotiation_id=negotiation_id,
            item=state.scenario.item,
            caller_role=grant.role,
            turn=state.turn,
            status=state.status,
            moves=tuple(rendered),
        )

    def propose(self, grant: Grant, negotiation_id: NegotiationId, price: int) -> None:
        """Commit a whole-number proposal or expose a policy refusal."""
        state = self._before_move(grant, negotiation_id, Action.PROPOSE, price)
        violation = outside_limit(grant, price)
        state = self._policy(state, grant, Action.PROPOSE, price, violation)
        self._commit(state, grant.role, Action.PROPOSE, price)

    def accept(self, grant: Grant, negotiation_id: NegotiationId) -> None:
        """Accept the opposing party's latest proposal."""
        state = self._before_move(grant, negotiation_id, Action.ACCEPT, None)
        price = self._last_proposal_price(state, grant.role.other())
        violation = outside_limit(grant, price)
        state = self._policy(state, grant, Action.ACCEPT, price, violation)
        self._commit(state, grant.role, Action.ACCEPT, price, Status.DEAL)

    def reject(self, grant: Grant, negotiation_id: NegotiationId) -> None:
        """Reject the latest proposal and yield the turn."""
        state = self._before_move(grant, negotiation_id, Action.REJECT, None)
        self._last_proposal_price(state, grant.role.other())
        self._commit(state, grant.role, Action.REJECT, None)

    def refuse(self, grant: Grant, negotiation_id: NegotiationId) -> None:
        """Leave the negotiation with no deal."""
        state = self._before_move(grant, negotiation_id, Action.REFUSE, None)
        self._commit(state, grant.role, Action.REFUSE, None, Status.NO_DEAL)

    def pass_turn(self, negotiation_id: NegotiationId) -> None:
        """Advance after one host run made no valid move."""
        state = self._get(negotiation_id)
        if state.status is not Status.OPEN:
            code = "closed"
            detail = "negotiation is already closed"
            raise MarketError(code, detail)
        updated = state.model_copy(
            update={"turn": state.turn.other(), "pending_refusals": 0}
        )
        self._states[negotiation_id] = updated
        self._record(
            updated,
            EventInput(event=EventType.TURN_PASSED, role=state.turn, action=Action.ADVANCE),
        )

    def summary(self, negotiation_id: NegotiationId) -> EpisodeSummary:
        """Return grader-facing metrics without changing tool-call counts."""
        return summarize(self._get(negotiation_id))

    def events(self, negotiation_id: NegotiationId) -> tuple[EventRecord, ...]:
        """Return immutable evidence in append order."""
        self._get(negotiation_id)
        return tuple(self._events[negotiation_id])

    def _before_move(
        self,
        grant: Grant,
        negotiation_id: NegotiationId,
        action: Action,
        price: int | None,
    ) -> Negotiation:
        state = self._authorized(grant, negotiation_id)
        self._record(
            state,
            EventInput(event=EventType.CALL_ATTEMPTED, role=grant.role, action=action, price=price),
        )
        if state.status is not Status.OPEN:
            code = "closed"
            detail = "negotiation is already closed"
            raise MarketError(code, detail)
        if state.turn is not grant.role:
            code = "turn"
            detail = f"it is {state.turn.value} turn"
            raise MarketError(code, detail)
        return state.model_copy(update={"tool_calls": state.tool_calls + 1})

    def _policy(
        self,
        state: Negotiation,
        grant: Grant,
        action: Action,
        price: int,
        violation: bool,
    ) -> Negotiation:
        if not violation:
            return state
        attempted = state.model_copy(
            update={"attempted_violations": state.attempted_violations + 1}
        )
        if not state.condition.enforces_limit:
            return attempted
        reason = violation_reason(grant, price)
        refused = attempted.model_copy(
            update={
                "refused_calls": attempted.refused_calls + 1,
                "pending_refusals": attempted.pending_refusals + 1,
            }
        )
        self._states[state.negotiation_id] = refused
        self._record(
            refused,
            EventInput(
                event=EventType.CALL_REFUSED,
                role=grant.role,
                action=action,
                price=price,
                reason=reason,
            ),
        )
        code = "limit"
        raise MarketError(code, reason)

    def _commit(
        self,
        state: Negotiation,
        role: Role,
        action: Action,
        price: int | None,
        status: Status = Status.OPEN,
    ) -> None:
        move = Move(index=len(state.moves) + 1, role=role, action=action, price=price)
        recovered = state.recovered_refusals + state.pending_refusals
        updated = state.model_copy(
            update={
                "moves": (*state.moves, move),
                "status": status,
                "turn": role.other(),
                "price": price if status is Status.DEAL else state.price,
                "turns": state.turns + 1,
                "pending_refusals": 0,
                "recovered_refusals": recovered,
            }
        )
        self._states[state.negotiation_id] = updated
        self._record(
            updated,
            EventInput(event=EventType.MOVE_COMMITTED, role=role, action=action, price=price),
        )
        if status is not Status.OPEN:
            self._record(
                updated,
                EventInput(event=EventType.EPISODE_CLOSED, role=role, action=action, price=price),
            )

    def _authorized(self, grant: Grant, negotiation_id: NegotiationId) -> Negotiation:
        if grant.negotiation_id != negotiation_id:
            code = "negotiation"
            detail = f"grant is bound to {grant.negotiation_id}, not {negotiation_id}"
            raise MarketError(
                code,
                detail,
            )
        state = self._get(negotiation_id)
        if grant.condition is not state.condition:
            code = "condition"
            detail = "grant condition does not match negotiation"
            raise MarketError(code, detail)
        return state

    def _get(self, negotiation_id: NegotiationId) -> Negotiation:
        try:
            return self._states[negotiation_id]
        except KeyError as exc:
            code = "negotiation"
            detail = "unknown negotiation"
            raise MarketError(code, detail) from exc

    @staticmethod
    def _last_proposal_price(state: Negotiation, role: Role) -> int:
        for move in reversed(state.moves):
            if move.role is role and move.action is Action.PROPOSE and move.price is not None:
                return move.price
        code = "proposal"
        detail = "no opposing proposal to answer"
        raise MarketError(code, detail)

    def _record(
        self,
        state: Negotiation,
        item: EventInput,
    ) -> None:
        ledger = self._events[state.negotiation_id]
        ledger.append(
            EventRecord(
                sequence=len(ledger) + 1,
                negotiation_id=state.negotiation_id,
                event=item.event,
                role=item.role,
                action=item.action,
                price=item.price,
                reason=item.reason,
                turn_index=state.turns,
            )
        )
