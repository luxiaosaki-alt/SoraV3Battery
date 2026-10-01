"""protocol.validate_response 的测试。

样例全部来自实抓的原始包（生产日志与探针留档，见 README 踩坑记录 5），
不依赖 hid/pystray，任何装了 Python 的机器都能跑：

    python tests/test_protocol.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from protocol import validate_response

# 接收器（EB02）awake 包：回显 0x06 0x12，byte[8]=0x5A=90
AWAKE_RX = [0x06, 0x12, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00,
            0x5A, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00]
# 有线本体（E010）awake 包：9 字节短包，byte[8]=0x47=71
AWAKE_WIRED = [0x06, 0x12, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x47]
# 深睡包：只回显 report ID，其余全零（2026-10-01 探针实抓）
ASLEEP = [0x06] + [0x00] * 15


class TestValidateResponse(unittest.TestCase):
    def test_awake_receiver_packet(self):
        self.assertEqual(validate_response(AWAKE_RX), 90)

    def test_awake_wired_short_packet(self):
        self.assertEqual(validate_response(AWAKE_WIRED), 71)

    def test_deep_sleep_packet_rejected(self):
        self.assertIsNone(validate_response(ASLEEP))

    def test_all_zero_without_report_id(self):
        self.assertIsNone(validate_response([0x00] * 16))

    def test_truncated_packet(self):
        self.assertIsNone(validate_response(AWAKE_RX[:8]))

    def test_empty_packet(self):
        self.assertIsNone(validate_response([]))

    def test_byte8_out_of_range_rejected(self):
        r = list(AWAKE_RX)
        r[8] = 0xFF
        self.assertIsNone(validate_response(r))

    def test_real_zero_with_echo_accepted(self):
        # 真 0% 电量的包有回显——必须如实放行，这正是回显校验
        # 优于「屏蔽所有 0」的地方
        r = list(AWAKE_RX)
        r[8] = 0x00
        self.assertEqual(validate_response(r), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
