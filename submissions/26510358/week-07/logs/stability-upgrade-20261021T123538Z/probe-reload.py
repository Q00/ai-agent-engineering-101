"""Operator quote-only deployment check: real model, no order or purchase submission."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import uuid
import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from protocol import A2AClient, task_metadata
from tracing import Trace

url = json.loads((HERE / 'tunnel-start.json').read_text())['url']
trace = Trace('operator-reload-probe', HERE / 'quote-probe')
client = A2AClient(url, trace, timeout=50)
control = httpx.Client(transport=httpx.HTTPTransport(uds=str(ROOT / '.market/shop-service/control.sock')), timeout=30)
report = {'operator_probe': True, 'purchase_attempted': False, 'market_order_submitted': False,
          'started': datetime.now(timezone.utc).isoformat(), 'public_url': url, 'steps': []}

def opening(item, **metadata):
    started = time.monotonic()
    task = client.rpc('SendMessage', {'message': {'role': 'ROLE_USER', 'messageId': uuid.uuid4().hex,
        'parts': [{'text': 'I want to buy a ' + item}],
        'metadata': {'probePurpose': 'operator quote-only, cancel without purchase', **metadata}}})
    report['steps'].append({'action': 'quote_only', 'item': item, 'task_id': task['id'],
                            'state': task['status']['state'], 'seconds': round(time.monotonic()-started,3),
                            'ask': task_metadata(task).get('ask')})
    return task

try:
    before = control.get('http://localhost/').json()
    first = opening('lamp')
    assert task_metadata(first)['ask'] == 100, first
    response = control.post('http://localhost/', json={'action':'reload'})
    response.raise_for_status()
    after = response.json()
    assert after['active_generation'] > before['active_generation']
    assert after['public_url'] == before['public_url'] == url
    old = client.rpc('GetTask', {'id': first['id']})
    assert task_metadata(old)['quoteId'] == task_metadata(first)['quoteId']
    canceled = client.rpc('CancelTask', {'id': first['id']})
    assert 'CANCELED' in canceled['status']['state'].upper()
    second = opening('chair')
    assert task_metadata(second)['ask'] == 180, second
    client.rpc('CancelTask', {'id': second['id']})
    denied = opening('lamp', buyerName='cheapest-in-town')
    assert 'REJECTED' in denied['status']['state'].upper()
    # Control endpoints must remain inaccessible through the public tunnel.
    assert httpx.post(url+'/reload', json={'action':'reload'}, timeout=10).status_code == 404
    card = httpx.get(url+'/.well-known/agent-card.json', timeout=10).json()
    assert card['description'] == json.loads((HERE/'card-before.json').read_text())['description']
    assert all(i['url'].startswith(url + '/') for i in card['supportedInterfaces'])
    report.update(passed=True, old_quote_preserved=True, old_quote_canceled=True,
                  new_seller_quoted=True, anonymous_buyer_allowed=True, named_denylist_enforced=True,
                  control_not_public=True, profile_description_preserved=True,
                  generations_before=before, generations_after=control.get('http://localhost/').json())
except BaseException as exc:
    report.update(passed=False, error=type(exc).__name__+': '+str(exc))
    raise
finally:
    client.close(); control.close()
    (HERE/'public-reload-probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
