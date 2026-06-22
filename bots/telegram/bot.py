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

    async def start(self):
        """Start polling."""
        if not self.application:
            await self.initialize()

        logger.info("[بوت] بدء استطلاع البوت...")
        async with self.application:
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)
            logger.info("[بوت] ✅ استطلاع البوت بدأ. في انتظار الرسائل.")

            try:
                while True:
                    await asyncio.sleep(3600)
            except (KeyboardInterrupt, SystemExit):
                logger.info("[بوت] استلام إشارة إيقاف.")
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

    async def stop(self):
        """Stop bot gracefully."""
        if self.application:
            logger.info("[بوت] جاري إيقاف البوت...")
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()
            logger.info("[بوت] تم إيقاف البوت.")

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
