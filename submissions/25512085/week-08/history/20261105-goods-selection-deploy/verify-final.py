"""Read-only final process, hash, and public board-role verification."""
import datetime
import json
import urllib.request
import deploy

after = json.loads((deploy.OUT / 'remote-after.json').read_text())
assert deploy.snapshot() == after['after']
assert deploy.git('rev-parse', 'HEAD') == deploy.TARGET
assert deploy.hashes(deploy.RUNTIME) == after['runtime_sha256']
assert not deploy.git('status', '--porcelain', '--', *deploy.RUNTIME)
with urllib.request.urlopen('https://market.ourolabs.ai/api/board', timeout=20) as response:
    board = json.load(response)
    http = response.status
matches = []
def walk(obj, location=''):
    if isinstance(obj, dict):
        if obj.get('name') == 'mjforge':
            matches.append(dict(location=location, name='mjforge', alive=obj.get('alive'), fields=sorted(obj)))
        for key, value in obj.items():
            walk(value, location + '/' + key)
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            walk(value, location + '/' + str(i))
walk(board)
report = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), head=deploy.TARGET,
              runtime_clean=True, process_identity_unchanged_since_restart=True, board_http=http,
              mjforge_matches=matches)
deploy.save('final-check.json', report)
print(json.dumps(report))
