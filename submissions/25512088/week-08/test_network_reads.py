import unittest
from urllib.error import URLError, HTTPError
from network_reads import retry_read

class ReadRetries(unittest.TestCase):
    def test_dns_recovery_without_mutating_request(self):
        calls=[];delays=[]
        def read():
            calls.append(1)
            if len(calls)<3:raise URLError('temporary DNS failure')
            return {'balance':123}
        self.assertEqual(retry_read(read,sleep=delays.append),{'balance':123})
        self.assertEqual(delays,[2,4])
    def test_auth_error_never_retried(self):
        def read():raise HTTPError('https://example.test',401,'auth',{},None)
        with self.assertRaises(HTTPError):retry_read(read,sleep=lambda _:self.fail('retried auth'))
    def test_exhaustion_keeps_error(self):
        def read():raise TimeoutError()
        with self.assertRaises(TimeoutError):retry_read(read,sleep=lambda _:None)
