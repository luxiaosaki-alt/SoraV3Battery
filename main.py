"""Sora V3 电量托盘指示器。

常驻 Windows 系统托盘，实时显示 Ninjutso Sora V3 鼠标电量百分比；
电量跌破阈值时弹 Windows 通知（去重逻辑见 alerts.py）。
协议（逆向自 ClickSync）：VID 093A / PID EB02（4K 接收器）或 PID E010
（有线模式下的鼠标本体，充电时接收器不可见），HID feature report 0x06，
命令 0x12，回包第 8 字节为电量。

用法：
    python main.py          # 常驻托盘
    python main.py --once   # 读一次电量打印后退出（最小检查）
"""
import os
import sys
import json
import time
import webbrowser
import threading
import logging
from logging.handlers import RotatingFileHandler

# 打包成 exe 时用 exe 所在目录（日志、vendor 都相对它）；源码运行时用脚本目录
if getattr(sys, "frozen", False):
    _HERE = os.path.dirname(sys.executable)
else:
    _HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(_HERE, "vendor")
if os.path.isdir(_VENDOR) and _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

import hid
import pystray
from PIL import Image, ImageDraw, ImageFont

from alerts import LowBatteryAlerter, alert_message
from protocol import (VID, PID, WIRED_PID, RID, USAGE_PAGE, USAGE, REQ,
                      validate_response)


def _patch_pystray_message_filter():
    """兼容性 shim。

    pystray 建窗口时调用 ChangeWindowMessageFilterEx 以捕获 explorer 重启
    (WM_TASKBARCREATED)。该调用是可选的，但在受限令牌的进程里会抛
    WinError 5（拒绝访问），导致托盘根本起不来。让它失败不致命即可。
    """
    try:
        from pystray._util import win32 as _win32

        _orig = _win32.ChangeWindowMessageFilterEx

        def _safe(*args, **kwargs):
            try:
                return _orig(*args, **kwargs)
            except Exception:
                return None

        _win32.ChangeWindowMessageFilterEx = _safe
    except Exception:
        pass


_patch_pystray_message_filter()

POLL_SEC = 30        # 正常刷新间隔
RETRY_SEC = 5        # 未连接时的探测间隔
DRIVER_URL = "https://ninjaforce.ninjutso.cn/customization"  # 官方驱动设置页
HIGH_WHITE = (0xFF, 0xFF, 0xFF)  # 白图标：配深色任务栏（默认）
HIGH_BLACK = (0x00, 0x00, 0x00)  # 黑图标：配浅色任务栏
MID = (0xF0, 0xC0, 0x40)         # 21-50% 黄：两种模式都一样
LOW = (0xE6, 0x40, 0x40)         # <=20% 红：两种模式都一样
GONE = (0x99, 0x99, 0x99)
CHARGING = (0x40, 0xC0, 0xF0)    # 充电中：青色，不随电量变化（红/黄是告警色，插着线不该报）

LOG_PATH = os.path.join(_HERE, "sora_v3_battery.log")
LOG_MAX_BYTES = 512 * 1024   # 单文件上限 512 KiB（约 3~4 天的轮询记录）
LOG_BACKUPS = 1              # 保留 1 个 .1 备份，日志总占用有界 ≤ 1 MiB
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[RotatingFileHandler(LOG_PATH, maxBytes=LOG_MAX_BYTES,
                                  backupCount=LOG_BACKUPS, encoding="utf-8")],
)

_GOOD_PATH = []   # 上次读成功的 (pid, path)，避免每次先试坏的那个
_NO_ECHO_RUN = [False]   # 是否正处于「应答无回显」状态（鼠标深睡/失联），只记状态切换日志
_LAST_NO_ECHO = [False]  # 最近一次 _try_read 是否为无回显应答，供 read_battery 提前定论


