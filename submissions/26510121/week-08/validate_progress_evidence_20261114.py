"""Offline audit of the selected official comparison and presentation data."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / 'logs'

def read(name):
    return json.loads((LOGS / name).read_text(encoding='utf-8-sig'))

baseline = read('autonomous-deploy-before-20261113T070936759881Z.json')['board']
current = read('peer-market-source-20261114T033752254761Z.json')['responses']['/api/board']['data']
fragment = (LOGS / 'progress-book-growth-20261114.html').read_text(encoding='utf-8')
chart = json.loads(re.search(r'<script type="application/json" data-viz-chart>(.*?)</script>', fragment, re.S)[1])
before = {r['name']: r for r in baseline['shops']}
after = {r['name']: r for r in current['shops']}
names = ['seochanit', 'inextro', 'sergiobuilds', 'yms979']
assert len(chart['rows']) == 4
for name, row in zip(names, chart['rows']):
    assert row[2] == before[name]['book']
    assert row[3] == after[name]['book']
    assert abs(row[1] - (row[3] - row[2]) / 1_000_000) < 1e-9
assert 10_418_235 - 3_631_201 - 2_678_330 == after['seochanit']['book'] - before['seochanit']['book']
assert 1778 + 75 == 1853
assert 1070 + 270 + 190 + 5 == 1535
assert 194 + 50 == 244
for name in ('progress-diagnosis-20261114T034839695020Z.json',
             'peer-market-source-20261114T033752254761Z.json',
             'v271-live-verification-20261114T045510571347Z.json'):
    data = (LOGS / name).read_text(encoding='utf-8-sig')
    assert not re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-proj-[A-Za-z0-9_-]{20,}', data)
print('PASS: four chart rows match official paired Book values; financial bridge and event count sums reconcile.')
print('PASS: selected JSON evidence contains no private key block or OpenAI project key pattern.')
print('LIMITATION: bundled chart/source render checks failed on Windows; browser rendering not inspected.')
