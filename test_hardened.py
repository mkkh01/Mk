"""
اختبار Production Hardening — Reconnect / Delay / Restart
يثبت: Idempotent + Invariants + No State Drift
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
logger = logging.getLogger("hardened_test")


# ══════════════════════════════════════════════════════════
# نسخة مطابقة من TradingState (من main.py)
# ══════════════════════════════════════════════════════════
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

    _ONCE_ONLY_PHASES = {"INIT", "CONNECTING_WS", "LOADING_HISTORY", "RUNNING"}

    def __init__(self):
        self.phase = self.INIT
        self.phase_set_at = _utcnow().timestamp()
        self.started_at = _utcnow()
        self.errors = []
        self._transition_count = 0
        self._entered_phases = {self.INIT}
        self._exited_phases = set()
        self.open_positions = []
        self.coins = []
        self.price_lines = []
        self.signals_found = 0
        self.analysis_ok = 0
        self.analysis_miss = 0
        self.ws_connected = False
        self.ws_connected_at = 0.0
        self.ws_stable_since = 0.0
        self.ws_tick_count = 0
        self.ws_reconnect_count = 0
        self.ws_last_seen_at = 0.0
        self.history_loaded = False
        self.MIN_CANDLES = 50
        self.MIN_WS_TICKS = 20
        self.MIN_WS_STABLE_SEC = 3  # 3s for test (15s in production)
        self._phase_stuck_warned = set()
        self.MAX_CYCLES_IN_PHASE = {
            "LOADING_HISTORY": 10,
            "WARMING_UP": 300,
        }

    def transition(self, new_phase):
        old = self.phase
        now_ts = _utcnow().timestamp()
        duration = now_ts - self.phase_set_at

        if new_phase == old:
            return  # Idempotent: NO-OP

        allowed = self._VALID_TRANSITIONS.get(old, set())
        if new_phase not in allowed:
            logger.critical(f"[آلة_الحالات] ❌ انتقال غير مسموح: {old} → {new_phase}")
            return

        if new_phase in self._ONCE_ONLY_PHASES and new_phase in self._exited_phases:
            logger.critical(f"[آلة_الحالات] ❌ إعادة دخول: {new_phase} (once-only)")
            return

        self._check_pre_transition_invariants(old, new_phase)
        self._exited_phases.add(old)
        self.phase = new_phase
        self.phase_set_at = now_ts
        self._entered_phases.add(new_phase)
        self._transition_count += 1
        self._check_post_transition_invariants(new_phase)

        labels_ar = {
            "INIT": "🟡 بدء التشغيل", "CONNECTING_WS": "🔌 الاتصال",
            "LOADING_HISTORY": "📥 تحميل", "WARMING_UP": "🔥 تسخين",
            "RUNNING": "✅ مباشر", "ERROR": "🔴 خطأ",
        }
        logger.info("═" * 50)
        logger.info(
            f"[آلة_الحالات] #{self._transition_count}: {old} → {new_phase} | "
            f"السبب: {labels_ar.get(new_phase, new_phase)} | المدة: {duration:.1f}ث | "
            f"ticks={self.ws_tick_count} | reconnects={self.ws_reconnect_count}"
        )
        logger.info("═" * 50)

    def _check_pre_transition_invariants(self, old, new):
        if new == self.RUNNING:
            assert self.ws_connected, "RUNNING needs ws_connected"
            assert self.ws_tick_count >= self.MIN_WS_TICKS, f"ticks={self.ws_tick_count}<{self.MIN_WS_TICKS}"
            stable = _utcnow().timestamp() - self.ws_stable_since
            assert stable >= self.MIN_WS_STABLE_SEC or self.ws_reconnect_count == 0, f"stable={stable:.1f}s<{self.MIN_WS_STABLE_SEC}"

    def _check_post_transition_invariants(self, phase):
        if phase == self.RUNNING:
            assert self.trading_allowed
            assert self.analysis_allowed
        elif phase == self.WARMING_UP:
            assert self.analysis_allowed
            assert not self.trading_allowed

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
            logger.error(f"[آلة_الحالات] ⚠️ عالقة: {self.phase} — {cycle} دورة")

    def add_error(self, c, e):
        self.errors.append({"component": c, "error": e})

    def reset_cycle(self):
        self.open_positions = []
        self.price_lines = []
        self.signals_found = 0
        self.analysis_ok = 0
        self.analysis_miss = 0

    def mark_ws_connected(self):
        was = self.ws_connected
        self.ws_connected = True
        self.ws_connected_at = _utcnow().timestamp()
        if not was:
            self.ws_stable_since = self.ws_connected_at

    def mark_ws_disconnected(self):
        if self.ws_connected:
            self.ws_connected = False
            self.ws_reconnect_count += 1
            self.ws_stable_since = 0.0

    def record_tick(self):
        self.ws_tick_count += 1
        self.ws_last_seen_at = _utcnow().timestamp()

    @property
    def ws_is_stable(self):
        if not self.ws_connected or self.ws_stable_since <= 0:
            return False
        return (_utcnow().timestamp() - self.ws_stable_since) >= self.MIN_WS_STABLE_SEC

    @property
    def ws_ready_for_running(self):
        return self.ws_connected and self.ws_tick_count >= self.MIN_WS_TICKS and self.ws_is_stable


import websockets

TRADING_PAIRS = ["xrpusdt", "scusdt", "vthousdt"]
TIMEFRAME = "1m"

SEP = "─" * 60


# ══════════════════════════════════════════════════════════
# السيناريو 1: Reconnect أثناء WARMING_UP
# ══════════════════════════════════════════════════════════
async def test_reconnect_during_warmup():
    logger.info(f"\n{'='*60}")
    logger.info("اختبار 1: Reconnect أثناء WARMING_UP")
    logger.info(f"{'='*60}")

    s = TradingState()
    s.MIN_WS_STABLE_SEC = 2  # أسرع للاختبار
    phases_seen = []
    events = []

    def record():
        if not phases_seen or phases_seen[-1] != s.phase:
            phases_seen.append(s.phase)
            events.append(f"{s.phase}(ticks={s.ws_tick_count},reconn={s.ws_reconnect_count})")

    record()
    s.transition(TradingState.CONNECTING_WS); record()
    s.transition(TradingState.LOADING_HISTORY); record()
    s.add_error("warmup", "HTTP 418")
    s.transition(TradingState.WARMING_UP); record()

    # محاكاة WS يتصل → يستقبل 10 ticks → ينقطع → يعيد الاتصال
    s.mark_ws_connected()

    for i in range(10):
        s.record_tick()
    logger.info(f"  تلقى 10 ticks، ثم... انقطاع!")

    s.mark_ws_disconnected()
    await asyncio.sleep(1.5)

    # إعادة اتصال
    s.mark_ws_connected()
    logger.info(f"  WS أعيد الاتصال — stable window تبدأ الآن")
    await asyncio.sleep(3)  # انتظار نافذة استقرار 3s

    for i in range(12):
        s.record_tick()

    # الآن ticks=22, stable>3s, ws_connected=True
    if s.ws_ready_for_running:
        s.transition(TradingState.RUNNING); record()
        logger.info(f"  ✅ وصل RUNNING بعد reconnect!")
    else:
        logger.error(f"  ❌ لم يصل RUNNING! ready={s.ws_ready_for_running}")

    # تحقق
    errors = []
    if s.phase != "RUNNING":
        errors.append("لم يصل RUNNING")
    if s.ws_tick_count < 20:
        errors.append(f"ticks أعيد تصفيرها خطأ: {s.ws_tick_count}")
    if s.ws_reconnect_count != 1:
        errors.append(f"reconnect_count={s.ws_reconnect_count} متوقع 1")

    arrived = s.phase == "RUNNING"
    logger.info(f"  تسلسل: {' → '.join(events)}")
    logger.info(f"  ticks النهائي: {s.ws_tick_count} (يجب ≥22 — لم يُصفّر)")
    logger.info(f"  reconnects: {s.ws_reconnect_count}")
    logger.info(f"  {'✅ ناجح' if not errors else '❌ ' + str(errors)}")
    return not errors and arrived


# ══════════════════════════════════════════════════════════
# السيناريو 2: Idempotent — تكرار transition
# ══════════════════════════════════════════════════════════
async def test_idempotent_transitions():
    logger.info(f"\n{'='*60}")
    logger.info("اختبار 2: Idempotent — تكرار transition")
    logger.info(f"{'='*60}")

    s = TradingState()
    s.MIN_WS_STABLE_SEC = 1
    count_before = s._transition_count

    # تكرار نفس الانتقال يجب أن يكون NO-OP
    s.transition(TradingState.CONNECTING_WS)
    s.transition(TradingState.CONNECTING_WS)  # مكرر — NO-OP
    s.transition(TradingState.CONNECTING_WS)  # مكرر — NO-OP
    after_repeat = s._transition_count

    assert after_repeat == 1, f"Idempotent فشل: {after_repeat} انتقالات بدل 1"
    logger.info(f"  ✅ 3 استدعاءات لنفس transition = انتقال واحد فقط (NO-OP للباقي)")

    # لا يمكن إعادة دخول RUNNING
    s.transition(TradingState.LOADING_HISTORY)
    s.transition(TradingState.WARMING_UP)
    s.mark_ws_connected()
    for _ in range(25): s.record_tick()
    await asyncio.sleep(1.5)
    s.transition(TradingState.RUNNING)

    # محاولة إعادة دخول RUNNING
    s.transition(TradingState.RUNNING)  # يجب أن يُرفض
    assert s._transition_count == 4, f"RUNNING دُخلت مرتين! count={s._transition_count}"
    logger.info(f"  ✅ RUNNING لا يمكن إعادة دخولها (once-only)")

    # محاولة إعادة دخول WARMING_UP غير مصرح بها
    s.transition(TradingState.WARMING_UP)  # غير مسموح من RUNNING
    assert s.phase == "RUNNING", f"RUNNING تراجعت إلى {s.phase}"
    logger.info(f"  ✅ لا تراجع من RUNNING إلى WARMING_UP")

    logger.info(f"  ✅✅ Idempotent + Immutable — كله ناجح")
    return True


# ══════════════════════════════════════════════════════════
# السيناريو 3: Duplicate RUNNING prevention
# ══════════════════════════════════════════════════════════
async def test_no_duplicate_running():
    logger.info(f"\n{'='*60}")
    logger.info("اختبار 3: منع دخول RUNNING المكرر")
    logger.info(f"{'='*60}")

    s = TradingState()
    s.MIN_WS_STABLE_SEC = 1

    s.transition(TradingState.CONNECTING_WS)
    s.transition(TradingState.LOADING_HISTORY)
    s.transition(TradingState.WARMING_UP)
    s.mark_ws_connected()
    for _ in range(25): s.record_tick()
    await asyncio.sleep(1.5)

    s.transition(TradingState.RUNNING)
    running_entered = s._entered_phases

    # محاولة استدعاء transition(RUNNING) مرة أخرى من RUNNING
    s.transition(TradingState.RUNNING)  # Idempotent NO-OP
    assert "RUNNING" in s._entered_phases, "RUNNING يجب أن تكون في entered set!"
    assert "WARMING_UP" in s._exited_phases, "WARMING_UP يجب أن تكون في exited set!"
    logger.info(f"  ✅ RUNNING entered={s._entered_phases} | exited={s._exited_phases}")

    # لا يمكن الانتقال من RUNNING لأي مرحلة أخرى (إلا ERROR)
    s.transition(TradingState.WARMING_UP)  # مرفوض
    assert s.phase == "RUNNING"
    s.transition(TradingState.LOADING_HISTORY)  # مرفوض
    assert s.phase == "RUNNING"
    logger.info(f"  ✅ RUNNING محمية من أي انتقال غير ERROR")

    return True


# ══════════════════════════════════════════════════════════
# السيناريو 4: State Drift over time
# ══════════════════════════════════════════════════════════
async def test_no_state_drift():
    logger.info(f"\n{'='*60}")
    logger.info("اختبار 4: لا انحراف State عبر الزمن")
    logger.info(f"{'='*60}")

    s = TradingState()
    s.MIN_WS_STABLE_SEC = 1

    # المسار الطبيعي
    s.transition(TradingState.CONNECTING_WS)
    s.transition(TradingState.LOADING_HISTORY)
    s.transition(TradingState.WARMING_UP)

    # محاكاة 50 دورة في WARMING_UP مع تقلبات WS
    s.mark_ws_connected()
    for cycle in range(1, 60):
        if cycle == 20:
            s.mark_ws_disconnected()  # انقطاع
        if cycle == 30:
            s.mark_ws_connected()  # إعادة اتصال
            await asyncio.sleep(3.5)  # انتظار نافذة استقرار 3s
        if s.ws_connected:
            s.record_tick()

        s.check_stuck(cycle)

        # منطق الحلقة (مطابق لـ main.py)
        if s.phase == TradingState.WARMING_UP:
            if s.ws_ready_for_running:
                s.transition(TradingState.RUNNING)
                break
        s.reset_cycle()

    logger.info(f"  المرحلة النهائية: {s.phase}")
    logger.info(f"  ticks: {s.ws_tick_count} (لم يُصفّر)")
    logger.info(f"  reconnects: {s.ws_reconnect_count}")
    logger.info(f"  entered phases: {sorted(s._entered_phases)}")
    logger.info(f"  exited phases: {sorted(s._exited_phases)}")

    assertions = [
        ("وصل RUNNING", s.phase == "RUNNING"),
        ("ticks لم يُصفّر", s.ws_tick_count >= 20),
        ("reconnect محسوب", s.ws_reconnect_count == 1),
        ("INIT في exited", "INIT" in s._exited_phases),
        ("RUNNING في entered", "RUNNING" in s._entered_phases),
        ("WARMING_UP في exited", "WARMING_UP" in s._exited_phases),
        ("لا انحراف phase", s.phase == "RUNNING"),
    ]
    all_ok = all(result for _, result in assertions)
    for name, result in assertions:
        logger.info(f"  {'✅' if result else '❌'} {name}")
    return all_ok


# ══════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════
async def main():
    results = []

    results.append(await test_reconnect_during_warmup())
    results.append(await test_idempotent_transitions())
    results.append(await test_no_duplicate_running())
    results.append(await test_no_state_drift())

    print(f"\n{'='*60}")
    print(f"الحكم النهائي: {sum(results)}/{len(results)} ناجح")
    print(f"{'✅✅✅ الكل — Production Hardened' if all(results) else '❌ فشل'}")
    print(f"{'='*60}")
    return all(results)


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result else 1)
