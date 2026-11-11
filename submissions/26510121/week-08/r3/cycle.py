"""Nonblocking market-cycle supervision, preparation and supply serialization."""
import atexit
import threading
import time
import uuid
from .autonomy import BackgroundLane
from .state import identity
from .lifecycle import lease


def claim_supply(journal, inv, item, execution):
    window=(inv.get('restock') or {}).get('opens_at')
    sku=item.get('sku')
    key='supply-claim:'+identity([window,sku])
    with journal.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
        previous=__import__('json').loads(row[0]) if row else {}
        if previous.get('status') in ('started','uncertain') or (previous.get('status')=='complete' and previous.get('execution')==execution):
            return None
        value={'status':'started','execution':execution,'window':window,'sku':sku,'qty':item.get('qty'),'at':time.time()}
        db.execute('INSERT OR REPLACE INTO kv VALUES(?,?)',(key,__import__('json').dumps(value)))
    return key


def finish_supply(journal, key, status):
    row=journal.get(key,{})
    row.update(status=status,finished_at=time.time())
    journal.put(key,row)
    journal.add('supply_execution_status',row,role='shop')


class MarketCycle:
    def __init__(self, journal, config, market, original_restock):
        self.j,self.cfg,self.market,self.original_restock=journal,config,market,original_restock
        self.stop=threading.Event();self.thread=None;self.last_kick=None
        self.prepare_lane=BackgroundLane('cycle-preparation')
        self.research_lane=BackgroundLane('cycle-research')
        self.supply_lane=BackgroundLane('cycle-supply')
        self.last_analysis=0;self.args=None
        atexit.register(self.stop.set)

    def start(self, args):
        self.args=args
        if self.thread is None:
            self.thread=threading.Thread(target=self.run,name='market-cycle-v15',daemon=True)
            self.thread.start()

    def restock(self,*args):
        try:
            with lease(self.j.md/'supply-executor.lock'):
                execution=uuid.uuid4().hex
                self.j.put('supply-current-execution',execution)
                self.original_restock(*args)
        except RuntimeError as exc:
            if 'already holds' not in str(exc):
                raise

    def prepare(self):
        from .adaptive import effective
        from .prepared import update
        from .prewindow import prepare
        from .research import analytics
        started=time.monotonic()
        now=time.time()
        if now-self.last_analysis>=60:
            analytics(self.j,now);self.last_analysis=now
            if self.cfg.get('objective_feedback_enabled'):
                from .quality import update as quality_update
                from .feedback import update as feedback_update
                quality_update(self.j,self.cfg,now)
                feedback_update(self.j,self.cfg,now)
            self.meta(now)
            from .usage import report
            report(self.j,self.cfg,now)
        update(self.j,self.cfg)
        cfg,strategy=effective(self.j,'shop',base=self.cfg)
        prepare(self.j,cfg,strategy)
        self.j.put('cycle-last-preparation',{'at':time.time(),'seconds':time.monotonic()-started})

    def meta(self,now):
        framework=self.j.get('framework-plan',{})
        evaluation=framework.get('evaluation',{})
        state=self.j.get('autonomy-v14:state',{})
        issues=list(framework.get('issues',[]))
        for arm,row in evaluation.get('stats',{}).items():
            if row.get('closed',0)>=50 and row.get('paid',0)<20:
                issues.append('insufficient_paid_evidence:'+arm)
        if self.j.get('market_control',{}).get('pause_restock'):
            issues.append('execution_guard_closed')
        research=self.j.get('research-last-result',{})
        summary={'at':now,'issues':issues,'source_evaluation_at':evaluation.get('at'),
                 'skills_executed':state.get('executed_skills',0),'research_at':research.get('at'),
                 'next_actions':['retain_mature_gates','prepare_before_window','research_new_bounded_profiles'],
                 'promotion_authority':'mature_evaluators_only'}
        if self.cfg.get('objective_feedback_enabled'):
            feedback=self.j.get('feedback-last-policy',{})
            summary['operating_policy']={k:feedback.get(k) for k in ('id','at','status','regime','patches','reasons','execution_feedback')}
            summary['next_actions']=[{'issue':reason,'action':'restore_or_refresh_facts' if 'stale' in reason or 'guards' in reason else 'independent_research_review'} for reason in feedback.get('reasons',[])]
            summary['quality_holdout']=self.j.get('quality-holdout-summary',{})
        self.j.put('cycle-meta-state',summary);self.j.add('cycle_meta_evaluation',summary)

    def tick(self,now=None):
        now=time.time() if now is None else now
        self.prepare_lane.submit(self.prepare)
        self.research_lane.submit(self.research)
        inv=self.j.snapshot('inventory.json',{})
        window=inv.get('restock') or {};opens=window.get('opens_at');closes=window.get('closes_at')
        if (self.args and type(opens) in (int,float) and type(closes) in (int,float)
                and opens<=now<closes-20 and self.last_kick!=opens):
            self.last_kick=opens
            def supply():
                try:
                    response=self.market.call('GET','/api/inventory',role='shop')
                    if response.status_code==200:
                        current=response.json()
                        self.restock(self.args[0],self.args[1],self.market,self.j.md,current,
                                     self.j.snapshot('demand.json',[]))
                except Exception as exc:
                    self.j.add('cycle_supply_error',{'error_type':type(exc).__name__})
            self.supply_lane.submit(supply)
        self.j.put('cycle-heartbeat',{'at':now,'preparing':self.prepare_lane.busy,'researching':self.research_lane.busy,
                                    'preparation_error':self.prepare_lane.last_error,'research_error':self.research_lane.last_error})

    def research(self):
        from .research import update
        update(self.j,self.cfg)

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.j.add('cycle_supervisor_error',{'error_type':type(exc).__name__})
            self.stop.wait(5)