def candidate_paths():
    """VID 下所有电量 collection，按设备分组：{接收器 PID: [...], 有线本体 PID: [...]}。

    有线模式下接收器的 collection 会从 HID 枚举里消失，鼠标本体以
    PID E010 出现，电量协议相同（见 README 踩坑记录 4）。
    """
    groups = {PID: [], WIRED_PID: []}
    try:
        for info in hid.enumerate(VID):
            pid = info.get("product_id")
            if pid not in groups:
                continue
            if (info.get("usage_page") == USAGE_PAGE
                    and info.get("usage") == USAGE):
                groups[pid].append(info["path"])
    except Exception as e:
        logging.warning("enumerate failed: %s", e)
    return groups


def _try_read(path):
    """对单个 path 读一次电量；失败返回 None（绝不抛出）。

    校验：回包必须回显 report ID（0x06）和命令字（0x12）。实测鼠标
    深睡后接收器应答「只回显 report ID、其余全零」的包
    （[06 00 00 ... 00]），byte[8] 也是 0——不校验回显就会把深睡
    当成真实的 0% 电量（曾因此显示 0 并弹过假警报）。
    """
    d = hid.device()
    try:
        d.open_path(path)
        d.send_feature_report(REQ)
        time.sleep(0.012)
        pct = validate_response(d.get_feature_report(RID, 16))
        if pct is not None:
            _LAST_NO_ECHO[0] = False
            _NO_ECHO_RUN[0] = False
            return pct
        _LAST_NO_ECHO[0] = True
        if not _NO_ECHO_RUN[0]:
            # 原始包留档；只记零段第一条——深睡期 5s 一轮会刷爆轮转日志
            logging.info("no-echo response (device asleep / no link?)")
            _NO_ECHO_RUN[0] = True
        return None
    except Exception as e:
        _LAST_NO_ECHO[0] = False
        logging.info("read failed on %s: %s", path, e)
        return None
    finally:
        try:
            d.close()
        except Exception:
            pass


def read_battery():
    """读电量，返回 (pct, charging)；读不到返回 (None, False)。

    有线本体优先：它出现在枚举里时鼠标必然插着线（接收器此时读不到
    它），从本体读才能把「充电中」如实显示出来；本体不在则回落接收器。
    """
    groups = candidate_paths()
    for pid, charging in ((WIRED_PID, True), (PID, False)):
        paths = groups[pid]
        if _GOOD_PATH and _GOOD_PATH[0][0] == pid and _GOOD_PATH[0][1] in paths:
            paths = [_GOOD_PATH[0][1]] + [p for p in paths if p != _GOOD_PATH[0][1]]
        for p in paths:
            pct = _try_read(p)
            if pct is not None:
                if not _GOOD_PATH or _GOOD_PATH[0] != (pid, p):
                    _GOOD_PATH[:] = [(pid, p)]
                    logging.info("using path: %s (pid=%04X)", p, pid)
                return pct, charging
            if _LAST_NO_ECHO[0]:
                # 无回显 = 接收器那边没有活数据（鼠标深睡/失联），同一台
                # 接收器的另一个 collection 也给不出更多——直接定论，
                # 顺带省掉坏 collection 每轮一次的 read-error 日志。
                break
    return None, False


# --- 托盘图标渲染 -----------------------------------------------------------
# pystray 用 LoadImage(..., LR_DEFAULTSIZE) 载入图标，无论传入多大的图都会被
# 重采样到 SM_CXICON（本机 32x32）。所以按 32 渲染是唯一能省掉那次重采样的
# 尺寸：更大只会被压回去（两次廉价滤波叠加 = 糊），更小则先放大再缩小。
ICON_NATIVE = 32                  # pystray 最终得到的 HICON 边长
ICON_SS = 4                       # 内部超采样倍数，画完用 LANCZOS 缩回 ICON_NATIVE
ICON_CANVAS = ICON_NATIVE * ICON_SS
ICON_MARGIN = max(2, round(ICON_CANVAS * 4 / 256))   # 留给抗锯齿的边
ICON_BOX = ICON_CANVAS - 2 * ICON_MARGIN

