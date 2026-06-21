import asyncio
import json
import websockets
import os
from datetime import datetime
from sqlalchemy import select
from database import AsyncSessionLocal, LiveTrade, TrackedCoin, UserConfig
from config import ADMIN_ID

PRICES_CACHE_FILE = "/tmp/live_prices.json"
KLINES_CACHE_FILE = "/tmp/live_klines.json"

class TradeMonitor:
    def __init__(self, bot=None):
        self.bot = bot
        self.chat_id = ADMIN_ID
        self.is_running = False
        self.live_prices = {}
        self.live_klines = {}

    def _save_data(self):
        try:
            with open(PRICES_CACHE_FILE, 'w') as f:
                json.dump(self.live_prices, f)
            with open(KLINES_CACHE_FILE, 'w') as f:
                json.dump(self.live_klines, f)
        except: pass

    async def check_prices(self):
        from Core.ai_engine import AIEngine
        ai = AIEngine(bot=self.bot, chat_id=self.chat_id)
        self.is_running = True
        print("📡 [MONITOR] انطلاق الرادار المؤسسي V4.0")
        
        while self.is_running:
            try:
                async with AsyncSessionLocal() as session:
                    coins_res = await session.execute(select(TrackedCoin).where(TrackedCoin.enabled == True))
                    symbols = [c.symbol for c in coins_res.scalars().all()]
                    
                    if not symbols:
                        await asyncio.sleep(10)
                        continue

                    streams = [f"{s.lower()}@miniTicker" for s in symbols] + [f"{s.lower()}@kline_15m" for s in symbols]
                    uri = f"wss://stream.binance.com:9443/stream?streams={'/'.join(streams)}"
                    
                    async with websockets.connect(uri) as ws:
                        last_analysis_time = datetime.now()
                        while self.is_running:
                            # التحقق من وجود عملات جديدة تمت إضافتها لإعادة الاتصال
                            async with AsyncSessionLocal() as check_session:
                                current_symbols = [c.symbol for c in (await check_session.execute(select(TrackedCoin).where(TrackedCoin.enabled == True))).scalars().all()]
                                if set(current_symbols) != set(symbols):
                                    print("🔄 [MONITOR] تم اكتشاف تغيير في العملات، إعادة تشغيل البث...")
                                    break # سيخرج من الحلقة الداخلية ويعيد الاتصال بالقائمة الجديدة

                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                                payload = json.loads(msg)
                                data = payload['data']
                                symbol = data['s']
                            except asyncio.TimeoutError:
                                continue
                            
                            if 'miniTicker' in payload['stream']:
                                price = float(data['c'])
                                self.live_prices[symbol] = {'price': price, 'time': datetime.now().strftime('%H:%M:%S')}
                                await self._check_live_trades(symbol, price)
                            elif 'kline' in payload['stream']:
                                k = data['k']
                                self.live_klines[symbol] = {'o': float(k['o']), 'h': float(k['h']), 'l': float(k['l']), 'c': float(k['c']), 'v': float(k['v']), 'x': k['x']}
                            
                            self._save_data()

                            if (datetime.now() - last_analysis_time).seconds >= 120: # زيادة الفاصل الزمني إلى دقيقتين
                                for s in symbols:
                                    await ai.analyze_and_trade(s)
                                    await asyncio.sleep(2) # زيادة التأخير بين العملات إلى ثانيتين
                                last_analysis_time = datetime.now()

            except Exception as e:
                print(f"⚠️ [MONITOR] Connection Error: {e}")
                await asyncio.sleep(5)

    async def _check_live_trades(self, symbol, price):
        """مراقبة الصفقات الحقيقية (Phase 4)"""
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(LiveTrade).where((LiveTrade.symbol == symbol) & (LiveTrade.status == "OPEN")))
            for trade in res.scalars().all():
                closed = False
                if trade.type == "BUY":
                    if price >= trade.take_profit:
                        trade.status, closed = "WON", True
                        trade.exit_reason = "Take Profit Hit"
                    elif price <= trade.stop_loss:
                        trade.status, closed = "LOST", True
                        trade.exit_reason = "Stop Loss Hit"
                
                if closed:
                    trade.exit_price = price
                    trade.closed_at = datetime.utcnow()
                    trade.duration = (trade.closed_at - trade.timestamp).seconds
                    pnl_pct = ((price - trade.entry_price) / trade.entry_price) * 100
                    trade.pnl = (trade.amount * pnl_pct) / 100
                    
                    # Capital Protection Engine (Phase 4)
                    if trade.status == "LOST":
                        cfg_res = await session.execute(select(UserConfig).where(UserConfig.telegram_id == self.chat_id))
                        cfg = cfg_res.scalars().first()
                        cfg.consecutive_losses += 1
                        if cfg.consecutive_losses >= 5:
                            cfg.emergency_stop = True
                            if self.bot: await self.bot.send_message(self.chat_id, "🚨 *EMERGENCY STOP*: 5 consecutive losses detected!")
                    else:
                        cfg_res = await session.execute(select(UserConfig).where(UserConfig.telegram_id == self.chat_id))
                        cfg = cfg_res.scalars().first()
                        cfg.consecutive_losses = 0

                    await session.commit()
                    if self.bot:
                        icon = "✅" if trade.status == "WON" else "❌"
                        await self.bot.send_message(self.chat_id, f"{icon} *صفقة مغلقة*\n{symbol}: {trade.pnl:.2f} USDT")
