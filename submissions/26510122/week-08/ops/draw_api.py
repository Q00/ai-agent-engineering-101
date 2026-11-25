"""Read-only settlement draw proof API.

Requires py-ecc==8.0.0 for resolve and verify. Freeze the eligible ledger
interval, publish the printed commitment before the beacon round, then resolve.
The HTTP endpoint only reads the resulting proof, including while it is frozen.
"""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

LEDGER = 'https://market.ourolabs.ai/api/ledger'
QUICKNET = '52db9ba70e0cc0f6eaf7803dd07447a1f5477735fd3f661792ba94600c84e971'
QUICKNET_KEY = (
    '83cf0f2896adee7eb8b5f01fcad3912212c437e0073e911fb90022d3e760183c8c4'
    'b450b6a0a6c3ac6a5776a2d1064510d1fec758c921cc22b0e17e63aaf4bcb5ed66'
    '304de9cf809bd274ca73bab4af5a6e9c76a4bc09e76eae8991ef5ece45a'
)
QUICKNET_GENESIS = 1692803367
QUICKNET_PERIOD = 3
BEACON = f'https://api.drand.sh/v2/chains/{QUICKNET}/rounds'
DOMAIN = b'BLS_SIG_BLS12381G1_XMD:SHA-256_SSWU_RO_NUL_'
ALGORITHM = 'min sha256(beacon_randomness_bytes || commitment_bytes || ledger_seq_uint64_be), then ledger_seq'


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii')


def fetch(url):
    for attempt in range(2):
        try:
            with urlopen(url, timeout=12) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code < 500 or attempt:
                raise
        except (TimeoutError, URLError):
            if attempt:
                raise


def beacon_time(round_number):
    return QUICKNET_GENESIS + QUICKNET_PERIOD * (round_number - 1)


def eligible_draw(campaign, *, page_limit=100):
    """Read the full interval between two ledger entries, accepting paid deals only."""
    start = campaign['opens_at']
    end = campaign['closes_at']
    cursor = campaign['after_seq']
    round_number = campaign['beacon_round']
    if (not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', campaign['id'])
            or not campaign['shop'].startswith('https://') or not campaign['skus']
            or type(cursor) is not int or cursor < 2
            or type(round_number) is not int or round_number <= 0
            or not start < end < beacon_time(round_number)):
        raise ValueError('invalid campaign')
    page = fetch(f'{LEDGER}?after={cursor - 1}')
    if not page or page[0]['seq'] != cursor or page[0]['ts'] >= start:
        raise ValueError('ledger start anchor is missing or falls inside enrollment')
    previous = page[0]
    rows = page[1:]
    entrants = {}
    for _ in range(page_limit):
        for row in rows:
            if row['seq'] <= previous['seq'] or row['prev'] != previous['hash'] or row['ts'] < previous['ts']:
                raise ValueError('ledger interval has a gap or is out of order')
            previous = row
            if row['ts'] >= end:
                return {'version': 1, 'campaign': campaign, 'ledger_close_seq': row['seq'],
                        'entrants': list(entrants.values()), 'drand_chain': QUICKNET,
                        'selection_rule': ALGORITHM}
            if row['ts'] < start or row['kind'] != 'settle' or row['round'] != campaign['market_round']:
                continue
            data = row['data']
            if (data['shop'] == campaign['shop'] and data['item'] in campaign['skus']
                    and type(data['amount']) is int and data['amount'] > 0):
                buyer = data['buyer']
                entrants.setdefault(buyer, {'buyer': buyer, 'sku': data['item'], 'ledger_seq': row['seq']})
        rows = fetch(f'{LEDGER}?after={previous["seq"]}')
        if not rows:
            break
    raise ValueError('complete ledger interval not yet available')


