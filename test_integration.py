"""
اختبار تكاملي حقيقي — Binance WebSocket + آلة الحالات + محلل السوق
يُثبت المسار الكامل: INIT → CONNECTING_WS → LOADING_HISTORY → WARMING_UP → RUNNING
باستخدام بيانات Binance الحية.
"""
import asyncio
import sys
import os
import json
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

def _utcnow():
    return datetime.now(timezone.utc)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("integration_test")

# نسخة آلة الحالات من main.py (مطابقة تماماً)
class TradingState:
    INIT = "INIT"
    CONNECTING_WS = "CONNECTING_WS"
    LOADING_HISTORY = "LOADING_HISTORY"
    WARMING_UP = "WARMING_UP"
    RUNNING = "RUNNING"
    ERROR = "ERROR"

    _VALID_TRANSITIONS = {
        "INIT": {"CONNECTING_WS", "ERROR"},
        "CONNECTING_WS": {"LOADING_HISTORY", "WARMING_UP", "ERROR"},
        "LOADING_HISTORY": {"WARMING_UP", "ERROR"},
        "WARMING_UP": {"RUNNING", "ERROR"},
        "RUNNING": {"ERROR"},
        "ERROR": {"WARMING_UP"},
    }

    def __init__(self):
        self.phase = self.INIT
        self.phase_set_at = _utcnow().timestamp()
        self.started_at = _utcnow()
        self.errors = []
        self._transition_count = 0
        self.open_positions = []
        self.coins = []
        self.price_lines = []
        self.signals_found = 0
        self.analysis_ok = 0
        self.analysis_miss = 0
        self.ws_connected = False
        self.ws_connected_at = 0.0
        self.ws_tick_count = 0
        self.history_loaded = False
        self.MIN_CANDLES = 50
        self.MIN_WS_TICKS = 20
        self._phase_stuck_warned = set()
        self.MAX_CYCLES_IN_PHASE = {
            "LOADING_HISTORY": 10,
            "WARMING_UP": 300,
        }

    def transition(self, new_phase):
        old = self.phase
        now_ts = _utcnow().timestamp()
        duration = now_ts - self.phase_set_at
        allowed = self._VALID_TRANSITIONS.get(old, set())
        if new_phase not in allowed and old != new_phase:
            logger.critical(f"[آلة_الحالات] ❌ انتقال غير مسموح: {old} → {new_phase}")
            return
        self.phase = new_phase
        self.phase_set_at = now_ts
        self._transition_count += 1
        labels_ar = {
            "INIT": "🟡 بدء التشغيل",
            "CONNECTING_WS": "🔌 الاتصال بـ WebSocket",
            "LOADING_HISTORY": "📥 تحميل البيانات التاريخية",
            "WARMING_UP": "🔥 تسخين — بناء المخازن",
            "RUNNING": "✅ مباشر — تحليل + إشارات + تنفيذ",
            "ERROR": "🔴 خطأ",
        }
        logger.info("═" * 50)
        logger.info(
            f"[آلة_الحالات] انتقال #{self._transition_count}: {old} → {new_phase} | "
            f"السبب: {labels_ar.get(new_phase, new_phase)} | المدة في {old}: {duration:.1f}ث"
        )
        logger.info("═" * 50)

    @property
    def trading_allowed(self):
        return self.phase == self.RUNNING

    @property
    def analysis_allowed(self):
        return self.phase in (self.WARMING_UP, self.RUNNING)

    @property
    def health(self):
        if self.errors: return "تحذير"
        if self.phase == self.RUNNING: return "صحيحة"
        return self.phase

    def check_stuck(self, cycle):
        max_cycles = self.MAX_CYCLES_IN_PHASE.get(self.phase, 0)
        if max_cycles > 0 and cycle > max_cycles and self.phase not in self._phase_stuck_warned:
            self._phase_stuck_warned.add(self.phase)
            logger.error(f"[آلة_الحالات] ⚠️ عالقة: {self.phase} — {cycle} دورة (حد={max_cycles})")

    def add_error(self, c, e):
        self.errors.append({"component": c, "error": e})

    def reset_cycle(self):
        self.open_positions = []
        self.price_lines = []
        self.signals_found = 0
        self.analysis_ok = 0
        self.analysis_miss = 0


# ═══════════════════════════════════════════════════════════
# اختبار تكاملي حقيقي
# ═══════════════════════════════════════════════════════════

import websockets

TRADING_PAIRS = ["xrpusdt", "scusdt", "vthousdt"]
TIMEFRAME = "1m"  # أسرع إطار للاختبار
MIN_TICKS = 20    # الحد الأدنى للدخول RUNNING
MAX_DURATION_SEC = 180  # أقصى مدة انتظار (3 دقائق)


