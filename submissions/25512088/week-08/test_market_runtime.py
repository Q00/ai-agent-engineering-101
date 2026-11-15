import io
import os
import unittest
from unittest.mock import patch,Mock
from market_runtime import tunnel_url,open_tunnel,install_parent_exit_handlers

class TunnelTests(unittest.TestCase):
    def test_tmux_hangup_uses_normal_exit_cleanup_path(self):
        registered={}
        with patch('market_runtime.signal.signal',side_effect=lambda sig,handler:registered.update({sig:handler})):
            install_parent_exit_handlers()
        if hasattr(__import__('signal'),'SIGHUP'):
            import signal
            self.assertIn(signal.SIGHUP,registered)
            with self.assertRaises(SystemExit):registered[signal.SIGHUP](signal.SIGHUP,None)

    def test_api_endpoint_is_never_a_shop(self):
        self.assertIsNone(tunnel_url('failed POST https://api.trycloudflare.com/tunnel'))
        self.assertIsNone(tunnel_url('https://api.trycloudflare.com'))
        self.assertEqual(tunnel_url('| https://wheel-finder-dylan-last.trycloudflare.com |'),
                         'https://wheel-finder-dylan-last.trycloudflare.com')
    def test_error_api_line_before_real_tunnel(self):
        proc=Mock()
        proc.stdout=io.StringIO('failed https://api.trycloudflare.com\nhttps://actual-shop-test-host.trycloudflare.com\n')
        proc.poll.return_value=None
        with patch('market_runtime.subprocess.Popen',return_value=proc) as popen:
            result,url=open_tunnel(9999)
        self.assertIs(result,proc)
        self.assertEqual(url,'https://actual-shop-test-host.trycloudflare.com')
        if os.name!='nt':
            self.assertIs(popen.call_args.kwargs.get('start_new_session'),True)
        proc.terminate.assert_not_called()
    def test_failed_process_reports_creation_failure(self):
        proc=Mock();proc.stdout=io.StringIO('failed https://api.trycloudflare.com\n');proc.poll.return_value=1
        with patch('market_runtime.subprocess.Popen',return_value=proc):
            with self.assertRaisesRegex(RuntimeError,'did not create'):open_tunnel(9999)

if __name__=='__main__':unittest.main()