def verify_beacon(beacon, round_number):
    """Verify the exact Quicknet round against its pinned BLS public key."""
    from py_ecc.bls.g2_primitives import pubkey_to_G1, signature_to_G2, subgroup_check
    from py_ecc.bls.hash_to_curve import hash_to_G1
    from py_ecc.optimized_bls12_381 import G2, FQ12, final_exponentiate, is_inf, neg, pairing

    if type(beacon['round']) is not int or beacon['round'] != round_number:
        raise ValueError('incorrect beacon round')
    signature = bytes.fromhex(beacon['signature'])
    if len(signature) != 48:
        raise ValueError('incorrect signature length')
    point = pubkey_to_G1(signature)
    key = signature_to_G2(bytes.fromhex(QUICKNET_KEY))
    message = hashlib.sha256(round_number.to_bytes(8, 'big')).digest()
    expected = hash_to_G1(message, DOMAIN, hashlib.sha256)
    if is_inf(point) or not subgroup_check(point):
        raise ValueError('invalid beacon signature point')
    check = pairing(key, expected, final_exponentiate=False) * pairing(G2, neg(point), final_exponentiate=False)
    if final_exponentiate(check) != FQ12.one():
        raise ValueError('invalid beacon signature')
    return hashlib.sha256(signature).digest()


def resolve(proof, beacon):
    frozen = proof['frozen']
    commitment = hashlib.sha256(json_bytes(frozen)).hexdigest()
    if proof['commitment'] != commitment or frozen['drand_chain'] != QUICKNET or frozen['selection_rule'] != ALGORITHM:
        raise ValueError('invalid draw commitment')
    randomness = verify_beacon(beacon, frozen['campaign']['beacon_round'])
    seed = randomness + bytes.fromhex(commitment)

    def rank(ticket):
        seq = ticket['ledger_seq']
        return hashlib.sha256(seed + seq.to_bytes(8, 'big')).digest(), seq

    winner = min(frozen['entrants'], key=rank, default=None)
    return {'beacon': beacon, 'randomness': randomness.hex(), 'winner': winner}


def write_proof(path, proof, *, create=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        stream.write(json_bytes(proof) + b'\n')
    try:
        if create:
            os.link(temp, path)
        else:
            temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def serve(path, port):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            proof = json.loads(path.read_bytes())
            campaign = proof['frozen']['campaign']['id']
            if self.path != f'/api/draw/{campaign}':
                self.send_error(404)
                return
            body = json_bytes(proof)
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    with ThreadingHTTPServer(('127.0.0.1', port), Handler) as server:
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('freeze', 'resolve', 'verify', 'serve'))
    parser.add_argument('proof', type=Path)
    parser.add_argument('--campaign', type=Path)
    parser.add_argument('--commitment')
    parser.add_argument('--port', type=int, default=18771)
    args = parser.parse_args()
    if args.command == 'freeze':
        if args.campaign is None:
            parser.error('freeze requires --campaign')
        campaign = json.loads(args.campaign.read_bytes())
        now = time.time()
        scheduled = beacon_time(campaign['beacon_round'])
        if not campaign['closes_at'] <= now < scheduled:
            raise ValueError('freeze must occur after enrollment and before the beacon')
        frozen = eligible_draw(campaign)
        proof = {'frozen': frozen, 'commitment': hashlib.sha256(json_bytes(frozen)).hexdigest(), 'result': None}
        if time.time() >= scheduled:
            raise ValueError('beacon round arrived before the commitment was ready')
        write_proof(args.proof, proof, create=True)
    elif args.command == 'serve':
        serve(args.proof, args.port)
        return
    else:
        proof = json.loads(args.proof.read_bytes())
        if args.commitment is not None and proof['commitment'] != args.commitment:
            raise ValueError('proof does not match the published commitment')
        if args.command == 'resolve' and proof['result'] is None:
            beacon = fetch(f'{BEACON}/{proof["frozen"]["campaign"]["beacon_round"]}')
            proof['result'] = resolve(proof, beacon)
            write_proof(args.proof, proof)
        elif resolve(proof, proof['result']['beacon']) != proof['result']:
            raise ValueError('stored draw result is not reproducible')
        if args.command == 'verify' and eligible_draw(proof['frozen']['campaign']) != proof['frozen']:
            raise ValueError('frozen entrants do not match the public ledger')
    print(json.dumps({'commitment': proof['commitment'], 'entrants': len(proof['frozen']['entrants']),
                      'result': proof['result']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
