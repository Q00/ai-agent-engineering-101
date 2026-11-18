"""Explicit buyer refusals; identity claims are not cryptographic identity."""
import re
from pathlib import Path
from optimizer import read

ROOT=Path(__file__).resolve().parent


def blocked_buyer(texts,metadata=None):
    policy=read(ROOT/'seller-access.json',{})
    blocked={str(v).casefold() for v in policy.get('blocked_buyers',[])}
    metadata=metadata or {}
    if not isinstance(metadata,dict) and hasattr(metadata,'DESCRIPTOR'):
        from google.protobuf.json_format import MessageToDict
        metadata=MessageToDict(metadata)
    metadata=metadata if isinstance(metadata,dict) else {}
    # Refusing a named blocked customer does not require authenticating their
    # claim. This is a refusal decision, never a source of trusted identity.
    claims=[metadata[k] for k in ('buyer','buyer_id','buyerId') if isinstance(metadata.get(k),str)]
    for text in texts:claims.extend(re.findall(r'\bbuyer\s*[:=]\s*([a-zA-Z0-9_.-]+)',text,re.I))
    claims=[name.rstrip('.,;:!?') for name in claims]
    named=next((name for name in claims if name.casefold() in blocked),None)
    if named:return named
    combined='\n'.join(texts).casefold()
    for name,groups in policy.get('anonymous_request_fingerprints',{}).items():
        if name.casefold() in blocked and any(group and all(phrase.casefold() in combined for phrase in group) for group in groups):
            return name+' (anonymous refusal template)'
    for name,groups in policy.get('anonymous_request_regex_groups',{}).items():
        if name.casefold() in blocked and any(group and all(re.search(pattern,combined,re.I|re.M) for pattern in group) for group in groups):
            return name+' (anonymous refusal template)'
    from strategy_policy import load
    if load(ROOT/'.market').get('verified_buyer_access_enabled'):
        from review_policy import identity
        buyer,confidence=identity(ROOT/'.market',texts,metadata)
        # Generic negotiation language is never treated as authenticated identity.
        if buyer and buyer.casefold() in blocked:return buyer
        if confidence=='unverified_claim':
            from optimizer import append
            import time
            append(ROOT/'.market/buyer-access-audit.jsonl',dict(at=time.time(),action='allow_unverified_claim',confidence=confidence))
        return None
    identities=[]
    if isinstance(metadata,dict):
        for key in ('buyer','buyer_id','buyerId'):
            value=metadata.get(key)
            if isinstance(value,str):identities.append(value)
    for text in texts:
        identities.extend(re.findall(r'\bbuyer\s*[:=]\s*([a-zA-Z0-9_.-]+)',text,re.I))
    named=next((name for name in identities if name.casefold() in blocked),None)
    if named:return named
    # Fingerprints are a refusal policy for observed anonymous client templates,
    # not authenticated identities. Require every phrase in a configured group.
    combined='\n'.join(texts).casefold()
    for name,groups in policy.get('anonymous_request_fingerprints',{}).items():
        if name.casefold() in blocked and any(group and all(phrase.casefold() in combined for phrase in group) for group in groups):
            return name+' (anonymous request pattern)'
    return None
