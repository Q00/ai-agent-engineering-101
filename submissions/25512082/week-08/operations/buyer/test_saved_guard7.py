"""saved_guard7 판단 함수 단위 테스트 (네트워크·프로세스 조작 없음)."""
import unittest
import saved_guard7 as g


class T(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(g.decide(0.7918, 0.7881)[0], "ok")

    def test_warn(self):
        self.assertEqual(g.decide(0.7900, 0.7881)[0], "warn")
        self.assertEqual(g.decide(0.7895, 0.7860)[0], "warn")

    def test_stop_level(self):
        self.assertEqual(g.decide(0.7885, 0.7800)[0], "stop")
        self.assertEqual(g.decide(0.7880, None)[0], "stop")

    def test_stop_gap(self):
        self.assertEqual(g.decide(0.7918, 0.7914)[0], "stop")   # 차이 0.0004
        self.assertEqual(g.decide(0.7918, 0.7913)[0], "ok")     # 차이 0.0005

    def test_float_gap(self):
        self.assertEqual(g.decide(0.7901, 0.7897)[0], "stop")   # 실측 03:16 (부동소수점 0.00040000000000007)

    def test_no_lower(self):
        self.assertEqual(g.decide(0.7918, None)[0], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
