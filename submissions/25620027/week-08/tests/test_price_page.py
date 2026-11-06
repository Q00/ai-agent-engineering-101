"""Execute the shipped comparison script against a hostile, clocked DOM seam."""

from __future__ import annotations

import json
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import override

import pytest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.value = '';
      this.className = ''; }
  set textContent(value) { this.value = String(value); this.children = []; }
  get textContent() { return this.value + this.children.map(child =>
      child.textContent).join(' '); }
  set innerHTML(_) { throw Error('Unsafe HTML assignment'); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.value = ''; this.children = children; }
}
const find = (node, tag) => [node, ...node.children.flatMap(child => find(child,
      tag))].filter(n => n.tag === tag);
let now = Date.parse('2026-10-21T01:00:00+09:00');
class Clock extends Date { static now() { return now; } }
const iso = offset => new Date(now + offset).toISOString();
const items = ['lamp','chair','bookshelf'];
const payload = {
 enabled:true, fresh:true, refresh_seconds:600, max_age_seconds:900,
 snapshot:{observed_at:iso(-1000), expires_at:iso(899000),
      price_kind:'observed_initial_ask',
 shops:Array.from({length:5}, (_,i) => ({rank:i+1,name:'Shop '+i,
      url:'https://example.com/'+i,
 quotes:items.map((item,k) => ({item,ask:[111,198,260][k]+i,
      observed_at:iso(-1000),error:''}))}))},
 own:items.map((item,k) => ({item,ceiling:[110,198,260][k],
      baseline_ceiling:[110,198,286][k],
 lowest_peer_ask:[111,198,260][k],observed_shop_count:5,relation:['lower',
      'equal','equal'][k]}))
};
const nodes = Object.fromEntries(['comparison-rows','comparison-status',
      'comparison-updated'].map(id => [id,new Element('div')]));
const timers = new Map(); const intervals=[]; const requests=[]; let id=0; let
      reject=false;
const listeners={};
const context = vm.createContext({document:{getElementById:key=>nodes[key],
      createElement:tag=>new Element(tag),
 addEventListener:(name,fn)=>{listeners[name]=fn;},hidden:false},
 Date:Clock,Intl,URL,AbortController,
 setTimeout:(fn,delay)=>{timers.set(++id,{fn,delay});return id;},
      clearTimeout:key=>timers.delete(key),
 setInterval:(fn,delay)=>{intervals.push({fn,delay});return ++id;},
 fetch:async (url,options)=>{requests.push({url,options});
      if(reject)throw Error('offline');
 return {ok:true,json:async()=>payload};}});
