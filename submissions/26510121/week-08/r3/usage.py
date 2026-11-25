"""KST daily agent token/cost observations; not the provider billing ledger."""
from datetime import datetime, timedelta, timezone
import json
import time
from .state import atomic_json

KST=timezone(timedelta(hours=9))


def report(journal, config, now=None):
    now=time.time() if now is None else now
    local=datetime.fromtimestamp(now,KST)
    start=local.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
    buckets={}
    with journal.connect() as db:
        rows=db.execute('SELECT ts,data FROM events WHERE kind=? AND ts>=? AND ts<=? ORDER BY ts',
                        ('model_usage',start,now)).fetchall()
        reserved=db.execute("SELECT day,count(*),sum(amount) FROM spending WHERE status='reserved' GROUP BY day").fetchall()
    total={'calls':0,'input_tokens':0,'output_tokens':0,'cached_input_tokens':0,'cache_write_tokens':0,
           'reasoning_tokens_included_in_output':0,'priced_token_estimate_usd':0,'conservative_recorded_usd':0}
    for at,raw in rows:
        row=json.loads(raw);model=row.get('model','unknown');role=row.get('role','unknown');usage=row.get('usage') or {}
        details=usage.get('input_tokens_details') or {};outdetails=usage.get('output_tokens_details') or {}
        inp=usage.get('input_tokens',0);out=usage.get('output_tokens',0)
        cached=details.get('cached_tokens',0);written=details.get('cache_write_tokens',0)
        rates=config.get('prices_usd_per_million',{}).get(model)
        estimate=row.get('estimated_usd',0)
        if rates:
            cache=config.get('cache_prices_usd_per_million',{}).get(model)
            estimate=((max(0,inp-cached-written)*rates[0]+cached*cache[0]+written*cache[1]+out*rates[1])/1e6
                      if cache else row.get('estimated_usd',0))
        values={'calls':1,'input_tokens':inp,'output_tokens':out,'cached_input_tokens':cached,
                'cache_write_tokens':written,'reasoning_tokens_included_in_output':outdetails.get('reasoning_tokens',0),
                'priced_token_estimate_usd':estimate,'conservative_recorded_usd':row.get('estimated_usd',0)}
        bucket=buckets.setdefault(role+' / '+model,{key:0 for key in total})
        for key,value in values.items():total[key]+=value;bucket[key]+=value
    result={'date':local.date().isoformat(),'timezone':'Asia/Seoul','at':now,'window_start':start,
            'scope':'Shop/Buyer journal only; other applications using this API key are not included',
            'cost_cap_usd':config.get('daily_api_budget_usd'),'budget_mode':config.get('research_budget_mode'),
            'totals':total,'by_role_and_model':buckets,'unresolved_reservations_by_utc_day':reserved,
            'cost_note':'Token-priced estimate, not an OpenAI invoice. Raw usage and reservations remain inspectable.'}
    atomic_json(journal.md/'model-usage-daily.json',result)
    previous=journal.get('model-usage-day:'+result['date'],{})
    if previous.get('totals',{}).get('calls')!=total['calls'] or now-previous.get('at',0)>=3600:
        journal.put('model-usage-day:'+result['date'],result);journal.add('model_usage_daily_snapshot',result)
    return result
