"""
Telegram Bot — UI layer only. NO analysis, NO trading decisions.
All actions route to Services layer.
"""
import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

from bots.telegram.handlers import Handlers
from bots.telegram.keyboards import get_main_menu

logger = logging.getLogger("telegram_engine")

# Conversation states
ADD_SYMBOL, ADD_CAPITAL, ADD_RISK, ADD_TF = range(4)


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

    async def initialize(self):
        """Build and configure the Telegram application."""
        self.application = Application.builder().token(self.token).build()

        # ── Conversation Handler for adding coins ──
        conv_handler = ConversationHandler(
            entry_points=[
                MessageHandler(filters.Regex("^➕ إضافة عملة$"), self.handlers.start_add_coin),
            ],
            states={
                ADD_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.process_add_symbol)],
                ADD_CAPITAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.process_add_capital)],
                ADD_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.process_add_risk)],
                ADD_TF: [CallbackQueryHandler(self.handlers.process_add_tf, pattern='^tf_')],
            },
            fallbacks=[CommandHandler('start', self.handlers.start)],
            per_message=False,
        )

        # ── Register handlers ──
        self.application.add_handler(CommandHandler("start", self.handlers.start))
        self.application.add_handler(conv_handler)
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.handle_message))
        self.application.add_handler(CallbackQueryHandler(self.handlers.handle_callback))

        logger.info("Telegram bot handlers registered.")

    async def start(self):
        """Start polling."""
        if not self.application:
            await self.initialize()
        async with self.application:
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram bot started polling.")

            try:
                while True:
                    await asyncio.sleep(3600)
            except (KeyboardInterrupt, SystemExit):
                await self.application.updater.stop()
                await self.application.stop()
                await self.application.shutdown()

    async def stop(self):
        """Stop bot gracefully."""
        if self.application:
            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

    async def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown"):
        """Send a message to a specific chat."""
        if self.application and self.application.bot:
            try:
                await self.application.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode
                )
            except Exception as e:
                logger.error(f"Failed to send message: {e}")
