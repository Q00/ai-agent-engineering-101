"""Synthetic reviews only; network calls are mocked under the guarded runner."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import seller_reputation as reputation


NOW = 1_800_000_000


def review(stars=5, login='AccountA', **extra):
    return dict(stars=stars, owner={'login': login}, author='synthetic-agent',
                review='Synthetic positive feedback.', ts=NOW, **extra)


class ReputationTests(unittest.TestCase):
    def analyze(self, rows, **kwargs):
        return reputation.analyze_snapshot(rows, shop='synthetic', round_number=3,
                                           fetched_at=NOW, now=NOW, **kwargs)

    def test_account_balancing_not_raw_mean(self):
        result = self.analyze([review() for _ in range(3)] + [review(1, 'B')])
        self.assertEqual(result['raw_review_mean'], 4)
        self.assertEqual(result['observed_account_mean'], 3)
        self.assertEqual(result['accounts']['accounta']['n'], 3)
        self.assertEqual(result['accounts']['b']['low_count'], 1)

    def test_unknown_accounts_excluded_not_pooled_and_author_not_identity(self):
        rows = [review(5), review(1, ''), review(1, None)]
        rows[-1].pop('owner')
        result = self.analyze(rows)
        self.assertAlmostEqual(result['raw_review_mean'], 7 / 3)
        self.assertEqual(result['observed_account_mean'], 5)
        self.assertEqual(result['data_quality']['unknown_account_valid_ratings'], 2)
        self.assertEqual(len(result['accounts']), 1)

    def test_owner_normalized_across_different_agents(self):
        rows = [review(5, ' AccountA '), review(1, 'ACCOUNTA')]
        rows[1]['author'] = 'other-synthetic-agent'
        self.assertEqual(self.analyze(rows)['accounts']['accounta']['mean'], 3)

    def test_invalid_stars_records_and_times(self):
        rows = [review(stars) for stars in (True, float('nan'), float('inf'), 0, 6, '5', None)]
        rows += [None, [], {'stars': 3, 'ts': 'bad', 'review': 7}]
        result = self.analyze(rows)
        self.assertEqual(result['valid_rating_count'], 1)
        self.assertEqual(result['data_quality']['invalid_stars'], 7)
        self.assertEqual(result['data_quality']['invalid_record'], 2)
        self.assertEqual(result['data_quality']['invalid_time'], 1)
        self.assertEqual(result['data_quality']['invalid_review_text'], 1)

    def test_scope_mismatches_rejected_even_with_bad_stars(self):
        for extra in ({'shop': 'wrong'}, {'round': 2}, {'round': True}, {'round': 3.0}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.analyze([review(0, **extra)])
        self.assertIn('not per-record verified', self.analyze([review()])['source']['source_scope'])
        self.assertEqual(self.analyze([review(shop='synthetic', round=3)])['source']['source_scope'],
                         'per-record shop and round verified')

    def test_no_dedup_no_merge_no_mutation(self):
        rows = [review(), review()]
        before = copy.deepcopy(rows)
        result = self.analyze(rows)
        self.assertEqual(result['valid_rating_count'], 2)
        self.assertEqual(result['data_quality']['possible_duplicate_records'], 1)
        self.assertEqual(rows, before)
        self.assertEqual(self.analyze([review(1)])['raw_review_mean'], 1)
        self.assertFalse(result['source']['whole_history'])
        self.assertTrue(result['source']['not_official_rank'])

    def test_empty_unknown_only_and_invalid_only(self):
        for rows in ([], [None], [review(1, '')]):
            self.assertIsNone(self.analyze(rows)['observed_account_mean'])
        self.assertIsNone(self.analyze([])['raw_review_mean'])

    def test_generic_and_negated_low_ratings_remain_unknown(self):
        for text in ('Payment settled; no unobserved quality or failure claims.',
                     'Bad experience.', 'Great purchase.', 'Not broken; no late delivery.'):
            row = review(1)
            row['review'] = text
            tags = self.analyze([row], cues=True)['low_rating_diagnostics']['tags']
            self.assertEqual(tags['unknown']['count'], 1)
            self.assertEqual(sum(tag['count'] for tag in tags.values()), 1)

    def test_optional_cues_are_bounded_unverified(self):
        row = review(1)
        row['review'] = 'defective ' + 'x' * 500
        self.assertEqual(self.analyze([row])['low_rating_diagnostics']['tags']['unknown']['count'], 1)
        diag = self.analyze([row] * 5, cues=True)['low_rating_diagnostics']
        self.assertIn('unverified', diag['meaning'])
        self.assertEqual(diag['tags']['quality']['count'], 5)
        self.assertEqual(len(diag['tags']['quality']['examples']), 3)
        self.assertEqual(len(diag['tags']['quality']['examples'][0]), reputation.SNIPPET_CHARS)

    def test_timestamps_and_deterministic_freshness(self):
        rows = [review(), review()]
        rows[0]['ts'] = '2027-01-15T08:00:00Z'
        rows[1]['ts'] = NOW - 10
        result = self.analyze(rows)
        self.assertEqual(result['source']['age_seconds'], 0)
        self.assertEqual(result['source']['freshness'], 'fresh')
        self.assertIsNotNone(result['accounts']['accounta']['last_ts'])
        for age, expected in ((3601, 'stale'), (-1, 'future')):
            result = reputation.analyze_snapshot([], shop='synthetic', round_number=3,
                                                  fetched_at=NOW, now=NOW + age)
            self.assertEqual(result['source']['freshness'], expected)
        for value in (True, '2026-01-01', float('nan'), {}, 10**100):
            self.assertIsNone(reputation.timestamp(value))

    def test_input_validation(self):
        with self.assertRaises(ValueError):
            self.analyze({})
        with self.assertRaises(ValueError):
            reputation.analyze_snapshot([], shop='', round_number=3, fetched_at=NOW, now=NOW)

    def test_extreme_numbers_count_as_invalid_without_overflow(self):
        row = review(10**400)
        row['ts'] = 10**400
        quality = self.analyze([row])['data_quality']
        self.assertEqual(quality['invalid_stars'], 1)
        self.assertEqual(quality['invalid_time'], 1)

    def test_each_optional_text_cue_remains_unverified(self):
        for tag, text in (('price', 'too expensive'), ('quality', 'broken'),
                          ('warranty', 'warranty denied'), ('delay', 'delivery delayed')):
            row = review(2)
            row['review'] = text
            self.assertEqual(self.analyze([row], cues=True)['low_rating_diagnostics']['tags'][tag]['count'], 1)

    def fetch(self, payload=b'[]', url=None):
        response = Mock()
        response.geturl.return_value = url or reputation.ENDPOINT + '?shop=synthetic&round=3'
        response.read.return_value = payload
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        return opener, response

    def test_fetch_mock_timeout_limit_and_query_encoding(self):
        url = reputation.ENDPOINT + '?shop=a%26b&round=3'
        opener, response = self.fetch(url=url)
        with patch.object(reputation.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(reputation.fetch_reviews('a&b', 3), [])
        opener.open.assert_called_once_with(url, timeout=reputation.TIMEOUT_SECONDS)
        response.read.assert_called_once_with(reputation.MAX_BYTES + 1)
        self.assertIsInstance(build.call_args.args[0], reputation.NoRedirect)
        opener.open.side_effect = TimeoutError('synthetic timeout')
        with patch.object(reputation.urllib.request, 'build_opener', return_value=opener):
            with self.assertRaises(TimeoutError):
                reputation.fetch_reviews('synthetic', 3)

    def test_fetch_rejects_large_invalid_json_shape_and_url(self):
        for payload, url, exception in (
                (b'x' * (reputation.MAX_BYTES + 1), None, ValueError),
                (b'not JSON', None, ValueError), (b'{}', None, ValueError),
                (b'[]', 'https://untrusted.invalid/', ValueError)):
            opener, _ = self.fetch(payload, url)
            with patch.object(reputation.urllib.request, 'build_opener', return_value=opener):
                with self.assertRaises(exception):
                    reputation.fetch_reviews('synthetic', 3)

    def test_redirects_rejected_including_same_origin(self):
        for url in (reputation.ENDPOINT, 'http://market.ourolabs.ai/', 'https://untrusted.invalid/'):
            with self.assertRaises(ValueError):
                reputation.NoRedirect().redirect_request(None, None, 302, '', {}, url)

    def test_output_exclusive_success_and_failure_preserve_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.json'
            reputation.write_exclusive(path, {'synthetic': True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                reputation.write_exclusive(path, {'replace': True})
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(ValueError):
                reputation.write_exclusive(Path(tmp) / 'invalid.json', {'bad': float('nan')})
            self.assertFalse((Path(tmp) / 'invalid.json').exists())

    def test_cli_offline_metadata_and_no_implicit_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic.json'
            fixture = dict(shop='synthetic', round=3, fetched_at=NOW, reviews=[review()])
            path.write_text(json.dumps(fixture), encoding='utf-8')
            args = ['--input', str(path), '--shop', 'synthetic', '--round', '3']
            with patch.object(reputation, 'fetch_reviews', side_effect=AssertionError('no network')):
                with contextlib.redirect_stdout(io.StringIO()) as stdout:
                    self.assertEqual(reputation.main(args), 0)
                result = json.loads(stdout.getvalue())
                self.assertIn('offline', result['source']['mode'])
                for field, value in (('shop', 'wrong'), ('round', True)):
                    bad = dict(fixture, **{field: value})
                    path.write_text(json.dumps(bad), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        reputation.main(args)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    reputation.main(['--shop', 'synthetic', '--round', '3'])

    def test_cli_explicit_fetch_mock_and_exclusive_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'synthetic-output.json'
            args = ['--fetch', '--shop', 'synthetic', '--round', '3', '--output', str(path)]
            with patch.object(reputation, 'fetch_reviews', return_value=[review()]) as fetch:
                self.assertEqual(reputation.main(args), 0)
                fetch.assert_called_once_with('synthetic', 3)
                original = path.read_bytes()
                self.assertEqual(json.loads(original)['source']['mode'], 'explicit manual GET')
                with self.assertRaises(FileExistsError):
                    reputation.main(args)
                self.assertEqual(path.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
