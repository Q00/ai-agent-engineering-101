import time
from r3.score_campaign import saved_goals, quality_cells,safe_service,public_demand_categories
from r3.market_memory import buyer_price_actions

def board():
 rows=[{'name':'seochanit','saved':.757,'deals':23348,'posts':100,'forum':.9}]
 rows += [{'name':'p'+str(i),'saved':v,'deals':100,'posts':100,'forum':.9} for i,v in enumerate([.7576,.7600,.7604]+[.5]*64)]
 return {'round':3,'buyer_forum_metric':'raw','buyers':rows}

def test_goals_cover_two_point_steps_without_a_purchase_cap():
 b=board();goals,bids=saved_goals(b,b['buyers'][0],140,24)
 assert len(goals)==3 and goals[-1]['all_gain_scenario']>=.02
 assert any(.85<v<.95 for v in bids)
 assert saved_goals(b,b['buyers'][0],0,24)==([],[])

def test_upward_peer_motion_requires_stronger_conditions():
 b=board();_,a=saved_goals(b,b['buyers'][0],140,24)
 _,c=saved_goals(b,b['buyers'][0],140,24,{'p2':.0002})
 assert max(c)>max(a)

def test_saturation_accepts_without_more_haggling():
 state={'metric':'saved','saved':.75,'deals':100,'score_formula':'margin_share_v36','unit_cost':55,'cost_samples':[55,70],'score_campaign_enabled':True}
 rows=buyer_price_actions(50,100,{'list_price':100},90,.92,0,4,saved_state=state,failure_stress=.1,covered_failure=.1)
 assert len(rows)==1 and rows[0]['id']=='accept' and rows[0]['saturation_accept']
 rows=buyer_price_actions(60,100,{'list_price':100},90,.92,0,4,saved_state=state)
 assert len(rows)>1

def test_repeated_bad_ratings_from_one_account_do_not_veto_all_customers():
 sales={str(i):{'sku':'s','grade':'C'} for i in range(30)}
 reviews=[{'ledger':i,'author':'a','identity_known':True,'stars':1,'source':'verified_market_result_and_accepted_own_task'} for i in range(30)]
 assert not quality_cells(sales,reviews,{'s':{'kind':'food'}},4.3)['food:C']['supported_downside']
 for i,r in enumerate(reviews):r['author']='a'+str(i%6)
 cell=quality_cells(sales,reviews,{'s':{'kind':'food'}},4.3)['food:C']
 assert cell['supported_downside']
 p={'quality_cells':{'food:C':cell}};u={'kind':'food','sku':'s','expires_at':1000+20*3600}
 cat={'s':{'shelf_hours':100}}
 assert not safe_service(u,cat,{},p,1000)
 assert safe_service(u,cat,{'explicit_use':True,'explicit_grade':True,'grades':['C']},p,1000)
 assert safe_service(u,cat,{}, {},1000)

def test_public_demand_ignores_old_self_and_non_buyer_posts():
 class J:
  def events(self,*a):return [{'author':'peer','role':'buyer','wanted':True,'categories':['rice'],'_ts':999},
   {'author':'seochanit','role':'buyer','wanted':True,'categories':['self'],'_ts':999},
   {'author':'peer','role':'buyer','wanted':True,'categories':['old'],'_ts':0}]
 assert public_demand_categories(J(),1000)=={'rice'}

def test_sales_guard_is_wired_to_real_seller_reservation(tmp_path):
 from r3.state import Journal,atomic_json,identity
 from r3.score_campaign import KEY
 from seller_r3 import Seller
 j=Journal(tmp_path);now=time.time();j.put('market_rules',{'sha256':'rules'});j.put('board_checked_at',now)
 p={'at':now,'expires_at':now+180,'rules_hash':'rules','quality_cells':{'food:C':{'supported_downside':True}}};p['id']=identity(p);j.put(KEY,p)
 atomic_json(tmp_path/'catalog.json',{'G111':{'name':'food','kind':'food','shelf_hours':100,'list_price':1000,'category':'food'}})
 atomic_json(tmp_path/'inventory.json',{'round':3,'balance':100000,'units':[{'unit':'u','sku':'G111','name':'food','category':'food','kind':'food','cost':100,'list_price':1000,'grade':'C','supplied_at':now-1,'expires_at':now+20*3600}]})
 s=Seller(j);s.cfg=dict(s.cfg,score_campaign_enabled=True,finalday_departments_enabled=True,service_contract_v26_enabled=True)
 assert s.select_unit('G111','anonymous') is None
 assert j.events('stockout')[0]['reason']=='quality_contract'
 assert s.select_unit('G111 require grade C; use within 1 hour','explicit')['unit']=='u'

def test_quality_priority_prefers_fresher_unknown_use_within_allowed_grade():
 from r3.service_contracts import priority
 now=1000;cat={'s':{'shelf_hours':100}}
 older={'unit':'o','sku':'s','kind':'food','expires_at':now+85*3600}
 fresh={'unit':'f','sku':'s','kind':'food','expires_at':now+95*3600}
 spec={'quality_preferred':True,'explicit_use':False}
 assert priority(fresh,[older,fresh],cat,spec,[],now)<priority(older,[older,fresh],cat,spec,[],now)
