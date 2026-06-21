import pandas as pd
import ccxt.async_support as ccxt
from database import AsyncSessionLocal, LiveTrade, ShadowTrade, UserConfig, TrackedCoin
from sqlalchemy import select
from strategies import InstitutionalStrategies
from datetime import datetime
import asyncio
import json
import os

class AIEngine:
    def __init__(self, bot=None, chat_id=None):
        self.strategies = InstitutionalStrategies()
        self.bot = bot
        self.chat_id = chat_id
        self.exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})

    async def get_higher_timeframe_data(self, symbol, current_tf):
        """جلب بيانات الإطار الزمني الأعلى للفلترة (Phase 2)"""
        tf_map = {"5m": "15m", "15m": "1h", "30m": "4h", "1h": "4h", "4h": "1d"}
        higher_tf = tf_map.get(current_tf, "1d")
        
        # استخدام التخزين المؤقت للأطر الزمنية العالية لتقليل الطلبات
        HTF_CACHE = f"/tmp/htf_{symbol}_{higher_tf}.json"
        try:
            if os.path.exists(HTF_CACHE):
                # إذا كان الملف موجوداً وتم تحديثه مؤخراً (أقل من 30 دقيقة)
                if (datetime.now().timestamp() - os.path.getmtime(HTF_CACHE)) < 1800:
                    with open(HTF_CACHE, 'r') as f:
                        ohlcv = json.load(f)
                        return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            await asyncio.sleep(1) # تأخير أمان إضافي
            ohlcv = await self.exchange.fetch_ohlcv(symbol, higher_tf, limit=100)
            with open(HTF_CACHE, 'w') as f:
                json.dump(ohlcv, f)
            return pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            print(f"⚠️ [SYSTEM] HTF Fetch Error for {symbol}: {e}")
            return None

    async def analyze_and_trade(self, symbol: str, **kwargs):
        print(f"🧠 [SYSTEM] جاري التحليل المؤسسي لـ {symbol}...")
        
        async with AsyncSessionLocal() as session:
            # 1. جلب إعدادات المستخدم والعملة
            cfg_res = await session.execute(select(UserConfig).where(UserConfig.telegram_id == self.chat_id))
            cfg = cfg_res.scalars().first()
            if not cfg or not cfg.is_active or cfg.emergency_stop: return
            
            coin_res = await session.execute(select(TrackedCoin).where(TrackedCoin.symbol == symbol))
            coin = coin_res.scalars().first()
            if not coin or not coin.enabled: return

            # 2. جلب البيانات من الذاكرة اللحظية (Institutional Grade via WebSocket Only)
            # نعتمد على البيانات المخزنة في الذاكرة والتي يتم تحديثها بواسطة WebSocket
            # لمنع حظر IP نهائياً (418 Error)
            try:
                HISTORICAL_CACHE = f"/tmp/hist_{symbol}_{coin.timeframe}.json"
                
                # إذا لم توجد بيانات تاريخية مخزنة، نجلبها مرة واحدة فقط (مع تأخير لمنع الحظر)
                if not os.path.exists(HISTORICAL_CACHE):
                    print(f"📥 [SYSTEM] جلب بيانات تاريخية أولية لـ {symbol}...")
                    await asyncio.sleep(2) # تأخير أمان
                    ohlcv = await self.exchange.fetch_ohlcv(symbol, coin.timeframe, limit=250)
                    with open(HISTORICAL_CACHE, 'w') as f:
                        json.dump(ohlcv, f)
                else:
                    with open(HISTORICAL_CACHE, 'r') as f:
                        ohlcv = json.load(f)
                
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                # تحديث الشمعة الأخيرة والبيانات التاريخية بالبيانات اللحظية من WebSocket
                KLINES_CACHE = "/tmp/live_klines.json"
                if os.path.exists(KLINES_CACHE):
                    with open(KLINES_CACHE, 'r') as f:
                        klines_data = json.load(f)
                    if symbol in klines_data:
                        k = klines_data[symbol]
                        # إذا كانت الشمعة الحالية قد أغلقت (x=True)، نضيفها للبيانات التاريخية
                        if k.get('x', False):
                            new_row = [datetime.now().timestamp()*1000, k['o'], k['h'], k['l'], k['c'], k['v']]
                            ohlcv.append(new_row)
                            if len(ohlcv) > 300: ohlcv.pop(0) # الحفاظ على حجم البيانات
                            with open(HISTORICAL_CACHE, 'w') as f:
                                json.dump(ohlcv, f)
                        
                        # تحديث الشمعة الأخيرة دائماً للسعر اللحظي
                        df.iloc[-1] = [df.iloc[-1]['timestamp'], k['o'], k['h'], k['l'], k['c'], k['v']]
            except Exception as e:
                print(f"⚠️ [SYSTEM] خطأ في معالجة بيانات {symbol}: {e}")
                return
            
            # 3. تحليل الإطار الزمني الأعلى (Filter)
            df_higher = await self.get_higher_timeframe_data(symbol, coin.timeframe)
            
            # 4. نظام التقييم (Scoring Engine)
            analysis = self.strategies.calculate_combined_score(df, df_higher)
            total_score = analysis["total_score"]
            
            # تسجيل صفقة ظل (Shadow Trade) دائماً للتعلم (Phase 8)
            new_shadow = ShadowTrade(
                symbol=symbol,
                indicators_snapshot=analysis,
                market_state=analysis["market_state"],
                score=total_score
            )
            session.add(new_shadow)
            await session.commit()

            # 5. الفلترة الصارمة (Phase 4)
            if total_score < 85 or analysis["quality_score"] < 70:
                print(f"🚫 [VALIDATION] تم رفض {symbol}: النقاط {total_score}, الجودة {analysis['quality_score']}")
                return

            # 6. تنفيذ الصفقة الحقيقية (Live Trade)
            params = self.strategies.get_trade_params(df)
            
            # حساب حجم الصفقة (Risk Engine)
            risk_amount = coin.capital * (coin.risk_percentage / 100)
            sl_dist = abs(params["entry"] - params["sl"])
            amount = risk_amount / (sl_dist / params["entry"]) if sl_dist > 0 else 0
            
            if amount <= 0: return

            # التأكد من عدم وجود صفقة مفتوحة
            check = await session.execute(select(LiveTrade).where((LiveTrade.symbol == symbol) & (LiveTrade.status == "OPEN")))
            if check.scalars().first(): return

            new_live = LiveTrade(
                symbol=symbol,
                type="BUY",
                entry_price=params["entry"],
                stop_loss=params["sl"],
                take_profit=params["tp"],
                amount=amount,
                score=total_score,
                entry_reason=analysis["report"],
                market_state=analysis["market_state"]
            )
            session.add(new_live)
            await session.commit()
            
            print(f"🚀 [EXECUTION] تم فتح صفقة مؤسسية لـ {symbol} بنقاط {total_score}")
            if self.bot:
                msg = (f"🚀 *صفقة مؤسسية جديدة*\n"
                       f"━━━━━━━━━━━━━━\n"
                       f"🪙 العملة: #{symbol}\n"
                       f"🎯 النقاط: {total_score}/100\n"
                       f"💰 الدخول: `{params['entry']}`\n"
                       f"🛡️ الوقف: `{params['sl']}`\n"
                       f"🏁 الهدف: `{params['tp']}`\n"
                       f"━━━━━━━━━━━━━━\n"
                       f"📊 السبب: {analysis['report']}")
                await self.bot.send_message(self.chat_id, msg, parse_mode='Markdown')
