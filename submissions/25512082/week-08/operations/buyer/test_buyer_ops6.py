"""감독 스크립트의 검증·중단 판단 단위 테스트 (네트워크·실거래 없음)."""
import unittest
import buyer_ops6 as b


def bd(deals, saved): return {"deals": deals, "saved": saved}


class T(unittest.TestCase):
    def test_segment_matches_test_run(self):
        # 실측 100주문 테스트: 23건/0.0977 -> 110건/0.389, 결제 87, 파손 1
        s = b.segment(bd(23, 0.0977), bd(110, 0.389), 100, 87, 1)
        self.assertEqual(s["deals_board"], 87)
        self.assertAlmostEqual(s["saved_per_deal"], 0.801, places=2)
        self.assertAlmostEqual(s["settle_rate"], 0.87)

    def test_block_continue_and_stops(self):
        good = b.segment(bd(110, 0.389), bd(190, 0.47), 100, 80, 0)
        self.assertTrue(b.block_ok(good)[0])
        low = b.segment(bd(110, 0.389), bd(190, 0.40), 100, 80, 0)       # 구간 Saved 낮음
        self.assertFalse(b.block_ok(low)[0])
        lag = b.segment(bd(110, 0.389), bd(150, 0.42), 100, 80, 0)       # board 미반영
        self.assertIn("검증 불가", b.block_ok(lag)[1])
        bad = b.segment(bd(110, 0.389), bd(190, 0.47), 100, 80, 3)       # 파손 3/80 > 2%
        self.assertFalse(b.block_ok(bad)[0])
        zero = b.segment(bd(110, 0.389), bd(110, 0.389), 100, 0, 0)
        self.assertFalse(b.block_ok(zero)[0])

    def test_gate(self):
        g = b.segment(bd(110, 0.389), bd(510, 0.70), 500, 400, 2)        # 80%, 거래당 약 0.77, 파손 0.5%
        self.assertTrue(b.gate_ok(g, [])[0], b.gate_ok(g, []))
        r = b.segment(bd(110, 0.389), bd(400, 0.62), 500, 290, 0)        # 정산률 58% (<65%)
        self.assertIn("정산률", b.gate_ok(r, [])[1])
        s = b.segment(bd(110, 0.389), bd(510, 0.60), 500, 400, 0)        # 거래당 Saved < 0.75
        self.assertIn("Saved", b.gate_ok(s, [])[1])
        self.assertFalse(b.gate_ok(g, ["buyer token refused"])[0])       # 인증 문제


class P(unittest.TestCase):
    def test_rate_65(self):
        s = b.segment(bd(375, 0.6329), bd(443, 0.6604), 100, 68, 0)     # 실측 첫 100주문: 68%, 0.852
        self.assertTrue(b.gate_ok(s, [])[0])
    def test_402_rule(self):
        L = ["x [acp] payment not completed: 402 {}"]
        self.assertEqual(b.scan_problems(L, 0)[0], [])                   # 1건은 문제 아님
        self.assertEqual(b.scan_problems(L * 2 + ["y [acp] paid 100"] + L, 0)[1], 1)  # 성공 시 초기화
        self.assertTrue(b.scan_problems(L * 3, 0)[0])                    # 연속 3건은 문제
        self.assertTrue(b.scan_problems(L, 2)[0])                        # 블록 경계 넘어 누적
        self.assertTrue(b.scan_problems(["z buyer token refused"], 0)[0])  # 인증 실패는 즉시


class R(unittest.TestCase):
    def test_refund_not_counted_minus1_counted(self):
        s = b.segment(bd(661, 0.714), bd(730, 0.73), 100, 69, 0, 2)    # 환불 2건, −1 0건 → 통과
        self.assertTrue(b.block_ok(s)[0]); self.assertEqual(s["refunds"], 2)
        s = b.segment(bd(661, 0.714), bd(730, 0.73), 100, 69, 2, 0)    # −1 2건(2.9%) → 중단
        self.assertIn("−1", b.block_ok(s)[1])


class V(unittest.TestCase):
    def test_board_lag(self):
        ok = b.segment(bd(1816, 0.7899), bd(1895, 0.7915), 100, 78, 0, 1)   # 실측: board 79 vs 로컬 78
        self.assertTrue(b.block_ok(ok)[0])
        miss = b.segment(bd(1816, 0.7899), bd(1890, 0.7915), 100, 78, 0, 0)  # board 74 < 로컬 78 → 중단
        self.assertIn("검증 불가", b.block_ok(miss)[1])
        big = b.segment(bd(1816, 0.7899), bd(1898, 0.7915), 100, 78, 0, 0)   # board +4 → 중단
        self.assertFalse(b.block_ok(big)[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
