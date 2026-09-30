"""alerts.py 的逻辑测试。

不依赖 hid/pystray，任何装了 Python 的机器都能跑：

    python tests/test_alert.py          # 直接运行
    python -m unittest discover -s tests -t .   # unittest 发现模式
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alerts import LowBatteryAlerter, alert_message


T0 = 1000.0


class TestLowBatteryAlerter(unittest.TestCase):
    def test_first_drop_below_threshold_fires(self):
        a = LowBatteryAlerter()
        self.assertIsNone(a.update(21, T0))
        self.assertEqual(a.update(20, T0 + 1), 20)

    def test_no_repeat_while_below(self):
        a = LowBatteryAlerter()
        a.update(19, T0)
        for i in range(100):          # 持续低于阈值 100 个轮询周期只报一次
            self.assertIsNone(a.update(19 - i % 5, T0 + 30 * (i + 1)))

    def test_rearm_after_hysteresis(self):
        a = LowBatteryAlerter()
        a.update(19, T0)
        self.assertIsNone(a.update(22, T0 + 600))    # 未越过迟滞线，不复位
        self.assertIsNone(a.update(26, T0 + 3600))   # >= 20+5，重新武装
        self.assertEqual(a.update(18, T0 + 7200), 20)  # 距上次提示 > 冷却期

    def test_jump_to_deep_fires_deepest_only(self):
        a = LowBatteryAlerter()
        self.assertEqual(a.update(8, T0), 10)      # 30% → 8% 只报 10% 级
        self.assertEqual(alert_message(10, 8, a.urgent_stage)[0],
                         "Sora V3 电量严重不足：8%")

    def test_deep_then_shallow_recovery(self):
        a = LowBatteryAlerter()
        a.update(9, T0)                              # 报了 10% 级
        self.assertIsNone(a.update(17, T0 + 600))    # 15≤17<25：两级都未复位
        self.assertEqual(a.update(26, T0 + 3600), None)  # 全部重新武装
        self.assertEqual(a.update(19, T0 + 7200), 20)

    def test_cooldown_defers_not_drops(self):
        a = LowBatteryAlerter()
        self.assertEqual(a.update(19, T0), 20)          # 第一次提示
        self.assertIsNone(a.update(9, T0 + 600))        # 冷却期内跌破更深一级：推迟
        self.assertEqual(a.update(9, T0 + 1810), 10)    # 冷却结束后补报最深级

    def test_disconnect_noop_and_no_realert_on_reconnect(self):
        a = LowBatteryAlerter()
        a.update(19, T0)
        self.assertIsNone(a.update(None, T0 + 30))
        self.assertIsNone(a.update(18, T0 + 7200))  # 重连不重复提示（已报标记仍在）

    def test_start_low_fires_immediately(self):
        a = LowBatteryAlerter()
        self.assertEqual(a.update(5, T0), 10)      # 程序启动时就在低位 → 立即报

    def test_config_high_battery_never_fires(self):
        a = LowBatteryAlerter()
        for p in (100, 80, 55, 51):
            self.assertIsNone(a.update(p, T0))

    def test_zero_pct_treated_as_low(self):
        a = LowBatteryAlerter()
        self.assertEqual(a.update(0, T0), 10)

    def test_alert_message_wording(self):
        self.assertEqual(alert_message(20, 20, 10),
                         ("Sora V3 电量低：20%", "请尽快充电。"))
        self.assertEqual(alert_message(10, 9, 10),
                         ("Sora V3 电量严重不足：9%", "电量即将耗尽，请立即充电。"))

    def test_single_stage(self):
        a = LowBatteryAlerter(stages=(15,))
        self.assertEqual(a.update(15, T0), 15)
        self.assertIsNone(a.update(14, T0 + 30))
        self.assertEqual(a.update(21, T0 + 3600), None)  # 21>=15+5，重新武装
        self.assertEqual(a.update(10, T0 + 7200), 15)


if __name__ == "__main__":
    unittest.main(verbosity=2)
