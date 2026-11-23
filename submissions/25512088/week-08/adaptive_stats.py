"""Deterministic experiment statistics for adaptive market decisions."""
from __future__ import annotations

import hashlib
import math
from statistics import mean


MASK64=(1<<64)-1;MASK128=(1<<128)-1


class PCG64:
    MULT=47026247687942121848144207491837523525
    INC=117397592171526113268558934119004209487
    def __init__(self,seed):
        self.state=(int(seed)+self.INC)&MASK128
        self._next();self.state=(self.state+int(seed))&MASK128;self._next()
    def _next(self):
        old=self.state;self.state=(old*self.MULT+self.INC)&MASK128
        x=((old>>64)^old)>>64;rot=(old>>122)&63
        return ((x>>rot)|(x<<((-rot)&63)))&MASK64
    def randbelow(self,n):
        if n<=0:raise ValueError('empty population')
        limit=((1<<64)//n)*n
        while True:
            value=self._next()
            if value<limit:return value%n


def seed_v1(experiment_id,metric_version):
    raw=(str(experiment_id)+'\x1f'+str(metric_version)).encode('utf-8')
    return int.from_bytes(hashlib.sha256(raw).digest()[:8],'big')


def percentile_type7(values,q):
    if not values:raise ValueError('no values')
    rows=sorted(values);h=(len(rows)-1)*q;lo=math.floor(h);hi=math.ceil(h)
    return rows[lo] if lo==hi else rows[lo]+(rows[hi]-rows[lo])*(h-lo)


def _cluster_means(rows):
    result={}
    for cluster,values in rows.items():
        clean=[float(v) for v in values if isinstance(v,(int,float)) and math.isfinite(v)]
        if clean:result[str(cluster)]=mean(clean)
    return result


def cluster_bootstrap(experiment_id,metric_version,treatment,control,resamples=10000):
    """Equal-cluster-weight effect; positive always means treatment is better."""
    t=_cluster_means(treatment);c=_cluster_means(control)
    if len(t)<5 or len(c)<5:raise ValueError('at least five clusters per arm required')
    tv=[t[k] for k in sorted(t,key=lambda x:x.encode())]
    cv=[c[k] for k in sorted(c,key=lambda x:x.encode())]
    observed=mean(tv)-mean(cv);rng=PCG64(seed_v1(experiment_id,metric_version));effects=[]
    for _ in range(resamples):
        effects.append(mean(tv[rng.randbelow(len(tv))] for _ in tv)-
                       mean(cv[rng.randbelow(len(cv))] for _ in cv))
    low=percentile_type7(effects,.0125);high=percentile_type7(effects,.9875)
    p=(1+sum(x<=0 for x in effects))/(resamples+1)
    return {'effect':observed,'ci_low':low,'ci_high':high,'p':p,
            'resamples':resamples,'seed':seed_v1(experiment_id,metric_version),
            'treatment_clusters':len(tv),'control_clusters':len(cv)}


def holm(p_values,alpha=.025):
    ordered=sorted(p_values.items(),key=lambda row:(row[1],row[0].encode()))
    result={key:False for key in p_values};failed=False;m=len(ordered)
    for rank,(key,p) in enumerate(ordered,1):
        if failed or p>alpha/(m-rank+1):failed=True
        else:result[key]=True
    return result


def decide(stats,delta,guardrails_ok,sample_phase='minimum',family_pass=None):
    significance=(stats['p']<=.025) if family_pass is None else bool(family_pass)
    if not guardrails_ok or stats['ci_high']<=0:return 'REJECTED'
    passed=stats['ci_low']>=delta and significance
    if sample_phase=='minimum':return 'EXPANDING' if passed else 'RUNNING'
    if sample_phase=='2x':return 'ADOPTED' if passed else 'REJECTED'
    raise ValueError('sample_phase must be minimum or 2x')

