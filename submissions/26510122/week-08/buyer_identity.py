"""Optional signed buyer-name metadata, reusing the existing market buyer key.

A self-signed name alone is not a market identity. The public key must also
verify a past market-verified buyer result under that name. No new auth service.
"""
from __future__ import annotations

import base64
import json
import math
from pathlib import Path
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_AGE_SECONDS = 300


def canon(value: dict) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def payload(name,recipient,message_id,task_id,message,at):
    return {'scope':'ax-buyer-identity-v1','buyer_name':name,'recipient':recipient,
            'message_id':message_id,'task_id':task_id or '', 'message':message,'at':at}


def load_key(path: Path = Path('buyer_key.pem')):
    try:
        return serialization.load_pem_private_key(path.read_bytes(),password=None)
    except (OSError,ValueError,TypeError):
        return None


def sign(key,name: str,recipient: str,message_id: str,task_id: str | None,message: str,
         now: float | None = None) -> dict:
    at=int(time.time() if now is None else now)
    public=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    return {'buyer_name':name,'recipient':recipient,'task_id':task_id or '', 'at':at,
            'public_key':base64.b64encode(public).decode(),
            'signature':base64.b64encode(key.sign(canon(payload(name,recipient,message_id,task_id,message,at)))).decode()}


def identify(metadata: dict, *, recipient: str,message_id: str,task_id: str | None,
             message: str,proofs: list[dict],now: float | None = None) -> dict:
    now=time.time() if now is None else now
    envelope=metadata.get('buyer_identity')
    claimed=metadata.get('buyer_name')
    if not isinstance(envelope,dict):
        return {'buyer_name':claimed if isinstance(claimed,str) and len(claimed)<=100 else None,
                'status':'claimed_only' if claimed else 'anonymous','verified':False}
    name=envelope.get('buyer_name');at=envelope.get('at')
    result={'buyer_name':name if isinstance(name,str) and 0<len(name)<=100 else None,
            'status':'invalid_signature','verified':False}
    if (not result['buyer_name'] or type(at) not in (int,float) or not math.isfinite(at) or at!=int(at)
            or abs(now-at)>MAX_AGE_SECONDS or envelope.get('recipient')!=recipient
            or envelope.get('task_id','')!=(task_id or '')):
        return result
    # Protobuf Struct represents JSON integers as floats; sign canonical integers.
    at=int(at)
    try:
        raw=base64.b64decode(envelope['public_key'],validate=True)
        key=Ed25519PublicKey.from_public_bytes(raw)
        key.verify(base64.b64decode(envelope['signature'],validate=True),
                   canon(payload(name,recipient,message_id,task_id,message,at)))
    except (KeyError,ValueError,TypeError,InvalidSignature):
        return result
    result['status']='self_signed_unlinked'
    for proof in proofs:
        if proof.get('buyer_name')!=name:
            continue
        try:
            key.verify(base64.b64decode(proof['sig_buyer'],validate=True),canon(proof['signed']))
        except (KeyError,ValueError,TypeError,InvalidSignature):
            continue
        return {**result,'status':'market_linked_signature','verified':True,
                'public_key':envelope['public_key'],'proof_seq':proof['seq']}
    return result
