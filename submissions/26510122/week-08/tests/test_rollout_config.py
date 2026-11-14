import json,shlex,tempfile,unittest
from pathlib import Path
from ops.rollout_linux import rebound_seller_config
from ops.seller_service import kit_entrypoint


class RolloutConfigTests(unittest.TestCase):
    def test_updated_supervisor_can_start_old_or_rolled_back_frozen_release(self):
        live=Path('/runtime');kit=Path('/shared/market_kit.py')
        self.assertEqual(kit_entrypoint(live,kit,{'sha256':{'seller_r3.py':'hash'}}),[str(kit)])
        self.assertEqual(kit_entrypoint(live,kit,{'sha256':{'seller_kit.py':'hash'}}),
                         ['/runtime/release/seller_kit.py',str(kit)])
    def test_new_restock_entrypoint_is_rebound_from_stage_to_live_release(self):
        with tempfile.TemporaryDirectory() as directory:
            stage=Path(directory)/'stage';stage.mkdir();live=Path(directory)/'live'
            original={'harness':'seller','seller':{'cmd':shlex.join(['python',str(stage/'release/seller_r3.py')])},
                      'restock':{'cmd':shlex.join(['python',str(stage/'release/procurement_scheduler.py'),'--execute','--state','{state}'])}}
            (stage/'agent.json').write_text(json.dumps(original))
            result=rebound_seller_config(stage,live)
            self.assertEqual(shlex.split(result['restock']['cmd']),
                             ['python',str(live/'release/procurement_scheduler.py'),'--execute','--state','{state}'])
            self.assertEqual(json.loads((stage/'agent.json').read_text()),original)


class TunnelDomainTests(unittest.TestCase):
    def test_failed_start_can_choose_the_second_provider_domain(self):
        from ops.seller_service import tunnel_url
        urls=['https://a.run.pinggy-free.link','https://b.free.pinggy.net']
        self.assertEqual(tunnel_url(urls),'https://b.free.pinggy.net')
        self.assertEqual(tunnel_url(urls,True),'https://a.run.pinggy-free.link')
    def test_unrelated_url_cannot_masquerade_as_provider_domain(self):
        from ops.seller_service import tunnel_url
        self.assertIsNone(tunnel_url(['https://example.com/.free.pinggy.net','https://a.free.pinggy.net.evil.com','https://u:p@a.free.pinggy.net','http://a.free.pinggy.net']))


class CloudflareRecoveryTests(unittest.TestCase):
    def test_url_discovery_rejects_similar_unrelated_hosts(self):
        from ops.seller_service import cloudflare_url
        self.assertEqual(cloudflare_url('INF | https://real-name.trycloudflare.com |'),'https://real-name.trycloudflare.com')
        self.assertIsNone(cloudflare_url('https://real-name.trycloudflare.com.evil.example'))
        self.assertIsNone(cloudflare_url('https://evil.example/trycloudflare.com'))
    def test_recovery_forwards_only_the_existing_local_api_proxy(self):
        from ops.seller_service import cloudflare_command
        command=cloudflare_command('/home/user/.local/bin/cloudflared',9300)
        self.assertEqual(command[command.index('--url')+1],'http://127.0.0.1:9300')
        self.assertNotIn('--no-tls-verify',command)


class OfflineRecoveryGuardTests(unittest.TestCase):
    def test_only_stale_inventory_with_explicit_absence_can_use_offline_path(self):
        import os
        from ops.rollout_linux import offline_gap
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.market').mkdir();p=root/'.market/inventory.json';p.write_text('{}');os.utime(p,(1000,1000))
            self.assertFalse(offline_gap(root,1050,{'shops':[]}))
            self.assertFalse(offline_gap(root,1200,{'shops':[{'name':'outseop-26510122'}]}))
            self.assertFalse(offline_gap(root,1200,{}))
            self.assertTrue(offline_gap(root,1200,{'shops':[]}))


