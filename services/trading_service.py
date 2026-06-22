"""
Trading Service — orchestrates the full trade pipeline.
Evidence → Risk → Execution.
"""
import logging
from datetime import datetime

from engines.evidence_engine import EvidenceEngine
from engines.risk_engine import RiskEngine
from engines.execution_engine import ExecutionEngine
from engines.market_analyzer import MarketAnalyzer
from engines.strategy_engine import StrategyEngine
from engines.market_data_engine import MarketDataEngine
from core.types import EvidenceResult, RiskDecision, ExecutionResult
from database.repositories import CoinRepository, UserRepository, get_session
from config.constants import ADMIN_ID

logger = logging.getLogger("trading_service")


class TradingService:
    """Coordinates the trading pipeline."""

    def __init__(self, evidence_engine: EvidenceEngine,
                 risk_engine: RiskEngine,
                 execution_engine: ExecutionEngine,
                 market_analyzer: MarketAnalyzer,
                 strategy_engine: StrategyEngine,
                 market_data_engine: MarketDataEngine):
        self.evidence = evidence_engine
        self.risk = risk_engine
        self.execution = execution_engine
        self.analyzer = market_analyzer
        self.strategies = strategy_engine
        self.market_data = market_data_engine

    async def process_symbol(self, symbol: str, user_id: str) -> tuple:
        """
        Full trading pipeline for a single symbol.
        Analysis → Strategies → Evidence → Risk → Execution.
        Returns (EvidenceResult, RiskDecision, ExecutionResult) or None.
        """
        # 1. Get market analysis
        analysis = self.analyzer.get_analysis(symbol)
        if not analysis:
            logger.debug(f"No analysis available for {symbol}")
            return None

        # 2. Run strategies
        signals = await self.strategies.run_strategies(symbol, analysis)
        if not signals:
            return None

        # 3. Evaluate evidence
        whale_events = []  # Future: feed from whale engine
        evidence = await self.evidence.evaluate(analysis, signals, whale_events)

        if evidence.decision in ("HOLD", "IGNORE"):
            return (evidence, None, None)

        # 4. Get coin config for risk calculation
        capital = 100.0
        risk_pct = 1.0
        try:
            async for session in get_session():
                coin = await CoinRepository.get_by_symbol(session, user_id, symbol)
                if coin:
                    capital = coin.capital_allocated
                    risk_pct = coin.risk_per_trade
        except Exception as e:
            logger.warning(f"Could not load coin config for {symbol}: {e}")

        # 5. Risk evaluation
        price = self.market_data.get_price(symbol) or 0.0
        risk_decision = await self.risk.evaluate(
            evidence, entry_price=price, capital=capital,
            risk_percentage=risk_pct,
        )

        if not risk_decision.trade_allowed:
            return (evidence, risk_decision, None)

        # 6. Execute — pass telegram_id as int (repository resolves to UUID)
        strategy_name = signals[0].strategy_name if signals else "unknown"
        tid = int(user_id) if user_id else 0
        execution = await self.execution.execute_simulated(
            risk_decision, symbol=symbol, entry_price=price,
            strategy=strategy_name, telegram_id=tid,
            entry_reason=evidence.reasoning,
        )

        return (evidence, risk_decision, execution)

    def get_status(self) -> dict:
        return {
            "evidence_decisions": self.evidence.decision_count,
            "risk_blocked": self.risk._trading_blocked,
            "execution_metrics": self.execution.get_metrics(),
        }