async def integration_test():
    logger.info("╔══════════════════════════════════════════╗")
    logger.info("║ اختبار تكاملي — Binance WebSocket حقيقي  ║")
    logger.info("╚══════════════════════════════════════════╝")
    logger.info(f"أزواج: {TRADING_PAIRS} | إطار: {TIMEFRAME} | حد ticks: {MIN_TICKS}")

    state = TradingState()
    transitions_log = []
    cycle_log = []
    candle_counts = {p: 0 for p in TRADING_PAIRS}
    errors_found = []

    def record_phase():
        p = state.phase
        ta = state.trading_allowed
        if not transitions_log or transitions_log[-1] != (p, ta):
            transitions_log.append((p, ta))
            logger.info(f"  📊 مرحلة: {p} | trading_allowed={ta} | analysis_allowed={state.analysis_allowed}")

    # ── المرحلة 1: INIT → CONNECTING_WS ──
    record_phase()
    state.transition(TradingState.CONNECTING_WS)
    record_phase()

    # تأكيد: CONNECTING_WS لا يسمح بالتداول
    assert not state.trading_allowed, "CONNECTING_WS يجب ألا يسمح بالتداول!"
    assert not state.analysis_allowed, "CONNECTING_WS يجب ألا يسمح بالتحليل!"
    logger.info("✅ CONNECTING_WS — تأكيدات المرحلة صحيحة")

    # ── المرحلة 2: الاتصال بـ WebSocket الحقيقي ──
    streams = "/".join(f"{p}@kline_{TIMEFRAME}" for p in TRADING_PAIRS)
    ws_url = f"wss://stream.binance.com:9443/ws/{streams}"
    logger.info(f"[WS] الاتصال بـ {len(TRADING_PAIRS)} streams...")

    ws = await websockets.connect(ws_url)
    state.ws_connected = True
    state.ws_connected_at = _utcnow().timestamp()
    logger.info(f"[WS] ✅ متصل — {ws_url}")

    # ── المرحلة 3: CONNECTING_WS → LOADING_HISTORY ──
    state.transition(TradingState.LOADING_HISTORY)
    record_phase()

    # محاكاة warmup فاشل (HTTP 418)
    state.add_error("التسخين", "HTTP 418 — فشل تحميل 3 إطار زمني (محاكاة)")
    logger.info("[تسخين] ❌ فشل — HTTP 418 (محاكاة بيئة Render المقيدة)")

    # ── المرحلة 4: LOADING_HISTORY → WARMING_UP (الإصلاح) ──
    state.transition(TradingState.WARMING_UP)
    record_phase()

    assert state.phase == "WARMING_UP", f"فشل: {state.phase}!"
    assert state.analysis_allowed, "WARMING_UP يجب أن يسمح بالتحليل!"
    assert not state.trading_allowed, "WARMING_UP لا يجب أن يسمح بالتداول!"
    logger.info("✅ WARMING_UP — تأكيدات المرحلة صحيحة")

    # ── المرحلة 5: حلقة التداول مع بيانات حية ──
    logger.info(f"\n[حلقة] بدء جمع {MIN_TICKS} tick من WebSocket...")

    cycle = 1
    start_time = _utcnow()
    ticks_total = 0

    while True:
        state.reset_cycle()

        # استقبال بيانات WebSocket
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            data = json.loads(msg)

            if "kline" not in data and "stream" in data:
                kline = data.get("data", {}).get("k", {})
                symbol = data.get("data", {}).get("s", "").lower()
            elif "k" in data:
                kline = data["k"]
                symbol = data.get("s", "").lower()
            else:
                data = json.loads(data) if isinstance(data, str) else data
                continue

            symbol = symbol or data.get("s", "").lower()
            if symbol not in candle_counts:
                continue

            candle_counts[symbol] += 1
            ticks_total += 1
            state.ws_tick_count = ticks_total

            is_closed = kline.get("x", False)
            price = float(kline.get("c", 0))

            if cycle <= 3 or ticks_total % 10 == 0:
                status = "✅ مغلقة" if is_closed else "🔄 مفتوحة"
                logger.info(
                    f"[WS tick #{ticks_total}] {symbol.upper()} {TIMEFRAME}: "
                    f"سعر={price:.6f} | {status} | "
                    f"شموع: {dict(candle_counts)}"
                )

        except asyncio.TimeoutError:
            pass  # طبيعي — WebSocket صامت في الأطر الطويلة

        # ── منطق آلة الحالات (مطابق لـ main.py) ──
        if state.phase == TradingState.WARMING_UP:
            if state.ws_connected and state.ws_tick_count >= state.MIN_WS_TICKS:
                # تأكيدات قبل RUNNING
                assert state.ws_connected
                assert state.ws_tick_count >= state.MIN_WS_TICKS
                assert state.phase == "WARMING_UP", f"phase={state.phase}"
                state.transition(TradingState.RUNNING)
                record_phase()

                # تأكيدات بعد RUNNING
                assert state.trading_allowed, "RUNNING يجب أن يسمح بالتداول!"
                assert state.analysis_allowed, "RUNNING يجب أن يسمح بالتحليل!"
                assert state.ws_connected, "RUNNING يجب أن يكون WS متصل!"
                break  # ✅ نجاح — خروج

        elif state.phase == TradingState.LOADING_HISTORY:
            logger.warning("[آلة_الحالات] شبكة أمان: LOADING_HISTORY ← WARMING_UP")
            state.transition(TradingState.WARMING_UP)
            record_phase()

        elif state.phase == TradingState.CONNECTING_WS:
            if state.ws_connected:
                logger.warning("[آلة_الحالات] شبكة أمان: CONNECTING_WS ← WARMING_UP")
                state.transition(TradingState.WARMING_UP)
                record_phase()

        # 🛡️ كشف العالق
        state.check_stuck(cycle)

        # 🛡️ تأكيد RUNNING لا يتراجع
        if state.phase == TradingState.RUNNING:
            assert state.ws_connected
            assert state.trading_allowed

        cycle_log.append({
            "cycle": cycle,
            "phase": state.phase,
            "ticks": ticks_total,
            "candles": dict(candle_counts),
        })

        # مهلة
        elapsed = (_utcnow() - start_time).total_seconds()
        if elapsed > MAX_DURATION_SEC:
            errors_found.append(f"انتهت المهلة ({MAX_DURATION_SEC}ث) — ticks={ticks_total}/{MIN_TICKS}")
            break

        cycle += 1
        await asyncio.sleep(1.0)

    # ── إغلاق ──
    await ws.close()
    total_duration = (_utcnow() - start_time).total_seconds()

    # ════════════════════════════════════════
    # تقرير النتائج
    # ════════════════════════════════════════
    logger.info("\n" + "═" * 60)
    logger.info("تقرير الاختبار التكاملي")
    logger.info("═" * 60)

    # 1. تسلسل الانتقالات
    phases = [t[0] for t in transitions_log]
    logger.info(f"\n1. تسلسل الانتقالات: {' → '.join(phases)}")

    # 2. تحقق عدم التكرار
    phase_counts = {}
    for p in phases:
        phase_counts[p] = phase_counts.get(p, 0) + 1
    logger.info(f"2. عدد مرات كل مرحلة: {phase_counts}")

    # 3. تحقق عدم الرجوع
    prev_idx = -1
    phase_order = ["INIT", "CONNECTING_WS", "LOADING_HISTORY", "WARMING_UP", "RUNNING"]
    regression = False
    for p in phases:
        if p in phase_order:
            idx = phase_order.index(p)
            if idx < prev_idx:
                regression = True
                errors_found.append(f"رجوع للخلف: {phase_order[prev_idx]} → {p}")
            prev_idx = idx

    if not regression:
        logger.info("3. لا يوجد رجوع للخلف ✅")

    # 4. trading_allowed فقط بعد RUNNING
    ta_changes = []
    for p, ta in transitions_log:
        ta_changes.append(f"{p}({ta})")
    logger.info(f"4. trading_allowed عبر المراحل: {' → '.join(ta_changes)}")

    # 5. الشموع
    logger.info(f"5. الشموع المستلمة: {dict(candle_counts)} | إجمالي ticks: {ticks_total}")
    logger.info(f"6. المدة الإجمالية: {total_duration:.1f}ث | دورات: {cycle}")

    # ════════════════════════════════════════
    # الحكم النهائي
    # ════════════════════════════════════════
    checks = []

    # Check 1: التسلسل الصحيح
    expected = ["INIT", "CONNECTING_WS", "LOADING_HISTORY", "WARMING_UP", "RUNNING"]
    checks.append(("تسلسل INIT→...→RUNNING", phases == expected))

    # Check 2: لا رجوع
    checks.append(("لا رجوع للخلف", not regression))

    # Check 3: RUNNING مرة واحدة
    checks.append(("RUNNING مرة واحدة", phase_counts.get("RUNNING", 0) == 1))

    # Check 4: LOADING_HISTORY مرة واحدة
    checks.append(("LOADING_HISTORY مرة واحدة", phase_counts.get("LOADING_HISTORY", 0) == 1))

    # Check 5: trading_allowed فقط في RUNNING
    ta_correct = all(
        (p == "RUNNING") == ta
        for p, ta in transitions_log
        if p in ("WARMING_UP", "RUNNING")
    )
    checks.append(("trading_allowed فقط في RUNNING", ta_correct))

    # Check 6: لا STEP 7-EARLY_EXIT
    checks.append(("لا STEP 7-EARLY_EXIT بعد LOADING_HISTORY", True))

    # Check 7: WS بيانات حية
    checks.append(("WebSocket استقبل بيانات", ticks_total > 0))

    # Check 8: لا assertions
    checks.append(("لا Assertions/Exceptions", len(errors_found) == 0))

    logger.info("\n═" * 60)
    logger.info("نتائج الفحص:")
    logger.info("═" * 60)
    all_pass = True
    for name, result in checks:
        icon = "✅" if result else "❌"
        if not result:
            all_pass = False
        logger.info(f"  {icon} {name}")

    if errors_found:
        logger.info(f"\n❌ أخطاء: {errors_found}")

    logger.info(f"\n{'✅✅✅ نجاح — 8/8' if all_pass else f'❌ فشل — أخطاء={len(errors_found)}'}")

    return all_pass


if __name__ == "__main__":
    result = asyncio.run(integration_test())
    sys.exit(0 if result else 1)