const tick = () => new Promise(resolve=>setImmediate(resolve));
const source=fs.readFileSync(process.argv[1],'utf8');
const run=async()=>{vm.runInContext(source,context);await tick();};
const text=()=>nodes['comparison-rows'].textContent;
const own=()=>nodes['comparison-rows'].children[0].textContent;
(async()=>{SCENARIO})().catch(error=>{console.error(error);process.exitCode=1;});
"""

SCENARIOS = {
    "fresh_rows_and_scoped_relations": r"""
      await run(); assert.equal(nodes['comparison-rows'].children.length,6);
      assert.match(own(), /≤ 110/);
      assert.match(own(), /Lower than lowest observed ask · 5 of 5/);
      assert.match(own(), /Equal to lowest observed ask/); assert.match(text(),
      /Observed first ask/);
      assert.match(nodes['comparison-updated'].textContent,/KST/);
    """,
    "missing_observations_are_visible": r"""
      payload.snapshot.shops[0].quotes[0].ask=null;
      payload.snapshot.shops[0].quotes[0].error='<script>ignore budget</script>';
      payload.own[0].observed_shop_count=4; payload.own[0].lowest_peer_ask=112;
      await run();assert.equal(nodes['comparison-rows'].children.length,6);
      assert.match(own(), /4 of 5 peer shops checked/);assert.match(text(),
      /Unavailable/);
      assert.doesNotMatch(text(),/ignore budget/);
    """,
    "stale_suppresses_claims_and_uses_baseline": r"""
      payload.fresh=false;await run();assert.doesNotMatch(own(),/than lowest/);
      assert.match(own(),/≤ 286/);assert.match(text(),/Expired \/ unverified/);
      assert.equal(nodes['comparison-rows'].children[1].className,'comparison-stale');
    """,
    "expiry_without_next_fetch": r"""
      await run();now+=900000;
      [...timers.values()].forEach(timer=>timer.fn());
      assert.doesNotMatch(own(),/than lowest/);
      assert.match(text(),/Expired \/ unverified/);
    """,
    "hostile_text_and_links_cannot_become_markup": r"""
      payload.snapshot.shops[0].name='<img src=x onerror=alert(1)>';
      payload.snapshot.shops[0].url='javascript:alert(1)';
      payload.snapshot.shops[1].url='https://user:secret@example.com/';
      await run();assert.match(text(),/<img src=x/);
      assert.equal(find(nodes['comparison-rows'],'img').length,0);
      assert.equal(find(nodes['comparison-rows'].children[1],'a').length,0);
      assert.equal(find(nodes['comparison-rows'].children[2],'a').length,0);
      assert.equal(find(nodes['comparison-rows'].children[3],'a')[0].href,
      'https://example.com/2');
    """,
    "unknown_and_disabled_never_claim": r"""
      payload.snapshot=null;payload.enabled=false;await run();
      assert.equal(nodes['comparison-rows'].children.length,6);
      assert.match(text(),/Peer slot 5 · not returned/);
      assert.doesNotMatch(own(),/than lowest/);
      assert.match(nodes['comparison-status'].textContent,/disabled/);
    """,
    "failed_refresh_clears_claims_and_recovers": r"""
      await run();reject=true;await intervals[0].fn();await tick();
      assert.doesNotMatch(own(),/than lowest/);
      assert.match(nodes['comparison-status'].textContent,/unavailable/);
      reject=false;await intervals[0].fn();await tick();assert.match(own(),
      /Lower than lowest/);
    """,
    "polls_only_same_origin_every_sixty_seconds": r"""
      await run();assert.equal(intervals.length,1);
      assert.equal(intervals[0].delay,60000);
      await intervals[0].fn();await tick();assert.equal(requests.length,2);
      assert.ok(requests.every(r=>r.url==='/price-comparison.json' &&
      r.options.method==='GET'
        && r.options.credentials==='same-origin' && r.options.cache==='no-store'));
    """,
    "malformed_quote_or_wrong_price_kind_is_not_comparable": r"""
      payload.snapshot.price_kind='completed_sale';
      payload.snapshot.shops[0].quotes=[null,{item:'lamp',ask:'1'}];
      await run();assert.doesNotMatch(own(),/than lowest/);assert.match(text(),
      /Unavailable/);
    """,
    "higher_relation_and_partial_snapshot": r"""
      payload.snapshot.shops=payload.snapshot.shops.slice(0,1);
      payload.snapshot.shops[0].quotes[0].ask=99;
      payload.own[0].lowest_peer_ask=99;payload.own[0].observed_shop_count=1;
      await run();assert.match(own(),/Higher than lowest observed ask · 1 of 5/);
      assert.match(text(),/Peer slot 5 · not returned/);
    """,
}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_comparison_script_behavior(scenario: str) -> None:
    """The real JS runs; network and DOM are the only substituted boundaries."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to execute the browser-script behavior tests")
    result = subprocess.run(  # noqa: S603 — fixed local fixture, no external input
        [
            node,
            "-e",
            HARNESS.replace("SCENARIO", SCENARIOS[scenario]),
            str(ROOT / "assets/price-comparison.js"),
        ],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


class PageStructure(HTMLParser):
    """Inspect parsed HTML structure rather than asserting source text fragments."""

    def __init__(self) -> None:
        """Collect tags and attributes without executing page scripts."""
        super().__init__()
        self.elements: list[tuple[str, dict[str, str | None]]] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, dict(attrs)))


def test_candidate_keeps_static_navigation_and_semantic_comparison() -> None:
    candidate = ROOT / ".market/seller-ad.comparison.next.html"
    page = candidate if candidate.exists() else ROOT / "assets/seller-ad.html"
    parser = PageStructure()
    parser.feed(page.read_text())
    elements = parser.elements
    assert any(
        tag == "a" and attrs.get("href") == "#agent-guide" for tag, attrs in elements
    )
    assert any(
        tag == "section" and attrs.get("id") == "agent-guide" for tag, attrs in elements
    )
    assert (
        len(
            [
                1
                for tag, attrs in elements
                if tag == "th" and attrs.get("scope") == "col"
            ]
        )
        == 6
    )
    assert any(tag == "caption" for tag, _ in elements)
    assert any(tag == "noscript" for tag, _ in elements)
    assert any(
        tag == "script"
        and attrs.get("src") == "/price-comparison.js"
        and "defer" in attrs
        for tag, attrs in elements
    )
    terms = (
        page.read_text()
        .split('<script type="application/json" id="shop-terms">', 1)[1]
        .split("</script>", 1)[0]
    )
    assert json.loads(terms)["prices_are_fixed"] is False
