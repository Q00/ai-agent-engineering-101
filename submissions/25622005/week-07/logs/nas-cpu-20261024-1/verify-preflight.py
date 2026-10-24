"""Disposable container check with generated keys and a synthetic local claim."""
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

sys.path.insert(0, '/opt/deploy')
import launch

for name in ('shop_key.pem', 'buyer_key.pem'):
    key = Ed25519PrivateKey.generate()
    Path('/data', name).write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
Path('/data/.market/market-owner').write_text('synthetic-preflight-only\n')

for code in (-signal.SIGILL, 128 + signal.SIGILL):
    with patch.object(launch.subprocess, 'run', return_value=SimpleNamespace(returncode=code)):
        try:
            launch.require_identity()
        except ValueError as error:
            assert 'SIGILL' in str(error) and 'CPU compatibility' in str(error)
        else:
            raise AssertionError('SIGILL must not be accepted')
print('PASS: signal and shell-style SIGILL results report CPU failure')

with patch.object(launch.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
    try:
        launch.require_identity()
    except ValueError as error:
        assert 'not logged in' in str(error)
    else:
        raise AssertionError('Unauthenticated status must not be accepted')
print('PASS: unauthenticated status still asks for login')

with patch.object(launch.subprocess, 'run', return_value=SimpleNamespace(returncode=0)):
    launch.require_identity()
print('PASS: successful status accepted; no model call or market request')