#: 图标样式："battery" 画电池图形（默认），"digits" 直接画百分比数字。
ICON_STYLE = "battery"

#: 数字按长度分两档拟合：<=2 字符按最宽的两字符串定字号，"100" 单独一档。
#: 若全部按 "100" 拟合，常见的两位数会被压小。
_SIZE_REF_SHORT = tuple(str(n) for n in range(10, 100)) + ("--",)
_SIZE_REF_LONG = ("100",)

_SCRATCH_IMG = Image.new("RGBA", (8, 8))
_SCRATCH = ImageDraw.Draw(_SCRATCH_IMG)
_FONT_CACHE = {}
_SIZE_CACHE = {}


def _font_path():
    root = os.environ.get("WINDIR", "C:/Windows")
    for name in ("seguisb.ttf", "arialbd.ttf", "segoeui.ttf", "arial.ttf"):
        p = os.path.join(root, "Fonts", name)
        if os.path.exists(p):
            return p
    return None


def _font(size):
    if size not in _FONT_CACHE:
        path = _font_path()
        try:
            _FONT_CACHE[size] = ImageFont.truetype(path, size) if path else None
        except Exception:
            _FONT_CACHE[size] = None
    return _FONT_CACHE[size]


#: 图标颜色：False=白图标，True=黑图标。
#: 只影响 >50% 那一档，黄/红固定不变——颜色只留给告警。
_BLACK_ICON = [False]
#: 最近一次读到的电量。切换颜色要立刻重绘，不能等下一次轮询（最长 30 秒）。
_LAST_PCT = [-1]
#: 最近一次是否在充电（有线本体在线）。配合 _LAST_PCT 让重绘保留充电色。
_LAST_CHARGING = [False]
MODE_PATH = os.path.join(_HERE, "sora_v3_battery.mode")


def _load_mode():
    """读取上次记住的图标颜色；读不到就默认白图标。

    旧版本写的是 "dark"/"light"，一并以黑处理：升级不该把用户选的黑色图标
    悄悄改回白色 —— 那正是这个功能要防的情况。
    """
    try:
        with open(MODE_PATH, encoding="utf-8") as f:
            return f.read().strip() in ("black", "dark")
    except Exception:
        return False


def _save_mode():
    """记住选择。存不下最多是下次回到默认，不该影响运行。"""
    try:
        with open(MODE_PATH, "w", encoding="utf-8") as f:
            f.write("black" if _BLACK_ICON[0] else "white")
    except Exception:
        pass


_BLACK_ICON[0] = _load_mode()


def _color(pct, charging=False):
    if pct < 0:
        return GONE
    if charging:
        return CHARGING
    if pct <= 20:
        return LOW
    if pct <= 50:
        return MID
    return HIGH_BLACK if _BLACK_ICON[0] else HIGH_WHITE


def _ink(font, text, stroke):
    """串在给定字体/描边下的墨迹宽高。"""
    l, t, r, b = _SCRATCH.textbbox((0, 0), text, font=font, stroke_width=stroke)
    return r - l, b - t


def _fitted_size(stroke, candidates):
    """让 candidates 中最宽的串也能塞进 ICON_BOX 的最大字号。

    关键：字号按**一组候选串**拟合，而不是按当前这一个串 —— 否则 "5" 会被
    撑满、"100" 会被压小，电量每跳一格数字大小就变一次，看着很跳。
    """
    key = (stroke, candidates)
    if key in _SIZE_CACHE:
        return _SIZE_CACHE[key]
    path = _font_path()
    size = 8
    if path is not None:
        ref = ImageFont.truetype(path, 100)
        est = []
        for s in candidates:
            w, h = _ink(ref, s, 0)
            if w > 0 and h > 0:
                est.append(int(100 * min(ICON_BOX / w, ICON_BOX / h)))
        size = max(8, min(ICON_BOX, min(est, default=ICON_BOX)))
        # 每次回退只建一次字体对象：候选串有近百个，建在循环内会白白多建 90 倍
        while size > 8:
            font = ImageFont.truetype(path, size)
            if all(max(_ink(font, s, stroke)) <= ICON_BOX for s in candidates):
                break
            size -= 1
    _SIZE_CACHE[key] = size
    return size


