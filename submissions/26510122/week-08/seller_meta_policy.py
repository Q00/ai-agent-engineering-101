"""Small, expiring operator-bounded policy overrides; no network or model calls."""
import json,math
from pathlib import Path
PATH=Path('state/seller-meta-policy.json')
POLICY_VERSION='capital-share-v3'
PREVIOUS_ROLLING_DEFAULT = 24_000_000
LEGACY_KEYS={'window_budget','target_capital_share','epsilon','focus'}
DEFAULT={'window_budget':24_000_000,'rolling_exploration_budget':32_000_000,
         'inventory_capital_share':.80,'target_capital_share':.65,'epsilon':.30,'focus':'balanced'}
BOUNDS={'window_budget':(1_200_000,1_000_000_000),'rolling_exploration_budget':(1_200_000,1_000_000_000),
        'inventory_capital_share':(.65,.90),'target_capital_share':(.50,.85),'epsilon':(.20,.40)}
FOCUS=('balanced','replenishment','discovery')

def validate(config):
    if set(config)!=set(DEFAULT):raise ValueError('unknown or missing policy key')
    for key,(low,high) in BOUNDS.items():
        v=config[key]
        if type(v) not in (int,float) or not math.isfinite(v) or not low<=v<=high:raise ValueError('policy range: '+key)
    if any(type(config[k]) is not int for k in ('window_budget','rolling_exploration_budget')):raise ValueError('integer budget required')
    if config['focus'] not in FOCUS:raise ValueError('unknown focus')
    return config

def upgrade(config, contract_version=None):
    """Keep learned preferences; replace the old operator's capped defaults."""
    if set(config)==LEGACY_KEYS:
        result={**DEFAULT,**config}
        if config['window_budget']==12_000_000:result['window_budget']=DEFAULT['window_budget']
        return validate(result)
    if contract_version == 'capital-share-v2' and config.get('rolling_exploration_budget') == PREVIOUS_ROLLING_DEFAULT:
        config = {**config, 'rolling_exploration_budget': DEFAULT['rolling_exploration_budget']}
    return validate(config)

def active(now,path=None):
    try:
        d=json.loads((path or PATH).read_text())
        if d.get('schema_version') not in (1,2) or not d['at']<=now<d['expires_at'] or d['expires_at']-d['at']>14400:raise ValueError('expired or invalid policy')
        config = upgrade(d['config'], d.get('policy_contract_version'))
        migrated = config != d['config']
        return {**config,'meta_version':d['version']+(':'+POLICY_VERSION if migrated else ''),
                'meta_status':'migrated' if migrated else 'active'}
    except (OSError,ValueError,KeyError,TypeError):
        return {**DEFAULT,'meta_version':None,'meta_status':'operator_default'}
