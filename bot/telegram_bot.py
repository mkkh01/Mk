"""
File: bot/telegram_bot.py
1. Single Responsibility: Be a thin Telegram user interface for CT -- receive
   button taps and text, format replies. The bot MUST NOT decide, score, size,
   or simulate anything itself (Section 0 hard-constraint 1). Every button maps
   to one function in app/main.py, engine/orchestrator.py, or storage and only
   formats the result.
2. Consumes: contracts.config (CoinConfig, SystemConfig),
   contracts.simulation.SimulatedTrade, contracts.portfolio.PerformanceMetrics,
   storage.supabase.SupabaseClient, storage.redis_cache.RedisCache,
   portfolio.performance.PerformanceCalculator, config.thresholds.
3. Produces: CTTelegramBot class (Application builder + async handlers).
4. Downstream: app/main.py instantiates CTTelegramBot and registers the start /
   stop engine callbacks via bot_data; users interact with it through Telegram.
5. New Dependencies: python-telegram-bot==21.4 (already pinned in
   requirements.txt). Uses the v21 Application / handler / context API.
6. Touches Section 6 bugs? No (bot is UI-only; no trading paths).
   Touches Section 0 hard constraints? Yes -- enforces #1 (no trading logic in
   UI), #7 (never labels simulated trades as "live"/"executed"), #6/#8 (min 3
   timeframes validated via CoinConfig before write).
7. Tests: tests/unit/test_bot.py validates the message templates, the add-coin
   validation helpers, the trade-history formatting with the mandatory
   simulation warning, and the main-menu structure.
8. Logging: bot_command {timestamp, user_id, command},
   bot_reply {timestamp, user_id, reply_kind},
   bot_add_coin_ok {timestamp, user_id, symbol},
   bot_delete_coin_ok {timestamp, user_id, symbol},
   bot_engine_state_change {timestamp, user_id, action, running},
   error {timestamp, module, error_type, error_message} (Section 9 catalog).
9. Dependency Order: ... portfolio/performance.py -> monitoring -> bot (this
   file). No upstream violations -- the bot imports only from already-built
   layers (contracts, storage, portfolio, monitoring, config).

DESIGN NOTES
------------
* PTB v21 Application builder pattern: ``Application.builder().token(t).build()``
  then ``add_handler`` for each handler. ``build_application`` returns the
  uninitialised Application; ``app/main.py`` is responsible for
  ``initialize()``/``start()``/``run_polling()``.
* The bot never starts the engine directly. It calls the
  ``start_engine_callback`` / ``stop_engine_callback`` injected via
  ``__init__`` (preferred) or looked up in ``context.bot_data``. Both callables
  are async and live in ``app/main.py``.
* Every simulated-trade-facing reply includes the literal warning text required
  by Section 0 hard-constraint 7. Templates are taken verbatim from Section 20.
* All thresholds come from ``config/thresholds.py`` (e.g. ``VALID_TIMEFRAMES``).
  No magic numbers in this file.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from pydantic import ValidationError

from config.thresholds import VALID_TIMEFRAMES
from contracts.config import CoinConfig, SystemConfig
from contracts.portfolio import PerformanceMetrics
from contracts.simulation import SimulatedTrade
from monitoring.logger import get_logger
from storage.redis_cache import RedisCache
from storage.supabase import SupabaseClient
from analysis.result_aggregator import ResultAggregator
from analysis.result_formatter import ResultFormatter
from analysis.performance_analyzer import PerformanceAnalyzer

if TYPE_CHECKING:
    # Imported only for type hints so the bot stays importable in test
    # environments where portfolio/performance.py is not yet wired up. The
    # actual PerformanceCalculator instance is injected via __init__.
    from portfolio.performance import PerformanceCalculator

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants -- callback-data prefixes, conversation states, regex patterns
# ---------------------------------------------------------------------------
# Callback-data prefixes are short to fit Telegram's 64-byte limit. Each prefix
# is namespaced so the dispatcher can route by prefix.
CB_MAIN_MENU = "main_menu"
CB_ADD_COIN = "add_coin"
CB_EDIT_COIN = "edit_coin"
CB_EDIT_COIN_SELECT = "edit_select:"      # edit_select:<SYMBOL>
CB_EDIT_TIMEFRAMES = "edit_tf:"           # edit_tf:<SYMBOL>
CB_EDIT_CAPITAL = "edit_cap:"             # edit_cap:<SYMBOL>
CB_EDIT_RISK = "edit_risk:"               # edit_risk:<SYMBOL>
CB_DELETE_COIN = "del_coin:"              # del_coin:<SYMBOL>
CB_DELETE_COIN_CONFIRM = "del_coin_conf:" # del_coin_conf:<SYMBOL>
CB_START_ENGINE = "start_engine"
CB_STOP_ENGINE = "stop_engine"
CB_LIVE_PRICES = "live_prices"
CB_TRADE_HISTORY = "trade_history"
CB_SYS_PERF = "sys_perf"
CB_SYS_PERF_PERIOD = "perf_period:"       # perf_period:<period-key>
CB_CONFIRM_YES = "confirm_yes"
CB_CONFIRM_NO = "confirm_no"
CB_CANCEL = "cancel"

# Conversation states for the add-coin flow (Section 7).
SYMBOL, TIMEFRAMES, CAPITAL, RISK, CONFIRM = range(5)

# Pre-compiled symbol regex: 2-10 uppercase letters followed by USDT.
# Matches BTCUSDT, ETHUSDT, SOLUSDT, etc. Rejects lowercase / suffixes other
# than USDT (per task spec: ^[A-Z]{2,10}USDT$).
SYMBOL_RE = re.compile(r"^[A-Z]{2,10}USDT$")

# Simulation-only warning text -- mandatory per Section 0 #7 + Section 20.
# Centralised here so it is impossible to forget when adding a new template.
SIM_WARNING_TRADE = "WARNING: This is a simulated trade only."
SIM_WARNING_LIST = "WARNING: All trades are simulation only."
SIM_WARNING_PERF = "WARNING: All results are from simulated trades."
SIM_WARNING_ENGINE = (
    "WARNING: Simulation Mode Only\nNo real trades are being executed."
)

# Performance-period selector keys -> human label + timedelta-less lookups.
# Periods are computed dynamically against ``datetime.now(utc)`` so we never
# store absolute datetimes in code.
PERF_PERIODS: dict[str, str] = {
    "all": "All-Time",
    "1d": "Last 24 Hours",
    "7d": "Last 7 Days",
    "30d": "Last 30 Days",
    "90d": "Last 90 Days",
}

# A type alias for the async start/stop engine callbacks injected by the app.
EngineCallback = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# CTTelegramBot
# ---------------------------------------------------------------------------
class CTTelegramBot:
    """Thin Telegram UI for the CT simulation-only crypto bot.

    The bot is constructed once at app startup. ``build_application`` returns a
    PTB v21 ``Application`` that ``app/main.py`` initialises and runs.

    Parameters
    ----------
    supabase:
        Connected ``SupabaseClient`` used for coins / trades / decisions.
    redis:
        Connected ``RedisCache`` used for cached live prices and the
        ``engine_running`` flag.
    performance_calc:
        ``PerformanceCalculator`` used by the System Performance button.
    settings:
        ``SystemConfig`` -- only the Telegram token is used here.
    start_engine_callback:
        Optional async callable invoked by the Start Engine button. The app
        registers this so the bot stays a thin wrapper.
    stop_engine_callback:
        Optional async callable invoked by the Stop Engine button.
    """

    # ---------------- construction ----------------
    def __init__(
        self,
        supabase: SupabaseClient,
        redis: RedisCache,
        performance_calc: PerformanceCalculator,
        settings: SystemConfig,
        start_engine_callback: Optional[EngineCallback] = None,
        stop_engine_callback: Optional[EngineCallback] = None,
        reload_engine_callback: Optional[EngineCallback] = None,
    ) -> None:
        self._supabase: SupabaseClient = supabase
        self._redis: RedisCache = redis
        self._performance_calc: PerformanceCalculator = performance_calc
        self._settings: SystemConfig = settings
        self._start_engine_callback: Optional[EngineCallback] = start_engine_callback
        self._stop_engine_callback: Optional[EngineCallback] = stop_engine_callback
        self._reload_engine_callback: Optional[EngineCallback] = reload_engine_callback
        
        # New analysis components
        self._aggregator = ResultAggregator(supabase)
        self._formatter = ResultFormatter()
        self._analyzer = PerformanceAnalyzer()

    async def _trigger_engine_reload(self, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> None:
        """Notify the app layer that coins changed so it can refresh subscriptions.

        The callback is looked up in two places (in order):
          1. ``self._reload_engine_callback`` (injected at construction)
          2. ``context.bot_data["reload_engine_callback"]`` (if context is provided)

        Errors are logged but never raised -- the bot must stay responsive.
        """
        callback = self._reload_engine_callback

        # Fallback to bot_data if context is available and callback is not set.
        if callback is None and context is not None:
            callback = context.bot_data.get("reload_engine_callback")

        if callback is not None:
            try:
                await callback()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="bot.telegram_bot",
                    error_type=type(exc).__name__,
                    error_message=f"reload_engine_callback failed: {exc}",
                )

    # ---------------- Application builder ----------------
    def build_application(self) -> Application:
        """Build the python-telegram-bot v21 ``Application`` with all handlers.

        The returned application is NOT initialised -- ``app/main.py`` is
        responsible for ``await application.initialize()``,
        ``await application.start()``, ``await application.updater.start_polling()``
        and the symmetric shutdown sequence.
        """
        application = ApplicationBuilder().token(self._settings.telegram_bot_token).build()

        # /start -> main menu
        application.add_handler(CommandHandler("start", self.start_command))

        # Add-coin conversation flow (Section 7).
        add_coin_conversation = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.cmd_add_coin, pattern=f"^{CB_ADD_COIN}$")],
            states={
                SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._add_coin_symbol)],
                TIMEFRAMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._add_coin_timeframes)],
                CAPITAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._add_coin_capital)],
                RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._add_coin_risk)],
                CONFIRM: [
                    CallbackQueryHandler(
                        self._add_coin_confirm, pattern=f"^({CB_CONFIRM_YES}|{CB_CONFIRM_NO}|{CB_CANCEL})$"
                    )
                ],
            },
            fallbacks=[
                CommandHandler("cancel", self._add_coin_cancel),
                CallbackQueryHandler(self._add_coin_cancel, pattern=f"^{CB_CANCEL}$"),
            ],
            name="add_coin_conversation",
            per_user=True,
            per_chat=True,
            allow_reentry=True,
        )
        application.add_handler(add_coin_conversation)

        # Generic callback dispatcher for everything else (edit-coin sub-flows,
        # main-menu button taps, engine start/stop, etc.).
        application.add_handler(CallbackQueryHandler(self.handle_callback))

        # Free-text message handler -- used as a safety net so unknown text
        # gets a polite reply instead of being silently ignored.
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))

        return application

    # ---------------- /start command ----------------
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """``/start`` -- greet the user and show the main menu."""
        if update.effective_user is None or update.effective_chat is None:
            return
        user_id = update.effective_user.id
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="/start",
        )
        text = (
            "Welcome to CT -- Simulation-Only Crypto Spot Bot.\n\n"
            "Pick an action below. All trades produced by this bot are "
            "simulated; no real exchange orders are ever placed.\n\n"
            "WARNING: Simulation Mode Only. No real trades are being executed."
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=self._build_main_menu(),
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="main_menu",
        )

    # ---------------- generic callback dispatcher ----------------
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Dispatch a button tap to the right handler.

        Routes by the ``callback_data`` prefix. Buttons handled inside the
        add-coin ``ConversationHandler`` never reach here -- PTB consumes them
        first.
        """
        query = update.callback_query
        if query is None:
            return
        # Always acknowledge the query to clear the spinner.
        try:
            await query.answer()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=f"query.answer() failed: {exc}",
            )

        data = query.data or ""
        user_id = query.from_user.id if query.from_user else 0
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command=f"callback:{data}",
        )

        try:
            if data == CB_MAIN_MENU:
                await self._show_main_menu(update, context)
            elif data == CB_EDIT_COIN:
                await self.cmd_edit_coin(update, context)
            elif data.startswith(CB_EDIT_COIN_SELECT):
                symbol = data[len(CB_EDIT_COIN_SELECT):]
                await self._edit_coin_show_options(update, context, symbol)
            elif data.startswith(CB_EDIT_TIMEFRAMES):
                symbol = data[len(CB_EDIT_TIMEFRAMES):]
                await self._edit_coin_ask_timeframes(update, context, symbol)
            elif data.startswith(CB_EDIT_CAPITAL):
                symbol = data[len(CB_EDIT_CAPITAL):]
                await self._edit_coin_ask_capital(update, context, symbol)
            elif data.startswith(CB_EDIT_RISK):
                symbol = data[len(CB_EDIT_RISK):]
                await self._edit_coin_ask_risk(update, context, symbol)
            elif data.startswith(CB_DELETE_COIN_CONFIRM):
                symbol = data[len(CB_DELETE_COIN_CONFIRM):]
                await self._edit_coin_delete_confirm(update, context, symbol)
            elif data.startswith(CB_DELETE_COIN):
                symbol = data[len(CB_DELETE_COIN):]
                await self._edit_coin_ask_delete(update, context, symbol)
            elif data == CB_START_ENGINE:
                await self.cmd_start_engine(update, context)
            elif data == CB_STOP_ENGINE:
                await self.cmd_stop_engine(update, context)
            elif data == CB_LIVE_PRICES:
                await self.cmd_live_prices(update, context)
            elif data == CB_TRADE_HISTORY:
                await self.cmd_trade_history(update, context)
            elif data == CB_SYS_PERF:
                await self._system_performance_prompt_period(update, context)
            elif data.startswith(CB_SYS_PERF_PERIOD):
                period_key = data[len(CB_SYS_PERF_PERIOD):]
                await self.cmd_system_performance(update, context, period_key)
            else:
                # Unknown callback -- log and reply with main menu.
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="bot.telegram_bot",
                    error_type="UnknownCallback",
                    error_message=f"unknown callback_data: {data!r}",
                )
                await self._show_main_menu(update, context)
        except Exception as exc:  # noqa: BLE001
            # Section 22 Bot Level -- never crash the bot on a single callback.
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
                callback_data=data,
            )
            await self._reply_safe(
                update, context, "Something went wrong processing that action. Please try again."
            )

    # ---------------- free-text message handler ----------------
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle free-text messages.

        The add-coin flow's per-state ``MessageHandler`` consumes text inside
        the conversation. This handler picks up everything else -- typically
        the user typing outside any conversation.
        """
        if update.effective_user is None or update.effective_chat is None or update.message is None:
            return
        user_id = update.effective_user.id
        text = update.message.text or ""
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command=f"message:{text[:40]}",
        )

        # If we are inside an edit-coin sub-flow (tracked in user_data),
        # route to the right editor.
        flow = context.user_data.get("edit_flow")
        if flow == "timeframes":
            symbol = context.user_data.get("edit_symbol", "")
            await self._edit_coin_apply_timeframes(update, context, symbol, text)
            return
        if flow == "capital":
            symbol = context.user_data.get("edit_symbol", "")
            await self._edit_coin_apply_capital(update, context, symbol, text)
            return
        if flow == "risk":
            symbol = context.user_data.get("edit_symbol", "")
            await self._edit_coin_apply_risk(update, context, symbol, text)
            return

        # Default: nudge the user back to the main menu.
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Tap a button below to continue.",
            reply_markup=self._build_main_menu(),
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="main_menu_nudge",
        )

    # =====================================================================
    # Add Coin flow (ConversationHandler)
    # =====================================================================
    async def cmd_add_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Entry point for the Add Coin conversation.

        Sends the "Enter symbol" prompt and transitions to the ``SYMBOL``
        state. The conversation is owned by PTB's ``ConversationHandler``.
        """
        query = update.callback_query
        if query is not None:
            try:
                await query.answer()
            except Exception:  # noqa: BLE001
                pass
        user_id = query.from_user.id if query and query.from_user else 0
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="add_coin:start",
        )

        # Clear any stale edit-coin flow state.
        context.user_data.pop("edit_flow", None)
        context.user_data.pop("edit_symbol", None)

        await self._reply_safe(
            update,
            context,
            (
                "Add Coin\n\n"
                "Enter symbol (e.g. BTCUSDT):\n\n"
                "Rules:\n"
                "- Must end in USDT\n"
                "- 2-10 uppercase letters before USDT\n"
                "- Type /cancel at any time to abort."
            ),
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="add_coin_prompt_symbol",
        )
        return SYMBOL

    async def _add_coin_symbol(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Validate the symbol the user just sent and ask for timeframes."""
        user_id = update.effective_user.id if update.effective_user else 0
        text = (update.message.text or "").strip() if update.message else ""
        try:
            symbol = self._normalise_symbol(text)
        except ValueError as exc:
            await self._reply_safe(update, context, f"Invalid symbol: {exc}\n\nTry again:")
            return SYMBOL

        context.user_data["add_symbol"] = symbol
        await self._reply_safe(
            update,
            context,
            (
                f"Symbol: {symbol}\n\n"
                "Enter timeframes (minimum 3, comma-separated).\n"
                "Example: 15m,1h,4h\n\n"
                f"Valid timeframes: {', '.join(sorted(VALID_TIMEFRAMES))}"
            ),
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="add_coin_prompt_timeframes",
            symbol=symbol,
        )
        return TIMEFRAMES

    async def _add_coin_timeframes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Validate timeframes and ask for capital."""
        user_id = update.effective_user.id if update.effective_user else 0
        text = (update.message.text or "").strip() if update.message else ""
        try:
            timeframes = self._validate_timeframes(text)
        except ValueError as exc:
            await self._reply_safe(update, context, f"Invalid timeframes: {exc}\n\nTry again:")
            return TIMEFRAMES

        context.user_data["add_timeframes"] = timeframes
        await self._reply_safe(
            update,
            context,
            (
                f"Timeframes: {', '.join(timeframes)}\n\n"
                "Enter allocated capital (USDT). Must be greater than 0."
            ),
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="add_coin_prompt_capital",
            timeframes=timeframes,
        )
        return CAPITAL

    async def _add_coin_capital(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Validate capital and ask for risk percentage."""
        user_id = update.effective_user.id if update.effective_user else 0
        text = (update.message.text or "").strip() if update.message else ""
        try:
            capital = self._validate_capital(text)
        except ValueError as exc:
            await self._reply_safe(update, context, f"Invalid capital: {exc}\n\nTry again:")
            return CAPITAL

        context.user_data["add_capital"] = capital
        await self._reply_safe(
            update,
            context,
            (
                f"Capital: {capital} USDT\n\n"
                "Enter risk percentage per trade (%).\n"
                "Must be greater than 0 and at most 100."
            ),
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="add_coin_prompt_risk",
            capital=capital,
        )
        return RISK

    async def _add_coin_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Validate risk percentage and show the confirmation summary."""
        user_id = update.effective_user.id if update.effective_user else 0
        text = (update.message.text or "").strip() if update.message else ""
        try:
            risk = self._validate_risk_percent(text)
        except ValueError as exc:
            await self._reply_safe(update, context, f"Invalid risk percentage: {exc}\n\nTry again:")
            return RISK

        symbol = context.user_data.get("add_symbol", "")
        timeframes: list[str] = context.user_data.get("add_timeframes", [])
        capital: float = context.user_data.get("add_capital", 0.0)
        context.user_data["add_risk"] = risk

        summary = (
            "Please confirm the new coin:\n\n"
            f"- Symbol: {symbol}\n"
            f"- Timeframes: {', '.join(timeframes)}\n"
            f"- Capital: {capital} USDT\n"
            f"- Risk per trade: {risk}%\n"
            f"- Active: yes\n\n"
            "The coin will be evaluated across "
            f"{len(timeframes)} timeframes simultaneously."
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Confirm", callback_data=CB_CONFIRM_YES),
                    InlineKeyboardButton("Cancel", callback_data=CB_CANCEL),
                ]
            ]
        )
        await self._reply_safe(update, context, summary, reply_markup=keyboard)
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="add_coin_prompt_confirm",
            symbol=symbol,
        )
        return CONFIRM

    async def _add_coin_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle the Confirm / Cancel buttons at the end of the add-coin flow."""
        query = update.callback_query
        if query is not None:
            try:
                await query.answer()
            except Exception:  # noqa: BLE001
                pass
        data = query.data if query else CB_CANCEL
        user_id = query.from_user.id if query and query.from_user else 0

        if data == CB_CANCEL or data == CB_CONFIRM_NO:
            await self._reply_safe(
                update,
                context,
                "Add Coin cancelled.",
                reply_markup=self._build_main_menu(),
            )
            context.user_data.clear()
            return ConversationHandler.END

        # data == CB_CONFIRM_YES
        symbol = context.user_data.get("add_symbol")
        timeframes = context.user_data.get("add_timeframes")
        capital = context.user_data.get("add_capital")
        risk = context.user_data.get("add_risk")
        if not (symbol and timeframes and capital and risk):
            await self._reply_safe(
                update,
                context,
                "Missing data -- Add Coin aborted. Please start again.",
                reply_markup=self._build_main_menu(),
            )
            context.user_data.clear()
            return ConversationHandler.END

        # Build the CoinConfig -- this re-validates via Pydantic (Section 0 #6).
        try:
            coin = CoinConfig(
                symbol=symbol,
                timeframes=timeframes,
                capital=capital,
                risk_percent=risk,
                is_active=True,
            )
        except ValidationError as exc:
            await self._reply_safe(
                update,
                context,
                f"CoinConfig validation failed: {exc}",
                reply_markup=self._build_main_menu(),
            )
            context.user_data.clear()
            return ConversationHandler.END

        # Persist via the Supabase client. The bot never touches SQL directly.
        try:
            await self._supabase.upsert_coin(coin)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
                symbol=symbol,
            )
            await self._reply_safe(
                update,
                context,
                f"Failed to save coin to the database: {exc}",
                reply_markup=self._build_main_menu(),
            )
            context.user_data.clear()
            return ConversationHandler.END

        # Section 20 -- Add Coin Success template.
        success_text = (
            f"{symbol} added successfully!\n\n"
            "Details:\n"
            f"- Timeframes: {', '.join(timeframes)}\n"
            f"- Allocated Capital: {capital} USDT\n"
            f"- Risk Percentage: {risk}%\n"
            "- Status: Active\n\n"
            f"The coin will be evaluated across {len(timeframes)} timeframes simultaneously."
        )
        await self._reply_safe(
            update, context, success_text, reply_markup=self._build_main_menu()
        )
        logger.info(
            "bot_add_coin_ok",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            symbol=symbol,
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="add_coin_success",
            symbol=symbol,
        )

        # Notify the app layer so the engine can pick up the new coin live.
        await self._trigger_engine_reload(context)

        context.user_data.clear()
        return ConversationHandler.END

    async def _add_coin_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancel the Add Coin conversation from /cancel or the Cancel button."""
        user_id = update.effective_user.id if update.effective_user else 0
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="add_coin:cancel",
        )
        context.user_data.clear()
        await self._reply_safe(
            update,
            context,
            "Add Coin cancelled.",
            reply_markup=self._build_main_menu(),
        )
        return ConversationHandler.END

    # =====================================================================
    # Edit Coin flow
    # =====================================================================
    async def cmd_edit_coin(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """List every coin in the database as an inline keyboard.

        Selecting a coin transitions to ``_edit_coin_show_options``.
        """
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else 0
        )
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="edit_coin:list",
        )
        try:
            coins = await self._supabase.fetch_all_coins(only_active=False)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(
                update,
                context,
                f"Could not load coins: {exc}",
                reply_markup=self._build_main_menu(),
            )
            return

        if not coins:
            await self._reply_safe(
                update,
                context,
                "No coins configured yet. Use Add Coin to add one.",
                reply_markup=self._build_main_menu(),
            )
            return

        # Build a one-button-per-coin keyboard. Each row also shows the
        # active state so the user can see at a glance which coins are running.
        rows: list[list[InlineKeyboardButton]] = []
        for coin in coins:
            state_marker = "" if coin.is_active else " (paused)"
            rows.append(
                [
                    InlineKeyboardButton(
                        f"{coin.symbol}{state_marker}",
                        callback_data=f"{CB_EDIT_COIN_SELECT}{coin.symbol}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton("<< Back to Menu", callback_data=CB_MAIN_MENU)])
        keyboard = InlineKeyboardMarkup(rows)

        await self._reply_safe(update, context, "Edit Coin\n\nSelect a coin:", reply_markup=keyboard)
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="edit_coin_list",
            coin_count=len(coins),
        )

    async def _edit_coin_show_options(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
    ) -> None:
        """Show the Edit Timeframes / Edit Capital / Edit Risk / Delete buttons."""
        try:
            coin = await self._supabase.fetch_coin(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
                symbol=symbol,
            )
            await self._reply_safe(update, context, f"Could not load coin {symbol}: {exc}")
            return

        if coin is None:
            await self._reply_safe(update, context, f"Coin {symbol} not found.")
            return

        body = (
            f"Edit Coin -- {coin.symbol}\n\n"
            f"- Timeframes: {', '.join(coin.timeframes)}\n"
            f"- Capital: {coin.capital} USDT\n"
            f"- Risk per trade: {coin.risk_percent}%\n"
            f"- Active: {'yes' if coin.is_active else 'no'}\n\n"
            "Pick an action:"
        )
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Edit Timeframes", callback_data=f"{CB_EDIT_TIMEFRAMES}{symbol}"),
                    InlineKeyboardButton("Edit Capital", callback_data=f"{CB_EDIT_CAPITAL}{symbol}"),
                ],
                [
                    InlineKeyboardButton("Edit Risk", callback_data=f"{CB_EDIT_RISK}{symbol}"),
                    InlineKeyboardButton("Delete Coin", callback_data=f"{CB_DELETE_COIN}{symbol}"),
                ],
                [InlineKeyboardButton("<< Back", callback_data=CB_EDIT_COIN)],
            ]
        )
        await self._reply_safe(update, context, body, reply_markup=keyboard)
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=(update.callback_query.from_user.id if update.callback_query and update.callback_query.from_user else 0),
            reply_kind="edit_coin_options",
            symbol=symbol,
        )

    async def _edit_coin_ask_timeframes(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
    ) -> None:
        """Prompt the user for new timeframes and remember the in-flight flow."""
        context.user_data["edit_flow"] = "timeframes"
        context.user_data["edit_symbol"] = symbol
        await self._reply_safe(
            update,
            context,
            (
                f"Edit Timeframes for {symbol}\n\n"
                "Enter new timeframes (minimum 3, comma-separated).\n"
                f"Valid timeframes: {', '.join(sorted(VALID_TIMEFRAMES))}"
            ),
        )

    async def _edit_coin_apply_timeframes(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, text: str
    ) -> None:
        """Validate and apply new timeframes to an existing coin."""
        try:
            timeframes = self._validate_timeframes(text)
        except ValueError as exc:
            await self._reply_safe(update, context, f"Invalid timeframes: {exc}\n\nTry again:")
            return
        try:
            coin = await self._supabase.fetch_coin(symbol)
        except Exception as exc:  # noqa: BLE001
            await self._reply_safe(update, context, f"Could not load coin: {exc}")
            return
        if coin is None:
            await self._reply_safe(update, context, f"Coin {symbol} not found.")
            context.user_data.pop("edit_flow", None)
            return
        coin.timeframes = timeframes
        try:
            await self._supabase.upsert_coin(coin)
        except Exception as exc:  # noqa: BLE001
            await self._reply_safe(update, context, f"Failed to update: {exc}")
            return
        context.user_data.pop("edit_flow", None)
        await self._reply_safe(
            update,
            context,
            f"{symbol} updated. New timeframes: {', '.join(timeframes)}",
            reply_markup=self._build_main_menu(),
        )
        await self._trigger_engine_reload(context)

    async def _edit_coin_ask_capital(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
    ) -> None:
        context.user_data["edit_flow"] = "capital"
        context.user_data["edit_symbol"] = symbol
        await self._reply_safe(
            update,
            context,
            f"Edit Capital for {symbol}\n\nEnter new allocated capital (USDT), greater than 0:",
        )

    async def _edit_coin_apply_capital(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, text: str
    ) -> None:
        try:
            capital = self._validate_capital(text)
        except ValueError as exc:
            await self._reply_safe(update, context, f"Invalid capital: {exc}\n\nTry again:")
            return
        try:
            coin = await self._supabase.fetch_coin(symbol)
        except Exception as exc:  # noqa: BLE001
            await self._reply_safe(update, context, f"Could not load coin: {exc}")
            return
        if coin is None:
            await self._reply_safe(update, context, f"Coin {symbol} not found.")
            context.user_data.pop("edit_flow", None)
            return
        coin.capital = capital
        try:
            await self._supabase.upsert_coin(coin)
        except Exception as exc:  # noqa: BLE001
            await self._reply_safe(update, context, f"Failed to update: {exc}")
            return
        context.user_data.pop("edit_flow", None)
        await self._reply_safe(
            update,
            context,
            f"{symbol} updated. New capital: {capital} USDT",
            reply_markup=self._build_main_menu(),
        )
        await self._trigger_engine_reload(context)

    async def _edit_coin_ask_risk(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
    ) -> None:
        context.user_data["edit_flow"] = "risk"
        context.user_data["edit_symbol"] = symbol
        await self._reply_safe(
            update,
            context,
            f"Edit Risk for {symbol}\n\nEnter new risk percentage per trade (0 < r <= 100):",
        )

    async def _edit_coin_apply_risk(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, text: str
    ) -> None:
        try:
            risk = self._validate_risk_percent(text)
        except ValueError as exc:
            await self._reply_safe(update, context, f"Invalid risk percentage: {exc}\n\nTry again:")
            return
        try:
            coin = await self._supabase.fetch_coin(symbol)
        except Exception as exc:  # noqa: BLE001
            await self._reply_safe(update, context, f"Could not load coin: {exc}")
            return
        if coin is None:
            await self._reply_safe(update, context, f"Coin {symbol} not found.")
            context.user_data.pop("edit_flow", None)
            return
        coin.risk_percent = risk
        try:
            await self._supabase.upsert_coin(coin)
        except Exception as exc:  # noqa: BLE001
            await self._reply_safe(update, context, f"Failed to update: {exc}")
            return
        context.user_data.pop("edit_flow", None)
        await self._reply_safe(
            update,
            context,
            f"{symbol} updated. New risk per trade: {risk}%",
            reply_markup=self._build_main_menu(),
        )
        await self._trigger_engine_reload(context)

    async def _edit_coin_ask_delete(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
    ) -> None:
        """Show the 'are you sure?' prompt before deleting a coin."""
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "Yes, delete", callback_data=f"{CB_DELETE_COIN_CONFIRM}{symbol}"
                    ),
                    InlineKeyboardButton("No, keep it", callback_data=f"{CB_EDIT_COIN_SELECT}{symbol}"),
                ]
            ]
        )
        await self._reply_safe(
            update,
            context,
            (
                f"Delete {symbol}?\n\n"
                "Historical decisions and simulated trades will be PRESERVED.\n"
                "Only ws_checkpoints for this symbol will be cascade-deleted."
            ),
            reply_markup=keyboard,
        )

    async def _edit_coin_delete_confirm(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str
    ) -> None:
        """Actually delete the coin via SupabaseClient."""
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else 0
        )
        try:
            await self._supabase.delete_coin(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
                symbol=symbol,
            )
            await self._reply_safe(
                update,
                context,
                f"Failed to delete {symbol}: {exc}",
                reply_markup=self._build_main_menu(),
            )
            return

        # Section 20 -- Edit Coin flow reply template.
        await self._reply_safe(
            update,
            context,
            f"{symbol} deleted. Historical data preserved.",
            reply_markup=self._build_main_menu(),
        )
        logger.info(
            "bot_delete_coin_ok",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            symbol=symbol,
        )

        # Notify the app layer so the engine stops monitoring the deleted coin.
        await self._trigger_engine_reload(context)

    # =====================================================================
    # Start / Stop Engine
    # =====================================================================
    async def cmd_start_engine(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Start Engine button -- sets the Redis flag and calls the app callback."""
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else 0
        )
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="start_engine",
        )

        try:
            running = await self._redis.get_engine_running()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(update, context, f"Could not read engine state: {exc}")
            return

        if running:
            await self._reply_safe(update, context, "Engine is already running.")
            logger.info(
                "bot_reply",
                timestamp=datetime.now(timezone.utc),
                user_id=user_id,
                reply_kind="start_engine_already_running",
            )
            return

        # Resolve the start_engine callback: prefer the injected one, fall back
        # to context.bot_data (set by app/main.py at startup).
        callback = self._start_engine_callback or context.bot_data.get("start_engine_callback")
        if callback is None:
            await self._reply_safe(
                update,
                context,
                "Engine start handler is not registered. Contact the operator.",
            )
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type="MissingEngineCallback",
                error_message="start_engine_callback not registered",
            )
            return

        try:
            await callback()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(
                update,
                context,
                f"Engine failed to start: {exc}",
                reply_markup=self._build_main_menu(),
            )
            return

        # Render the Section 20 "Start Engine" template using the coins list.
        try:
            coins = await self._supabase.fetch_all_coins(only_active=True)
        except Exception:  # noqa: BLE001
            coins = []
        active_coins_list = (
            ", ".join(c.symbol for c in coins) if coins else "(no active coins configured)"
        )
        timeframes_set: set[str] = set()
        for c in coins:
            timeframes_set.update(c.timeframes)
        timeframes_list = (
            ", ".join(sorted(timeframes_set)) if timeframes_set else "(none)"
        )

        # Check for open trades that will resume monitoring.
        try:
            open_trades_count = await self._supabase.count_open_trades()
        except Exception:
            open_trades_count = 0

        resume_note = ""
        if open_trades_count > 0:
            resume_note = (
                f"\nResumed Monitoring: {open_trades_count} open trade(s)\n"
                "Previously open trades will continue being monitored.\n"
            )

        body = (
            "Engine Started!\n\n"
            f"Active Coins:\n{active_coins_list}\n\n"
            f"Monitored Timeframes:\n{timeframes_list}\n"
            f"{resume_note}"
            f"{SIM_WARNING_ENGINE}"
        )
        await self._reply_safe(update, context, body, reply_markup=self._build_main_menu())
        logger.info(
            "bot_engine_state_change",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            action="start",
            running=True,
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="start_engine_ok",
        )

    async def cmd_stop_engine(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Stop Engine button -- calls the app callback and clears the Redis flag."""
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else 0
        )
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="stop_engine",
        )

        try:
            running = await self._redis.get_engine_running()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(update, context, f"Could not read engine state: {exc}")
            return

        if not running:
            await self._reply_safe(update, context, "Engine is already stopped.")
            logger.info(
                "bot_reply",
                timestamp=datetime.now(timezone.utc),
                user_id=user_id,
                reply_kind="stop_engine_already_stopped",
            )
            return

        callback = self._stop_engine_callback or context.bot_data.get("stop_engine_callback")
        if callback is None:
            await self._reply_safe(
                update,
                context,
                "Engine stop handler is not registered. Contact the operator.",
            )
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type="MissingEngineCallback",
                error_message="stop_engine_callback not registered",
            )
            return

        try:
            await callback()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(
                update,
                context,
                f"Engine failed to stop cleanly: {exc}",
                reply_markup=self._build_main_menu(),
            )
            # Even on failure we never claim the engine is still "running" if
            # the flag is already cleared -- re-read the flag for accuracy.
            try:
                still_running = await self._redis.get_engine_running()
            except Exception:  # noqa: BLE001
                still_running = True
            if not still_running:
                await self._reply_safe(
                    update, context, "Engine stopped (with warnings). Checkpoints may have been saved."
                )
            return

        # Count open simulated trades for the Section 20 template.
        try:
            open_trades_count = await self._supabase.count_open_trades()
        except Exception:  # noqa: BLE001
            open_trades_count = 0

        if open_trades_count > 0:
            body = (
                "Engine Stopped Safely.\n\n"
                "Checkpoints saved.\n"
                f"Open Trades: {open_trades_count} (kept open, monitoring paused)\n\n"
                "Open trades remain active — they will be monitored again\n"
                "when the engine resumes. Press Start to resume."
            )
        else:
            body = (
                "Engine Stopped Safely.\n\n"
                "Checkpoints saved.\n"
                "Open Trades: 0\n\n"
                "You can resume operation later without data loss."
            )
        await self._reply_safe(update, context, body, reply_markup=self._build_main_menu())
        logger.info(
            "bot_engine_state_change",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            action="stop",
            running=False,
        )
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="stop_engine_ok",
        )

    # =====================================================================
    # Live Prices
    # =====================================================================
    async def cmd_live_prices(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the last cached price for each active coin.

        Per Section 7: reads from Redis only -- never makes a fresh REST call.
        """
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else 0
        )
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="live_prices",
        )
        try:
            coins = await self._supabase.fetch_all_coins(only_active=True)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(
                update,
                context,
                f"Could not load coins: {exc}",
                reply_markup=self._build_main_menu(),
            )
            return

        prices: dict[str, Optional[tuple[float, datetime]]] = {}
        for coin in coins:
            try:
                prices[coin.symbol] = await self._redis.get_live_price(coin.symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="bot.telegram_bot",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    symbol=coin.symbol,
                )
                prices[coin.symbol] = None

        body = self._format_live_prices(prices)
        await self._reply_safe(update, context, body, reply_markup=self._build_main_menu())
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="live_prices",
            coin_count=len(coins),
        )

    # =====================================================================
    # Trade History
    # =====================================================================
    async def cmd_trade_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the last 10 simulated trades with the mandatory warning."""
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else 0
        )
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="trade_history",
        )
        try:
            trades = await self._supabase.fetch_recent_trades(limit=10)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(
                update,
                context,
                f"Could not load trade history: {exc}",
                reply_markup=self._build_main_menu(),
            )
            return

        # Enhanced reporting using the new analysis package
        summary = await self._aggregator.get_performance_summary()
        body = self._formatter.format_summary_telegram(summary)
        body += "\n\n" + self._format_trade_history(trades)
        
        await self._reply_safe(update, context, body, reply_markup=self._build_main_menu())
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="trade_history",
            trade_count=len(trades),
        )

    # =====================================================================
    # System Performance
    # =====================================================================
    async def _system_performance_prompt_period(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show the period selector for the System Performance report."""
        rows = [
            [InlineKeyboardButton(label, callback_data=f"{CB_SYS_PERF_PERIOD}{key}")]
            for key, label in PERF_PERIODS.items()
        ]
        rows.append([InlineKeyboardButton("<< Back to Menu", callback_data=CB_MAIN_MENU)])
        await self._reply_safe(
            update,
            context,
            "System Performance\n\nSelect a period:",
            reply_markup=InlineKeyboardMarkup(rows),
        )

    async def cmd_system_performance(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE, period_key: str = "all"
    ) -> None:
        """Compute and render the system performance report.

        Delegates all number-crunching to ``PerformanceCalculator``. The bot
        only formats the result -- no trading logic here (Section 0 #1).
        """
        user_id = (
            update.callback_query.from_user.id
            if update.callback_query and update.callback_query.from_user
            else 0
        )
        logger.info(
            "bot_command",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            command="system_performance",
            period=period_key,
        )

        period_start, period_end = self._resolve_period(period_key)
        try:
            metrics = await self._performance_calc.calculate_metrics(
                symbol=None, period_start=period_start, period_end=period_end
            )
            per_coin_metrics = await self._performance_calc.calculate_for_all_symbols(
                period_start=period_start, period_end=period_end
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            await self._reply_safe(
                update,
                context,
                f"Could not compute performance: {exc}",
                reply_markup=self._build_main_menu(),
            )
            return

        period_label = PERF_PERIODS.get(period_key, "All-Time")
        body = self._format_performance(
            metrics, 
            period_label=period_label,
            per_coin_metrics=per_coin_metrics
        )
        await self._reply_safe(update, context, body, reply_markup=self._build_main_menu())
        logger.info(
            "bot_reply",
            timestamp=datetime.now(timezone.utc),
            user_id=user_id,
            reply_kind="system_performance",
            period=period_key,
            total_trades=metrics.total_trades,
        )

    # =====================================================================
    # Helpers -- navigation
    # =====================================================================
    async def _show_main_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Re-display the main menu."""
        if update.effective_chat is None:
            return
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Main Menu",
            reply_markup=self._build_main_menu(),
        )

    async def _reply_safe(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Send a reply, editing the callback message if possible.

        Per Section 22 Bot Level: retries 3 times on Telegram API failure and
        logs the error. Never raises out -- the bot must keep serving other
        users.
        """
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id is None:
            return

        # Prefer editing the existing callback message so we don't pile up
        # new messages when the user is tapping buttons.
        query = update.callback_query
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                if query is not None and update.effective_chat is not None:
                    try:
                        await query.edit_message_text(
                            text=text, reply_markup=reply_markup, parse_mode=ParseMode.HTML
                        )
                        return
                    except Exception as exc:  # noqa: BLE001
                        # Log the original edit failure for diagnosis
                        error_msg = str(exc)
                        if "Message is not modified" in error_msg:
                            logger.debug(
                                "telegram_edit_skipped",
                                chat_id=chat_id,
                                message_id=query.message.message_id if query.message else None,
                                note="Message content identical, skipping edit"
                            )
                            return
                        
                        logger.warning(
                            "telegram_edit_failed_fallback",
                            timestamp=datetime.now(timezone.utc),
                            chat_id=chat_id,
                            message_id=query.message.message_id if query.message else None,
                            error_type=type(exc).__name__,
                            error_message=error_msg,
                            note="Falling back to send_message"
                        )
                        # edit_message_text fails when the text is identical or
                        # the message is too old -- fall back to a fresh send.
                        pass
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "error",
                    timestamp=datetime.now(timezone.utc),
                    module="bot.telegram_bot",
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    attempt=attempt,
                )
        if last_exc is not None:
            logger.error(
                "error",
                timestamp=datetime.now(timezone.utc),
                module="bot.telegram_bot",
                error_type="TelegramApiRetriesExhausted",
                error_message=f"giving up after 3 attempts: {last_exc}",
            )

    # =====================================================================
    # Helpers -- formatting (Section 20 templates)
    # =====================================================================
    def _build_main_menu(self) -> InlineKeyboardMarkup:
        """Return the main-menu inline keyboard (Section 7 full menu)."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Add Coin", callback_data=CB_ADD_COIN),
                    InlineKeyboardButton("Edit Coin", callback_data=CB_EDIT_COIN),
                ],
                [
                    InlineKeyboardButton("Start Engine", callback_data=CB_START_ENGINE),
                    InlineKeyboardButton("Stop Engine", callback_data=CB_STOP_ENGINE),
                ],
                [
                    InlineKeyboardButton("Live Prices", callback_data=CB_LIVE_PRICES),
                    InlineKeyboardButton("Trade History", callback_data=CB_TRADE_HISTORY),
                ],
                [InlineKeyboardButton("System Performance", callback_data=CB_SYS_PERF)],
            ]
        )

    def _format_trade_history(self, trades: list[SimulatedTrade]) -> str:
        """Render the last-N trades per the Section 20 Trade History template for Spot.

        Always appends the mandatory simulation warning -- Section 0 #7.
        """
        if not trades:
            return (
                "Trade History (Last 10)\n\n"
                "No trades recorded yet.\n\n"
                f"{SIM_WARNING_LIST}"
            )
        lines: list[str] = ["Trade History (Last 10)", ""]
        for idx, t in enumerate(trades, start=1):
            entry = self._fmt_price(t.entry_price)
            stop = self._fmt_price(t.stop_loss) if t.stop_loss is not None else "n/a"
            target = self._fmt_price(t.take_profit) if t.take_profit is not None else "n/a"
            if t.status == "closed":
                pnl = t.pnl if t.pnl is not None else 0.0
                pnl_line = f"PnL: {pnl:+.4f} USDT"
                close_price_str = self._fmt_price(t.close_price) if t.close_price is not None else "n/a"
                status_line = f"Status: closed ({t.close_reason or 'manual'})"
            else:
                pnl_line = ""
                close_price_str = "n/a"
                status_line = "Status: open"

            lines.append(f"{idx}. {t.symbol} (SPOT)")
            lines.append(f"   Quantity: {self._fmt_price(t.size)}")
            lines.append(f"   Entry: {entry}")
            lines.append(f"   Stop Loss (Current): {stop}")
            # Show initial stop loss if it differs from current
            if t.initial_stop_loss is not None:
                initial_stop = self._fmt_price(t.initial_stop_loss)
                if t.stop_loss is not None and abs(t.initial_stop_loss - t.stop_loss) > 0.00001:
                    lines.append(f"   Stop Loss (Initial): {initial_stop}")
            lines.append(f"   Target: {target}")
            lines.append(f"   Close Price: {close_price_str}")
            lines.append(f"   {status_line}")
            if pnl_line:
                lines.append(f"   {pnl_line}")
            lines.append("")

        lines.append(SIM_WARNING_LIST)
        return "\n".join(lines)

    def _format_performance(
        self, 
        metrics: PerformanceMetrics, 
        period_label: str = "All-Time",
        per_coin_metrics: Optional[dict[str, PerformanceMetrics]] = None
    ) -> str:
        """Render the system performance report with per-coin breakdown."""
        win_rate_pct = metrics.win_rate * 100.0 if metrics.win_rate else 0.0
        lines = [
            f"📊 <b>System Performance ({period_label})</b>",
            "━━━━━━━━━━━━━━━",
            f"📈 <b>Total Trades:</b> {metrics.total_trades}",
            f"✅ <b>Wins:</b> {metrics.winning_trades} | ❌ <b>Losses:</b> {metrics.losing_trades}",
            f"🎯 <b>Win Rate:</b> <code>{win_rate_pct:.2f}%</code>",
            f"💰 <b>Total PnL:</b> <code>{metrics.total_pnl:+.4f} USDT</code>",
            f"📉 <b>Max Drawdown:</b> <code>{metrics.max_drawdown:.2f} USDT ({metrics.max_drawdown_percent:.2f}%)</code>",
            "",
        ]

        if per_coin_metrics:
            lines.append("🪙 <b>Per-Coin Breakdown:</b>")
            lines.append("━━━━━━━━━━━━━━━")
            for symbol, m in per_coin_metrics.items():
                wr = m.win_rate * 100.0 if m.win_rate else 0.0
                lines.append(f"<b>{symbol}:</b>")
                lines.append(f"  Trades: {m.total_trades} (W:{m.winning_trades}/L:{m.losing_trades})")
                lines.append(f"  PnL: <code>{m.total_pnl:+.4f}</code> | WR: <code>{wr:.1f}%</code>")
            lines.append("")

        lines.append(f"<i>{SIM_WARNING_PERF}</i>")
        return "\n".join(lines)

    def _format_live_prices(
        self, prices: dict[str, Optional[tuple[float, datetime]]]
    ) -> str:
        """Render live prices per Section 20.

        ``prices`` maps ``symbol`` -> ``(price, timestamp)`` or ``None`` when
        no cached price is available.
        """
        if not prices:
            return (
                "Live Prices\n\n"
                "No active coins configured. Use Add Coin first."
            )
        lines: list[str] = ["Live Prices", ""]
        for symbol, value in prices.items():
            if value is None:
                lines.append(f"{symbol}: (no cached price)")
                lines.append("Last Update: n/a")
                lines.append("")
                continue
            price, ts = value
            lines.append(f"{symbol}: {self._fmt_price(price)} USDT")
            lines.append(f"Last Update: {ts.strftime('%Y-%m-%d %H:%M:%S UTC')}")
            lines.append("")
        return "\n".join(lines).rstrip()

    @staticmethod
    def format_trade_opened(trade: SimulatedTrade, confidence: Optional[float] = None) -> str:
        """Format a trade-opened notification for Spot."""
        entry = CTTelegramBot._fmt_price(trade.entry_price)
        stop = CTTelegramBot._fmt_price(trade.stop_loss) if trade.stop_loss is not None else "n/a"
        target = CTTelegramBot._fmt_price(trade.take_profit) if trade.take_profit is not None else "n/a"
        opened_time = trade.opened_at.strftime('%Y-%m-%d %H:%M:%S UTC') if trade.opened_at else "n/a"
        confidence_str = f"{confidence * 100:.1f}%" if confidence is not None else "n/a"
        
        return (
            "🚀 <b>Spot Trade Opened!</b>\n\n"
            f"<b>Coin:</b> {trade.symbol}\n"
            f"<b>Quantity:</b> {CTTelegramBot._fmt_price(trade.size)}\n"
            f"<b>Entry Price:</b> {entry}\n"
            f"<b>Confidence:</b> {confidence_str}\n"
            f"<b>Opened At:</b> {opened_time}\n\n"
            f"<b>Stop Loss (Initial):</b> {stop}\n"
            f"<b>Target:</b> {target}\n\n"
            f"<i>{SIM_WARNING_TRADE}</i>"
        )

    @staticmethod
    def format_trade_closed(trade: SimulatedTrade) -> str:
        """Format a trade-closed notification for Spot."""
        entry = CTTelegramBot._fmt_price(trade.entry_price)
        closed_time = trade.closed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if trade.closed_at else "n/a"
        pnl = trade.pnl if trade.pnl is not None else 0.0
        reason = (trade.close_reason or "manual").upper()
        
        # Display both initial and current stop loss if they differ
        stop_current = CTTelegramBot._fmt_price(trade.stop_loss) if trade.stop_loss is not None else "n/a"
        stop_initial = CTTelegramBot._fmt_price(trade.initial_stop_loss) if trade.initial_stop_loss is not None else "n/a"
        
        stop_info = f"<b>Stop Loss (Current):</b> {stop_current}\n"
        if trade.initial_stop_loss is not None and trade.stop_loss is not None:
            if abs(trade.initial_stop_loss - trade.stop_loss) > 0.00001:  # Account for floating point precision
                stop_info += f"<b>Stop Loss (Initial):</b> {stop_initial}\n"
        
        icon = "✅" if pnl > 0 else "❌" if pnl < 0 else "➖"
        close_price = CTTelegramBot._fmt_price(trade.close_price) if trade.close_price is not None else "n/a"
        
        return (
            f"{icon} <b>Spot Trade Closed!</b>\n\n"
            f"<b>Coin:</b> {trade.symbol}\n"
            f"<b>Close Reason:</b> {reason}\n"
            f"<b>Closed At:</b> {closed_time}\n\n"
            f"<b>Entry Price:</b> {entry}\n"
            f"<b>Close Price:</b> {close_price}\n"
            f"{stop_info}"
            f"<b>PnL:</b> <code>{pnl:+.4f} USDT</code>\n\n"
            f"<i>{SIM_WARNING_TRADE}</i>"
        )

    @staticmethod
    def format_trade_alert(
        symbol: str,
        direction: str,
        entry_price: float,
        confidence: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        risk_reward: Optional[float],
    ) -> str:
        """Format a new-signal push notification for Spot."""
        rr_line = (
            f"Risk/Reward: 1:{risk_reward:.2f}"
            if risk_reward is not None
            else "Risk/Reward: n/a"
        )
        return (
            "🚀 <b>New Spot Signal!</b>\n\n"
            f"<b>Coin:</b> {symbol}\n"
            f"<b>Price:</b> {CTTelegramBot._fmt_price(entry_price)}\n"
            f"<b>Confidence:</b> {confidence * 100.0:.1f}%\n\n"
            f"<b>Stop Loss:</b> {CTTelegramBot._fmt_price(stop_loss)}\n"
            f"<b>Target:</b> {CTTelegramBot._fmt_price(take_profit)}\n"
            f"{rr_line}\n\n"
            f"<i>{SIM_WARNING_TRADE}</i>"
        )

    # =====================================================================
    # Helpers -- validation
    # =====================================================================
    def _normalise_symbol(self, raw: str) -> str:
        """Uppercase and validate a symbol string.

        Raises
        ------
        ValueError
            If the symbol does not match ``^[A-Z]{2,10}USDT$`` after uppercasing.
        """
        symbol = (raw or "").strip().upper()
        if not self._validate_symbol(symbol):
            raise ValueError(
                "symbol must match ^[A-Z]{2,10}USDT$ (e.g. BTCUSDT, ETHUSDT)"
            )
        return symbol

    @staticmethod
    def _validate_symbol(symbol: str) -> bool:
        """Return True if ``symbol`` matches the strict USDT format.

        Used by ``_normalise_symbol`` and exposed for unit tests.
        """
        return bool(SYMBOL_RE.match(symbol))

    @staticmethod
    def _validate_timeframes(tf_str: str) -> list[str]:
        """Parse a comma-separated timeframes string and validate it.

        Validation rules (mirror CoinConfig's Pydantic validator so we fail
        early, before constructing the model):
          * at least 3 timeframes
          * all distinct (case-insensitive)
          * every entry must be in ``VALID_TIMEFRAMES`` (config/thresholds.py)

        Returns
        -------
        list[str]
            Lowercased, de-duplicated, order-preserved list of timeframes.

        Raises
        ------
        ValueError
            On any validation failure with a descriptive message.
        """
        parts = [p.strip().lower() for p in (tf_str or "").split(",") if p.strip()]
        if len(parts) < 3:
            raise ValueError("at least 3 timeframes are required per coin")
        # Distinctness check (case-insensitive).
        seen: set[str] = set()
        for p in parts:
            if p in seen:
                raise ValueError(f"duplicate timeframe: {p}")
            seen.add(p)
        invalid = [p for p in parts if p not in VALID_TIMEFRAMES]
        if invalid:
            raise ValueError(
                f"invalid timeframes: {invalid}. "
                f"Valid: {sorted(VALID_TIMEFRAMES)}"
            )
        return parts

    @staticmethod
    def _validate_capital(s: str) -> float:
        """Validate the allocated-capital input and return it as float.

        Raises
        ------
        ValueError
            If the input is not a number or is not greater than 0.
        """
        try:
            capital = float((s or "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("capital must be a number") from exc
        if capital <= 0:
            raise ValueError("capital must be greater than 0")
        return capital

    @staticmethod
    def _validate_risk_percent(s: str) -> float:
        """Validate the risk-per-trade percentage input.

        Raises
        ------
        ValueError
            If the input is not a number or is outside (0, 100].
        """
        try:
            risk = float((s or "").strip())
        except (TypeError, ValueError) as exc:
            raise ValueError("risk percentage must be a number") from exc
        if risk <= 0 or risk > 100:
            raise ValueError("risk percentage must be greater than 0 and at most 100")
        return risk

    # =====================================================================
    # Helpers -- misc
    # =====================================================================
    @staticmethod
    def _resolve_period(period_key: str) -> tuple[Optional[datetime], Optional[datetime]]:
        """Map a period-key to ``(period_start, period_end)`` datetimes.

        ``period_end`` is always ``None`` (i.e. up to now) and ``period_start``
        is computed against ``datetime.now(utc)``. ``"all"`` returns
        ``(None, None)`` (i.e. no filter).
        """
        if period_key == "all":
            return None, None
        if period_key == "1d":
            return timedelta_days(1), None
        if period_key == "7d":
            return timedelta_days(7), None
        if period_key == "30d":
            return timedelta_days(30), None
        if period_key == "90d":
            return timedelta_days(90), None
        # Unknown key -- default to all-time.
        return None, None

    @staticmethod
    def _fmt_price(value: Optional[float]) -> str:
        """Format a price with up to 8 significant decimals, never scientific."""
        if value is None:
            return "n/a"
        if value == 0:
            return "0"
        # 8 decimals is enough for crypto (satoshis) and small alt prices.
        return f"{value:.8f}".rstrip("0").rstrip(".")

    @staticmethod
    def format_trailing_stop_update(trade: SimulatedTrade, old_stop_loss: float) -> str:
        """Format a trailing-stop update notification.
        
        Shows the new stop loss and compares it with the initial stop loss
        to demonstrate that the system is protecting profits correctly.
        """
        direction = trade.direction.upper()
        new_stop = CTTelegramBot._fmt_price(trade.stop_loss) if trade.stop_loss is not None else "n/a"
        old_stop = CTTelegramBot._fmt_price(old_stop_loss)
        initial_stop = CTTelegramBot._fmt_price(trade.initial_stop_loss) if trade.initial_stop_loss is not None else "n/a"
        
        return (
            f"📍 <b>Trailing Stop Updated!</b>\n\n"
            f"<b>Coin:</b> {trade.symbol}\n"
            f"<b>Direction:</b> {direction}\n\n"
            f"<b>Stop Loss (Initial):</b> {initial_stop}\n"
            f"<b>Stop Loss (Previous):</b> {old_stop}\n"
            f"<b>Stop Loss (New):</b> {new_stop}\n\n"
            f"<i>{SIM_WARNING_TRADE}</i>"
        )


# ---------------------------------------------------------------------------
# Small helpers kept at module level for testability
# ---------------------------------------------------------------------------
def timedelta_days(days: int) -> datetime:
    """Return ``datetime.now(utc) - timedelta(days=days)``.

    Kept as a tiny helper so the validation tests can monkeypatch it without
    importing ``datetime`` internals.
    """
    from datetime import timedelta

    return datetime.now(timezone.utc) - timedelta(days=days)


__all__ = ["CTTelegramBot"]
