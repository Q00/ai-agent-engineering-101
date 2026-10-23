"""Record the current reconnection while retaining the event's original boundary."""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def main():
    process=json.loads((ROOT/'.market/process.json').read_text(encoding='utf-8-sig'))
    verification=sorted((ROOT/'logs/review-event').glob('live-verification-20*.json'))[-1]
    live=json.loads(verification.read_text(encoding='utf-8'))
    assert all(live['checks'].values())
    before=ROOT/f"logs/satisfaction-refund/memory-before-recovery-{process['started']}.jsonl"
    assert (ROOT/'.market/buyer-memory.jsonl').read_bytes().startswith(before.read_bytes())
    state_path=ROOT/'.market/refund-deployment.json'
    state=json.loads(state_path.read_text(encoding='utf-8'))
    previous=dict(state)
    state['urls']=sorted(set(state.get('urls',[state['url']]))|{live['url']})
    state.update(url=live['url'],process=process,
                 runtime_started_at=datetime.strptime(process['started'],'%Y%m%d-%H%M%S').replace(tzinfo=timezone(timedelta(hours=9))).isoformat())
    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output=ROOT/'logs/satisfaction-refund'/f'recovery-verification-{stamp}.json'
    evidence={'checked_at':datetime.now(timezone.utc).isoformat(),'previous_deployment':previous,'current_deployment':state,
              'live_verification':str(verification.relative_to(ROOT)),'memory_preserved':True,
              'last_exit':'httpx.ReadError: [WinError 10054] remote host forcibly closed connection',
              'prior_stderr':'logs/satisfaction-refund/restart-20261022-165010.stderr.log'}
    output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'url':state['url'],'pid':process['pid'],'memory_preserved':True,'evidence':str(output)},indent=2))


if __name__=='__main__':
    main()
