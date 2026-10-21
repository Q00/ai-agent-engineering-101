"""Use the unchanged seller with its existing public URL and private signing key."""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import uvicorn

from harness_app import emit, isolated
from http_client import create_client
from market_a2a.catalog import Catalog
from market_a2a.harness import Harness
from market_a2a.model_provider import create_model_client
from market_a2a.receipts import ReceiptSigner
from market_a2a.seller import create_app

PUBLIC_URL = 'https://ax-week07.treasuresky.xyz/'
PORT = 8900


async def serve() -> None:
    _ = os.environ.setdefault('AX_SELLER_STATE_DB', '/data/seller-state.sqlite3')
    signer = ReceiptSigner.load(ROOT / 'shop_key.pem')
    async with create_client(model=True) as model_http:
        async with create_model_client(model_http) as model:
            harness = Harness(model, emit)
            app = create_app(PUBLIC_URL, harness,
                             Catalog(ROOT / 'catalog.json'), 'Sungjin Shop',
                             signer=signer)
            emit(f'HARNESS model={harness.model} source=week05-tool-loop directory=False')
            await uvicorn.Server(uvicorn.Config(
                app, host='127.0.0.1', port=PORT, log_level='warning',
            )).serve()


if __name__ == '__main__':
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format='%(message)s')
    isolated(serve)
