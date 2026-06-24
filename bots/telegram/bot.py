"""
Telegram Bot — UI layer only. NO analysis, NO trading decisions.
All actions route to Services layer.
"""
import asyncio
import logging
import traceback
import sys
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

from bots.telegram.handlers import (
    Handlers, ADD_SYMBOL, ADD_CAPITAL, ADD_RISK, ADD_TIMEFRAMES
)
from bots.telegram.keyboards import get_main_menu

logger = logging.getLogger("telegram_engine")


class TelegramEngine:
    """Telegram bot interface. Stateless UI layer."""

    def __init__(self, token: str, admin_id: int,
                 analysis_service=None, trading_service=None,
                 portfolio_service=None, risk_service=None):
        self.token = token
        self.admin_id = admin_id
        self.analysis_service = analysis_service
        self.trading_service = trading_service
        self.portfolio_service = portfolio_service
        self.risk_service = risk_service
        self.application: Optional[Application] = None
        self._running: bool = False                # 🛡️ Singleton guard
        self._start_lock = asyncio.Lock()          # 🛡️ منع التشغيل المزدوج
        self.handlers = Handlers(
            admin_id=admin_id,
            analysis_service=analysis_service,
            trading_service=trading_service,
            portfolio_service=portfolio_service,
            risk_service=risk_service,
        )
        logger.info(f"[بوت] تم تهيئة المحرك (admin={admin_id})")

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Global PTB error handler — catches all unhandled exceptions."""
        tb_list = traceback.format_exception(
            type(context.error), context.error, context.error.__traceback__
        )
        tb_str = "".join(tb_list)
        logger.critical(
            f"[بوت] استثناء غير معالج في المعالج: {context.error}",
        )
        logger.critical(f"[بوت] تتبع الخطأ:\n{tb_str}")

        # Try to notify user
        if update and hasattr(update, "effective_chat"):
            try:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="⚠️ حدث خطأ داخلي. جاري تسجيل المشكلة وإعادة المحاولة...",
                )
            except Exception:
                pass

    async def initialize(self):
        """Build and configure the Telegram application."""
        if self.application:
            return

        logger.info("[بوت] جاري تهيئة تطبيق البوت...")
        self.application = Application.builder().token(self.token).build()

        # ── Global error handler ──
        self.application.add_error_handler(self._error_handler)
        logger.info("[بوت] تم تسجيل معالج الأخطاء.")

        # ── Conversation Handler for adding coins ──
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(
                    filters.Regex("^➕ إضافة عملة$"),
                    self.handlers.start_add_coin
                ),
            ],
            states={
                ADD_SYMBOL: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handlers.process_add_symbol
                    ),
                ],
                ADD_CAPITAL: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handlers.process_add_capital
                    ),
                ],
                ADD_RISK: [
                    MessageHandler(
                        filters.TEXT & ~filters.COMMAND,
                        self.handlers.process_add_risk
                    ),
                ],
                ADD_TIMEFRAMES: [
                    CallbackQueryHandler(
                        self.handlers.process_add_tf_toggle,
                        pattern=r'^tf_toggle_'
                    ),
                    CallbackQueryHandler(
                        self.handlers.process_add_tf_done,
                        pattern=r'^tf_done$'
                    ),
                    CallbackQueryHandler(
                        self.handlers.cancel_conversation,
                        pattern=r'^main_menu$'
                    ),
                ],
            },
            fallbacks=[
                CommandHandler('start', self.handlers.start),
                CommandHandler('cancel', self.handlers.cancel_conversation),
            ],
            name="add_coin_conversation",
            allow_reentry=True,
        )

        # ── Register handlers ──
        self.application.add_handler(CommandHandler("start", self.handlers.start))
        self.application.add_handler(CommandHandler("cancel", self.handlers.cancel_conversation))
        self.application.add_handler(conv_handler)
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_message)
        )
        self.application.add_handler(
            CallbackQueryHandler(self.handlers.handle_callback)
        )

        logger.info("[بوت] تم تسجيل المعالجات.")

    async def _delete_existing_webhook(self) -> bool:
        """حذف أي webhook موجود لضمان أن polling يعمل."""
        import httpx
        url = f"https://api.telegram.org/bot{self.token}/deleteWebhook"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"drop_pending_updates": True})
                data = resp.json()
                if data.get("ok"):
                    logger.info("[بوت] 🧹 تم التأكد من عدم وجود webhook (Polling Mode Only)")
                    return True
                else:
                    logger.warning(f"[بوت] ⚠️ تعذر حذف webhook: {data}")
                    return False
        except Exception as e:
            logger.warning(f"[بوت] ⚠️ فشل الاتصال لفحص webhook: {e}")
            return False

    async def start(self):
        """بدء استطلاع البوت — Singleton + Webhook Cleanup."""
        async with self._start_lock:
            if self._running:
                logger.warning("[بوت] ⚠️ محاولة تشغيل ثانية مرفوضة — البوت يعمل بالفعل (Instance exists)")
                return
            self._running = True
            logger.info("[بوت] 🚀 بدء تشغيل Telegram Bot Instance...")

        await self.initialize()

        # ── 🧹 تنظيف الـ Webhook ──
        await self._delete_existing_webhook()

        attempt = 0
        while self._running:
            attempt += 1
            try:
                logger.info(f"[بوت] ⏳ محاولة بدء Polling #{attempt}...")
                
                # تهيئة التطبيق إذا لزم الأمر
                if not self.application.base_updater:
                    await self.application.initialize()
                
                await self.application.start()
                
                # بدء الاستطلاع
                await self.application.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query"],
                )
                
                logger.info("[بوت] ✅ Telegram Bot يعمل الآن بنظام Polling.")

                # حلقة الانتظار الرئيسية
                while self._running:
                    await asyncio.sleep(10)
                    if not self.application.updater.running:
                        logger.warning("[بوت] ⚠️ Updater توقف بشكل غير متوقع!")
                        break

            except Exception as e:
                err_msg = str(e)
                if "Conflict" in err_msg or "terminated by other" in err_msg:
                    wait = min(10 * attempt, 60)
                    logger.error(f"[بوت] ❌ تعارض (Conflict): نسخة أخرى تعمل! إعادة المحاولة بعد {wait}ث...")
                else:
                    logger.error(f"[بوت] ❌ خطأ في الاستطلاع: {e}")
                    wait = 5
                
                await self._cleanup()
                await asyncio.sleep(wait)
                
        logger.info("[بوت] 🛑 توقف حلقة الاستطلاع.")

    async def _cleanup(self):
        """تنظيف الموارد عند الخطأ أو التوقف."""
        if self.application:
            try:
                if self.application.updater and self.application.updater.running:
                    await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()
            except Exception as e:
                logger.debug(f"[بوت] خطأ أثناء التنظيف: {e}")

    async def stop(self):
        """إيقاف البوت بشكل آمن."""
        logger.info("[بوت] جاري إيقاف البوت...")
        self._running = False
        await self._cleanup()
        logger.info("[بوت] ✅ تم إيقاف البوت بنجاح.")

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown"):
        """إرسال رسالة."""
        if self.application and self.application.bot:
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode
                )
            except Exception as e:
                logger.error(f"[بوت] فشل إرسال رسالة: {e}")
