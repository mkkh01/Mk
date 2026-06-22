"""
اختبار آلة الحالات — إثبات عدم وجود مراحل طرفية.
يُشغّل الحالات الثلاث المطلوبة:
  1. Warmup ناجح
  2. Warmup فاشل (HTTP 418)
  3. تأخر WebSocket

لا يتطلب اتصال Binance أو قاعدة بيانات.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

# محاكاة _utcnow و logger قبل استيراد main
from datetime import datetime, timezone

def _utcnow():
    return datetime.now(timezone.utc)

# إعداد logger للاختبار
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("test")

# استيراد TradingState مباشرة
from main import TradingState

SEPARATOR = "─" * 60

def test_scenario(name: str, warmup_success: bool, ws_delay_cycles: int):
    """محاكاة دورة حياة كاملة للنظام."""
    print(f"\n{'='*60}")
    print(f"اختبار: {name}")
    print(f"  warmup ناجح: {warmup_success}")
    print(f"  تأخير WebSocket: {ws_delay_cycles} دورة")
    print(f"{'='*60}\n")

    state = TradingState()
    transitions_seen = []
    phase_history = set()
    phase_counts = {}

    def record():
        p = state.phase
        if p not in phase_history:
            phase_history.add(p)
            phase_counts[p] = 0
        phase_counts[p] = phase_counts.get(p, 0) + 1
        if not transitions_seen or transitions_seen[-1] != p:
            transitions_seen.append(p)

    # 1. INIT → CONNECTING_WS
    record()
    state.transition(TradingState.CONNECTING_WS)
    record()
    assert state.phase == "CONNECTING_WS", f"فشل: {state.phase}"
    assert not state.trading_allowed
    assert not state.analysis_allowed

    # 2. CONNECTING_WS → LOADING_HISTORY
    state.transition(TradingState.LOADING_HISTORY)
    record()
    assert state.phase == "LOADING_HISTORY"

    # 3. محاكاة warmup
    if warmup_success:
        state.history_loaded = True
        print("  ✅ [تسخين] ناجح — 200 شمعة لكل إطار")
    else:
        state.add_error("التسخين", "HTTP 418 — فشل تحميل 3 إطار زمني")
        print("  ❌ [تسخين] فاشل — HTTP 418")

    # 4. LOADING_HISTORY → WARMING_UP (الإصلاح)
    state.transition(TradingState.WARMING_UP)
    record()
    assert state.phase == "WARMING_UP", f"فشل: {state.phase}"
    assert state.analysis_allowed, f"WARMING_UP يجب أن يسمح بالتحليل"
    assert not state.trading_allowed, f"WARMING_UP لا يجب أن يسمح بالتداول"

    # 5. محاكاة الحلقة
    ws_connected = False
    ws_ticks = 0

    for cycle in range(1, 50):
        # محاكاة تأخير WebSocket
        if cycle >= ws_delay_cycles and not ws_connected:
            ws_connected = True
            state.ws_connected = True
            state.ws_connected_at = _utcnow().timestamp()

        # محاكاة ticks
        if ws_connected:
            ws_ticks += 1
            state.ws_tick_count = ws_ticks

        # منطق الحلقة
        if state.phase == TradingState.WARMING_UP:
            if state.ws_connected and state.ws_tick_count >= state.MIN_WS_TICKS:
                state.transition(TradingState.RUNNING)
                record()
                break
        elif state.phase == TradingState.LOADING_HISTORY:
            state.transition(TradingState.WARMING_UP)
            record()
        elif state.phase == TradingState.CONNECTING_WS:
            if ws_connected:
                state.transition(TradingState.WARMING_UP)
                record()

        state.check_stuck(cycle)
        record()

    # 6. تحقق نهائي
    print(f"\n{SEPARATOR}")
    print(f"نتائج {name}:")
    print(f"{SEPARATOR}")

    arrived = state.phase == "RUNNING"
    print(f"  وصل لـ RUNNING: {'✅' if arrived else '❌ (علق في ' + state.phase + ')'}")

    if arrived:
        assert state.trading_allowed, "RUNNING يجب أن يسمح بالتداول"
        assert state.analysis_allowed, "RUNNING يجب أن يسمح بالتحليل"
        assert state.ws_connected, "RUNNING يجب أن يكون WebSocket متصل"

    print(f"  تسلسل الانتقالات: {' → '.join(transitions_seen)}")
    print(f"  عدد المراحل الفريدة: {len(phase_history)}")
    print(f"  trading_allowed: {state.trading_allowed}")

    # تحقق من عدم وجود تكرار غير طبيعي
    init_count = transitions_seen.count("INIT")
    connecting_count = transitions_seen.count("CONNECTING_WS")
    loading_count = transitions_seen.count("LOADING_HISTORY")
    warming_count = transitions_seen.count("WARMING_UP")
    running_count = transitions_seen.count("RUNNING")

    print(f"  مرات INIT: {init_count} | CONNECTING_WS: {connecting_count} | LOADING_HISTORY: {loading_count}")
    print(f"  مرات WARMING_UP: {warming_count} | RUNNING: {running_count}")

    # التأكيدات المطلوبة
    errors = []
    if init_count != 1:
        errors.append(f"INIT ظهر {init_count} مرة (متوقع 1)")
    if connecting_count != 1:
        errors.append(f"CONNECTING_WS ظهر {connecting_count} مرة (متوقع 1)")
    if loading_count != 1:
        errors.append(f"LOADING_HISTORY ظهر {loading_count} مرة (متوقع 1)")
    if warming_count != 1:
        errors.append(f"WARMING_UP ظهر {warming_count} مرة (متوقع 1)")
    if not arrived:
        errors.append("لم يصل لـ RUNNING!")
    elif running_count != 1:
        errors.append(f"RUNNING ظهر {running_count} مرة (متوقع 1)")

    if errors:
        print(f"\n  ❌ أخطاء:")
        for e in errors:
            print(f"     • {e}")
        return False

    print(f"\n  ✅ جميع التأكيدات اجتازت — لا أخطاء")
    return True


if __name__ == "__main__":
    results = []

    # السيناريو 1: Warmup ناجح + WS فوري
    results.append(test_scenario(
        "1. Warmup ناجح + WebSocket فوري",
        warmup_success=True, ws_delay_cycles=1
    ))

    # السيناريو 2: Warmup فاشل + WS فوري
    results.append(test_scenario(
        "2. Warmup فاشل (HTTP 418) + WebSocket فوري",
        warmup_success=False, ws_delay_cycles=1
    ))

    # السيناريو 3: Warmup فاشل + WS متأخر
    results.append(test_scenario(
        "3. Warmup فاشل + WebSocket متأخر (10 دورات)",
        warmup_success=False, ws_delay_cycles=10
    ))

    print(f"\n{'='*60}")
    print(f"الحكم النهائي:")
    print(f"{'='*60}")
    passed = sum(results)
    total = len(results)
    print(f"  {passed}/{total} سيناريوهات نجحت")
    print(f"  {'✅ الكل ناجح' if passed == total else '❌ فشل في ' + str(total-passed)}")

    sys.exit(0 if passed == total else 1)
