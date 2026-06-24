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
        logger.info("[بوت] جاري تهيئة تطبيق البوت...")
        self.application = Application.builder().token(self.token).build()

        # ── Global error handler ──
        self.application.add_error_handler(self._error_handler)
        logger.info("[بوت] تم تسجيل معالج الأخطاء.")

        # ── Conversation Handler for adding coins ──
        # NOTE: entry_point is the ONLY way to enter — NOT via handle_message route dict
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
        # Order matters: ConversationHandler BEFORE global handlers
        self.application.add_handler(CommandHandler("start", self.handlers.start))
        self.application.add_handler(CommandHandler("cancel", self.handlers.cancel_conversation))
        self.application.add_handler(conv_handler)
        # Global MessageHandler — catches everything NOT in a conversation
        self.application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_message)
        )
        # Global CallbackQueryHandler — ONLY for non-conversation callbacks
        self.application.add_handler(
            CallbackQueryHandler(self.handlers.handle_callback)
        )

        logger.info("[بوت] تم تسجيل المعالجات (start, cancel, conv_handler, message, callback).")

    async def _delete_existing_webhook(self) -> bool:
        """حذف أي webhook موجود لضمان أن polling يعمل.
        
        Returns:
            True إذا تم حذف webhook، False إذا لم يوجد أصلاً.
        """
        import httpx
        url = f"https://api.telegram.org/bot{self.token}/deleteWebhook"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, json={"drop_pending_updates": True})
                data = resp.json()
                if data.get("ok"):
                    if data.get("result"):
                        logger.info("[بوت] 🧹 تم حذف webhook قديم + إسقاط التحديثات المعلقة")
                        return True
                    else:
                        logger.info("[بوت] ℹ️ لا يوجد webhook قديم للحذف")
                        return False
                else:
                    logger.warning(f"[بوت] ⚠️ تعذر حذف webhook: {data}")
                    return False
        except Exception as e:
            logger.warning(f"[بوت] ⚠️ فشل الاتصال لفحص webhook: {e}")
            return False

    async def start(self):
        """بدء استطلاع البوت — Singleton + Webhook Cleanup.
        
        الآليات:
        1. قفل asyncio.Lock يمنع التشغيل المزدوج
        2. حذف webhook قديم قبل بدء polling
        3. حلقة إعادة محاولة لا نهائية مع backoff تكيفي
        4. تمييز بين خطأ Webhook وخطأ تضارب الجلسات
        """
        # ── 🛡️ Singleton guard ──
        async with self._start_lock:
            if self._running:
                logger.warning("[بوت] ⚠️ تم تجاهل محاولة تشغيل ثانية — البوت يعمل بالفعل")
                return
            self._running = True

        if not self.application:
            await self.initialize()

        # ── 🧹 حذف webhook قديم قبل أي شيء ──
        logger.info("[بوت] 🧹 فحص وحذف webhook القديم...")
        await self._delete_existing_webhook()

        logger.info("[بوت] ⏳ انتظار 30 ثانية لتجنب تضارب النشر...")
        await asyncio.sleep(30)

        # ── تأكيد ثانٍ: قد يكون webhook عاد أثناء الانتظار (deploy سابق) ──
        await self._delete_existing_webhook()

        attempt = 0
        while self._running:
            attempt += 1
            try:
                logger.info(f"[بوت] 🚀 بدء استطلاع البوت (محاولة {attempt})...")
                await self.application.initialize()
                await self.application.start()
                await self.application.updater.start_polling(
                    drop_pending_updates=True,
                    allowed_updates=["message", "callback_query"],
                    poll_interval=2.0,
                )
                logger.info("[بوت] ✅ استطلاع البوت بدأ. في انتظار الرسائل.")

                # البقاء حياً
                while self._running:
                    await asyncio.sleep(60)

            except Exception as e:
                err_msg = str(e)
                if "webhook" in err_msg.lower() and "delete" in err_msg.lower():
                    # 🧹 Webhook ما زال نشط — نحذفه ونعيد المحاولة فوراً
                    logger.warning(
                        f"[بوت] ⚠️ Webhook ما زال نشطاً (محاولة {attempt}) — "
                        f"جاري الحذف وإعادة المحاولة..."
                    )
                    await self._delete_existing_webhook()
                    wait = 5
                elif "terminated by other" in err_msg or "Conflict" in err_msg:
                    # 🔄 تضارب جلسات — انتظار أطول
                    wait = min(15 * attempt, 120)
                    logger.warning(
                        f"[بوت] ⚠️ تضارب جلسات (محاولة {attempt}) — "
                        f"هل يوجد instance آخر يعمل؟ انتظار {wait}ث..."
                    )
                elif "already running" in err_msg or "Cannot close" in err_msg:
                    wait = min(10 * attempt, 120)
                    logger.warning(
                        f"[بوت] ⚠️ تضارب event loop (محاولة {attempt}) — "
                        f"انتظار {wait}ث..."
                    )
                else:
                    logger.critical(f"[بوت] ❌ خطأ غير متوقع: {e}", exc_info=True)
                    raise

                # تنظيف قبل إعادة المحاولة
                try:
                    await self.application.updater.stop()
                except Exception:
                    pass
                try:
                    await self.application.stop()
                except Exception:
                    pass
                try:
                    await self.application.shutdown()
                except Exception:
                    pass
                await asyncio.sleep(wait)
                await self.initialize()

        logger.info("[بوت] 🛑 تم الخروج من حلقة الاستطلاع")

    async def stop(self):
        """Stop bot gracefully — signals the polling loop to exit."""
        self._running = False
        if self.application:
            logger.info("[بوت] جاري إيقاف البوت...")
            try:
                await self.application.updater.stop()
            except Exception:
                pass
            try:
                await self.application.stop()
            except Exception:
                pass
            try:
                await self.application.shutdown()
            except Exception:
                pass
            logger.info("[بوت] ✅ تم إيقاف البوت.")

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown"):
        """Send a message to a specific chat."""
        if self.application and self.application.bot:
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode
                )
                logger.debug(f"[بوت] تم إرسال رسالة إلى {chat_id}")
            except Exception as e:
                logger.error(f"[بوت] فشل إرسال رسالة إلى {chat_id}: {e}")
