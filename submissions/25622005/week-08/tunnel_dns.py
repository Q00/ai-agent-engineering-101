"""Process-local DNS fallback for public tunnel domains after native DNS fails.

HTTPS requests retain their original hostname and normal TLS verification.
No system resolver configuration or hosts file is changed.
"""
import ipaddress
import json
import re
import socket
import sys
import threading
import time
from urllib.parse import urlencode
from urllib.request import urlopen

_cache = {}
_lock = threading.Lock()


def public_addresses(host, family):
    kind = 'AAAA' if family == socket.AF_INET6 else 'A'
    key = (host, kind)
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
    request = 'https://cloudflare-dns.com/dns-query?' + urlencode({'name': host, 'type': kind})
    from urllib.request import Request
    with urlopen(Request(request, headers={'Accept': 'application/dns-json'}), timeout=3) as response:
        data = json.loads(response.read())
    addresses, ttls = [], []
    for answer in data.get('Answer', []) if data.get('Status') == 0 else []:
        if answer.get('type') != (28 if kind == 'AAAA' else 1):
            continue
        address = ipaddress.ip_address(answer['data'])
        if address.is_global and address.version == (6 if kind == 'AAAA' else 4):
            addresses.append(str(address))
            ttls.append(max(1, min(int(answer.get('TTL', 30)), 300)))
    if not addresses:
        raise ValueError('HTTPS DNS returned no public tunnel addresses')
    with _lock:
        _cache[key] = (now + min(ttls), addresses)
    print(f'[tunnel-dns] native lookup failed for {host}; using HTTPS DNS', file=sys.stderr, flush=True)
    return addresses


def install():
    if getattr(socket.getaddrinfo, '_tunnel_dns', False) is True:
        return
    native = socket.getaddrinfo

    def resolve(host, port, family=0, type=0, proto=0, flags=0):
        try:
            return native(host, port, family, type, proto, flags)
        except socket.gaierror as original_error:
            name = host.decode('ascii', errors='ignore') if isinstance(host, bytes) else host
            if (not isinstance(name, str) or not re.fullmatch(
                    r'[a-z0-9-]+\.(?:trycloudflare\.com|lhr\.life)', name, re.I)
                    or family not in (socket.AF_UNSPEC, socket.AF_INET, socket.AF_INET6)):
                raise
            try:
                addresses = public_addresses(name.lower(), family)
                results = []
                for address in addresses:
                    results.extend(native(address, port, family, type, proto, flags))
                if results:
                    return results
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                pass
            raise original_error

    resolve._tunnel_dns = True
    socket.getaddrinfo = resolve
