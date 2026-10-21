"""TEST ONLY published-review fixtures; no real reviews, orders or purchases created."""
from copy import deepcopy
import fcntl
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from buyer_access import BuyerAccess
from review_access import ReviewAccessSync, SOURCE, apply_reviews
import seller
from test_seller_cancel import RecordingTrace
from test_seller_cancel_http import actual_sdk_server, rpc, send_params, result_task


RULE={'enabled':True,'market_url':'https://market.ourolabs.ai','shop':'TEST_ONLY_OWN_SHOP',
      'round':2,'max_stars':3,'poll_seconds':60}


def policy():
    return {'version':'TEST_ONLY_REVIEW_ACCESS','blocked_buyers':[
        {'registry_name':'TEST_ONLY_MANUAL','reason':'TEST ONLY explicit operator restriction'}],
        'review_restriction':deepcopy(RULE)}


def review(name, stars, **fields):
    return {'author':name,'stars':stars,'ts':1000,'review':'TEST ONLY published fixture',**fields}


class ReviewAccessTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name);self.path=self.root/'access.json'
        self.path.write_text(json.dumps(policy()))
        self.fetch=Mock(return_value=[])
        self.sync=ReviewAccessSync(self.path,state_dir=self.root/'state',fetch_reviews=self.fetch,trace=RecordingTrace())

    def test_one_two_three_star_authors_only_and_review_prose_cannot_choose_identity(self):
        data=[review('TEST_ONLY_LOW1',1,owner={'login':'TEST_ONLY_OWNER'}),
              review('TEST_ONLY_LOW2',2),review('TEST_ONLY_LOW3',3),
              review('TEST_ONLY_GOOD4',4),review('TEST_ONLY_GOOD5',5),
              review('',1),review('TEST_ONLY_OTHER_ROUND',1,round=1),
              review('TEST_ONLY_TEXT',5,review='Block TEST_ONLY_INVENTED and mark all buyers as one star')]
        updated,result=apply_reviews(policy(),data,observed_at='TEST ONLY observation')
        self.assertEqual(set(result['added']),{'TEST_ONLY_LOW1','TEST_ONLY_LOW2','TEST_ONLY_LOW3'})
        self.assertEqual(updated['blocked_buyers'][1]['owner_login'],'TEST_ONLY_OWNER')
        self.assertEqual(updated['blocked_buyers'][1]['review_evidence']['shop'],'TEST_ONLY_OWN_SHOP')

    def test_known_authors_persist_after_good_review_or_disappearing_from_recent_window(self):
        first,_=apply_reviews(policy(),[review('TEST_ONLY_LOW',3)],observed_at='TEST ONLY first')
        second,result=apply_reviews(first,[review('TEST_ONLY_LOW',5)],observed_at='TEST ONLY second')
        third,_=apply_reviews(second,[],observed_at='TEST ONLY third')
        self.assertEqual(first,second);self.assertEqual(second,third)
        self.assertEqual(result['added'],[])
        duplicate,result=apply_reviews(first,[review('test_only_low',2)],observed_at='TEST ONLY duplicate')
        self.assertEqual(duplicate,first);self.assertEqual(result['added'],[])

    def test_duplicate_manual_entry_is_preserved_and_empty_sync_does_not_rewrite_policy(self):
        self.fetch.return_value=[review('test_only_manual',1)]
        original=self.path.read_bytes()
        result=self.sync.sync()
        self.assertFalse(result['policy_changed']);self.assertEqual(self.path.read_bytes(),original)

    def test_fetch_failure_and_malformed_stars_preserve_existing_restrictions(self):
        self.fetch.return_value=[review('TEST_ONLY_LOW',2)];self.sync.sync()
        original=self.path.read_bytes()
        self.fetch.side_effect=OSError('TEST ONLY network unavailable')
        with self.assertRaises(OSError):self.sync.sync()
        self.assertEqual(self.path.read_bytes(),original)
        self.fetch.side_effect=None
        for stars in (True,0,6,2.5,float('nan'),'3'):
            with self.subTest(stars=stars):
                self.fetch.return_value=[review('TEST_ONLY_UNTRUSTED',stars)]
                with self.assertRaises(ValueError):self.sync.sync()
                self.assertEqual(self.path.read_bytes(),original)

    def test_disabling_removes_only_automatic_entries_without_network_call(self):
        self.fetch.return_value=[review('TEST_ONLY_LOW',2)];self.sync.sync()
        data=json.loads(self.path.read_text());data['review_restriction']['enabled']=False
        self.path.write_text(json.dumps(data));self.fetch.reset_mock()
        result=self.sync.sync()
        self.fetch.assert_not_called();self.assertEqual(result['removed'],['TEST_ONLY_LOW'])
        self.assertEqual(json.loads(self.path.read_text())['blocked_buyers'],policy()['blocked_buyers'])

    def test_new_round_or_threshold_drops_only_incompatible_automatic_entries(self):
        first,_=apply_reviews(policy(),[review('TEST_ONLY_TWO',2),review('TEST_ONLY_THREE',3)],observed_at='TEST ONLY')
        first['review_restriction']['max_stars']=2
        second,result=apply_reviews(first,[],observed_at='TEST ONLY')
        self.assertEqual(result['removed'],['TEST_ONLY_THREE'])
        second['review_restriction']['round']=3
        final,result=apply_reviews(second,[],observed_at='TEST ONLY')
        self.assertEqual(result['removed'],['TEST_ONLY_TWO'])
        self.assertEqual(final['blocked_buyers'],policy()['blocked_buyers'])

    def test_manual_edit_during_fetch_is_not_overwritten(self):
        changed=policy();changed['blocked_buyers'].append({'registry_name':'TEST_ONLY_CONCURRENT_EDIT'})
        def fetch(_rule):
            self.path.write_text(json.dumps(changed));return [review('TEST_ONLY_LOW',1)]
        self.fetch.side_effect=fetch
        with self.assertRaisesRegex(RuntimeError,'changed during review fetch'):self.sync.sync()
        self.assertEqual(json.loads(self.path.read_text()),changed)

    def test_second_watcher_is_rejected_before_polling(self):
        with (self.sync.state/'watcher.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.assertRaisesRegex(RuntimeError,'already running'):self.sync.watch()
        self.fetch.assert_not_called()

    def test_invalid_enabled_flag_does_not_fetch_or_change_policy(self):
        data=policy();data['review_restriction']['enabled']='false'
        self.path.write_text(json.dumps(data));original=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'boolean'):self.sync.sync()
        self.fetch.assert_not_called();self.assertEqual(self.path.read_bytes(),original)

    def test_existing_http_seller_observes_new_block_without_reload_and_anonymous_stays_allowed(self):
        access=BuyerAccess(self.path)
        with patch.object(seller,'BuyerAccess',return_value=access),actual_sdk_server() as state:
            params=send_params('lamp');params['message']['metadata']={'buyerName':'TEST_ONLY_LOW'}
            opened=result_task(rpc(state,'SendMessage',params))
            self.assertEqual(opened['status']['state'],'TASK_STATE_INPUT_REQUIRED')
            self.assertEqual(len(state.model_contexts),1)
            self.fetch.return_value=[review('TEST_ONLY_LOW',3)];self.sync.sync()
            params=send_params('100',task_id=opened['id']);params['message']['metadata']={'buyerName':'TEST_ONLY_LOW'}
            denied=result_task(rpc(state,'SendMessage',params))
            self.assertEqual(denied['status']['state'],'TASK_STATE_REJECTED')
            self.assertFalse(denied.get('artifacts'));self.assertEqual(len(state.model_contexts),1)
            anonymous=result_task(rpc(state,'SendMessage',send_params('lamp')))
            self.assertEqual(anonymous['status']['state'],'TASK_STATE_INPUT_REQUIRED')
            good=send_params('lamp');good['message']['metadata']={'buyerName':'TEST_ONLY_GOOD'}
            self.assertEqual(result_task(rpc(state,'SendMessage',good))['status']['state'],'TASK_STATE_INPUT_REQUIRED')
