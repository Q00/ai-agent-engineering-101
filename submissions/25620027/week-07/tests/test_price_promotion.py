"""Verify promotion claims by executing shipped JS with controlled observations."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import cast

import pytest

from tests.test_price_page import HARNESS, ROOT, PageStructure

PROMOTION_HARNESS = HARNESS.replace(
    "'comparison-updated'].map",
    "'comparison-updated','comparison-headline','comparison-highlights'].map",
)
PROMOTION_SCENARIOS = {
    "fresh_lower_and_tie": r"""
      await run();
      const cards=nodes['comparison-highlights'].children;
      assert.match(cards[0].textContent,
      /≤ 110.*단독 최저/);
      assert.match(cards[1].textContent,
      /≤ 198.*공동최저/);
      assert.match(cards[2].textContent,
      /≤ 260.*공동최저/);
      assert.match(cards[0].textContent,
      /observed 5 other active shops \(top 5\)/);
      assert.match(nodes['comparison-headline'].textContent,
      /lower than or tied/);
      assert.match(nodes['comparison-updated'].textContent,
      /21 Oct, 00:59:59 KST/);
      assert.match(nodes['comparison-updated'].textContent,
      /expires 21 Oct, 01:14:59 KST/);
    """,
    "strictly_lower_all_items": r"""
      payload.own[1].ceiling=197;payload.own[2].ceiling=259;
      await run();
      assert.match(nodes['comparison-headline'].textContent,
      /below observed first asks/);
      assert.equal((nodes['comparison-highlights'].textContent
        .match(/단독 최저/g)||[]).length,3);
    """,
    "partial_unknown_and_higher": r"""
      payload.snapshot.shops=payload.snapshot.shops.slice(0,1);
      payload.snapshot.shops[0].quotes[0].ask=99;
      payload.snapshot.shops[0].quotes[2].ask=null;
      payload.own[0].observed_shop_count=1;payload.own[0].lowest_peer_ask=99;
      payload.own[1].observed_shop_count=1;
      await run();
      const cards=nodes['comparison-highlights'].children;
      assert.match(cards[0].textContent,
      /Above lowest.*observed 1 other/);
      assert.doesNotMatch(cards[0].textContent,
      /최저/);
      assert.match(cards[1].textContent,
      /공동최저/);
      assert.match(cards[2].textContent,
      /Comparison unavailable/);
      assert.match(nodes['comparison-headline'].textContent,
      /unavailable/);
    """,
    "expiry_clears_every_promotional_claim": r"""
      await run(); now+=900000;
      [...timers.values()].forEach(timer=>timer.fn());
      assert.doesNotMatch(nodes['comparison-highlights'].textContent,
      /최저|observed \d/);
      assert.match(nodes['comparison-highlights'].textContent,
      /≤ 286/);
      assert.match(nodes['comparison-headline'].textContent,
      /unavailable/);
    """,
    "error_clears_claims_then_recovers": r"""
      await run(); reject=true; await intervals[0].fn();await tick();
      assert.doesNotMatch(nodes['comparison-highlights'].textContent,
      /최저/);
      assert.match(nodes['comparison-headline'].textContent,
      /unavailable/);
      reject=false; await intervals[0].fn();await tick();
      assert.match(nodes['comparison-highlights'].textContent,
      /공동최저/);
    """,
    "deduplicates_and_cross_checks_shop_count": r"""
      payload.snapshot.shops[1]=payload.snapshot.shops[0];
      await run();
      assert.doesNotMatch(nodes['comparison-highlights'].textContent,
      /최저/);
      payload.own.forEach(item=>item.observed_shop_count=4);
      await intervals[0].fn();await tick();
      assert.match(nodes['comparison-highlights'].textContent,
      /observed 4 other active shops/);
    """,
    "unverified_snapshot_no_claim": r"""
      payload.snapshot.observed_at=iso(1000);
      await run();
      assert.doesNotMatch(nodes['comparison-highlights'].textContent,
      /최저/);
      assert.match(nodes['comparison-headline'].textContent,
      /unavailable/);
    """,
    "hostile_values_do_not_create_promotional_markup": r"""
      payload.snapshot.shops[0].name='<img src=x onerror=alert(1)>';
      payload.snapshot.shops[0].url='javascript:alert(1)';
      payload.own[0].ceiling='<img src=x onerror=alert(1)>';
      await run();
      assert.equal(find(nodes['comparison-highlights'],'img').length,0);
      assert.equal(find(nodes['comparison-rows'],'img').length,0);
      assert.doesNotMatch(nodes['comparison-highlights'].children[0].textContent,
      /최저|<img/);
      assert.equal(find(nodes['comparison-rows'].children[1],'a').length,0);
    """,
}


@pytest.mark.parametrize("scenario", PROMOTION_SCENARIOS)
def test_truthful_promotion(scenario: str) -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required for promotion validation"
    report = r"""
      console.log(JSON.stringify({headline:nodes['comparison-headline'].textContent,
      highlights:nodes['comparison-highlights'].textContent,
      updated:nodes['comparison-updated'].textContent,
      rows:nodes['comparison-rows'].textContent}));
    """
    result = subprocess.run(  # noqa: S603 — fixed local fixture
        [
            node,
            "-e",
            PROMOTION_HARNESS.replace(
                "SCENARIO", PROMOTION_SCENARIOS[scenario] + report
            ),
            str(ROOT / "assets/price-comparison.js"),
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    print(scenario + ": " + result.stdout)


def test_actual_release_page_puts_observations_before_baseline_catalog() -> None:
    page = (ROOT / "assets/seller-ad.html").read_text()
    parser = PageStructure()
    parser.feed(page)
    ids = [attrs.get("id") for _, attrs in parser.elements]
    assert ids.index("comparison-highlights") < ids.index("price-comparison")
    assert ids.index("comparison-updated") < ids.index("price-comparison")
    comparison_index = next(
        index
        for index, (_, attrs) in enumerate(parser.elements)
        if attrs.get("id") == "price-comparison"
    )
    catalog_index = next(
        index
        for index, (_, attrs) in enumerate(parser.elements)
        if attrs.get("aria-label") == "Basic catalog"
    )
    assert comparison_index < catalog_index
    terms = page.split('<script type="application/json" id="shop-terms">')[1]
    document = cast("dict[str, object]", json.loads(terms.split("</script>")[0]))
    comparison = cast("dict[str, object]", document["price_comparison"])
    coupon = cast("dict[str, object]", document["coupon"])
    assert document["strategy_version"] == "1.4.1"
    assert comparison["refresh_seconds"] == 600
    assert comparison["max_age_seconds"] == 900
    assert coupon["guaranteed"] is False
    assert coupon["extra_checkout_deduction"] is False
