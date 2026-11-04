import unittest

from redesign import rules


class Rules(unittest.TestCase):
    def test_rule_book_is_valid(self):
        self.assertTrue(rules.load())

    def test_check_finds_problems(self):
        bad = [{"id": "X", "area": "a", "status": "maybe", "rule": "r", "source": "", "harness": ""},
               {"id": "X", "area": "a"}]
        p = rules.check(bad)
        self.assertTrue(any("status" in x for x in p))
        self.assertTrue(any("no source" in x for x in p))
        self.assertTrue(any("twice" in x for x in p))
        self.assertTrue(any("lacks" in x for x in p))


if __name__ == "__main__":
    unittest.main()
