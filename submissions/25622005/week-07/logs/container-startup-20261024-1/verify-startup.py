"""Disposable, offline startup check through the real container launcher.

Only the already-authenticated CLI precondition is stubbed. Keys and claim are
synthetic. The actual supervisor and cloudflared run with networking disabled.
No registration, order, OAuth exchange or model request is made.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

data = Path('/data')
assert not (data / 'shop_key.pem').exists(), 'Use a fresh container without host volumes'
sys.path.insert(0, str(data))
import market_runtime

assert market_runtime.parse_arguments(['--name', 'fixture']).kit == Path('/opt/market/market_kit.py')
assert market_runtime.parse_arguments(['--name', 'fixture', '--kit', '/opt/market/market_kit.py']).kit.is_file()
print('PASS: /data layout resolves both default and explicit kit paths', flush=True)

for name in ('shop_key.pem', 'buyer_key.pem'):
    key = Ed25519PrivateKey.generate()
    (data / name).write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
(data / '.market/market-owner').write_text('synthetic-startup-fixture\n')
binary = data / 'test-bin'
binary.mkdir()
stub = binary / 'claude'
stub.write_text('#!/bin/sh\nif [ "$1" = auth ] && [ "$2" = status ]; then\n  printf \'{"loggedIn":true}\\n\'\nelse\n  exit 99\nfi\n')
stub.chmod(0o700)
environment = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'])
trace = data / 'logs/supervisor-startup-check.log'
with trace.open('w') as output:
    proc = subprocess.Popen([sys.executable, '/opt/deploy/launch.py', 'run'],
                            env=environment, stdout=output, stderr=subprocess.STDOUT)
    try:
        for _ in range(150):
            if proc.poll() is not None:
                raise AssertionError(trace.read_text())
            pidfile = data / '.market/runtime.pid'
            if pidfile.exists() and (data / 'logs/cloudflared-runtime.log').exists():
                assert int(pidfile.read_text()) == proc.pid
                break
            time.sleep(0.1)
        else:
            raise AssertionError('Supervisor did not reach tunnel startup: ' + trace.read_text())
        time.sleep(1)
        assert proc.poll() is None, trace.read_text()
        arguments = Path(f'/proc/{proc.pid}/cmdline').read_bytes().split(b'\0')
        assert b'/data/market_runtime.py' in arguments
        assert b'/opt/market/market_kit.py' in arguments
        assert b'--cloudflared' in arguments
        assert 'IndexError' not in trace.read_text()
        print('PASS: actual launcher execs /data supervisor and reaches cloudflared startup', flush=True)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise AssertionError('Supervisor failed to shut down on SIGTERM')
    assert proc.returncode == 0, trace.read_text()
print('PASS: SIGTERM shuts down the actual supervisor normally', flush=True)
print(json.dumps({'passed': True, 'scope': 'Offline launcher/supervisor startup; authenticated-status fixture only. No market or model verification.'}))
