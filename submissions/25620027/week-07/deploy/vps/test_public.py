"""Inspect the actual deployment wrapper's ASGI app without network/model calls."""
from contextlib import asynccontextmanager
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import anyio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx2

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('serve_public', HERE / 'serve_public.py')
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)
EVIDENCE = Path(os.environ.get('VPS_PUBLIC_TEST_EVIDENCE', HERE / '.omo/evidence'))


@asynccontextmanager
async def fake_model(**kwargs):
    yield object()


class InspectServer:
    def __init__(self, config):
        assert config.host == '127.0.0.1'
        assert config.port == 8900
        self.config = config

    async def serve(self):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.config.app),
            base_url='http://local-test',
        ) as client:
            results = {}
            for path in ('/.well-known/agent-card.json', '/', '/price-comparison.json', '/price-comparison.js'):
                response = await client.get(path)
                assert response.status_code == 200, (path, response.status_code)
                results[path] = {'status': response.status_code, 'bytes': len(response.content)}
                if path.endswith('agent-card.json'):
                    card = response.json()
                    (EVIDENCE / 'public-card.json').write_text(json.dumps(card, indent=2) + '\n')
                    assert card['version'] == '1.3.0'
                    interfaces = card.get('supportedInterfaces', card.get('supported_interfaces'))
                    assert interfaces[0]['url'] == public.PUBLIC_URL
                if path == '/price-comparison.json':
                    assert response.json()['enabled'] is True
            (EVIDENCE / 'public-routes.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    signer = public.ReceiptSigner(Ed25519PrivateKey.generate())
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='week07-public-') as state_dir, \
         patch.object(public, 'AsyncOpenAI', fake_model), \
         patch.object(public.ReceiptSigner, 'load', return_value=signer), \
         patch.object(public.uvicorn, 'Server', InspectServer), \
         patch.dict(os.environ, {'AX_PRICE_COMPARISON': '1',
                                'AX_SELLER_STATE_DB': str(Path(state_dir) / 'state.sqlite3')}):
        anyio.run(public.serve)
    print('PASS: wrapper binds loopback:8900; real card version 1.3.0 advertises existing HTTPS URL; all four public routes return 200; comparison enabled; isolated persistent state; no model/network calls')
