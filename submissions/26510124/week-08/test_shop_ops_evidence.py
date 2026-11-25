"""Synthetic evidence tests; never call network, models or mutate source data."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shop_ops_evidence import collect_snapshot, digest, evaluate_windows, evaluation_tail, quote_timing_by_source
from shop_policy import policy_at


class EvidenceTests(unittest.TestCase):
    NOW=1794700000
    SKU="G12345678"

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.md=self.root/".market"
        self.logs=self.root/"logs"
        self.md.mkdir();self.logs.mkdir()
        self.rules={"version":"3.6","round":3,"verified_at":self.NOW,
                    "ends_at":"2026-11-17T23:59:00+09:00"}
        self.clock={"now":self.NOW-780,"status":"synced","offset_seconds":-780,"uncertainty_seconds":1}
        self.policy=policy_at()
        self.write(self.root/"shop-policy.json",self.policy)
        product={"sku":self.SKU,"kind":"goods","grade":"A","category":"bedding",
                 "cost":50,"list_price":100,"supplied_at":self.NOW-90000,"expires_at":None}
        self.inv={"round":3,"balance":5000000,"start":5000000,"injected":0,
            "units":[{**product,"unit":"u-stock1","cert":{"private":"DO_NOT_EMIT"}},
                     {**product,"unit":"u-stock2"}],
            "restock":{"open":False,"opens_at":self.NOW-780+200,"closes_at":self.NOW-780+380}}
        self.write(self.md/"inventory.json",self.inv)
        self.write(self.md/"receipt-reservations.json",{})
        self.write(self.md/"catalog.json",{self.SKU:{"kind":"goods","category":"bedding","list_price":100}})
        self.events=[];self.sales=[]
        for i in range(13):
            event={"time":self.NOW-15000+i,"round":3,"task":f"t-{i}","sku":self.SKU,
                "unit":f"u-{i}","grade":"A","warranty_hours":6,"category":"bedding",
                "ops_policy_version":"v1" if i<3 else None,"outcome":"opened"}
            self.events.append(event)
            if i<3:
                receipt={**event,"time":event["time"]+1,"outcome":"receipt_issued","price":80}
                self.events.append(receipt)
                self.sales.append({**product,"unit":f"u-{i}","ledger":i+1,"price":80,
                    "warranty_hours":6,"receipt_time":receipt["time"],"observed_at":receipt["time"]+1,
                    "market_settled_at":receipt["time"]-780,"refunds":[]})
            else:
                self.events.append({**event,"time":event["time"]+1,"outcome":"rejected",
                                    "reason_code":"price_rejected" if i<6 else "buyer_cancelled"})
        # Make three sales mature with six-hour warranty while recent quotes
        # still provide sufficient current demand. Use 12h evidence window.
        for e in self.events:
            e["time"]-=12000
        for s in self.sales:
            for k in ("receipt_time","observed_at","market_settled_at"):s[k]-=12000
        self.persist()

    def write(self,path,value):
        path.write_text(json.dumps(value))
        os.utime(path,(self.NOW,self.NOW))

    def persist(self):
        self.write(self.md/"sales-performance.json",{"schema":1,"round":3,"updated_at":self.NOW,"sales":self.sales})
        path=self.logs/"inventory-seller-events.jsonl"
        path.write_text("".join(json.dumps(e)+"\n" for e in self.events));os.utime(path,(self.NOW,self.NOW))

    def collect(self,hours=12,**kwargs):
        with patch("shop_ops_evidence.schedule_now",return_value=self.clock),patch("shop_diagnostics.schedule_now",return_value=self.clock):
            return collect_snapshot(self.root,now=self.NOW,hours=hours,rules=self.rules,**kwargs)

    def test_read_only_safe_snapshot_candidates_and_version_cohorts(self):
        before={str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        result=self.collect()
        after={str(p):(p.read_bytes(),p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        self.assertTrue(result["application_ready"],result["blockers"])
        candidate=result["candidates"][0]
        self.assertTrue(candidate["eligible"],candidate)
        self.assertEqual(candidate["metrics"]["price_rejected"],3)
        self.assertEqual(candidate["metrics"]["confirmed_sales"],3)
        self.assertEqual(result["policy_cohorts"]["v1"]["observed_tasks"],3)
        self.assertNotIn("DO_NOT_EMIT",json.dumps(result))
        self.assertEqual(result["snapshot_id"],digest({k:v for k,v in result.items() if k!="snapshot_id"}))

    def test_cancellation_and_unconfirmed_receipt_never_become_price_or_failure(self):
        for e in self.events:
            if e.get("reason_code")=="price_rejected":e["reason_code"]="buyer_cancelled"
        self.sales=[];self.persist()
        result=self.collect();candidate=result["candidates"][0]
        self.assertFalse(candidate["eligible"])
        self.assertEqual(candidate["metrics"]["price_rejected"],0)
        self.assertEqual(candidate["metrics"]["unconfirmed_receipts"],3)
        self.assertEqual(candidate["metrics"]["settlement_refused"],0)

    def test_wrong_unit_checkout_and_duplicate_sales_do_not_prove_additional_sale(self):
        self.sales.append(dict(self.sales[0]));self.persist()
        path=self.md/"checkout-events.jsonl"
        path.write_text(json.dumps({"task":"t-7","unit":"wrong","time":self.NOW-50,
                                   "stage":"settlement_refused"})+"\n")
        result=self.collect()
        self.assertEqual(result["candidates"][0]["metrics"]["confirmed_sales"],3)
        self.assertEqual(result["candidates"][0]["metrics"]["settlement_refused"],0)

    def test_stale_clock_rules_and_inventory_block_application(self):
        self.clock={**self.clock,"status":"stale"}
        self.rules["verified_at"]-=86401
        os.utime(self.md/"inventory.json",(self.NOW-100,self.NOW-100))
        result=self.collect()
        self.assertFalse(result["application_ready"])
        self.assertIn("inventory_stale",result["blockers"])
        self.assertIn("market_clock_unavailable",result["blockers"])
        self.assertIn("rules_unverified",result["blockers"])

    def test_known_receipt_hold_and_grade_mismatch_block_stock_experiment(self):
        self.write(self.md/"receipt-reservations.json",{"u-stock1":{}})
        result=self.collect()
        self.assertEqual(result["candidates"][0]["available_units"],1)
        self.assertFalse(result["candidates"][0]["eligible"])

    def test_explicit_settlement_refusal_blocks_experiment_not_missing_checkout(self):
        path=self.md/"checkout-events.jsonl"
        path.write_text(json.dumps({"task":"t-0","unit":"u-0","time":self.NOW-20000,
                                   "stage":"settlement_refused"})+"\n")
        result=self.collect()
        self.assertIn("technical_or_settlement_error",result["candidates"][0]["reasons"])

    def test_stock_targets_keep_caps_and_unknown_stockouts_out_of_forecast(self):
        result=self.collect()
        row=result["inventory_analysis"]["targets"][0]
        self.assertEqual(row["cover_seconds"],1400)
        self.assertLessEqual(row["target_units"],row["existing_cap"])
        self.assertEqual(result["inventory_analysis"]["max_stock_units"],60)

    def test_stockout_without_unit_grade_is_retained_without_inventing_conditions(self):
        self.events.extend([{"time":self.NOW-30,"round":3,"task":"miss","sku":"G87654321",
                             "outcome":"stock_unavailable","reason_code":"stock_unavailable"}]*2)
        self.persist()
        result=self.collect()
        row=next(r for r in result["inventory_analysis"]["targets"] if r["sku"]=="G87654321")
        self.assertEqual(row["stockout_requests_observed"],1)
        self.assertEqual(row["target_units"],0)
        self.assertIn("buyer conditions unknown",row["stockout_qualification"])
        cohort=result["policy_cohorts"]["v1"]
        self.assertEqual(cohort["observed_span_seconds"],3)

    def test_six_hour_candidate_uses_separate_historical_maturity_not_false_current_profit(self):
        for e in self.events:
            if int(e["task"].split('-')[1])>=3:e["time"]+=12000
        self.persist()
        result=self.collect(hours=6)
        candidate=result["candidates"][0]
        self.assertTrue(candidate["eligible"],candidate)
        self.assertEqual(candidate["metrics"]["mature_sales"],0)
        self.assertFalse(candidate["metrics"]["sufficient_maturity"])
        self.assertEqual(candidate["historical_economics"]["mature_sales"],3)
        self.assertEqual(candidate["historical_economics"]["window_seconds"],86400)
        self.assertEqual(candidate["metrics"]["tasks"],10)

    def fixed_fixture(self):
        self.start,self.end=self.NOW-43200,self.NOW-21600
        for i in range(3):
            old=dict(self.sales[i],unit=f"baseline-{i}",ledger=100+i,
                receipt_time=self.start-20000+i,observed_at=self.start-20000+i,
                market_settled_at=self.start-20780+i)
            self.sales.append(old)
            self.events.extend([{"task":f"baseline-{i}","round":3,"time":old["receipt_time"]-1,
                "outcome":"opened","sku":self.SKU,"unit":old["unit"],"grade":"A","warranty_hours":6},
                {"task":f"baseline-{i}","round":3,"time":old["receipt_time"],"outcome":"receipt_issued",
                 "sku":self.SKU,"unit":old["unit"],"grade":"A","warranty_hours":6,"price":80}])
        # Retained events bracket both boundaries; unrelated experiment traffic
        # must not be assigned to the version-tagged intervention cohort.
        for task,when in (("boundary-before",self.start-21600),("boundary-after",self.end)):
            self.events.append({"task":task,"round":3,"time":when,"outcome":"opened",
                "sku":self.SKU,"unit":task,"grade":"A","warranty_hours":6})
        self.persist()
        (self.md/"checkout-events.jsonl").write_text("")

    def fixed(self):
        with patch("shop_ops_evidence.schedule_now",return_value=self.clock):
            return evaluate_windows(self.root,version="v1",start=self.start,end=self.end,
                                    as_of=self.NOW,rules=self.rules)

    def test_fixed_cohorts_preserve_mature_sales_after_rolling_window_and_share_refund_cutoff(self):
        self.fixed_fixture()
        self.sales[0]["refunds"]=[{"amount":5}]
        self.sales[3]["refunds"]=[{"amount":7}]
        self.persist()
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.fixed()
        self.assertTrue(result["comparable"],result["coverage"])
        self.assertEqual(result["experiment"]["mature_sales"],3)
        self.assertEqual(result["baseline"]["mature_sales"],3)
        self.assertEqual(result["experiment"]["observed_refunds"],5)
        self.assertEqual(result["baseline"]["observed_refunds"],7)
        self.assertEqual(result["experiment"]["observed_tasks"],3)
        self.assertEqual(result["experiment"]["settled_revenue_per_hour"],40)
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_fixed_cohort_joins_late_receipt_without_extending_exposure(self):
        self.fixed_fixture()
        late=dict(self.sales[0],unit="late-unit",ledger=200,receipt_time=self.end+5,
                  observed_at=self.end+5,market_settled_at=self.end-775,warranty_hours=0)
        self.sales.append(late)
        self.events.extend([{"task":"late","round":3,"time":self.end-5,"outcome":"opened",
            "sku":self.SKU,"unit":"late-unit","grade":"A","warranty_hours":0,"ops_policy_version":"v1"},
            {"task":"late","round":3,"time":self.end+5,"outcome":"receipt_issued","price":80,
             "sku":self.SKU,"unit":"late-unit","grade":"A","warranty_hours":0}])
        self.persist()
        result=self.fixed()
        self.assertTrue(result["comparable"],result["coverage"])
        self.assertEqual(result["experiment"]["confirmed_sales"],4)
        self.assertEqual(result["experiment"]["exposure_seconds"],21600)
        self.assertEqual(result["experiment"]["settled_revenue_per_hour"],320/6)

    def test_fixed_missing_settlement_and_source_coverage_are_unknown_not_failure(self):
        self.fixed_fixture()
        self.sales=[];self.persist()
        original=evaluation_tail
        def truncated(path,now):
            rows,meta=original(path,now)
            return rows,{**meta,"truncated":True}
        with patch("shop_ops_evidence.evaluation_tail",side_effect=truncated):result=self.fixed()
        self.assertFalse(result["comparable"])
        self.assertEqual(result["experiment"]["unconfirmed_receipts"],3)
        self.assertEqual(result["experiment"]["technical_errors"],0)
        self.assertIn("seller_events_truncated",result["coverage"]["reasons"])
        self.assertIn("experiment_unconfirmed_settlement_coverage",result["coverage"]["reasons"])

    def test_fixed_windows_reject_unbounded_and_future_cutoffs(self):
        self.fixed_fixture()
        with self.assertRaises(ValueError):
            evaluate_windows(self.root,version="v1",start=self.start,end=self.end,
                             as_of=self.end-1,rules=self.rules)
        with self.assertRaises(ValueError):
            evaluate_windows(self.root,version="v1",start=self.start,end=self.end,
                             as_of=self.end+43201,rules=self.rules)

    def test_first_quote_source_timing_never_borrows_later_row_latency(self):
        def event(task,offset,source,latency=None,**extra):
            return {"task":task,"time":self.NOW+offset,"round":3,"outcome":"quote","stage":"opening",
                    "decision_source":source,"first_response_seconds":latency,**extra}
        rows=[event("model",-20,"model",5.6),event("model",-10,"recent_unit_quote",.01),
              event("model",-5,"capacity_fallback",200,reason_code="buyer_cancelled",outcome="cancelled"),
              event("fresh",-20,"fresh_evidence_opening",.2),event("fresh",-20,"fresh_evidence_opening",.2),
              event("reuse",-20,"recent_unit_quote"),event("reuse",-5,"model",.1,outcome="cancelled",reason_code="buyer_cancelled"),
              event("fallback",-20,"capacity_fallback",.4),
              event("noquote",-5,"model",9,outcome="cancelled",reason_code="buyer_cancelled")]
        result=quote_timing_by_source(list(reversed(rows)),3,self.NOW-60,self.NOW)
        self.assertEqual(result["first_quote_tasks"],4)
        self.assertEqual(result["paths"]["model"]["p50_seconds"],5.6)
        self.assertEqual(result["paths"]["model"]["later_cancelled_tasks"],1)
        self.assertEqual(result["paths"]["fresh_evidence_opening"]["latency_samples"],1)
        self.assertEqual(result["paths"]["recent_unit_quote"]["latency_samples"],0)
        self.assertIsNone(result["paths"]["recent_unit_quote"]["p95_seconds"])
        self.assertEqual(result["paths"]["recent_unit_quote"]["later_cancelled_tasks"],1)
        self.assertEqual(result["paths"]["capacity_fallback"]["p50_seconds"],.4)


if __name__=="__main__":unittest.main()
