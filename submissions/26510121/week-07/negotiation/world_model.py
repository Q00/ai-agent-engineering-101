"""One-trade empirical forecasts and outcome criticism, with explicit priors.

This is a model of observed trading outcomes, not a simulator of the entire
market or neural actor-critic training. Forecasts never become ledger facts.
"""
from negotiation.ranking import FLOORS

MODEL_VERSION='empirical-world-v1'


def buyer_forecast(stats,item,budget):
    finalized=stats.get('finalized',0);wins=stats.get('successes',0)
    price=stats.get('mean_success_price')
    source='comparable observed paid prices' if price is not None else 'optimistic public-floor prior'
    reference=price if price is not None else FLOORS.get(item,budget)
    probability=(wins+1)/(finalized+2)
    seconds=max(1,stats.get('mean_cycle_seconds') or 30)
    reward=probability*max(0,budget-reference)
    return {'version':MODEL_VERSION,'role':'buyer','samples':finalized,'success_probability':probability,
            'expected_price':reference,'expected_cycle_seconds':seconds,'expected_reward':reward,
            'expected_rate':reward/seconds,'basis':source,'is_observation':False}


def seller_forecast(statistics,arm,reserve,quote):
    row=next((r for r in statistics.get('arm_results',[]) if r['arm']==arm),{})
    trials=row.get('matured_opportunities',0)
    successes=row.get('paid',0) if statistics.get('market_round',2)>=3 else row.get('accepted',0)
    probability=(successes+1)/(trials+2)
    margin=row.get('reward_sum',0)/successes if successes else max(0,quote-reserve)
    seconds=max(1,row.get('cycle_seconds_sum',0)/trials) if trials else 30
    reward=probability*margin
    return {'version':MODEL_VERSION,'role':'shop','samples':trials,'success_probability':probability,
            'expected_cycle_seconds':seconds,'expected_reward':reward,'expected_rate':reward/seconds,
            'basis':'observed arm outcomes' if successes else 'quoted-margin prior; concessions unknown',
            'is_observation':False}


def critique(prediction,reward,seconds,success,*,attribution_unknown=False):
    seconds=max(1,seconds)
    actual=reward/seconds
    p=prediction.get('success_probability',0.5)
    return {'model_version':MODEL_VERSION,'reward':reward,'cycle_seconds':seconds,
            'actual_rate':actual,'predicted_rate':prediction['expected_rate'],
            'advantage_rate':actual-prediction['expected_rate'],
            'duration_error':seconds-prediction['expected_cycle_seconds'],
            'brier_error':None if attribution_unknown else (p-int(success))**2,
            'attribution':'not established' if attribution_unknown else 'observed outcome, not proof of intent',
            'update_scope':'bounded policy selection and context; no model-weight or safety-rule updates'}
