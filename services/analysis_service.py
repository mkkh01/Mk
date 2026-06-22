"""
خدمة التحليل — تنسيق دورة التحليل الكاملة.
بيانات السوق ← محلل السوق ← استراتيجيات.
V4.0: دعم الأطر الزمنية المتعددة — تحليل منفصل لكل إطار زمني.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from engines.market_analyzer import MarketAnalyzer
from engines.market_data_engine import MarketDataEngine
from engines.strategy_engine import StrategyEngine
from database.repositories import CoinRepository, get_session
from core.types import MarketAnalysis

logger = logging.getLogger("تحليل_الخدمة")


class AnalysisService:
    """تنسيق تدفق التحليل بين المحركات — دعم كامل للأطر الزمنية المتعددة."""

    def __init__(self, market_data_engine: MarketDataEngine,
                 market_analyzer: MarketAnalyzer,
                 strategy_engine: StrategyEngine):
        self.market_data = market_data_engine
        self.analyzer = market_analyzer
        self.strategies = strategy_engine
        self._analyses: dict[str, dict[str, MarketAnalysis]] = {}
        self._signals: dict[str, dict[str, list]] = {}
        self._last_analysis: dict[str, dict[str, datetime]] = {}
        self._active_symbols: set[str] = set()
        self._active_coins: dict[str, object] = {}

    async def sync_symbols_from_db(self, telegram_id: int) -> tuple[list[str], list]:
        """تحميل العملات النشطة من قاعدة البيانات وتسجيلها في محرك بيانات السوق مع جميع أطرها الزمنية."""
        symbols: list[str] = []
        active_coins: list = []
        all_timeframes: set[str] = set()

        try:
            async for session in get_session():
                coins = await CoinRepository.get_all_active(session, telegram_id)
                if not coins:
                    logger.warning(f"[مزامنة] ⚠️ لا توجد عملات نشطة للمستخدم {telegram_id}")
                    return [], []

                for coin in coins:
                    symbols.append(coin.symbol)
                    active_coins.append(coin)
                    self._active_coins[coin.symbol] = coin
                    timeframes = getattr(coin, 'timeframes', ["15m"])
                    if not timeframes:
                        timeframes = ["15m"]
                    if isinstance(timeframes, str):
                        timeframes = [timeframes]
                    for tf in timeframes:
                        all_timeframes.add(str(tf))

                self._active_symbols = set(symbols)
                primary_timeframes: dict[str, str] = {}
                for coin in active_coins:
                    tfs = getattr(coin, 'timeframes', ["15m"])
                    if not tfs: tfs = ["15m"]
                    if isinstance(tfs, str): tfs = [tfs]
                    primary_timeframes[coin.symbol] = str(tfs[0])

                self.market_data.update_symbols(symbols, primary_timeframes)
                self.analyzer.update_symbols(symbols)

                tfs_display = ", ".join(sorted(all_timeframes)) if all_timeframes else "15m"
                logger.info(f"[مزامنة] ✅ تم تحميل {len(symbols)} عملة | الأطر الزمنية: {tfs_display}")

        except Exception as e:
            logger.error(f"[مزامنة] ❌ خطأ في تحميل العملات: {e}", exc_info=True)
            return [], []

        return symbols, active_coins

    async def run_analysis_cycle_for_timeframe(self, symbol: str,
                                                timeframe: str) -> Optional[MarketAnalysis]:
        """تشغيل دورة تحليل كاملة لرمز واحد في إطار زمني محدد."""
        if symbol not in self._analyses:
            self._analyses[symbol] = {}
        if symbol not in self._signals:
            self._signals[symbol] = {}
        if symbol not in self._last_analysis:
            self._last_analysis[symbol] = {}

        try:
            analysis = await self.analyzer.analyze(symbol)
            if analysis:
                self._analyses[symbol][timeframe] = analysis
                self._last_analysis[symbol][timeframe] = datetime.utcnow()
                signals = await self.strategies.run_strategies(symbol, analysis)
                if signals:
                    self._signals[symbol][timeframe] = signals
                    buy_count = sum(1 for s in signals if s.action == "BUY")
                    sell_count = sum(1 for s in signals if s.action == "SELL")
                    logger.info(
                        f"[{symbol}] [{timeframe}] 🧠 إشارات: "
                        f"شراء={buy_count} | بيع={sell_count} | الثقة={analysis.confidence:.0f}%"
                    )
                else:
                    logger.debug(f"[{symbol}] [{timeframe}] ⏸️ لا توجد إشارات")
                return analysis
            else:
                logger.debug(f"[{symbol}] [{timeframe}] ⚠️ لا توجد بيانات كافية للتحليل")
                return None
        except Exception as e:
            logger.error(f"[{symbol}] [{timeframe}] ❌ خطأ في التحليل: {e}", exc_info=True)
            return None

    async def run_full_analysis_cycle(self, symbol: str) -> dict[str, Optional[MarketAnalysis]]:
        """تشغيل دورة تحليل لجميع الأطر الزمنية للعملة دفعة واحدة."""
        results: dict[str, Optional[MarketAnalysis]] = {}
        coin = self._active_coins.get(symbol)
        if coin is None:
            logger.warning(f"[{symbol}] ⚠️ العملة غير موجودة في الذاكرة — تخطي التحليل")
            return results

        timeframes = getattr(coin, 'timeframes', ["15m"])
        if not timeframes: timeframes = ["15m"]
        if isinstance(timeframes, str): timeframes = [timeframes]
        tfs_str = [str(t) for t in timeframes]

        logger.debug(f"[{symbol}] 🔍 بدء تحليل {len(tfs_str)} أطر زمنية: {', '.join(tfs_str)}")
        for tf in tfs_str:
            result = await self.run_analysis_cycle_for_timeframe(symbol, tf)
            results[tf] = result
            await asyncio.sleep(0.1)

        return results

    def get_analysis(self, symbol: str, timeframe: str = None) -> Optional[MarketAnalysis]:
        """استرجاع آخر تحليل."""
        per_tf = self._analyses.get(symbol, {})
        if timeframe:
            return per_tf.get(timeframe)
        for tf in ["1h", "4h", "15m", "1d"]:
            if tf in per_tf:
                return per_tf[tf]
        if per_tf:
            return next(iter(per_tf.values()))
        return None

    def get_all_analyses(self, symbol: str) -> dict[str, MarketAnalysis]:
        return self._analyses.get(symbol, {})

    def get_all_signals(self, symbol: str) -> dict[str, list]:
        return self._signals.get(symbol, {})

    def get_active_symbols(self) -> list[str]:
        return list(self._active_symbols)

    def get_active_coins(self) -> dict[str, object]:
        return dict(self._active_coins)
