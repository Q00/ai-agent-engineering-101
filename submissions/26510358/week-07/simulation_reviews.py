"""Rank-based strategic ratings limited to the explicitly configured course simulation."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import urllib.request


SIMULATION_URL = "https://market.ourolabs.ai"


class SimulationReviewPolicy:
    def __init__(self, trace, path=None, *, fetch_board=None):
        self.trace = trace
        path = Path(path or Path(__file__).resolve().parent / "data/review-policy.json")
        self.policy = json.loads(path.read_text())
        self.enabled = self.policy.get("enabled") is True
        self.version = self.policy["version"]
        if self.enabled:
            if self.policy.get("environment") != "course-agent-market-simulation" \
                    or self.policy.get("market_url") != SIMULATION_URL or self.policy.get("round") != 2:
                raise ValueError("Strategic review policy is restricted to the configured course simulation")
            if self.policy.get("top_group_stars") != 1 or self.policy.get("higher_rank_stars") != 2:
                raise ValueError("Expected configured strategic ratings of 1 and 2")
        self.fetch_board = fetch_board or self._fetch_board
        self.trace.write("simulation_review_policy_loaded", policy_version=self.version, enabled=self.enabled)

    def _fetch_board(self):
        request = urllib.request.Request(SIMULATION_URL + "/api/board", headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=4) as response:
            return json.load(response)

    def _ranking(self, board):
        if not isinstance(board, dict) or board.get("round") != 2 or board.get("current") != 2:
            raise ValueError("Expected the configured current Round 2 board")
        shops = board.get("shops")
        if not isinstance(shops, list):
            raise ValueError("Expected a shop list")
        names = set()
        for row in shops:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"] \
                    or row["name"] in names:
                raise ValueError("Expected distinct shop registry names")
            names.add(row["name"])
            deals, stars = row.get("deals"), row.get("stars")
            if isinstance(deals, bool) or not isinstance(deals, int) or deals < 0:
                raise ValueError("Invalid counted deal total")
            if stars is not None and (isinstance(stars, bool) or not isinstance(stars, (int, float))
                    or not math.isfinite(stars) or not 0 <= stars <= 5):
                raise ValueError("Invalid rating in standings")
        # The board's default Deals order, with its stars tie-break and stable ties.
        return sorted(shops, key=lambda row: (-row["deals"], -(row.get("stars") or 0)))

    def evaluate(self, shop_name, transaction_stars):
        """Called only after a completed trade with a received seller signature.

        The kit still validates that signature and order before publishing any review.
        Missing rank evidence preserves the ordinary transaction-based rating.
        """
        if not self.enabled:
            return transaction_stars, ""
        try:
            ranked = self._ranking(self.fetch_board())
            positions = {row["name"]: index + 1 for index, row in enumerate(ranked)}
            own_rank = positions[self.policy["registry_name"]]
            shop_rank = positions[shop_name]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.trace.write("simulation_review_rank_unavailable", shop=shop_name,
                             policy_version=self.version, detail=str(exc))
            return transaction_stars, ""
        higher = shop_rank < own_rank
        stars = (self.policy["top_group_stars"] if shop_rank <= self.policy["top_group_rank"]
                 else self.policy["higher_rank_stars"]) if higher else transaction_stars
        self.trace.write("simulation_review_rated", policy_version=self.version, shop=shop_name,
                         observed_at=datetime.now(timezone.utc).isoformat(), shop_rank=shop_rank,
                         own_rank=own_rank, transaction_stars=transaction_stars, stars=stars,
                         ranked_higher=higher, ranking_order="deals_desc_stars_desc")
        note = (f" Simulation competition rating: shop rank {shop_rank} is above my shop rank "
                f"{own_rank}; {stars} stars reflects simulation strategy, not a product-quality finding."
                if higher else "")
        return stars, note
