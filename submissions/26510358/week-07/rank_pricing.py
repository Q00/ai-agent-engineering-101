"""Apply the owner's top-three pricing rule to new quotes only.

Rank selects configured margins, never a model decision or a lower reserve.
The official current-round board owns rank; unavailable evidence preserves phase.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import urllib.request


class RankPricing:
    def __init__(self, policy, trace, *, state_path=None, fetch_board=None, clock=time.monotonic):
        self.policy = policy
        self.rule = policy.get("rank_cycle") or {}
        self.trace = trace
        self.enabled = bool(self.rule.get("enabled"))
        self.phase = "acquisition" if self.enabled else "normal"
        self.state_path = Path(state_path or Path(__file__).resolve().parent / ".market" / "rank-pricing.json")
        self.fetch_board = fetch_board or self._fetch_board
        self.clock = clock
        self.next_poll = 0.0
        self.lock = threading.Lock()
        if self.enabled and self.state_path.exists():
            try:
                state = json.loads(self.state_path.read_text())
                if state.get("policy_version") == policy["version"] and state.get("phase") in self.rule["phases"]:
                    self.phase = state["phase"]
            except (OSError, ValueError, TypeError) as exc:
                self.trace.write("pricing_state_unavailable", detail=str(exc))

    def quote_policy(self):
        selected = self.rule["phases"][self.phase] if self.enabled else self.policy
        return deepcopy({"phase": self.phase,
                         "opening_margin": selected["opening_margin"],
                         "default_margin_rate": self.policy["default_margin_rate"],
                         "accept_floor_bids": selected["accept_floor_bids"],
                         "review_referral": self.policy.get("review_referral", {})})

    def _fetch_board(self):
        url = self.rule["market_url"].rstrip("/") + "/api/board"
        with urllib.request.urlopen(url, timeout=4) as response:
            return json.load(response)

    def _rank(self, board):
        if not isinstance(board, dict):
            raise ValueError("Expected a board object")
        if board.get("round") != self.rule["round"] or board.get("current") != self.rule["round"]:
            raise ValueError("The board is not the configured current round")
        shops = board.get("shops")
        if not isinstance(shops, list) or len({s["name"] for s in shops}) != len(shops):
            raise ValueError("Expected distinct shop names")
        for shop in shops:
            deals = shop.get("deals")
            stars = shop.get("stars") or 0
            if isinstance(deals, bool) or not isinstance(deals, int) or deals < 0:
                raise ValueError("Invalid counted deal total")
            if isinstance(stars, bool) or not isinstance(stars, (int, float)) or not math.isfinite(stars) or not 0 <= stars <= 5:
                raise ValueError("Invalid shop rating")
        # Matches the board's default Deals sort, including its stars tie-break.
        ranked = sorted(shops, key=lambda s: (-s["deals"], -(s.get("stars") or 0)))
        own = [i + 1 for i, shop in enumerate(ranked) if shop["name"] == self.rule["registry_name"]]
        if len(own) != 1:
            raise ValueError("Our existing registry identity is missing")
        return own[0], ranked

    def refresh(self):
        if not self.enabled:
            return self.quote_policy()
        with self.lock:
            if self.clock() < self.next_poll:
                return self.quote_policy()
            self.next_poll = self.clock() + self.rule["poll_seconds"]
            try:
                rank, shops = self._rank(self.fetch_board())
                phase = "recovery" if rank <= self.rule["top_n"] else "acquisition"
                state = {"policy_version": self.policy["version"], "phase": phase,
                         "rank": rank, "round": self.rule["round"],
                         "observed_at": datetime.now(timezone.utc).isoformat()}
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", dir=self.state_path.parent,
                                                 prefix=".rank-pricing-", delete=False) as stream:
                    temporary = Path(stream.name)
                    json.dump(state, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.replace(temporary, self.state_path)
                finally:
                    temporary.unlink(missing_ok=True)
                previous = self.phase
                self.phase = phase
                self.trace.write("pricing_rank_observed", **state,
                                 leaders=[{"name": s["name"], "deals": s["deals"],
                                           "stars": s.get("stars")} for s in shops[:4]])
                if previous != phase:
                    self.trace.write("pricing_phase_changed", previous=previous, phase=phase, rank=rank)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                self.trace.write("pricing_rank_unavailable", phase=self.phase, detail=str(exc))
            return self.quote_policy()
