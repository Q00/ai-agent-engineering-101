"""감독 스크립트의 검증·중단 판단 단위 테스트 (네트워크·실거래 없음)."""
import unittest
import buyer_ops2 as b


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
        r = b.segment(bd(110, 0.389), bd(400, 0.62), 500, 290, 0)        # 정산률 58%
        self.assertIn("정산률", b.gate_ok(r, [])[1])
        s = b.segment(bd(110, 0.389), bd(510, 0.60), 500, 400, 0)        # 거래당 Saved < 0.75
        self.assertIn("Saved", b.gate_ok(s, [])[1])
        self.assertFalse(b.gate_ok(g, ["buyer token refused"])[0])       # 인증 문제


if __name__ == "__main__":
    unittest.main(verbosity=2)
