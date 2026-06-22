"""
محرك التعلم — يجمع الصفقات التاريخية، يقيم الأداء لكل إطار زمني،
وينتج توصيات تحسين دون تغيير الاستراتيجيات تلقائياً.

V4.0 — تقييم منفصل لكل استراتيجية × إطار زمني.
توصيات بالعربية مع Sharpe-like score لكل إطار.
وسوم السجلات: [تعلم] | [تقييم] | [توصية]
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from core.base import BaseEngine
from core.events import (
    ExecutionEvent, PortfolioEvent, EventBus, HealthEvent, HealthStatus
)
from database.repositories import (
    TradeRepository, SignalRepository, StrategyStatRepository, get_session
)

logger = logging.getLogger("محرك_التعلم")


class LearningEngine(BaseEngine):
    """
    محرك تقييم الأداء التاريخي.
    يُنتج توصيات فقط — لا يُغير الاستراتيجيات تلقائياً.
    """

    def __init__(self, event_bus: EventBus):
        super().__init__("محرك_التعلم")
        self.event_bus = event_bus
        # أداء الاستراتيجيات: { (strategy_name, timeframe): {metrics} }
        self._strategy_tf_performance: dict[tuple, dict] = {}
        # أداء الاستراتيجيات القديم للتوافق الخلفي (بدون إطار زمني)
        self._strategy_performance: dict[str, dict] = {}
        # أداء الرموز
        self._symbol_performance: dict[str, dict] = {}
        # التوصيات المُنتجة
        self._recommendations: list[str] = []
        # توقيت آخر تقييم
        self._last_evaluation: datetime = datetime.utcnow()

    # ─────────────────────────────────────────────────────────
    #  دورة الحياة
    # ─────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        await self.event_bus.subscribe("ExecutionEvent", self._on_execution)
        self.logger.info("[تعلم] تم تهيئة محرك التعلم.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._evaluation_loop())
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[تعلم] بدأ محرك التعلم في العمل.")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("[تعلم] توقف محرك التعلم.")

    async def _on_execution(self, event: ExecutionEvent):
        """التعلم من أحداث التنفيذ (تُعالج في حلقة التقييم)."""
        pass

    # ─────────────────────────────────────────────────────────
    #  حلقة التقييم الدورية
    # ─────────────────────────────────────────────────────────

    async def _evaluation_loop(self):
        """تشغيل التقييم الشامل كل 5 دقائق."""
        while self._running:
            try:
                await self.evaluate_all()
            except Exception as e:
                self.logger.error(f"[تعلم] خطأ في حلقة التقييم: {e}")
            await asyncio.sleep(300)

    # ─────────────────────────────────────────────────────────
    #  التقييم الشامل
    # ─────────────────────────────────────────────────────────

    async def evaluate_all(self):
        """
        تقييم جميع الصفقات المغلقة:
        1. تجميع حسب الاستراتيجية + الإطار الزمني
        2. حساب المقاييس لكل مجموعة
        3. إنتاج توصيات تحسين
        """
        try:
            async for session in get_session():
                trades = await TradeRepository.get_all_closed(
                    session,
                    self.user_id if hasattr(self, 'user_id') else ""
                )

                if not trades:
                    return

                # ── تجميع حسب الاستراتيجية (قديم للتوافق الخلفي) ──
                strats: dict[str, list] = {}
                for t in trades:
                    strat = t.strategy_used or "غير_معروف"
                    strats.setdefault(strat, []).append(t)

                # ── تجميع حسب (الاستراتيجية, الإطار الزمني) ──
                strat_tf_groups: dict[tuple, list] = {}
                for t in trades:
                    strat = t.strategy_used or "غير_معروف"
                    # محاولة استخراج الإطار الزمني من السياق أو الافتراضي
                    tf = self._extract_timeframe(t)
                    key = (strat, tf)
                    strat_tf_groups.setdefault(key, []).append(t)

                # تقييم كل استراتيجية (قديم)
                for strat_name, strat_trades in strats.items():
                    await self._evaluate_strategy(session, strat_name, strat_trades)

                # تقييم كل (استراتيجية × إطار زمني) — V4.0
                for (strat_name, tf), tf_trades in strat_tf_groups.items():
                    await self._evaluate_strategy_tf(
                        session, strat_name, tf, tf_trades
                    )

                # تجميع حسب الرمز
                symbols: dict[str, list] = {}
                for t in trades:
                    symbols.setdefault(t.symbol, []).append(t)

                for sym, sym_trades in symbols.items():
                    await self._evaluate_symbol(session, sym, sym_trades)

                self._last_evaluation = datetime.utcnow()
                self._generate_recommendations(strats, symbols)

        except Exception as e:
            self.logger.error(f"[تعلم] فشل التقييم الشامل: {e}")

    # ─────────────────────────────────────────────────────────
    #  استخراج الإطار الزمني من الصفقة
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _extract_timeframe(trade) -> str:
        """
        استخراج الإطار الزمني من الصفقة.
        الأولوية: market_conditions.timeframe > strategy_used parsing > الافتراضي.
        """
        # المحاولة من market_conditions (مخزن JSON)
        if hasattr(trade, 'market_conditions') and isinstance(trade.market_conditions, dict):
            tf = trade.market_conditions.get('timeframe')
            if tf:
                return tf
        # الافتراضي
        return "15m"

    # ─────────────────────────────────────────────────────────
    #  تقييم استراتيجية (قديم — توافق خلفي)
    # ─────────────────────────────────────────────────────────

    async def _evaluate_strategy(self, session, strategy_name: str, trades: list):
        """حساب مقاييس الأداء لاستراتيجية واحدة (بدون عزل الأطر الزمنية)."""
        total = len(trades)
        if total == 0:
            return

        wins = [t for t in trades if t.status == "WON"]
        losses = [t for t in trades if t.status == "LOST"]
        win_rate = (len(wins) / total * 100)

        avg_profit = np.mean([t.pnl for t in wins]) if wins else 0.0
        avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 0.0

        cumulative = np.cumsum([t.pnl for t in trades])
        peak = np.maximum.accumulate(cumulative)
        drawdown = float(np.max(peak - cumulative)) if len(cumulative) > 0 else 0.0

        await StrategyStatRepository.upsert(
            session, strategy_name, "ALL",
            round(win_rate, 1), round(float(avg_profit), 2),
            round(float(avg_loss), 2), round(drawdown, 2),
            total, "15m",
        )

        self._strategy_performance[strategy_name] = {
            "win_rate": round(win_rate, 1),
            "avg_profit": round(float(avg_profit), 2),
            "avg_loss": round(float(avg_loss), 2),
            "total_trades": total,
            "drawdown": round(drawdown, 2),
        }

    # ─────────────────────────────────────────────────────────
    #  تقييم استراتيجية × إطار زمني — V4.0
    # ─────────────────────────────────────────────────────────

    async def _evaluate_strategy_tf(self, session, strategy_name: str,
                                     timeframe: str, trades: list):
        """
        حساب مقاييس الأداء لاستراتيجية واحدة على إطار زمني محدد.
        يُخزن النتائج في self._strategy_tf_performance بمفتاح (استراتيجية, إطار).
        """
        total = len(trades)
        if total == 0:
            return

        wins = [t for t in trades if t.status == "WON"]
        losses = [t for t in trades if t.status == "LOST"]
        win_rate = (len(wins) / total * 100) if total > 0 else 0.0

        avg_profit = np.mean([t.pnl for t in wins]) if wins else 0.0
        avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 0.0

        cumulative = np.cumsum([t.pnl for t in trades])
        peak = np.maximum.accumulate(cumulative)
        drawdown = float(np.max(peak - cumulative)) if len(cumulative) > 0 else 0.0

        # Sharpe-like score خاص بهذا الإطار
        sharpe = self._compute_sharpe(trades)

        key = (strategy_name, timeframe)

        await StrategyStatRepository.upsert(
            session, strategy_name, "ALL",
            round(win_rate, 1), round(float(avg_profit), 2),
            round(float(avg_loss), 2), round(drawdown, 2),
            total, timeframe,
        )

        self._strategy_tf_performance[key] = {
            "strategy": strategy_name,
            "timeframe": timeframe,
            "win_rate": round(win_rate, 1),
            "avg_profit": round(float(avg_profit), 2),
            "avg_loss": round(float(avg_loss), 2),
            "total_trades": total,
            "drawdown": round(drawdown, 2),
            "sharpe_like": sharpe,  # V4.0: Sharpe-like لكل إطار
        }

        self.logger.info(
            f"[تقييم] استراتيجية {strategy_name} على إطار {timeframe}: "
            f"نسبة نجاح {win_rate:.1f}% | صفقات: {total} | Sharpe: {sharpe}"
        )

    # ─────────────────────────────────────────────────────────
    #  تقييم لكل رمز
    # ─────────────────────────────────────────────────────────

    async def _evaluate_symbol(self, session, symbol: str, trades: list):
        """حساب مقاييس الأداء لكل رمز تداول."""
        total = len(trades)
        if total == 0:
            return

        wins = [t for t in trades if t.status == "WON"]
        win_rate = (len(wins) / total * 100)
        avg_pnl = np.mean([t.pnl for t in trades]) if trades else 0.0

        self._symbol_performance[symbol] = {
            "win_rate": round(win_rate, 1),
            "avg_pnl": round(float(avg_pnl), 2),
            "total_trades": total,
        }

    # ─────────────────────────────────────────────────────────
    #  Sharpe-like Score — V4.0
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _compute_sharpe(trades: list) -> float:
        """
        حساب نسخة مبسطة من Sharpe ratio:
        متوسط الربح ÷ الانحراف المعياري (للصفقات التي بها ربح).
        إذا لم توجد بيانات كافية: 0.0
        """
        if len(trades) < 2:
            return 0.0
        returns = [t.pnl for t in trades if hasattr(t, 'pnl')]
        if len(returns) < 2:
            return 0.0
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns))
        if std_ret == 0.0:
            return 0.0
        return round(mean_ret / std_ret, 2)

    # ─────────────────────────────────────────────────────────
    #  تقييم حسب إطار زمني محدد (واجهة خارجية) — V4.0
    # ─────────────────────────────────────────────────────────

    async def evaluate_by_timeframe(self, session, strategy_name: str,
                                     timeframe: str) -> dict:
        """
        تقييم أداء استراتيجية محددة على إطار زمني محدد.

        Args:
            session: جلسة قاعدة البيانات
            strategy_name: اسم الاستراتيجية (مثال: TrendFollowing)
            timeframe: الإطار الزمني (مثال: 1m, 5m, 15m, 1h, 4h, 1d)

        Returns:
            قاموس يحتوي على مقاييس الأداء للإطار الزمني المطلوب
        """
        try:
            trades = await TradeRepository.get_all_closed(
                session,
                self.user_id if hasattr(self, 'user_id') else ""
            )

            # تصفية الصفقات: تطابق الاستراتيجية والإطار الزمني
            filtered = []
            for t in trades:
                t_strat = t.strategy_used or "غير_معروف"
                t_tf = self._extract_timeframe(t)
                if t_strat == strategy_name and t_tf == timeframe:
                    filtered.append(t)

            if not filtered:
                self.logger.info(
                    f"[تقييم] لا توجد صفقات لاستراتيجية {strategy_name} "
                    f"على إطار {timeframe}."
                )
                return {
                    "strategy": strategy_name,
                    "timeframe": timeframe,
                    "total_trades": 0,
                    "win_rate": 0.0,
                    "avg_profit": 0.0,
                    "avg_loss": 0.0,
                    "drawdown": 0.0,
                    "sharpe_like": 0.0,
                    "message": "لا توجد بيانات كافية.",
                }

            total = len(filtered)
            wins = [t for t in filtered if t.status == "WON"]
            losses = [t for t in filtered if t.status == "LOST"]
            win_rate = (len(wins) / total * 100) if total > 0 else 0.0
            avg_profit = np.mean([t.pnl for t in wins]) if wins else 0.0
            avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 0.0

            cumulative = np.cumsum([t.pnl for t in filtered])
            peak = np.maximum.accumulate(cumulative)
            drawdown = float(np.max(peak - cumulative)) if len(cumulative) > 0 else 0.0

            sharpe = self._compute_sharpe(filtered)

            result = {
                "strategy": strategy_name,
                "timeframe": timeframe,
                "total_trades": total,
                "win_rate": round(win_rate, 1),
                "avg_profit": round(float(avg_profit), 2),
                "avg_loss": round(float(avg_loss), 2),
                "drawdown": round(drawdown, 2),
                "sharpe_like": sharpe,
            }

            key = (strategy_name, timeframe)
            self._strategy_tf_performance[key] = result

            self.logger.info(
                f"[تقييم] استراتيجية {strategy_name} على إطار {timeframe}: "
                f"نسبة نجاح {win_rate:.1f}% | صفقات: {total} | Sharpe: {sharpe}"
            )

            return result

        except Exception as e:
            self.logger.error(f"[تقييم] خطأ في تقييم الإطار الزمني: {e}")
            return {
                "strategy": strategy_name,
                "timeframe": timeframe,
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_profit": 0.0,
                "avg_loss": 0.0,
                "drawdown": 0.0,
                "sharpe_like": 0.0,
                "message": f"خطأ: {e}",
            }

    # ─────────────────────────────────────────────────────────
    #  توليد التوصيات بالعربية — V4.0
    # ─────────────────────────────────────────────────────────

    def _generate_recommendations(self, strats: dict, symbols: dict):
        """
        إنتاج توصيات تحسين مبنية على البيانات.
        تشمل توصيات لكل (استراتيجية × إطار زمني) وكل رمز.
        الصيغة العربية: 'استراتيجية TrendFollowing أداؤها ضعيف (38% نسبة نجاح) على إطار 1m — تقليل التخصيص'
        """
        recommendations = []

        # ── توصيات من التقييم بالإطار الزمني (V4.0) ──
        for (strat_name, tf), perf in self._strategy_tf_performance.items():
            total = perf.get("total_trades", 0)
            wr = perf.get("win_rate", 0.0)

            if total >= 3 and wr < 45:
                recommendations.append(
                    f"استراتيجية {strat_name} أداؤها ضعيف ({wr:.1f}% نسبة نجاح) "
                    f"على إطار {tf} — تقليل التخصيص"
                )
            elif total >= 5 and wr > 60:
                recommendations.append(
                    f"استراتيجية {strat_name} أداؤها ممتاز ({wr:.1f}% نسبة نجاح) "
                    f"على إطار {tf} — زيادة التخصيص"
                )

        # ── توصيات من التقييم العام (توافق خلفي) ──
        for name, perf in self._strategy_performance.items():
            total = perf.get("total_trades", 0)
            wr = perf.get("win_rate", 0.0)

            if total >= 3 and wr < 45:
                recommendations.append(
                    f"استراتيجية {name} أداؤها ضعيف ({wr:.1f}% نسبة نجاح) — تقليل التخصيص"
                )
            elif total >= 5 and wr > 60:
                recommendations.append(
                    f"استراتيجية {name} أداؤها ممتاز ({wr:.1f}% نسبة نجاح) — زيادة التخصيص"
                )

        # ── توصيات الرموز ──
        for sym, perf in self._symbol_performance.items():
            total = perf.get("total_trades", 0)
            wr = perf.get("win_rate", 0.0)

            if total >= 3 and wr < 40:
                recommendations.append(
                    f"الرمز {sym} أداؤه ضعيف ({wr:.1f}% نسبة نجاح) — التفكير في إزالته"
                )

        # إزالة التكرارات مع الحفاظ على الترتيب
        seen = set()
        deduped = []
        for rec in recommendations:
            if rec not in seen:
                seen.add(rec)
                deduped.append(rec)

        self._recommendations = deduped

        if self._recommendations:
            self.logger.info(
                f"[توصية] تم إنتاج {len(self._recommendations)} توصية:\n" +
                "\n".join(f"  • {r}" for r in self._recommendations)
            )

    # ─────────────────────────────────────────────────────────
    #  واجهات الاستعلام العامة
    # ─────────────────────────────────────────────────────────

    def get_recommendations(self) -> list:
        """إرجاع نسخة من التوصيات الحالية."""
        return list(self._recommendations)

    def get_strategy_performance(self) -> dict:
        """إرجاع أداء الاستراتيجيات (بدون عزل الأطر الزمنية)."""
        return dict(self._strategy_performance)

    def get_strategy_tf_performance(self) -> dict:
        """V4.0: إرجاع أداء الاستراتيجيات لكل إطار زمني."""
        # تحويل المفاتيح (tuple) إلى صيغة قابلة للقراءة
        result = {}
        for (strat, tf), perf in self._strategy_tf_performance.items():
            result[f"{strat}@{tf}"] = perf
        return result

    def get_symbol_performance(self) -> dict:
        """إرجاع أداء الرموز."""
        return dict(self._symbol_performance)

    def get_sharpe_like_score(self) -> float:
        """
        درجة Sharpe المبسطة الإجمالية (متوسط جميع الاستراتيجيات).
        """
        all_returns = []
        for perf in self._strategy_performance.values():
            if perf["total_trades"] > 0 and perf["avg_profit"] > 0:
                ret = perf["avg_profit"] / max(perf["avg_loss"], 1e-10)
                all_returns.append(ret)
        return round(float(np.mean(all_returns)), 2) if all_returns else 0.0

    def get_sharpe_by_timeframe(self, strategy_name: str, timeframe: str) -> float:
        """
        V4.0: استعلام درجة Sharpe لاستراتيجية محددة على إطار زمني محدد.

        Args:
            strategy_name: اسم الاستراتيجية
            timeframe: الإطار الزمني (1m, 5m, 15m, 1h, 4h, 1d)

        Returns:
            قيمة Sharpe المبسطة (float)
        """
        key = (strategy_name, timeframe)
        perf = self._strategy_tf_performance.get(key, {})
        return perf.get("sharpe_like", 0.0)

    # ─────────────────────────────────────────────────────────
    #  نبض الصحة
    # ─────────────────────────────────────────────────────────

    async def _heartbeat_loop(self):
        """إرسال نبض الصحة كل 5 ثوانٍ."""
        while self._running:
            await self.event_bus.publish(HealthEvent(
                engine=self.name,
                status=HealthStatus.HEALTHY,
                latency_ms=0,
                error_rate=0,
            ))
            await asyncio.sleep(60)
