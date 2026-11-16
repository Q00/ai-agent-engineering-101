"""Long-lived, one-job-at-a-time Buyer negotiation worker; no payment keys."""
import json
import os
import sys
import traceback
from pathlib import Path

from market_buyer import buy
from .state import Journal
from . import contracts


def main():
    os.environ['R3_PERSISTENT_BUYER'] = '1'
    journal = Journal()
    for line in sys.stdin:
        response = {'id': None, 'order': None}
        try:
            if len(line.encode()) > 2_000_000:
                raise ValueError('input_too_large')
            request = json.loads(line)
            order, shops = request['order'], request['shops']
            response.update(id=request['id'], order=order['order'])
            if 'mandates' in order or 'nonce' in order:
                raise ValueError('private_payment_material_not_allowed')
            contracts._IDENTITIES.clear()
            contracts._HISTORY.clear()
            response['result'] = buy(order, shops, journal)
        except Exception as exc:
            response['error'] = type(exc).__name__
            # No exception message, source text, locals or payment material.
            response['error_origin'] = [{'module':Path(f.filename).name,
                'function':f.name,'line':f.lineno} for f in traceback.extract_tb(exc.__traceback__)[-3:]]
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
