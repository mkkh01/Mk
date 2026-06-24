import asyncio
import sys
import os
from datetime import datetime, timezone

# إضافة المسار الحالي للاستيراد
sys.path.insert(0, os.path.dirname(__file__))

from main import TradingState

def test_ready_to_trade_invariants():
    print("\n🧪 اختبار ثوابت READY_TO_TRADE...")
    state = TradingState()
    
    # 1. INIT -> CONNECTING_WS
    state.transition(TradingState.CONNECTING_WS)
    assert not state.trading_allowed
    
    # 2. CONNECTING_WS -> LOADING_HISTORY
    state.transition(TradingState.LOADING_HISTORY)
    assert not state.trading_allowed
    
    # 3. LOADING_HISTORY -> WARMING_UP
    state.transition(TradingState.WARMING_UP)
    assert not state.trading_allowed
    
    # 4. WARMING_UP -> READY_TO_TRADE
    # يجب محاكاة الاتصال والـ ticks أولاً لتحقيق الثوابت
    state.mark_ws_connected()
    for _ in range(state.MIN_WS_TICKS):
        state.record_tick()
    
    state.transition(TradingState.READY_TO_TRADE)
    print(f"  الحالة الحالية: {state.phase}")
    print(f"  trading_allowed: {state.trading_allowed}")
    
    # التأكد من أن READY_TO_TRADE لا يسمح بالتداول الآن
    assert not state.trading_allowed, "خطأ: READY_TO_TRADE يجب ألا يسمح بالتداول"
    print("  ✅ READY_TO_TRADE لا يسمح بالتداول (تم الإصلاح)")
    
    # 5. READY_TO_TRADE -> TRADING_ACTIVE
    state.transition(TradingState.TRADING_ACTIVE)
    assert state.trading_allowed, "خطأ: TRADING_ACTIVE يجب أن يسمح بالتداول"
    print("  ✅ TRADING_ACTIVE يسمح بالتداول")
    
    print("\n🎉 جميع الاختبارات اجتازت بنجاح!")

if __name__ == "__main__":
    try:
        test_ready_to_trade_invariants()
    except AssertionError as e:
        print(f"  ❌ فشل الاختبار: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  ❌ خطأ غير متوقع: {e}")
        sys.exit(1)
