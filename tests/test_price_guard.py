import unittest

from src.collector import MAX_PRICE_JUMP_RATIO, is_confirmed_jump, suspicious_jump

PID = "iphone-17-pro-max-512"


def row(price, ok=True, suspect=False, pid=PID):
    r = {"product_id": pid, "price": price, "ok": ok}
    if suspect:
        r["suspect"] = True
    return r


class SuspiciousJumpTest(unittest.TestCase):
    def test_no_previous(self):
        self.assertIsNone(suspicious_jump(None, 227000))
        self.assertIsNone(suspicious_jump(0, 227000))

    def test_normal_move(self):
        self.assertIsNone(suspicious_jump(227000, 230000))

    def test_ratio_boundary_is_allowed(self):
        self.assertIsNone(suspicious_jump(100000, int(100000 * MAX_PRICE_JUMP_RATIO)))
        self.assertIsNone(suspicious_jump(150000, 100000))

    def test_just_over_boundary(self):
        self.assertIsNotNone(suspicious_jump(100000, int(100000 * MAX_PRICE_JUMP_RATIO) + 1))
        self.assertIsNotNone(suspicious_jump(150001, 100000))

    def test_digit_drop_and_gain(self):
        """桁落ち・桁増えは範囲チェック(1,000〜1,000,000)を通っても検知する。"""
        self.assertIsNotNone(suspicious_jump(227000, 22700))
        self.assertIsNotNone(suspicious_jump(22700, 227000))


class ConfirmedJumpTest(unittest.TestCase):
    def test_first_occurrence_not_confirmed(self):
        self.assertFalse(is_confirmed_jump([row(227000)], PID, 22700))

    def test_same_suspect_value_twice_is_confirmed(self):
        self.assertTrue(is_confirmed_jump([row(227000), row(22700, ok=False, suspect=True)], PID, 22700))

    def test_different_value_not_confirmed(self):
        self.assertFalse(is_confirmed_jump([row(227000), row(22700, ok=False, suspect=True)], PID, 22800))

    def test_other_product_rows_ignored(self):
        history = [row(22700, ok=False, suspect=True), row(500, pid="other")]
        self.assertTrue(is_confirmed_jump(history, PID, 22700))

    def test_empty_history(self):
        self.assertFalse(is_confirmed_jump([], PID, 22700))


if __name__ == "__main__":
    unittest.main()