def _outline_color(rgb):
    """给文字描边选颜色：亮字配黑边、暗字配白边，才能在任意任务栏底色上可读。"""
    r, g, b = rgb
    return (0, 0, 0) if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.5 else (255, 255, 255)


def _draw_text(dr, text, color):
    """画数字（或 -- 占位），按自身墨迹居中，带描边。"""
    stroke = max(2, round(ICON_BOX / 28))
    candidates = _SIZE_REF_LONG if len(text) >= 3 else _SIZE_REF_SHORT
    font = _font(_fitted_size(stroke, candidates))
    if font is None:
        return
    l, t, r, b = dr.textbbox((0, 0), text, font=font, stroke_width=stroke)
    dr.text((round((ICON_CANVAS - (r - l)) / 2 - l),
             round((ICON_CANVAS - (b - t)) / 2 - t)),
            text, font=font, fill=color, stroke_width=stroke,
            stroke_fill=_outline_color(color))


def _draw_battery(dr, pct, color):
    """画电池图形：圆角外壳 + 正极头 + 按电量比例填充。

    几何用 256 的设计基准等比缩到 ICON_CANVAS，改比例时只动这些常数。
    """
    k = ICON_CANVAS / 256.0
    stroke = round(16 * k)
    dr.rounded_rectangle(
        (round(16 * k), round(72 * k), round(224 * k), round(184 * k)),
        radius=round(20 * k), outline=color, width=stroke)
    dr.rounded_rectangle(
        (round(226 * k), round(100 * k), round(248 * k), round(156 * k)),
        radius=round(8 * k), fill=color)
    fw = round(160 * pct / 100.0 * k)
    if pct > 0 and fw > 0:
        dr.rounded_rectangle(
            (round(40 * k), round(96 * k), round(40 * k) + fw, round(96 * k) + round(64 * k)),
            radius=round(8 * k), fill=color)


def make_icon(pct, charging=False):
    """把电量画成托盘图标：超采样绘制后 LANCZOS 缩到 ICON_NATIVE。"""
    img = Image.new("RGBA", (ICON_CANVAS, ICON_CANVAS), (0, 0, 0, 0))
    dr = ImageDraw.Draw(img)
    color = _color(pct, charging)
    if pct < 0 or ICON_STYLE == "digits":
        # 未连接一律用文字占位：空电池会被误读成 0%
        _draw_text(dr, "--" if pct < 0 else str(pct), color)
    else:
        _draw_battery(dr, pct, color)
    return img.resize((ICON_NATIVE, ICON_NATIVE), Image.LANCZOS)


def title_for(pct, charging=False):
    if pct < 0:
        return "Sora V3: 未连接"
    if charging:
        return "Sora V3: 充电中 %d%%" % pct
    return "Sora V3: %d%%" % pct


def _mode_label(item):
    """菜单文字说的是「点了会变成哪样」，所以显示的是另一种颜色。"""
    return "用白色图标" if _BLACK_ICON[0] else "用黑色图标"


def _toggle_icon_mode(icon, item):
    """白/黑图标互切：立刻重绘，并刷新菜单文字。"""
    _BLACK_ICON[0] = not _BLACK_ICON[0]
    _save_mode()
    logging.info("icon colour -> %s", "黑图标" if _BLACK_ICON[0] else "白图标")
    try:
        icon.icon = make_icon(_LAST_PCT[0], _LAST_CHARGING[0])
        icon.update_menu()
    except Exception as e:
        logging.warning("mode toggle failed: %s", e)


