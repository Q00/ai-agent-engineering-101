"""Local v4 integration. Shared course scripts are imported read-only."""
import asyncio
import base64
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import time
from starlette.responses import JSONResponse
from .state import ROOT, Journal, atomic_json, policy, warranty_hours
from . import customer_memory


def load_kit():
    source = next((parent / 'scripts' / 'market_kit.py' for parent in ROOT.parents
                   if (parent / 'scripts' / 'market_kit.py').is_file()), ROOT.parents[2] / 'scripts' / 'market_kit.py')
    spec = importlib.util.spec_from_file_location("r3_course_kit", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.KIT_VERSION != "4":
        raise RuntimeError("This adapter is verified for course kit v4 only")
    return module


def install(kit, journal=None, config=None):
    j = journal or Journal()
    cfg = config or policy()
    if cfg.get('discovery_priority_enabled') and hasattr(kit,'Proxy'):
        from .discovery import install as install_discovery
        install_discovery(kit)
    from .autonomy import BackgroundLane
    analytics_lane = BackgroundLane('market-analysis-v14')
    publication_lane = BackgroundLane('market-publication-v14')
    original_accept = kit.check_accept
    original_call = kit.Market.call
    original_order = kit.handle_order
    original_news = kit.broken_news
    original_outbox = kit.outbox
    original_run_buyer = kit.run_buyer
    original_restock = kit.restock
    cycle = None
    buyer_bridge = None
    if cfg.get('buyer_worker_enabled'):
        from .worker_bridge import BuyerBridge
        buyer_bridge = BuyerBridge(ROOT, kit.stop, j)

    def run_buyer(command, directory):
        if buyer_bridge is None or 'market_buyer.py' not in command:
            return original_run_buyer(command, directory)
        order = j.snapshot('order.json', {})
        shops = j.snapshot('shops.json', [])
        if not order.get('order') or not order.get('acceptable'):
            return original_run_buyer(command, directory)
        timeout = min(kit.BUYER_TIMEOUT, max(.1, order.get('expires', time.time()) - time.time() - 5))
        lines = buyer_bridge.run(order, shops, timeout)
        for line in lines:
            kit.log('[buyer] ' + line)
        return lines
    BaseShop = kit.AcpShop

    def strict_accept(order, co, accept):
        if order.get("acceptable"):
            if not isinstance(accept, dict) or not accept.get("expected_sku"):
                return "exact SKU expectation is required by the R3 adapter"
            if co.get("item") != accept["expected_sku"]:
                return "checkout SKU differs from the requested SKU"
            if order.get("kind") == "food":
                required = order.get("use_within_hours", 0)
                if accept.get("min_hours_left", -1) < required:
                    return "food acceptance is weaker than the order use horizon"
        return original_accept(order, co, accept)

    class StrictShop(BaseShop):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.create_lock = asyncio.Lock()

        def expire_sessions(self):
            now = time.time()
            for session in self.sessions.values():
                if session["status"] == "ready_for_payment" and now - session.get("r3_created", now) > cfg["session_ttl_seconds"]:
                    session["status"] = "canceled"

        def pick_unit(self, rec, acceptable):
            self.expire_sessions()
            catalog = j.snapshot("catalog.json", {})
            if rec.get("item") in catalog:
                acceptable = [rec["item"]] if not acceptable or rec["item"] in acceptable else []
                if not acceptable:
                    return None, "requested SKU is outside the order"
            return super().pick_unit(rec, acceptable)

        async def create(self, body, public):
            async with self.create_lock:
                response = await super().create(body, public)
                for session in self.sessions.values():
                    session.setdefault("r3_created", time.time())
                return response

        async def handle(self, req, path, body, public):
            self.expire_sessions()
            response = await super().handle(req, path, body, public)
            if req.method == 'POST' and path.endswith('/complete') and response.status_code == 200:
                # Use the shop's verified checkout and the accepted market receipt,
                # never a buyer name or receipt supplied by the incoming request.
                try:
                    result = json.loads(response.body)
                    session = self.sessions[result['id']]
                    checkout = kit.jwt_payload(session['checkout_jwt'], kit.pub_b64(self.key))
                    receipt = result['order']['mandate_receipt']
                    if checkout and session['status'] == 'completed':
                        customer_memory.paid(j, checkout['task'], checkout['price'],
                                             receipt.get('sku', checkout['item']), receipt['ledger'], receipt)
                except Exception as exc:
                    # A nonessential memory error must not hide a successful payment.
                    kit.log('[memory] customer attribution unavailable: ' + type(exc).__name__)
            return response

    def market_call(self, method, path, role="buyer", **kwargs):
        if cfg.get('market_control_enabled') and method=='POST' and path=='/api/supply':
            # Recheck each actual lot, not just the plan made up to minutes ago.
            import httpx
            from .economy import cash_capacity,supply_open
            from .finance import supply_bound
            fresh = original_call(self, 'GET', '/api/inventory', role='shop')
            if fresh.status_code != 200:
                return fresh
            current = fresh.json()
            atomic_json(j.md/'inventory.json', current)
            if not supply_open(current):
                # A stale plan must return promptly for replanning, rather
                # than occupying the kit for 90 seconds per rejected line.
                return httpx.Response(409,json={'error':'supply window closed; replan'})
            item=kwargs.get('json',{}); product=j.snapshot('catalog.json',{}).get(item.get('sku'))
            quantity=item.get('qty')
            diagnosis=j.get('market_control',{})
            cap=cash_capacity(current,j,cfg)
            required=(supply_bound(product,current,item['sku'])*quantity*(1+.01*cap['remaining_days'])
                      if product and type(quantity) is int and 1<=quantity<=20 else float('inf'))
            if diagnosis.get('pause_restock',True) or time.time()-diagnosis.get('at',0)>900 or required>cap['spendable']:
                j.add('supply_blocked', {'sku':item.get('sku'),'qty':quantity,'reason':'fresh_cash_or_control_guard'})
                return httpx.Response(429,json={'error':'local capital guard','wait':30})
        supply_claim = None
        if (cfg.get('market_cycle_enabled') and cfg.get('market_control_enabled') and
                method == 'POST' and path == '/api/supply' and isinstance(current.get('restock'),dict)):
            from .cycle import claim_supply
            # The plan is a numeric preview, never a retry token. A pending POST
            # blocks new side effects until its outcome is known.
            plan = j.snapshot('shop-plan-v15.json', {})
            execution = str(plan.get('at')) if plan.get('window') == [current['restock']['opens_at'],current['restock']['closes_at']] else j.get('supply-current-execution','external')
            supply_claim = claim_supply(j,current,kwargs.get('json',{}),execution)
            if supply_claim is None:
                return httpx.Response(409,json={'error':'supply already completed or outcome uncertain; no blind retry'})
        try:
            response = original_call(self, method, path, role=role, **kwargs)
        except Exception:
            if supply_claim:
                from .cycle import finish_supply
                finish_supply(j,supply_claim,'uncertain')
            raise
        if supply_claim:
            from .cycle import finish_supply
            finish_supply(j,supply_claim,'complete' if response.status_code==200 else
                          'retryable' if response.status_code in (400,402,409,429) else 'uncertain')
        if response.status_code != 200:
            return response
        data = response.json()
        if path == "/api/shops" and method == "GET":
            if cfg.get('system_framework_enabled'):
                from .long_memory import observe_reviews
                observe_reviews(j,data.get('shops',[]))
            registry = {s["url"].rstrip("/"): {k: s.get(k) for k in ("name", "pubkey", "owner")} for s in data["shops"]}
            atomic_json(j.md / "registry.json", registry)
            if cfg.get('system_framework_enabled'):
                aliases=j.get('market-shop-identities',{})
                aliases.update(registry)
                j.put('market-shop-identities',dict(list(aliases.items())[-5000:]))
            if cfg.get('shop_growth_v5'):
                from collections import Counter
                counts = Counter(sku for s in data['shops'] if s.get('name') != 'seochanit'
                                 for sku, qty in (s.get('stock') or {}).items() if qty > 0)
                atomic_json(j.md / 'market_stock_counts.json', {'at': time.time(), 'counts': dict(counts)})
        elif path == "/api/inventory" and method == "GET":
            if cfg.get('shop_growth_v5'):
                from .merchant import observe_inventory
                observe_inventory(j, data)
            accounting = {k: data.get(k) for k in ("balance", "start", "injected", "bankruptcies_left")}
            if accounting != j.get("accounting_snapshot"):
                j.add("accounting_snapshot", accounting, role="shop")
                j.put("accounting_snapshot", accounting)
        elif path == "/api/settle" and method == "POST" and role == "shop":
            mr = data.get("mandate_receipt")
            if mr:
                token = kwargs.get("json", {}).get("checkout_jwt", "")
                try:
                    key = kit.load_key(ROOT / "shop_key.pem")
                    co = kit.jwt_payload(token, kit.pub_b64(key)) or {}
                    attribution = j.get('shop_strategy:' + str(co.get('task')), {})
                    record = {"task": co.get("task"), "unit": mr.get("unit"), "sku": mr.get("sku", co.get("item")),
                              "price": co.get("price", 0), "warranty_hours": warranty_hours(co.get('warranty_hours')), "paid_at": time.time(),
                              "strategy_version": attribution.get('strategy', {}).get('version', 0),
                              "category": attribution.get('category'), "cost": attribution.get('cost')}
                    record['grade'] = mr.get('grade')
                    j.add("shop_settled", record, eid="settlement:" + str(data.get("ledger")), role="shop")
                    j.release(co.get("task"))
                except (ValueError, OSError, KeyError):
                    j.add("attribution_error", {"ledger": data.get("ledger")}, role="shop")
        elif path == "/api/results" and method == "POST":
            submitted = kwargs.get("json", {})
            j.add("market_result", {"order": submitted.get("order"), "verified": data.get("verified"),
                                    "checks": data.get("checks"), "ledger": data.get("ledger")},
                  eid="result:" + str(submitted.get("order")), role="buyer")
        return response

    def order_handler(order, *args, **kwargs):
        os.environ["R3_STRICT_ADAPTER"] = "1"
        if order.get("round", 0) < 3:
            kit.log("[r3] Waiting for Round 3; no legacy order executed")
            time.sleep(10)
            return None
        if cfg.get('checkout_recovery_enabled') and order.get('mandates') and order.get('acceptable'):
            from .checkout import handle_order
            return handle_order(kit,j,cfg,order,*args,**kwargs)
        return original_order(order, *args, **kwargs)

    def personal_news(market, md, state):
        # A bounded slice, with enough per-request tolerance for the real server.
        me = market.joined.get('buyer', (None,))[0]
        if 'ledger_seq' not in state:
            response = market.c.get(market.base + '/api/ledger', params={'after': -1}, timeout=8)
            if response.status_code != 200:
                return
            rows = response.json()
            state['ledger_seq'] = rows[-1]['seq'] if rows else 0
        until = time.monotonic() + 15
        for _ in range(4):
            if time.monotonic() >= until:
                break
            response = market.c.get(market.base + '/api/ledger', params={'after': state['ledger_seq']}, timeout=8)
            rows = response.json() if response.status_code == 200 else []
            if not rows:
                break
            for event in rows:
                data = event.get('data', {})
                if event.get('kind') == 'broken' and data.get('buyer') == me:
                    note = {'name': kit.CATALOG.get(data.get('item'), {}).get('name') or data.get('item'),
                            'broke_at': int(event['ts'])}
                    with (md / 'broken.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(note, ensure_ascii=False) + '\n')
            state['ledger_seq'] = rows[-1]['seq']
            if len(rows) < 500:
                break

    def news(market, md, state):
        if not cfg.get('autonomy_v14_enabled'):
            original_news(market, md, state)
        else:
            try:
                personal_news(market, md, state)
            except Exception as exc:
                j.add('feed_probe_error', {'stage': 'personal_news', 'error_type': type(exc).__name__})
        seq = j.get("public_ledger_seq", -500)
        feed_until = time.monotonic() + 15
        for _ in range(4):
            if cfg.get('autonomy_v14_enabled') and time.monotonic() >= feed_until:
                break
            try:
                r = market.c.get(f"{market.base}/api/ledger", params={"after": seq},
                                 **({'timeout': 8} if cfg.get('autonomy_v14_enabled') else {}))
            except Exception as exc:
                if not cfg.get('autonomy_v14_enabled'):
                    raise
                j.add('feed_probe_error', {'stage': 'public_ledger', 'error_type': type(exc).__name__})
                break
            if r.status_code != 200:
                break
            rows = r.json()
            if not rows:
                j.put('public_feed_caught_up_at', time.time())
                break
            for event in rows:
                if event.get("kind") == "settle":
                    j.add("public_settle", event["data"], eid="public:" + str(event["seq"]), ts=event.get("ts"))
                    customer_memory.bind_market_payer(j, event['seq'], event['data'])
                elif event.get("kind") in {"refund", "broken"}:
                    j.add("public_" + event["kind"], event["data"], eid="public:" + str(event["seq"]), ts=event.get("ts"))
                elif event.get('kind')=='result' and cfg.get('system_framework_enabled'):
                    from .long_memory import observe_market_result
                    observe_market_result(j,event)
            seq = rows[-1]["seq"]
            j.put("public_ledger_seq", seq)
            if len(rows) < 500:
                j.put('public_feed_caught_up_at', time.time())
                break
        if not cfg.get('autonomy_v14_enabled'):
            j.reconcile()

    def analytical_outbox(market, md):
        if not cfg.get('autonomy_v14_enabled'):
            original_outbox(market, md)
        else:
            j.reconcile()
        if time.time() - j.get("board_checked_at", 0) > 60:
            with contextlib.suppress(Exception):
                response = market.c.get(f"{market.base}/api/board", timeout=5)
                if response.status_code == 200:
                    board = response.json()
                    if board.get("round") == 3:
                        atomic_json(j.md / "board.json", board)
                    j.put("board_checked_at", time.time())
                    j.put('board_round', board.get('round'))
        if cfg.get('market_control_enabled'):
            try:
                from .control import audit_rules, update as control_update
                if time.time()-j.get('market_rules',{}).get('at',0)>300:
                    response=market.c.get(f'{market.base}/agent.md',timeout=5)
                    if response.status_code==200:
                        audit_rules(j,response.text)
                diagnosis=control_update(j,cfg)
                if diagnosis.get('at')!=j.get('last_control_reported_at'):
                    j.put('last_control_reported_at',diagnosis.get('at'))
                    kit.log('[control] '+json.dumps({k:diagnosis.get(k) for k in
                            ['status','book','leader_gap','reasons','suggestions']},ensure_ascii=False))
            except Exception as exc:
                j.add('market_control_error', {'error_type':type(exc).__name__})
        try:
            from .adaptive import update
            update(j)
        except Exception as exc:
            j.add('strategy_error', {'error_type': type(exc).__name__})
        if cfg.get('shop_growth_v5'):
            try:
                from .merchant import context
                context(j, j.snapshot('catalog.json', {}))
            except Exception as exc:
                j.add('merchant_context_error', {'error_type': type(exc).__name__})
        if cfg.get('autonomy_v14_enabled'):
            try:
                from .buyer_quality import cohorts
                from .risk import shop_refund_risk
                from .autonomy import update as autonomy_update
                cohorts(j)
                shop_refund_risk(j, 'A', 3, cfg)
                autonomy_update(j, cfg)
            except Exception as exc:
                j.add('autonomy_v14_error', {'error_type': type(exc).__name__})
            return
        publish_outbox(market, md, legacy=True)

    def publish_outbox(market, md, legacy=False):
        if not legacy:
            original_outbox(market, md)
        from .forum import schedule, deliver
        try:
            schedule(j, cfg)
            if j.snapshot('inventory.json', {}).get('round') == 3 or j.get('board_round') == 3:
                deliver(market, j, cfg)
        except Exception as exc:
            j.add('forum_error', {'error_type': type(exc).__name__})

    def outbox(market, md):
        nonlocal cycle
        if (cfg.get('market_cycle_enabled') and getattr(market,'base',None)=='https://market.ourolabs.ai'
                and getattr(market,'joined',{}).get('shop') and cycle is None):
            from .cycle import MarketCycle
            cycle=MarketCycle(j,cfg,market,original_restock)
            # The kit passes agent/directory to its restock callback. Capture
            # them there; observations and preplans start immediately.
            cycle.start(None)
        if not cfg.get('autonomy_v14_enabled'):
            return analytical_outbox(market, md)
        analytics_lane.submit(lambda: analytical_outbox(market, md))
        publication_lane.submit(lambda: publish_outbox(market, md))

    def restock_callback(agent, directory, market, md, inv, demand):
        nonlocal cycle
        if not cfg.get('market_cycle_enabled') or getattr(market,'base',None)!='https://market.ourolabs.ai':
            return original_restock(agent,directory,market,md,inv,demand)
        if cycle is None:
            from .cycle import MarketCycle
            cycle=MarketCycle(j,cfg,market,original_restock)
            cycle.start((agent,directory))
        else:
            cycle.args=(agent,directory)
        return cycle.restock(agent,directory,market,md,inv,demand)

    kit.check_accept = strict_accept
    kit.AcpShop = StrictShop
    kit.Market.call = market_call
    kit.handle_order = order_handler
    kit.broken_news = news
    kit.outbox = outbox
    kit.restock = restock_callback
    kit.run_buyer = run_buyer
    os.environ["R3_STRICT_ADAPTER"] = "1"
    return kit
