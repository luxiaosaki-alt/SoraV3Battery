"""M1 探针：验证 Sora V3 电量协议，锁定正确的 HID collection。

同时探两个设备：4K 接收器（EB02，无线）和有线模式下的鼠标本体
（E010，充电时接收器不可见）。两者协议相同。
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import hid

VID = 0x093A
PIDS = {"EB02": 0xEB02,   # 接收器
        "E010": 0xE010}   # 有线本体
RID = 0x06
REQ = [0x06, 0x12, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
       0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

def hexs(bs):
    return " ".join("%02X" % b for b in bs)

for name, pid in PIDS.items():
    devs = hid.enumerate(VID, pid)
    print("== PID=%s (%s): %d collections" % (
        name, "接收器" if pid == 0xEB02 else "有线本体", len(devs)))
    found = []
    for info in devs:
        up, us = info.get("usage_page") or 0, info.get("usage") or 0
        tag = "iface=%s usage_page=0x%04X usage=0x%04X" % (
            info.get("interface_number"), up, us)
        if up < 0xFF00:   # 只探 vendor collection
            print("SKIP %s  非 vendor 页" % tag)
            continue
        d = hid.device()
        try:
            d.open_path(info["path"])
            d.send_feature_report(REQ)
            time.sleep(0.012)
            r = d.get_feature_report(RID, 16)
            print("OK   %s  resp=[%s]" % (tag, hexs(r)))
            pct = r[8] if len(r) > 8 else -1
            if 0 <= pct <= 100:
                found.append((tag, pct))
                print("     -> battery = %d%%" % pct)
        except Exception as e:
            print("SKIP %s  %s: %s" % (tag, type(e).__name__, e))
        finally:
            d.close()
    if found:
        for tag, pct in found:
            print("  VERIFIED %s -> %d%%" % (tag, pct))
    else:
        print("  NO VALID BATTERY RESPONSE")
    print()
