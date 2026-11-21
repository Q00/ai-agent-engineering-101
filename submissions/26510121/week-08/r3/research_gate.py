"""Avoid a paid re-review of the same non-trial proposal for one hour.

Cache identity is operational content plus rule/guard/issue regime. A cache
hit never publishes, extends a policy expiry, or authorizes a transaction.
"""
import math
from .state import identity

KEY='research-reviewed-proposals:v281'
FIELDS=('buyer_probe_percent','buyer_gap_power','buyer_diverse_quotes','shop_service_level',
    'shop_plan','forum_focus','operating_actions','operating_policy','skills','skill_steps')
TTL=3600

def fingerprint(journal,facts,proposal):
    control=facts.get('control') or {};framework=facts.get('framework') or {}
    gap=control.get('leader_gap')
    band=math.floor(gap/.05) if type(gap) in (int,float) and math.isfinite(gap) else None
    regime={'rules':journal.get('market_rules',{}).get('sha256'),
        'control':control.get('status'),'reasons':sorted(control.get('reasons') or []),
        'issues':sorted(framework.get('issues') or []),'gap_band':band,
        'parameters':facts.get('parameters') or {}}
    return identity([regime,{k:proposal.get(k) for k in FIELDS}])

def reviewed(journal,config,facts,proposal,now):
    if not config.get('research_noop_gate_enabled') or proposal.get('approve_trial') is not False:return None
    digest=fingerprint(journal,facts,proposal)
    row=journal.get(KEY,{}).get(digest,{})
    if row and row.get('no_trial_approved') and -2<=now-row.get('reviewed_at',0)<TTL:
        return {'proposal_id':digest,'reviewed_at':row['reviewed_at'],'expires_at':row['reviewed_at']+TTL}
    return None

def remember(journal,facts,proposal,critic,now):
    if proposal.get('approve_trial') is not False:return
    digest=fingerprint(journal,facts,proposal);rows=journal.get(KEY,{})
    rows[digest]={'reviewed_at':now,'no_trial_approved':not(proposal['approve_trial'] and critic['approve_trial'])}
    rows={k:v for k,v in rows.items() if -2<=now-v.get('reviewed_at',0)<TTL}
    journal.put(KEY,dict(sorted(rows.items(),key=lambda p:-p[1]['reviewed_at'])[:64]))
