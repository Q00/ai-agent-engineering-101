"""Round 3 persistent operator entry point and independent recommendation worker."""
import argparse
import json
import os
from pathlib import Path
import time
from launch_round3 import configure,main as market_main


def worker(once=False,dry_run=False):
    configure()
    from recommendations import tick
    from round3 import refresh_ledger,write,read
    from shop_profit import recent_ledger
    md=Path('.market')
    md.mkdir(exist_ok=True)
    with (md/'recommendation-worker.lock').open('a+b') as lock:
        lock.seek(0)
        if os.name=='nt':
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b'0');lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:
                print(json.dumps({'status':'worker_already_running'}),flush=True)
                return
        else:
            import fcntl
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
        strategy_initialized=False
        while True:
            write(md/'recommendation-heartbeat.json',{'at':time.time(),'pid':os.getpid()})
            try:
                from forum_votes import tick as vote_tick
                print(json.dumps({'at':time.time(),'forum_votes':vote_tick(md,enabled=not dry_run)}),flush=True)
            except Exception as exc:
                print(json.dumps({'at':time.time(),'status':'vote_error','error':str(exc)}),flush=True)
            try:
                try:
                    from strategy_policy import active
                    if active(md):
                        from demand_strategy import collect
                        market=collect(md,force=not strategy_initialized)
                        strategy_initialized=True
                        if market.get('at',0)!=locals().get('last_strategy_at'):
                            last_strategy_at=market.get('at',0)
                            print(json.dumps({'event':'strategy_market_refresh','at':last_strategy_at,'version':market.get('version'),
                                              'financial':market.get('financial'),'model_calls':0}),flush=True)
                    else:refresh_ledger(md)
                except (OSError,ValueError) as exc:
                    print(json.dumps({'at':time.time(),'status':'ledger_refresh_error','error':str(exc)}),flush=True)
                result=tick(md,enabled=not dry_run,refresh=once)
                print(json.dumps({'at':time.time(),**result}),flush=True)
                from recommendations import shop_analysis
                print(json.dumps({'at':time.time(),'shop_analysis':shop_analysis(md,enabled=not dry_run)}),flush=True)
                if not dry_run:
                    from batch_strategy_review import tick as joint_review_tick
                    review_status=joint_review_tick()
                    if review_status.get('status') in ('new_joint_cohort','joint_review_complete'):
                        print(json.dumps({'at':time.time(),'joint_strategy_review':review_status}),flush=True)
                if not dry_run and time.time()-read(md/'profit-recovery-metrics.json',{}).get('at',0)>=900:
                    from optimizer import interval_metrics
                    now=time.time()
                    summary=interval_metrics(md,max(now-900,read(md/'operating-policy.json',{}).get('effective_at',0)),now)
                    write(md/'profit-recovery-metrics.json',{'at':now,'version':read(md/'operating-policy.json',{}).get('version'),'metrics':summary})
                # Bounded publication measurement only; no pricing-policy
                # rewriting and no replacement for the retired optimizer.
                if not dry_run and read(md/'operating-policy.json',{}).get('guidance_enabled',True) and time.time()-read(md/'forum-injection-performance.json',{}).get('at',0)>=900:
                    from forum_injections import evaluate
                    metrics=evaluate(md)
                    print(json.dumps({'at':time.time(),'publication_measurements':len(metrics.get('events',[]))}),flush=True)
                if not dry_run and read(md/'operating-policy.json',{}).get('guidance_enabled',True) and time.time()-read(md/'purchase-guidance-results.json',{}).get('at',0)>=900:
                    from purchase_guidance import evaluate as measure_guidance
                    measured=measure_guidance(md)
                    print(json.dumps({'at':time.time(),'purchase_guidance':measured['decision']}),flush=True)
                pricing_policy=read(md/'extreme-quote-policy.json',{})
                pricing_worker=read(md/'extreme-quote-worker-state.json',{})
                pricing_records=None
                if not dry_run and pricing_policy.get('enabled') and (time.time()-pricing_worker.get('at',0)>=900
                        or pricing_worker.get('experiment')!=pricing_policy.get('experiment')):
                    from extreme_quotes import evaluate as measure_extreme
                    measured=measure_extreme(md,include_records=True)
                    pricing_records=measured
                    write(md/'extreme-quote-worker-state.json',{'at':time.time(),'experiment':pricing_policy.get('experiment')})
                    print(json.dumps({'at':time.time(),'extreme_quotes':measured['decision']}),flush=True)
                active_policy=read(md/'active-offer-policy.json',{})
                active_worker=read(md/'active-offer-worker-state.json',{})
                if not dry_run and active_policy.get('enabled') and (time.time()-active_worker.get('at',0)>=900
                        or active_worker.get('started_at')!=active_policy.get('started_at')):
                    from active_offer import evaluate as measure_active
                    measured=measure_active(md,pricing=pricing_records)
                    write(md/'active-offer-worker-state.json',{'at':time.time(),'started_at':active_policy['started_at']})
                    print(json.dumps({'at':time.time(),'active_offer':measured['decision']}),flush=True)
            except Exception as exc:
                print(json.dumps({'at':time.time(),'status':'error','error':str(exc)}),flush=True)
            if once:
                # One-shot validation must not inhibit the permanent worker.
                (md/'recommendation-heartbeat.json').unlink(missing_ok=True)
                return
            time.sleep(10)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--recommendations-only',action='store_true')
    p.add_argument('--once',action='store_true')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--publish',action='store_true')
    args=p.parse_args()
    if args.recommendations_only:
        worker(args.once,args.dry_run)
    else:
        market_main()
