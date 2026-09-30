"""M1 探针：验证 Sora V3 电量协议，锁定正确的 HID collection。"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import hid

VID, PID = 0x093A, 0xEB02
RID = 0x06
REQ = [0x06, 0x12, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
       0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

def hexs(bs):
    return " ".join("%02X" % b for b in bs)

devs = hid.enumerate(VID, PID)
print("enumerated", len(devs), "HID collections for VID=%04X PID=%04X" % (VID, PID))
print()
found = []
for info in devs:
    tag = "iface=%s usage_page=0x%04X usage=0x%04X" % (
        info.get("interface_number"), info.get("usage_page") or 0, info.get("usage") or 0)
    d = hid.device()
    try:
        d.open_path(info["path"])
        d.send_feature_report(REQ)
        time.sleep(0.012)
        r = d.get_feature_report(RID, 16)
        print("OK   %s  resp=[%s]" % (tag, hexs(r)))
        pct = r[8] if len(r) > 8 else -1
        if 0 <= pct <= 100:
            found.append((tag, pct, list(r)))
            print("     -> battery = %d%%" % pct)
    except Exception as e:
        print("SKIP %s  %s: %s" % (tag, type(e).__name__, e))
    finally:
        d.close()
print()
if found:
    print("VERIFIED candidates:", len(found))
    for tag, pct, raw in found:
        print("  %s -> %d%%  bytes[8..15]=%s" % (tag, pct, hexs(raw[8:])))
else:
    print("NO VALID BATTERY RESPONSE")