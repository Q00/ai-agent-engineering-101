"""Display the active pricing experiment and the archived x50 results."""
import argparse,time
from pathlib import Path
from datetime import datetime,timezone,timedelta
from optimizer import read

ROOT=Path(__file__).resolve().parent
def show(md,refresh=False):
    md=Path(md);policy=read(md/'extreme-quote-policy.json',{})
    if refresh:
        from extreme_quotes import evaluate
        result=evaluate(md)
    else:result=read(md/'extreme-quote-results.json',{})
    if result.get('experiment')!=policy.get('experiment'):result={}
    now=time.time();elapsed=max(0,now-policy.get('experiment_started_at',now))
    def number(value):return 'N/A' if value is None else f'{value:,.2f}'
    print('Round 3 Pricing Experiment')
    print('Experiment:',policy.get('experiment','N/A'))
    print(f'Elapsed: {int(elapsed//3600)}h {int(elapsed%3600//60)}m')
    upcoming=next((h for h in (2,6,12) if elapsed<h*3600),None)
    print('Next evaluation:',f'T+{upcoming}h (15-minute worker)' if upcoming else 'N/A / review decision')
    if result.get('at'):
        print('Snapshot KST:',datetime.fromtimestamp(result['at'],timezone(timedelta(hours=9))).isoformat(timespec='seconds'))
    for group in 'ABCD':
        values=result.get('groups',{}).get(group,{})
        print(f"Group {group}: quotes={values.get('quotes','N/A')}, settled={values.get('settled','N/A')}, rounds={number(values.get('mean_rounds') if values.get('quotes') else None)}, mean price={number(values.get('mean_settlement'))}")
    high=result.get('premium',{});actual=result.get('actual_premium_only',{})
    print('Valid x2 quotes:',actual.get('quotes','N/A'))
    print('Settled x2 transactions:',actual.get('premium_settled','N/A'))
    print('X2 settlement rate:',number(actual.get('conversion') if actual.get('quotes') else None))
    print('Fallbacks:',high.get('fallback','N/A'))
    print('Profit per quote (risk-adjusted estimate):')
    for label,key in [('Baseline','baseline'),('X2 assigned','premium'),('X2 issued only','actual_premium_only')]:
        values=result.get(key,{})
        print('-',label,number(values.get('expected_profit_per_quote') if values.get('quotes') else None))
    print('Guidance effect: compare A/B and C/D separately; not established without matched evidence.')
    print('Pricing effect:',result.get('decision','N/A'))
    print('Decision:',policy.get('status','N/A'))
    print('Shop Book delta (all activity):',number(result.get('shop_book_delta')))
    old=read(md/'experiments/pricing_x50_v1/extreme-quote-results.json',{})
    old_high=old.get('x50',{})
    print('Archived pricing_x50_v1: quotes=',old_high.get('valid_x50_quotes','N/A'),
          'settled=',old_high.get('x50_settled','N/A'))
    active_policy=read(md/'active-offer-policy.json',{})
    if active_policy:
        if refresh:
            from active_offer import evaluate as active_evaluate
            active=active_evaluate(md)
        else:active=read(md/'active-offer-results.json',{})
        print('\nACTIVE BUYER OFFER EXPERIMENT: guidance_active_offer_v1 (proposed amount = 200% of ask)')
        for arm in ('G0','G1','G2'):
            g=active.get('groups',{}).get(arm,{})
            print(arm,'quotes=',g.get('quotes','N/A'),'settled=',g.get('settled','N/A'),
                  'over-ask offers=',g.get('over_ask_offers','N/A'),'over-ask settlements=',g.get('over_ask_settlements','N/A'),
                  'offer/ask=',number(g.get('mean_offer_ratio')),'net/quote=',number(g.get('expected_net_per_quote')))
        print('Guidance decision:',active.get('decision','N/A'))
        print('Pricing decision:',policy.get('status','N/A'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--refresh',action='store_true');args=p.parse_args()
    show(ROOT/'.market',args.refresh)
