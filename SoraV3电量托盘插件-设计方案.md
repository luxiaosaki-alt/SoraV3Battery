# Ninjutso Sora V3 鼠标电量 · 任务栏托盘插件 —— 技术设计方案

> 版本：v1（调研 + 设计）
> 目标设备：Ninjutso **Sora V3**（4K 接收器，PixArt PAW3950 平台）
> 结论：协议已被开源项目完整逆向，无需再抓包；方案为「自研一个 ~150 行独立 Windows 托盘程序」，而非引入或修改现有项目。

---

## 0. 结论速览（TL;DR）

1. **现有开源项目已存在，但都覆盖不到你的鼠标。** 最活跃的通用方案 [Fan4Metal/mouse_tray](https://github.com/Fan4Metal/mouse_tray) 只支持旧款 Sora V2（VID 1915）；C# 的 [SoraV2-Battery-Meter](https://github.com/shroudedhorizon/SoraV2-Battery-Meter) 同样只支持 Sora V2。你的 Sora V3 是 PixArt 4K 接收器（VID 093A），两者都不识别。
2. **电量协议已被完整逆向**：[Nuitfanee/ClickSync](https://github.com/Nuitfanee/ClickSync)（GPL-2.0）里的 protocol_api_ninjutso.js 正是 Sora V3 的协议，VID/PID 与你的设备**实测完全吻合**。核心就是「发一个 16 字节 HID feature report，回包第 8 字节就是电量百分比」。
3. **落地形态**：一个独立 Windows **系统托盘**程序（时钟旁），不是浏览器插件，也不是字面意义的任务栏窗口；后台每 30s 直连鼠标读一次电量，网页关不关都行。

---

## 1. 目标与验收标准

**目标**：常驻系统托盘，实时显示 Sora V3 电量百分比，无需打开 ninjaforce 网页。

**验收标准（可观测）**：
- [ ] 托盘图标显示当前电量百分比，且与 ninjaforce 网页显示的值一致（±1%）。
- [ ] 30 秒内自动刷新（网页不动也能变）。
- [ ] 鼠标/接收器拔掉后，图标进入「未连接」状态，不崩溃、不报错。
- [ ] 重新插入后自动恢复显示。
- [ ] 开机自动启动，无需手动点开。

---

## 2. 调研结论

### 2.1 GitHub 现有项目

| 项目 | 语言 | 说明 | 能否直接用 |
|---|---|---|---|
| [Fan4Metal/mouse_tray](https://github.com/Fan4Metal/mouse_tray) | Python | 通用多品牌鼠标电量托盘，架构好、活跃维护；Ninjutso 驱动只含 **Sora V2**（VID 1915，report ID 5） | 否：无 Sora V3；无许可证 |
| [Nuitfanee/ClickSync](https://github.com/Nuitfanee/ClickSync) | JS/WebHID | 多品牌鼠标网页驱动，**含 Sora V3 完整协议**（VID 093A，report ID 6） | 是：协议来源（GPL-2.0，只复用协议事实，不复制代码） |
| [shroudedhorizon/SoraV2-Battery-Meter](https://github.com/shroudedhorizon/SoraV2-Battery-Meter) | C# | 仅 Sora V2，有现成 exe，2024 停更 | 否：VID 1915，不识别 Sora V3 |

### 2.2 许可证结论

- mouse_tray：**无许可证**（GitHub 默认「保留所有权利」）→ 不能复制/再分发其代码，只能借鉴架构思想。
- ClickSync：GPL-2.0 → 逆向出的**协议字节序列本身不受版权保护**，可自由重实现；不复制其源码即无合规负担。
- 结论：**自研一个小实现**最干净，既规避许可证问题，又避免为「读一个字节」引入整个项目。

---

## 3. 硬件与协议事实

### 3.1 设备识别（本机实测）

本机注册表枚举到的 Ninjutso 设备：

    USB\VID_093A&PID_EB02  "Ninjutso_Boot_Mouse"  (USB 复合设备)
      ├─ MI_00
      ├─ MI_01
      └─ MI_02   （含 7 个 HID collection：Col01…Col07）

- **VID = 0x093A**（PixArt），**PID = 0xEB02**，与 ClickSync 的 HID_CONST **完全一致** → 确认是 Sora V3 4K 接收器。
- 全机无 VID 1915（Sora V2）→ 排除旧款。
- 电量走的 feature report 在 MI_02 的某个 vendor collection 上（usage_page 待实机确认，见 §8）。

### 3.2 Sora V3 电量协议（字节级，源自 ClickSync 逆向）

| 项目 | 值 |
|---|---|
| VID / PID | 0x093A / 0xEB02 |
| 主通道 feature report ID | 0x06，包长 15 字节 |
| 安全通道 report ID | 0x03，63 字节（仅写命令/握手用，读电量不需要） |
| 电量读命令 opcode | BATTERY_R = 0x12 |
| 命令间隔 | ≈12ms |

**请求包**（hidapi 视角，含 report ID 共 16 字节）：

    [0x06, 0x12, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
     0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
       │     │     └─ 固定头 00 00 01 00 ─┘  │     └─ data(8字节全0)
       │     └─ cmd=0x12(BATTERY_R)          └─ byte[7]=profile(0x00)
       └─ byte[0]=report ID 0x06            byte[6]=len(0x00)

**回包**（get_feature_report(0x06, 16) 返回 16 字节）：

    byte[0]=0x06 (RID echo)
    byte[1]=0x12 (cmd echo)
    byte[6]=长度
    byte[8]=电量百分比 (0–100)  ← 就取这一个字节

**读取流程**：send_feature_report(REQ) → sleep 12ms → get_feature_report(0x06, 16) → resp[8]。

### 3.3 与 Sora V2 协议的区别（为什么不能沿用现成驱动）

| | Sora V2（现有驱动） | Sora V3（你的鼠标） |
|---|---|---|
| VID/PID | 1915 / AE1C | **093A / EB02** |
| report ID | 5 | **6** |
| 包长 | 32 字节 | **15 字节** |
| 命令 | 无（直接发 ID5） | **0x12** |
| 电量字节 | resp[9] | **resp[8]** |

协议完全不同，必须新写，不是「加一行 PID」能解决的。

---

## 4. 方案选型

### 4.1 候选对比

| 方案 | 工作量 | 许可证风险 | 结论 |
|---|---|---|---|
| A. 改 mouse_tray 加 Sora V3 驱动 | 需读懂其架构 + 新协议 | 无许可证，不可复制 | 否 |
| B. 用 SoraV2 现成 exe | 0 | 无许可证；且 VID 不匹配 | 否（根本不识别） |
| C. **自研独立托盘程序（~150 行 Python）** | 小 | 无（自写） | **选定** |

### 4.2 选型结论

协议已被逆向到「一个字节」，核心逻辑只有一次 HID feature report 往返。引入 mouse_tray 整个项目（几十个文件、无许可证）是过度设计。**自研独立程序**是「能跑的最小完整实现」。

---

## 5. 系统架构

    ┌─────────────────────────────────────────────┐
    │  Windows 系统托盘（时钟旁）                   │
    │   ┌─────────────┐                            │
    │   │  pystray 图标 │ ← Pillow 绘制百分比/电池    │
    │   └──────┬──────┘                            │
    │          │ 后台线程，每 30s                    │
    │   ┌──────▼──────┐                            │
    │   │  轮询循环     │                            │
    │   └──────┬──────┘                            │
    │   ┌──────▼──────┐   send/get feature report   │
    │   │  hidapi 传输  │ ──────────────────────────▶│
    │   └─────────────┘   (report ID 0x06)          │
    │                                               │  Sora V3 接收器
    │                                               │  VID 093A / PID EB02
    └─────────────────────────────────────────────┘

**依赖（仅 3 个，都是标准库之外的最小集）**：
- hidapi（import hid）—— HID 通信，Windows 免驱动。
- pystray —— 系统托盘图标。
- Pillow —— 绘制图标（百分比数字 / 电池图形）。
- 打包：PyInstaller（构建期依赖，运行时不需要）。

---

## 6. 详细设计

### 6.1 设备发现
- hid.enumerate(0x093A, 0xEB02) 返回该复合设备所有 interface/collection。
- 遍历每个 path，用 §7.1 的读法试读，能返回 0–100 的那条 path 即正确 collection。
- 拿到正确 path 后记录其 usage_page / usage / interface_number，后续直接按这三者定位（避免每次全遍历）。

### 6.2 HID 传输与电量读取
- 每次读：新建 hid.device() → open_path(path) → send_feature_report(REQ) → sleep 12ms → get_feature_report(0x06,16) → close()。
- 异常（拔线/忙）一律捕获，返回「未连接」，**绝不抛出**。
- 与 mouse_tray 相同的「每次开/关、不常驻句柄」策略：鼠标睡眠/重连时句柄会失效，短连接最稳。

### 6.3 托盘 UI
- pystray.Icon 显示：**百分比数字**（最简单直观），hover tooltip 显示 Sora V3: NN%。
- 低电量（≤20%）图标变红，正常绿色/白色。
- 右键菜单：刷新、退出。

### 6.4 轮询策略
- 默认每 30s 读一次（与 ninjaforce 网页刷新粒度一致，足够「实时」）。
- 未连接时降低到 5s 探测，插回后恢复 30s。
- （可选优化）Sora V3 也会通过 input report 主动推电量（report ID 0 或 6，cmd 0x12）；如需更省电可改被动监听，**非必需**。

### 6.5 配置
- 首版不做配置界面（YAGNI）。常量（轮询间隔、VID/PID、颜色阈值）写在文件顶部，需要时再抽 config.json。

### 6.6 开机自启
- 打好的 exe 放到固定目录（如 C:\Users\<你>\AppData\Local\SoraV3Battery\），
- 在「开始菜单 → 启动」文件夹放一个快捷方式（shell:startup），免管理员、用户可自行管理。

### 6.7 打包
- pyinstaller --onefile --windowed --name SoraV3Battery main.py
- 产物为单个 SoraV3Battery.exe，双击即驻留托盘。

---

## 7. 关键代码骨架

### 7.1 最小探针（先跑这个验证协议，~25 行）

    import hid, time

    VID, PID = 0x093A, 0xEB02
    RID = 0x06
    REQ = [0x06, 0x12, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
           0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

    for info in hid.enumerate(VID, PID):
        d = hid.device()
        try:
            d.open_path(info["path"])
            d.send_feature_report(REQ)
            time.sleep(0.012)
            r = d.get_feature_report(RID, 16)
            pct = r[8]
            if 0 <= pct <= 100:
                print("OK iface=%s usage_page=0x%04X usage=0x%04X -> %d%%"
                      % (info.get("interface_number"), info.get("usage_page"),
                         info.get("usage"), pct))
        except Exception as e:
            print("skip", info.get("interface_number"), e)
        finally:
            d.close()

**这是「能跑的最小检查」**：跑一次若打印出 OK … -> NN% 且 NN 与网页一致，协议即验证通过，同时锁定正确的 usage_page / path。

### 7.2 托盘主程序骨架（~120 行）

    import hid, time, threading
    import pystray
    from PIL import Image, ImageDraw, ImageFont

    VID, PID = 0x093A, 0xEB02
    RID = 0x06
    REQ = [0x06, 0x12, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00,
           0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
    POLL = 30

    def find_path():          # 用 §7.1 探针锁定的 usage_page/path 直接定位
        for i in hid.enumerate(VID, PID):
            if i.get("usage_page") == 0x0000:   # ← 替换为探针实测值
                return i["path"]
        return None

    def read_battery(path):
        d = hid.device(); d.open_path(path)
        try:
            d.send_feature_report(REQ)
            time.sleep(0.012)
            return d.get_feature_report(RID, 16)[8]
        finally:
            d.close()

    def make_icon(pct):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle((6, 18, 52, 46), outline="white", width=3)   # 电池框
        d.rectangle((52, 27, 57, 37), fill="white")              # 正极头
        if pct >= 0:
            color = (255, 60, 60) if pct <= 20 else (80, 220, 80)
            d.rectangle((9, 21, 9 + int(40 * pct / 100), 43), fill=color)
        return img

    def poll(icon):
        path = find_path()
        while True:
            try:
                p = read_battery(path) if path else -1
            except Exception:
                p = -1
            icon.icon = make_icon(p)
            icon.title = ("Sora V3: %d%%" % p) if p >= 0 else "Sora V3: 未连接"
            time.sleep(5 if p < 0 else POLL)

    icon = pystray.Icon("sora_v3_battery", make_icon(-1), "Sora V3 Battery")
    icon.menu = pystray.Menu(
        pystray.MenuItem("刷新", lambda: None),
        pystray.MenuItem("退出", lambda icon, _: icon.stop()),
    )
    threading.Thread(target=poll, args=(icon,), daemon=True).start()
    icon.run()

> 说明：图标里的百分比数字若要用 draw.text 绘制，需加载一个 TTF 字体（ImageFont.truetype）；上面骨架用「电池填充比例」表达电量，避免字体依赖。二者选其一即可。

---

## 8. 待实机验证项

| # | 待验证 | 方法 | 影响 |
|---|---|---|---|
| 1 | 电量 collection 的 usage_page / path | 跑 §7.1 探针 | 决定 find_path 定位方式 |
| 2 | 回包 byte[9…14] 是否含充电/满电标志 | 边充电边抓一次回包比对 | 若要显示「充电中」需扩展解析（当前 ClickSync 未解析充电态） |
| 3 | 12ms 间隔是否稳定（忙时偶发失败） | 探针连续读 50 次 | 若丢包可把间隔提到 50–100ms 或加重试 |

---

## 9. 实施里程碑

1. **M1 探针验证**（~10 分钟）：跑 §7.1，确认电量值、锁定 usage_page。
2. **M2 托盘程序**（~30 分钟）：§7.2 骨架 + 修正 find_path。
3. **M3 打包 + 自启**：PyInstaller 出 exe，放固定目录 + shell:startup 快捷方式。
4. **M4 验收**：对照 §1 六条验收标准逐项过（含拔插、充电、重启自启）。

---

## 10. 风险与边界

- **协议为第三方逆向，非官方**：若固件更新改变协议字节，需重新对协议（探针可再次定位）。
- **无充电态显示（首版）**：ClickSync 只解出百分比，充电/满电标志需 §8-2 补测；首版只显示百分比。
- **「任务栏」实为「系统托盘」**：这类电量指示器标准位置是时钟旁的托盘，非钉在任务栏的窗口；若要字面意义的任务栏窗口需另加一个置顶小窗（复杂度明显上升，不建议）。
- **许可证**：全部自写，仅复用不受版权保护的协议事实，无合规负担。