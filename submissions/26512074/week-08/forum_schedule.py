"""One role every 300 seconds, with a shared 150-second spacing gate."""
from pathlib import Path
from optimizer import read,atomic

def due(md,role,now):
    md=Path(md);state=read(md/'forum-schedule.json',{})
    times={r:state.get(r,0) for r in ('buyer','shop')}
    for post in read(md/'forum.json',[]):
        if post.get('author')=='totato2203-r3' and post.get('role') in times:
            r=post['role'];times[r]=max(times[r],post.get('ts',0))
    times['shop']=max(times['shop'],read(md/'shop-analysis-state.json',{}).get('at',0))
    return now-times[role]>=300 and now-max(times.values())>=150

def queued(md,role,now):
    path=Path(md)/'forum-schedule.json';state=read(path,{})
    state[role]=now;atomic(path,state)
