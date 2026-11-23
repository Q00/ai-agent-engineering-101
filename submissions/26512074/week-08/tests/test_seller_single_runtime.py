import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import seller_r3


class SellerSingleRuntimeTests(unittest.TestCase):
    def test_port_probe_tracks_real_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
            self.assertTrue(seller_r3.port_in_use(port))
        self.assertFalse(seller_r3.port_in_use(port))

    def test_supervisor_waits_for_existing_server_before_building(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(seller_r3,'MARKET_DIR',Path(d)),patch.object(seller_r3,'port_in_use',side_effect=[True,True,False]),patch.object(seller_r3.time,'sleep') as pause,patch.object(seller_r3,'build',return_value='app') as build,patch.object(seller_r3.uvicorn,'run') as run:
                seller_r3.run_single_seller(1100)
            pause.assert_called_once_with(2);build.assert_called_once_with(1100);run.assert_called_once()
