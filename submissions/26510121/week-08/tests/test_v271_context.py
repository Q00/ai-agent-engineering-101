"""Stock search must fit LLM context without changing economic decisions."""
import ast
import copy
import json
from pathlib import Path
from r3.inventory_portfolio import brief
from r3.objective_context import curate,FACT_BYTES


def packet():
    f={'book':100,'revenue':300,'stress_book':20,'rank_progress':.01,'units':40,'lines':10,'capital':5000}
    alternatives={'baseline':dict(f,book=50),'selected':f}
    alternatives.update({str(n):dict(f,unused_proof='numeric search evidence '*200) for n in range(100)})
    return {'id':'stock-evidence','at':1,'selection':'selected','dominance_guard_passed':True,
        'goods_units':10,'candidate_counts':{'all':1500},'alternatives':alternatives,'selected':[{'sku':'G1','qty':20}]*100}


def test_large_stock_search_has_a_small_exact_projection():
    p=packet();old=copy.deepcopy(p);result=brief(p)
    assert p==old and len(json.dumps(result))<1200
    assert result['baseline']['book']==50 and result['selected']['book']==100
    assert result['id']=='stock-evidence' and result['selection']=='selected' and result['forecast_not_realized']


def test_stock_context_is_compacted_before_the_budget_gate():
    p=packet();facts={'inventory_portfolio':p,'objective_decisions':{'shop':{'lot_portfolio_comparison':p}},
        'evidence_ids':['kv:inventory-portfolio:v27'],'current_operating_policy':{'cover_cycles':2}}
    value,metadata=curate(facts)
    assert metadata['source_bytes']>FACT_BYTES and metadata['fact_bytes']<FACT_BYTES
    assert value['inventory_portfolio']['selected']['units']==40
    assert value['objective_decisions']['shop']['lot_portfolio_comparison']['baseline']['book']==50
    assert value['evidence_ids']==facts['evidence_ids']


def test_service_bucket_examples_do_not_duplicate_long_memory_in_llm_context():
    needs={f'G{n:03d}':{'paid_sku_support':n+3,'distinct_minutes':5,'need_fraction_scenario':.5,
        'buckets':[{'minimum_hours':h,'grades':['A','B'],'weight':h+1,'paid':2,'warranty_hours':0} for h in range(100)]} for n in range(20)}
    facts={'service_contracts':{'generation':'exact-source','qualified_need_skus_total':25,'needs':needs}}
    old=copy.deepcopy(facts);value,metadata=curate(facts)
    assert facts==old and metadata['fact_bytes']<5000
    service=value['service_contracts'];assert len(service['needs'])==4 and service['provided_need_skus']==20
    assert service['qualified_need_skus_total']==25 and service['generation']=='exact-source'
    assert service['needs']['G019']['dominant_requirement']['minimum_hours']==99


def test_overlapping_capability_cohort_keeps_its_provenance_and_current_cohort():
    cohort={'cost_model':{'samples':[dict(value='x'*400) for _ in range(20)],
        'risk_rows':[dict(value='z'*400) for _ in range(20)]},'current_saved':.6}
    facts={'objective_decisions':{'buyer':cohort},'capability_evidence':{'id':'calculation-id','at':100,
        'calculations':{'buyer_frontier_audit':copy.deepcopy(cohort)}},'service_contracts':{'interpretation':'y'*8000}}
    value,metadata=curate(facts)
    audit=value['capability_evidence']['calculations']['buyer_frontier_audit']
    assert metadata['fact_bytes']<FACT_BYTES and value['objective_decisions']['buyer']['current_saved']==.6
    assert audit['brief_id']=='calculation-id' and audit['current_cohort_context']=='objective_decisions.buyer'


