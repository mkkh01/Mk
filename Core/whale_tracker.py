import asyncio
import json
import websockets
from config import BINANCE_WS_URL

# ✅ إضافة معالجة أمان: إذا لم يكن الملف موجوداً أو المحرك غير جاهز
try:
    from Core.ai_engine import AIEngine
    AI_ENGINE_AVAILABLE = True
except ImportError:
    AIEngine = None
    AI_ENGINE_AVAILABLE = False
    print("ℹ️ [RADAR V3] محرك الذكاء الاصطناعي غير متوفر حالياً، سيتم تعطيل التحليل المتقدم.")


class WhaleTracker:
    def __init__(self, bot=None, chat_id=None):
        self.bot = bot
        self.chat_id = chat_id
        self.tracking_symbols = set()
        # ✅ متغير جديد لمراقبة التحديثات
        self._needs_restart = False 
        
        # ✅ تهيئة المحرك فقط إذا كان متاحاً
        self.ai_engine = AIEngine(bot=bot, chat_id=chat_id) if AI_ENGINE_AVAILABLE and AIEngine else None

    async def start_tracking(self, symbols: list):
        """بدء تتبع الحيتان الكبار لجميع عملات قاعدة البيانات الممررة"""
        new_set = {s.lower() for s in symbols}
        
        # ✅ مقارنة القوائم، إذا تغيرت نعيد التشغيل
        if new_set != self.tracking_symbols:
            self.tracking_symbols = new_set
            self._needs_restart = True
            print(f"📡 [RADAR V3] انطلاق الرادار الديناميكي لمراقبة الحيتان لـ: {', '.join(self.tracking_symbols)}")

        # ✅ تشغيل الحلقة اللانهائية للاتصال
        while True:
            await self._connect_websocket()
            # إذا طلبنا إعادة تشغيل، نعود للأعلى فوراً
            if self._needs_restart:
                self._needs_restart = False
                await asyncio.sleep(1)
                continue
            await asyncio.sleep(5)

    async def _connect_websocket(self):
        """الاتصال بخدمة البث المباشر لـ Binance"""
        while True:
            try:
                streams = [f"{symbol}@trade" for symbol in self.tracking_symbols]
                if not streams:
                    print("⚠️ [RADAR V3] قاعدة البيانات فارغة من العملات، إعادة الفحص بعد 10 ثوانٍ...")
                    await asyncio.sleep(10)
                    continue

                uri = f"{BINANCE_WS_URL}/stream?streams={'/'.join(streams)}"
                print(f"🔌 [RADAR V3] جاري الاتصال بقنوات السيولة...")
                
                # ✅ إضافة مهلة اتصال لمنع التعليق
                async with websockets.connect(uri, ping_interval=20, ping_timeout=60) as ws:
                    print("✅ [RADAR V3] الرادار متصل ويراقب الحركات اللحظية الآن.")
                    
                    # ✅ حلقة القراءة، تنقطع فقط إذا تغيرت الرموز
                    while not self._needs_restart:
                        message = await ws.recv()
                        await self._process_message(json.loads(message))
                    
                    # ✅ إنهاء الاتصال الحالي بأمان للتحديث
                    await ws.close()
                    print("🔄 [RADAR V3] تحديث قائمة العملات... جاري إعادة الاتصال.")
                    return

            except websockets.exceptions.ConnectionClosedOK:
                print("🔄 [RADAR V3] إعادة تدوير الاتصال الروتيني بالشبكة.")
            except websockets.exceptions.ConnectionClosedError as e:
                print(f"🔌 [RADAR V3] انقطاع مفاجئ في الاتصال: {e}.")
            except Exception as e:
                print(f"❌ [RADAR V3] انقطاع في الشبكة الحية: {e}. إعادة المحاولة بعد 5 ثوانٍ...")
                await asyncio.sleep(5)

    async def _process_message(self, message):
        """تحليل الحركة وتصنيف الحيتان ديناميكياً حسب عمق السوق"""
        if 'stream' in message and 'data' in message:
            data = message['data']
            if data['e'] == 'trade':
                symbol = data['s'].upper()
                price = float(data['p'])
                quantity = float(data['q'])
                is_buyer_maker = data['m']

                # حساب الحجم الإجمالي للحركة بالدولار
                order_value_usd = price * quantity

                # 📊 تحديد الحد الأدنى ديناميكياً وحسب نوع وعمق سيولة العملة
                if "BTC" in symbol or "ETH" in symbol:
                    min_threshold = 1000000.0  # مليون دولار للكبار
                elif any(m in symbol for m in ["XRP", "SOL", "BNB", "ADA", "DOGE"]):
                    min_threshold = 150000.0   # 150 ألف دولار للمتوسطة
                else:
                    min_threshold = 30000.0    # 30 ألف دولار للعملات الصغيرة

                # تخطي الحركات الصغيرة
                if order_value_usd < min_threshold:
                    return

                # 🔍 كشف وتحليل طبيعة الحركة
                if is_buyer_maker:
                    action_en = "DISTRIBUTION"
                    action_ar = "توزيع سيولة / نقل وتدوير بين المحافظ (صامت)"
                    is_market_trade = False
                else:
                    action_en = "BUY" if not is_buyer_maker else "SELL"
                    action_ar = "شراء حقيقي مباشر (تأثير فوري على السعر)" if not is_buyer_maker else "بيع حقيقي مباشر"
                    is_market_trade = True

                # صياغة رسالة التنبيه
                whale_msg = (f"🐋 *تنبيه حركة حوت ذكية (V3)* 🐋\n\n"
                             f"🪙 العملة: {symbol}\n"
                             f"📊 طبيعة الحركة: {action_ar}\n"
                             f"💵 السعر اللحظي: ${price:,.8f}\n"
                             f"📦 الكمية المتداولة: {quantity:,.2f}\n"
                             f"💰 القيمة الإجمالية: ${order_value_usd:,.2f} USD\n"
                             f"⚡ التأثير المتوقع: {'قوي ومؤثر 🚀' if is_market_trade else 'تجميع وتدوير صامت 🔄'}")
                
                print(f"📥 [RADAR ALERT] {symbol} | القيمة: ${order_value_usd:,.2f} | النوع: {action_en}")

                # إرسال للتليجرام
                if self.bot and self.chat_id:
                    try:
                        await self.bot.send_message(chat_id=self.chat_id, text=whale_msg, parse_mode='Markdown')
                    except Exception as e:
                        print(f"❌ [RADAR V3] فشل إرسال التنبيه للتليجرام: {e}")

                # 🚀 تمرير البيانات لمحرك الذكاء (فقط إذا كان متاحاً)
                if self.ai_engine:
                    try:
                        signal = "BUY" if (is_market_trade and not is_buyer_maker) else ("SELL" if is_market_trade else "HOLD")
                        asyncio.create_task(
                            self.ai_engine.analyze_and_trade(
                                symbol=symbol, 
                                current_price=price, 
                                atr=0.0, 
                                whale_action=signal
                            )
                        )
                    except Exception as ai_err:
                        print(f"🚨 [RADAR V3] خطأ في معالجة بيانات الذكاء: {ai_err}")

    def add_symbol(self, symbol: str):
        sym_low = symbol.lower()
        if sym_low not in self.tracking_symbols:
            self.tracking_symbols.add(sym_low)
            self._needs_restart = True
            print(f"➕ [RADAR V3] إضافة {symbol} للمراقبة.")

    def remove_symbol(self, symbol: str):
        sym_low = symbol.lower()
        if sym_low in self.tracking_symbols:
            self.tracking_symbols.discard(sym_low)
            self._needs_restart = True
            print(f"🗑️ [RADAR V3] حذف {symbol} من الرادار.")

    async def update_tracking_symbols(self, new_symbols: list):
        """تحديث القائمة بالكامل وإعادة تشغيل الاتصال"""
        current_set = set(self.tracking_symbols)
        new_set = {s.lower() for s in new_symbols}
        
        if current_set != new_set:
            self.tracking_symbols = new_set
            self._needs_restart = True
            print(f"🔄 [RADAR V3] تحديث شامل للرادار لجميع عملات قاعدة البيانات المتاحة.")
