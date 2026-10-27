"""Native resolver failures and HTTPS DNS fallback, without network calls."""
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tunnel_dns


class TunnelDnsTests(unittest.TestCase):
    def setUp(self):
        tunnel_dns._cache.clear()

    def test_native_success_does_not_query_https_dns(self):
        with patch('socket.getaddrinfo', return_value=['native']) as native, \
                patch('tunnel_dns.public_addresses') as fallback:
            tunnel_dns.install()
            self.assertEqual(socket.getaddrinfo('shop.trycloudflare.com', 443), ['native'])
            fallback.assert_not_called()

    def test_failed_tunnel_lookup_resolves_only_socket_address(self):
        def native(host, *args):
            if host == 'shop.trycloudflare.com':
                raise socket.gaierror('cached negative lookup')
            return [('resolved', host)]
        with patch('socket.getaddrinfo', side_effect=native), \
                patch('tunnel_dns.public_addresses', return_value=['104.16.230.132']) as fallback:
            tunnel_dns.install()
            self.assertEqual(socket.getaddrinfo('shop.trycloudflare.com', 443),
                             [('resolved', '104.16.230.132')])
            fallback.assert_called_once()

    def test_other_hosts_do_not_use_fallback(self):
        for host in ('market.ourolabs.ai', 'localhost', 'shop.trycloudflare.com.example.org'):
            with self.subTest(host=host), patch('socket.getaddrinfo', side_effect=socket.gaierror()), \
                    patch('tunnel_dns.public_addresses') as fallback:
                tunnel_dns.install()
                with self.assertRaises(socket.gaierror):
                    socket.getaddrinfo(host, 443)
                fallback.assert_not_called()

    def test_https_query_keeps_only_public_ips_and_caches_them(self):
        data = {'Status': 0, 'Answer': [{'type': 1, 'data': '104.16.230.132', 'TTL': 300},
                                      {'type': 1, 'data': '127.0.0.1', 'TTL': 300}]}
        response = Mock()
        response.__enter__ = Mock(return_value=io.BytesIO(json.dumps(data).encode()))
        response.__exit__ = Mock(return_value=False)
        with patch('tunnel_dns.urlopen', return_value=response) as request, redirect_stderr(io.StringIO()):
            self.assertEqual(tunnel_dns.public_addresses('shop.trycloudflare.com', 0), ['104.16.230.132'])
            tunnel_dns.public_addresses('shop.trycloudflare.com', 0)
            request.assert_called_once()

    def test_failed_https_query_preserves_native_error(self):
        original = socket.gaierror('native failure')
        with patch('socket.getaddrinfo', side_effect=original), \
                patch('tunnel_dns.public_addresses', side_effect=OSError('offline')):
            tunnel_dns.install()
            with self.assertRaises(socket.gaierror) as caught:
                socket.getaddrinfo('shop.trycloudflare.com', 443)
            self.assertIs(caught.exception, original)

    def test_install_is_idempotent(self):
        with patch('socket.getaddrinfo', return_value=[]):
            tunnel_dns.install()
            first = socket.getaddrinfo
            tunnel_dns.install()
            self.assertIs(socket.getaddrinfo, first)


if __name__ == '__main__':
    unittest.main()