def _open_driver(icon, item):
    """打开 ninjaforce 驱动设置页（托盘图标的默认项：单击/双击图标即触发）。

    pystray 的默认项在每条 WM_LBUTTONUP 都触发，而双击是两条
    WM_LBUTTONUP（中间夹一条它不处理的 DBLCLK），不去重会开出
    两个标签——用单调时钟做 0.7s 内去重（系统双击间隔上限 500ms）。
    """
    now = time.monotonic()
    if now - _LAST_OPEN[0] < 0.7:
        return
    _LAST_OPEN[0] = now
    try:
        webbrowser.open(DRIVER_URL)
        logging.info("driver page opened: %s", DRIVER_URL)
    except Exception as e:
        logging.warning("open driver url failed: %s", e)


# ---- 低电量提示的开关持久化 ------------------------------------------------

CONFIG_PATH = os.path.join(_HERE, "sora_v3_battery.json")
_DEFAULT_CFG = {"alerts_enabled": True, "alert_warn": 20, "alert_urgent": 10}


def load_config():
    """读配置；缺失/损坏/越界一律回落默认值，绝不影响主功能。"""
    cfg = dict(_DEFAULT_CFG)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return cfg
        if isinstance(raw.get("alerts_enabled"), bool):
            cfg["alerts_enabled"] = raw["alerts_enabled"]
        for key in ("alert_warn", "alert_urgent"):
            v = raw.get(key)
            if isinstance(v, int) and 0 <= v <= 100:
                cfg[key] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        logging.warning("config read failed: %s", e)
    return cfg


def save_config(cfg):
    """临时文件 + os.replace 原子写，避免半截 JSON。"""
    try:
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception as e:
        logging.warning("config write failed: %s", e)


#: 请求轮询线程立刻重读一次（菜单「刷新」和「退出」用）。
#: 不做成「菜单里自己读一遍」—— 那会和轮询线程同时开 HID 句柄抢设备。
_WAKE = threading.Event()
#: 上次打开驱动页的时刻（monotonic 秒）。双击的两条 WM_LBUTTONUP 去重用。
_LAST_OPEN = [0.0]


def poll_loop(icon, stop_evt, alerter, alert_cfg):
    """setup 线程：先显示图标，再轮询电量刷新 + 低电量判断。"""
    # 必须显式显示：pystray 只在 *没有* setup 回调时才自动 visible=True
    icon.visible = True
    logging.info("poll loop started")
    while not stop_evt.is_set():
        # 进本轮先清唤醒标记：读取过程中来的「刷新」才会被保住，
        # 让下面那次 wait 立刻返回、紧接着再读一遍。
        _WAKE.clear()
        pct, charging = read_battery()
        _LAST_PCT[0] = -1 if pct is None else pct
        _LAST_CHARGING[0] = bool(charging and pct is not None)
        logging.info("state: %s", "disconnected" if pct is None
                     else "%d%%%s" % (pct, " charging" if charging else ""))
        try:
            icon.icon = make_icon(_LAST_PCT[0], _LAST_CHARGING[0])
            icon.title = title_for(pct if pct is not None else -1, _LAST_CHARGING[0])
        except Exception as e:
            logging.warning("icon update failed: %s", e)
        # 充电中不告警：插着线弹「请立即充电」是误报；回升会自然越过
        # 迟滞线重新武装，拔线后若仍在低电区会在下一个读数补报
        if pct is not None and not charging and alert_cfg["enabled"]:
            stage = alerter.update(pct)
            if stage is not None:
                title, body = alert_message(stage, pct, alerter.urgent_stage)
                logging.info("low battery alert: stage=%d pct=%d", stage, pct)
                try:
                    # 气泡在 Win10/11 上渲染为系统 toast，同 Shell_NotifyIcon
                    # 路径，与上面跨线程刷新图标一致，可安全调用
                    icon.notify(body, title)
                except Exception as e:
                    logging.warning("notify failed: %s", e)
        # 正常情况在此超时；被 set 说明点了「刷新」（马上重读）
        # 或「退出」（下一轮开头就会跳出循环）
        _WAKE.wait(RETRY_SEC if pct is None else POLL_SEC)
    logging.info("poll loop stopped")


