"""
Analysis Service — orchestrates market analysis pipeline.
Market Data → Analyzer → Strategies.
"""
import asyncio
import logging
from datetime import datetime

from engines.market_analyzer import MarketAnalyzer
from engines.market_data_engine import MarketDataEngine
from engines.strategy_engine import StrategyEngine
from database.repositories import CoinRepository, get_session

logger = logging.getLogger("analysis_service")


class AnalysisService:
    """Coordinates analysis flow between engines."""

    def __init__(self, market_data_engine: MarketDataEngine,
                 market_analyzer: MarketAnalyzer,
                 strategy_engine: StrategyEngine):
        self.market_data = market_data_engine
        self.analyzer = market_analyzer
        self.strategies = strategy_engine

    async def sync_symbols_from_db(self, user_id: str):
        """Load active coins from database into engines."""
        try:
            async for session in get_session():
                coins = await CoinRepository.get_all_active(session, user_id)
                symbols = [c.symbol for c in coins]
                timeframes = {c.symbol: c.timeframe for c in coins}

                self.market_data.update_symbols(symbols, timeframes)
                self.analyzer.update_symbols(symbols)
                logger.info(f"Synced {len(symbols)} symbols from DB")
                return symbols, coins
        except Exception as e:
            logger.error(f"Symbol sync error: {e}")
            return [], []

    async def run_analysis_cycle(self, symbol: str):
        """Run one complete analysis cycle for a symbol."""
        analysis = await self.analyzer.analyze(symbol)
        if analysis:
            await self.strategies.run_strategies(symbol, analysis)
        return analysis
