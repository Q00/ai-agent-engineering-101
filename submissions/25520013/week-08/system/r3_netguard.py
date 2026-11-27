"""Test network guard: import r3_netguard; r3_netguard.install()   (conftest.py does this).

With R3_NETGUARD=1, install() patches socket.getaddrinfo / gethostbyname / gethostbyname_ex and socket.socket
connect / connect_ex: any non-loopback name or address is refused (gaierror / PermissionError) and recorded in
ATTEMPTS as (op, host, port). Loopback (localhost, 127.0.0.0/8, ::1), unix sockets and getaddrinfo(None) pass.
Without R3_NETGUARD=1 install() does nothing.
"""

import ipaddress
import os
import socket

ATTEMPTS = []
_ORIG = {}


def _loopback(host):
    if host is None or host in ("", "localhost", "localhost.", b"localhost"):
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    try:
        return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _refuse(op, host, port, exc):
    ATTEMPTS.append((op, host, port))
    raise exc


def _getaddrinfo(host, port, *a, **kw):
    if not _loopback(host):
        _refuse(
            "dns",
            host,
            port,
            socket.gaierror(socket.EAI_NONAME, f"r3_netguard: {host} blocked"),
        )
    return _ORIG["getaddrinfo"](host, port, *a, **kw)


def _byname(name):
    def f(host):
        if not _loopback(host):
            _refuse(
                "dns",
                host,
                None,
                socket.gaierror(socket.EAI_NONAME, f"r3_netguard: {host} blocked"),
            )
        return _ORIG[name](host)

    return f


def _connect(name):
    def f(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and isinstance(
            address, tuple
        ):
            if not _loopback(address[0]):
                _refuse(
                    "connect",
                    address[0],
                    address[1] if len(address) > 1 else None,
                    PermissionError(f"r3_netguard: connect to {address[0]} blocked"),
                )
        return _ORIG[name](self, address)

    return f


def install():
    if os.environ.get("R3_NETGUARD") != "1" or _ORIG:
        return False
    _ORIG.update(
        getaddrinfo=socket.getaddrinfo,
        gethostbyname=socket.gethostbyname,
        gethostbyname_ex=socket.gethostbyname_ex,
        connect=socket.socket.connect,
        connect_ex=socket.socket.connect_ex,
    )
    socket.getaddrinfo = _getaddrinfo
    socket.gethostbyname = _byname("gethostbyname")
    socket.gethostbyname_ex = _byname("gethostbyname_ex")
    socket.socket.connect = _connect("connect")
    socket.socket.connect_ex = _connect("connect_ex")
    return True