_MUTEX = []


def _acquire_single_instance():
    """命名互斥体守卫：True=首个实例可运行，False=已有实例。

    没有这个守卫时重复启动会出现多个托盘图标。
    拿不到互斥体（异常）时一律放行，绝不因守卫本身挡住程序。
    """
    try:
        import ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k.CreateMutexW(None, False, "Local\\SoraV3BatteryTray")
        err = ctypes.get_last_error()
        if not h:
            return True
        if err == 183:          # ERROR_ALREADY_EXISTS
            return False
        _MUTEX.append(h)        # 持有句柄，进程存活期间互斥体一直有效
        return True
    except Exception:
        return True


def _say(msg):
    """日志 + 尽力打印（--windowed 下 sys.stdout 为 None，不能直接 print）。"""
    logging.info(msg)
    try:
        if sys.stdout is not None:
            print(msg)
    except Exception:
        pass


def run_once():
    groups = candidate_paths()
    if not groups[PID] and not groups[WIRED_PID]:
        _say("DISCONNECTED: no Sora V3 (VID %04X PID %04X/%04X)"
             % (VID, PID, WIRED_PID))
        return 1
    pct, charging = read_battery()
    if pct is None:
        _say("NO_RESPONSE: device present but no battery returned")
        return 1
    _say("BATTERY %d%%%s" % (pct, " CHARGING" if charging else ""))
    return 0


def main():
    if "--once" in sys.argv:
        return run_once()
    if not _acquire_single_instance():
        # 计划任务的保活触发每 2 分钟调用一次本程序；已在运行时静默退出，
        # 用 debug 级别避免日志被这条正常路径刷屏。
        logging.debug("another instance is already running; exiting")
        return 0
    cfg = load_config()
    alert_cfg = {"enabled": cfg["alerts_enabled"]}
    alerter = LowBatteryAlerter(stages=(cfg["alert_warn"], cfg["alert_urgent"]))

    def _toggle_alerts(icon, item):
        alert_cfg["enabled"] = not alert_cfg["enabled"]
        cfg.update(alerts_enabled=alert_cfg["enabled"])
        save_config(cfg)
        logging.info("alerts toggled: %s", alert_cfg["enabled"])

    def _test_notify(icon, item):
        try:
            icon.notify("这是测试通知。低电量提示触发时你会看到同样的气泡。",
                        "Sora V3 电量提示（测试）")
        except Exception as e:
            logging.warning("test notify failed: %s", e)

    stop_evt = threading.Event()
    icon = pystray.Icon("sora_v3_battery", make_icon(-1), title_for(-1))
    icon.menu = pystray.Menu(
        pystray.MenuItem("低电量提示", _toggle_alerts,
                         checked=lambda item: alert_cfg["enabled"]),
        pystray.MenuItem("测试通知", _test_notify),
        pystray.MenuItem("打开驱动设置", _open_driver, default=True),
        pystray.MenuItem("刷新", lambda icon, item: _WAKE.set()),
        pystray.MenuItem(_mode_label, _toggle_icon_mode),
        pystray.MenuItem("退出",
                         lambda icon, item: (stop_evt.set(), _WAKE.set(), icon.stop())),
    )
    logging.info("app starting")
    icon.run(setup=lambda ic: poll_loop(ic, stop_evt, alerter, alert_cfg))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # windowed 模式下 stderr 为 None，异常会静默消失——必须落盘
        logging.exception("fatal error")
        raise
