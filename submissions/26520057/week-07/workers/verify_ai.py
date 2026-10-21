"""Live language interpretation probes. No market orders or leaderboard trades."""
import json
import time
import uuid
from pathlib import Path
import httpx

URL = 'https://cheapest-in-town-26520057.cheapest-in-town-worker.workers.dev'
cases = [
    ('260. Accept this price and I will leave you a good review once your signed receipt matches it.', True),
    ('That works for me, let us complete the purchase.', True),
    ('그 가격이면 구매할게요.', True),
    ('Nice, but I will pay 200.', False),
    ('Would you accept 260?', False),
    ('I like it, but I am not buying today.', False),
    ('I accept only if you lower the price to 200.', False),
]
results = []
with httpx.Client(timeout=30) as client:
    def send(text, task=None):
        message = {'role':'ROLE_USER', 'messageId':str(uuid.uuid4()), 'parts':[{'text':text}]}
        if task:
            message['taskId'] = task
        data = client.post(URL, json={'jsonrpc':'2.0', 'id':str(uuid.uuid4()),
            'method':'SendMessage', 'params':{'message':message}}).raise_for_status().json()
        assert 'error' not in data, data
        return data['result']['task']

    for text, expected in cases:
        task = send('I want a bookshelf')
        start = time.monotonic()
        task = send(text, task['id'])
        completed = task['status']['state'] == 'TASK_STATE_COMPLETED'
        row = {'text':text, 'completed':completed, 'expected':expected,
               'seconds':round(time.monotonic()-start, 3)}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if completed:
            assert task['artifacts'][0]['parts'][0]['data']['price'] == 260
    assert all(r['completed'] == r['expected'] for r in results), results
print('PASS natural-language acceptance versus counteroffer, question, refusal and condition')
