"""低电量提示状态机 + 充满电提示。

纯逻辑、无 I/O：喂入电量读数，返回「此刻应弹出哪一级提示」。
本模块的全部职责是**去重**——30 秒一轮的轮询若不做去重，电量低于
阈值时每个周期都会弹一条通知，沦为骚扰（LowBatteryAlert 正是这个
问题，社区甚至专门有人写程序去屏蔽它的重复通知）。

规则（参考 mouse_tray 的边沿触发思路）：

- 两级阈值（默认 20% / 10%），每级只在电量**首次跌破**时报一次；
- 电量跳级跌破（如 30% → 8%）只报最深一级，浅级一并标记已报，
  避免连弹两条；
- 电量回升到「阈值 + 迟滞(5%)」以上后该级重新武装，充电后再放电
  会再次提示；迟滞防止在阈值附近 19% ↔ 20% 抖动刷屏；
- 任意两条提示之间有冷却期（默认 30 分钟），兜底读取毛刺；冷却期
  只推迟不丢弃——冷却结束后仍低于阈值会在下一次读数时补报；
- 断连（pct=None）不触发也不复位，重连后维持已报状态，不重复提示。
"""
import time


DEFAULT_STAGES = (20, 10)   # 提示阈值：20% 一般低电量，10% 严重不足
HYSTERESIS = 5              # 重新武装迟滞：回升到 阈值+5% 才算恢复
COOLDOWN_SEC = 30 * 60      # 兜底冷却：任意两条提示的最小间隔


class LowBatteryAlerter:
    """边沿触发的低电量提示状态机。线程不安全，只在轮询线程使用。"""

    def __init__(self, stages=DEFAULT_STAGES, hysteresis=HYSTERESIS,
                 cooldown_sec=COOLDOWN_SEC):
        # 降序去重，如 (20, 10)；stages[0] 浅级，stages[-1] 深级
        self.stages = sorted({int(s) for s in stages}, reverse=True)
        self.hysteresis = hysteresis
        self.cooldown_sec = cooldown_sec
        self._fired = [False] * len(self.stages)
        self._last_fired_at = None

    def update(self, pct, now=None):
        """喂入一次电量读数。

        pct: 0-100 的电量，None 表示断连。now: 当前时刻（time.time()），
        测试可注入假时钟。

        返回应提示的阈值级（int，如 20），无需提示返回 None。
        """
        if pct is None:
            return None
        if now is None:
            now = time.time()
        # 回升越过错滞线：该级重新武装
        for i, s in enumerate(self.stages):
            if self._fired[i] and pct >= s + self.hysteresis:
                self._fired[i] = False
        # 找最深的一个「已跌破且尚未报过」的级（stages 降序，越靠后越深）
        deepest = None
        for i, s in enumerate(self.stages):
            if not self._fired[i] and pct <= s:
                deepest = i
        if deepest is None:
            return None
        # 冷却期只推迟：不弹也不标记，冷却结束后仍低于阈值则补报
        if (self._last_fired_at is not None
                and now - self._last_fired_at < self.cooldown_sec):
            return None
        # 浅级一并标记已报，防止深级提示后紧跟一条浅级提示
        self._fired = [True if i <= deepest else f
                       for i, (f, _) in enumerate(zip(self._fired, self.stages))]
        self._last_fired_at = now
        return self.stages[deepest]

    @property
    def urgent_stage(self):
        """最深一级阈值，消息措辞（「低」vs「严重不足」）依据它区分。"""
        return self.stages[-1]


def alert_message(stage, pct, urgent_stage):
    """生成气泡提示的 (标题, 正文)。stage 为本次触发的阈值级。"""
    if stage <= urgent_stage:
        return ("Sora V3 电量严重不足：%d%%" % pct, "电量即将耗尽，请立即充电。")
    return ("Sora V3 电量低：%d%%" % pct, "请尽快充电。")


class FullChargeNotifier:
    """充满电提示：充电中到达 100% 报一次，拔线即复位。

    与 LowBatteryAlerter 同一套边沿触发思路，但只有 100% 一档：
    - 充电中首次读到 >=100 报一次，之后插着线不再重复（哪怕读数
      在 100 附近小幅抖动）；
    - 拔线（非充电读数）复位——下次充满会再报，一个充电周期一条；
    - 非充电读数永远不会触发（没插线谈何充满）。
    """

    def __init__(self):
        self._fired = False

    def update(self, pct, charging):
        """喂入一次电量读数。返回 True 表示此刻应弹『已充满』。"""
        if not charging:
            self._fired = False
            return False
        if self._fired or pct < 100:
            return False
        self._fired = True
        return True