class RegistrationRetryTests(unittest.TestCase):
    def fixture(self,errors):
        from types import SimpleNamespace
        from unittest.mock import Mock
        class Market:pass
        Market.join=Mock(side_effect=errors)
        kit=SimpleNamespace(Market=Market,log=Mock());sleep=Mock()
        from seller_kit import instrument_registration
        original=Market.join;instrument_registration(kit,sleep=sleep)
        return kit,sleep,original
    def timeout(self):
        return SystemExit('[market] join as shop failed: 400 '+json.dumps({'error':'cannot read an agent card at https://same.example/.well-known/agent-card.json (TimeoutError)'}))
    def test_card_timeout_retries_same_registration_without_rotating_url(self):
        kit,sleep,join=self.fixture([self.timeout(),{'name':'shop'}]);market=kit.Market();key=object()
        result=market.join('shop','same-shop',key,'https://same.example')
        self.assertEqual(result,{'name':'shop'});self.assertEqual(join.call_count,2)
        self.assertEqual(join.call_args_list[0],join.call_args_list[1]);sleep.assert_called_once_with(15)
    def test_other_refusals_and_buyer_errors_are_never_retried(self):
        for role,error in [('shop',SystemExit('[market] join as shop failed: 401 denied')),('shop',SystemExit('[market] join as shop failed: 400 bad key')),('buyer',self.timeout())]:
            kit,sleep,join=self.fixture([error])
            with self.assertRaises(SystemExit):kit.Market().join(role,'name',object())
            self.assertEqual(join.call_count,1);sleep.assert_not_called()
    def test_timeout_retry_is_bounded_and_still_surfaces_failure(self):
        kit,sleep,join=self.fixture([self.timeout() for _ in range(4)])
        with self.assertRaises(SystemExit):kit.Market().join('shop','name',object(),'https://same.example')
        self.assertEqual(join.call_count,4);self.assertEqual(sleep.call_count,3)


class TunnelContinuityTests(unittest.TestCase):
    def test_provisioning_rate_limit_uses_existing_fallback_and_backs_off(self):
        from ops.seller_service import tunnel_retry
        self.assertEqual(tunnel_retry('cloudflare','quick tunnel provisioning failed with status 429',1),
                         ('pinggy',15))
        self.assertEqual(tunnel_retry('cloudflare','HTTP 200, no provider URL',2),('cloudflare',30))
        self.assertEqual(tunnel_retry('pinggy','other failure',8),('pinggy',300))

    def test_registry_errors_are_unknown_instead_of_confirmed_absence(self):
        from unittest.mock import patch, MagicMock
        from ops.seller_service import registry_matches
        with patch('ops.seller_service.urllib.request.urlopen',side_effect=TimeoutError):
            self.assertIsNone(registry_matches('https://shop.example'))
        response=MagicMock()
        with patch('ops.seller_service.urllib.request.urlopen',return_value=response):
            response.__enter__.return_value.read.return_value=b'{"shops":[]}'
            self.assertFalse(registry_matches('https://shop.example'))
            response.__enter__.return_value.read.return_value=b'{"shops":[{"name":"outseop-26510122","url":"https://shop.example"}]}'
            self.assertTrue(registry_matches('https://shop.example'))
            response.__enter__.return_value.read.return_value=b'{"error":"restarting"}'
            self.assertIsNone(registry_matches('https://shop.example'))

    def test_renewal_waits_for_fresh_inventory_and_a_gap_after_supply(self):
        import os
        from ops.seller_service import safe_tunnel_renewal
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'.market').mkdir()
            path=root/'.market/inventory.json'
            def inventory(opened=False,opens_at=1200):
                path.write_text(json.dumps({'round':3,'restock':{'open':opened,'opens_at':opens_at}}))
                os.utime(path,(1000,1000))
            inventory()
            self.assertTrue(safe_tunnel_renewal(root,1010))
            self.assertFalse(safe_tunnel_renewal(root,1100))
            inventory(True)
            self.assertFalse(safe_tunnel_renewal(root,1010))
            inventory(False,1050)
            self.assertFalse(safe_tunnel_renewal(root,1010))

class RegistryTradeEvidenceTests(unittest.TestCase):
    def test_live_trade_and_public_card_prevent_stale_address_restart_only(self):
        import os,sqlite3
        from unittest.mock import patch,MagicMock
        from ops.seller_service import serving_recent_buyers
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.market').mkdir();(root/'state').mkdir()
            p=root/'.market/inventory.json';p.write_text('{}');os.utime(p,(1000,1000))
            db=sqlite3.connect(root/'state/seller.sqlite3')
            db.execute('create table purchase_contexts(updated_at real)')
            db.execute('insert into purchase_contexts values (1000)');db.commit();db.close()
            response=MagicMock()
            response.__enter__.return_value.read.return_value=b'{"name":"outseop-26510122","supportedInterfaces":[{}]}'
            with patch('ops.seller_service.urllib.request.urlopen',return_value=response):
                self.assertTrue(serving_recent_buyers(root,'https://shop.example',1010))
                self.assertFalse(serving_recent_buyers(root,'https://shop.example',1200))
                response.__enter__.return_value.read.return_value=b'{"name":"another-shop","supportedInterfaces":[{}]}'
                self.assertFalse(serving_recent_buyers(root,'https://shop.example',1010))
