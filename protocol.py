"""Sora V3 电量协议：常量与回包校验。

纯数据 + 纯逻辑、无 I/O，让协议层可以被单元测试覆盖
（tests/test_protocol.py 用实抓的原始包做样例）。
协议（逆向自 ClickSync）：HID feature report 0x06，命令 0x12，
回包第 8 字节为电量。
"""

VID = 0x093A                   # Ninjutso
PID = 0xEB02                   # 4K 接收器
WIRED_PID = 0xE010             # 有线模式下的鼠标本体（此时接收器不可见，见 README 踩坑记录 4）
RID = 0x06                     # 主通道 feature report ID
USAGE_PAGE, USAGE = 0xFF01, 0x0001  # 电量 collection 的 usage（MI_02 上有两个同值 collection）
REQ = [0x06, 0x12, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
       0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]


def validate_response(r):
    """校验电量回包，合法返回电量百分比（byte[8]），否则 None。

    回包必须回显 report ID（0x06）和命令字（0x12）。实测鼠标深睡后
    接收器应答「只回显 report ID、其余全零」的包（[06 00 00 ... 00]），
    byte[8] 也是 0——不校验回显就会把深睡当成真实的 0% 电量
    （2026-10-01 曾因此图标显示 0 并弹过假警报，见 README 踩坑记录 5）。
    """
    if len(r) > 8 and r[0] == RID and r[1] == REQ[1] and 0 <= r[8] <= 100:
        return r[8]
    return None
