"""Called by kit only AFTER successful payment; saves actual product conditions."""
import argparse
import json
from pathlib import Path
from buyer_memory import Memory


def review(deal, memory):
    memory.sync()
    order=deal['order']['order']
    settled=deal['settled']
    with memory.db:
        memory.db.execute('INSERT OR REPLACE INTO outcomes VALUES(?,?)', (order,json.dumps(settled,ensure_ascii=False)))
    memory.state(order,'paid')
    honest=settled.get('claim_honest')
    return {'stars':1 if honest is False else 4,
            'review':f"Paid {deal['receipt']['price']} for {deal['receipt']['item']}; grade {settled.get('grade','unknown')}; "
                     f"claim honest: {honest}; ledger #{settled.get('ledger','unknown')}. Future breakage not yet known."}


if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--deal',required=True); ap.add_argument('--state',default='.market')
    args=ap.parse_args(); memory=Memory(args.state)
    try:
        result=review(json.loads(Path(args.deal).read_text()),memory)
        from rival_feedback import refresh_feedback
        refresh_feedback(memory)
        print(json.dumps(result,ensure_ascii=False))
    finally:
        memory.close()
