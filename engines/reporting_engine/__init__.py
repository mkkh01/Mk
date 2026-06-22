"""
محرك التقارير — يُنتج تقارير منظمة للتلغرام والسجلات.
تقارير يومية / أداء / صفقات / حالة النظام.

V4.0 — جميع التقارير بالعربية مع دعم الأطر الزمنية.
صيغ التنسيق: 📊 *تقرير الأداء* | ✅ نسبة النجاح | 📅 *التقرير اليومي*
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from core.base import BaseEngine
from core.events import EventBus, HealthEvent, HealthStatus
from core.types import PortfolioSnapshot
from database.repositories import TradeRepository, get_session

logger = logging.getLogger("محرك_التقارير")


class ReportingEngine(BaseEngine):
    """محرك إنتاج التقارير. لا يحتوي على منطق تداول."""

    def __init__(self, event_bus: EventBus):
        super().__init__("محرك_التقارير")
        self.event_bus = event_bus

    # ─────────────────────────────────────────────────────────
    #  دورة الحياة
    # ─────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        self.logger.info("[تقارير] تم تهيئة محرك التقارير.")

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._heartbeat_loop())
        self.logger.info("[تقارير] بدأ محرك التقارير في العمل.")

    async def stop(self) -> None:
        self._running = False
        self.logger.info("[تقارير] توقف محرك التقارير.")

    # ─────────────────────────────────────────────────────────
    #  تقرير آخر الصفقات — مع الأطر الزمنية
    # ─────────────────────────────────────────────────────────

    async def generate_trade_report(self, telegram_id, limit: int = 20) -> str:
        """
        تقرير بآخر الصفقات المغلقة مع الأطر الزمنية.

        Args:
            telegram_id: معرف المستخدم في تلغرام
            limit: الحد الأقصى لعدد الصفقات (افتراضي: 20)

        Returns:
            نص منسق بالعربية جاهز للإرسال
        """
        try:
            async for session in get_session():
                trades = await TradeRepository.get_closed_trades(
                    session, telegram_id, limit
                )
                if not trades:
                    return "📋 لا توجد صفقات مغلقة بعد."

                # العنوان
                lines = [
                    "📋 *تقرير آخر الصفقات*",
                    "━━━━━━━━━━━━━━━━━━━━",
                ]

                for i, t in enumerate(trades[:limit], 1):
                    icon = "✅" if t.status == "WON" else "❌"
                    pnl_str = f"+{t.pnl:.2f}" if t.pnl > 0 else f"{t.pnl:.2f}"
                    side = "شراء" if t.side.upper() == "BUY" else "بيع"
                    strat = t.strategy_used or "—"

                    # محاولة استخراج الإطار الزمني
                    tf = ""
                    if hasattr(t, 'market_conditions') and isinstance(t.market_conditions, dict):
                        tf = t.market_conditions.get('timeframe', '')

                    base_line = (
                        f"{icon} #{i} `{t.symbol}` | {side} | {strat}"
                    )
                    if tf:
                        base_line += f" | ⏱ {tf}"

                    base_line += f"\n          💰 النتيجة: `{pnl_str} USDT`"

                    lines.append(base_line)

                # ملخص
                wins = [t for t in trades[:limit] if t.status == "WON"]
                total_pnl = sum(t.pnl for t in trades[:limit])
                summary = (
                    f"\n📊 *الملخص:* {len(wins)}/{len(trades[:limit])} صفقة رابحة "
                    f"| صافي: `{total_pnl:.2f} USDT`"
                )
                lines.append(summary)

                return "\n".join(lines)

        except Exception as e:
            self.logger.error(f"[تقارير] خطأ في تقرير الصفقات: {e}")
            return "❌ حدث خطأ أثناء جلب تقرير الصفقات. حاول مرة أخرى لاحقاً."

    # ─────────────────────────────────────────────────────────
    #  تقرير أداء شامل
    # ─────────────────────────────────────────────────────────

    async def generate_performance_report(self, telegram_id) -> str:
        """
        تقرير إحصائيات أداء شامل.

        Args:
            telegram_id: معرف المستخدم في تلغرام

        Returns:
            نص منسق بالعربية
        """
        try:
            async for session in get_session():
                trades = await TradeRepository.get_all_closed(session, telegram_id)
                if not trades:
                    return "📊 لا توجد بيانات أداء كافية بعد."

                total = len(trades)
                wins = [t for t in trades if t.status == "WON"]
                losses = [t for t in trades if t.status == "LOST"]
                win_rate = (len(wins) / total * 100) if total > 0 else 0

                total_pnl = sum(t.pnl for t in trades)
                avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
                avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0

                # عامل الربح
                gross_profit = sum(t.pnl for t in wins)
                gross_loss = abs(sum(t.pnl for t in losses))
                profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

                # أفضل وأسوأ صفقة
                best = max(trades, key=lambda t: t.pnl)
                worst = min(trades, key=lambda t: t.pnl)

                # توزيع حسب الاستراتيجيات
                strats: dict[str, list] = {}
                for t in trades:
                    s = t.strategy_used or "غير معروف"
                    strats.setdefault(s, []).append(t)

                strat_lines = ""
                for sn, st in strats.items():
                    sw = [t for t in st if t.status == "WON"]
                    swr = (len(sw) / len(st) * 100) if st else 0
                    spnl = sum(t.pnl for t in st)
                    strat_lines += (
                        f"  🧠 {sn}: {len(sw)}/{len(st)} صفقة ({swr:.1f}%) "
                        f"| PnL: `{spnl:.2f}`\n"
                    )

                # توزيع حسب الأطر الزمنية
                timeframes: dict[str, list] = {}
                for t in trades:
                    tf = "—"
                    if hasattr(t, 'market_conditions') and isinstance(t.market_conditions, dict):
                        tf = t.market_conditions.get('timeframe', "—")
                    timeframes.setdefault(tf, []).append(t)

                tf_lines = ""
                if len(timeframes) > 1 or (len(timeframes) == 1 and "—" not in timeframes):
                    for tf, tt in timeframes.items():
                        tw = [t for t in tt if t.status == "WON"]
                        twr = (len(tw) / len(tt) * 100) if tt else 0
                        tpnl = sum(t.pnl for t in tt)
                        tf_lines += (
                            f"  ⏱ {tf}: {len(tw)}/{len(tt)} ({twr:.1f}%) "
                            f"| PnL: `{tpnl:.2f}`\n"
                        )

                # بناء التقرير النهائي
                pf_display = f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞"

                report = (
                    f"📊 *تقرير الأداء*\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📈 إجمالي الصفقات: *{total}*\n"
                    f"✅ نسبة النجاح: *{win_rate:.1f}%*\n"
                    f"💰 صافي الربح: `{total_pnl:+.2f} USDT`\n"
                    f"📊 عامل الربح: *{pf_display}*\n"
                    f"🏆 متوسط الربح: `{avg_win:+.2f}`\n"
                    f"📉 متوسط الخسارة: `{avg_loss:+.2f}`\n"
                    f"📈 أفضل صفقة: `{best.symbol}` `{best.pnl:+.2f}`\n"
                    f"📉 أسوأ صفقة: `{worst.symbol}` `{worst.pnl:+.2f}`\n"
                )

                if strat_lines:
                    report += f"\n🧠 *الاستراتيجيات:*\n{strat_lines}"

                if tf_lines:
                    report += f"\n⏱ *الأطر الزمنية:*\n{tf_lines}"

                return report

        except Exception as e:
            self.logger.error(f"[تقارير] خطأ في تقرير الأداء: {e}")
            return "❌ حدث خطأ أثناء جلب تقرير الأداء."

    # ─────────────────────────────────────────────────────────
    #  التقرير اليومي
    # ─────────────────────────────────────────────────────────

    async def generate_daily_report(self, telegram_id) -> str:
        """
        ملخص يومي لصفقات اليوم.

        Args:
            telegram_id: معرف المستخدم في تلغرام

        Returns:
            نص منسق بالعربية
        """
        try:
            async for session in get_session():
                trades = await TradeRepository.get_closed_trades(
                    session, telegram_id, limit=100
                )

                # تصفية صفقات اليوم
                today = datetime.utcnow().date()
                today_trades = [
                    t for t in trades
                    if t.closed_at and t.closed_at.date() == today
                ]

                if not today_trades:
                    today_str = today.strftime("%Y-%m-%d")
                    return f"📅 لا توجد صفقات اليوم ({today_str})."

                wins = [t for t in today_trades if t.status == "WON"]
                losses = [t for t in today_trades if t.status == "LOST"]
                daily_pnl = sum(t.pnl for t in today_trades)
                win_rate = (len(wins) / len(today_trades) * 100) if today_trades else 0

                avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
                avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0

                # أفضل وأسوأ صفقة اليوم
                best = max(today_trades, key=lambda t: t.pnl)
                worst = min(today_trades, key=lambda t: t.pnl)

                # توزيع حسب الاستراتيجيات
                strats: dict[str, list] = {}
                for t in today_trades:
                    s = t.strategy_used or "غير معروف"
                    strats.setdefault(s, []).append(t)

                strat_summary = ""
                for sn, st in strats.items():
                    sw = [t for t in st if t.status == "WON"]
                    spnl = sum(t.pnl for t in st)
                    strat_summary += (
                        f"  🧠 {sn}: {len(sw)}/{len(st)} | `{spnl:+.2f}`\n"
                    )

                today_str = today.strftime("%Y-%m-%d")
                emotion = "🔥" if daily_pnl > 0 else ("😐" if daily_pnl == 0 else "📉")

                report = (
                    f"📅 *التقرير اليومي* — {today_str} {emotion}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📊 صفقات اليوم: *{len(today_trades)}*\n"
                    f"✅ نسبة النجاح: *{win_rate:.1f}%*\n"
                    f"💰 الربح/الخسارة: `{daily_pnl:+.2f} USDT`\n"
                    f"🏆 متوسط الربح: `{avg_win:+.2f}`\n"
                    f"📉 متوسط الخسارة: `{avg_loss:+.2f}`\n"
                    f"📈 أفضل صفقة: `{best.symbol}` `{best.pnl:+.2f}`\n"
                    f"📉 أسوأ صفقة: `{worst.symbol}` `{worst.pnl:+.2f}`\n"
                )

                if strat_summary:
                    report += f"\n🧠 *الاستراتيجيات اليوم:*\n{strat_summary}"

                return report

        except Exception as e:
            self.logger.error(f"[تقارير] خطأ في التقرير اليومي: {e}")
            return "❌ حدث خطأ أثناء جلب التقرير اليومي."

    # ─────────────────────────────────────────────────────────
    #  تقرير حالة النظام — نسخة موسعة للتوافق الخلفي
    # ─────────────────────────────────────────────────────────

    async def generate_status_report(self, portfolio_snapshot: PortfolioSnapshot,
                                     health_status: dict,
                                     learning_recommendations: list) -> str:
        """
        تقرير حالة النظام الشامل (للتوافق الخلفي).

        Args:
            portfolio_snapshot: لقطة المحفظة الحالية
            health_status: حالة صحة المحركات
            learning_recommendations: توصيات محرك التعلم

        Returns:
            نص منسق بالعربية
        """
        return await self.generate_dynamic_status_report(
            telegram_id=None,
            portfolio_snapshot=portfolio_snapshot,
            health=health_status,
            recommendations=learning_recommendations,
        )

    # ─────────────────────────────────────────────────────────
    #  تقرير حالة ديناميكي شامل — V4.0
    # ─────────────────────────────────────────────────────────

    async def generate_dynamic_status_report(
        self,
        telegram_id=None,
        portfolio_snapshot: PortfolioSnapshot = None,
        health: dict = None,
        recommendations: list = None,
    ) -> str:
        """
        تقرير حالة ديناميكي شامل — V4.0.

        يُجمع بيانات المحفظة والصحة والتوصيات في تقرير واحد منسق بالعربية.

        Args:
            telegram_id: معرف المستخدم (اختياري — لجلب بيانات إضافية)
            portfolio_snapshot: لقطة المحفظة
            health: قاموس حالة صحة النظام
            recommendations: قائمة توصيات التعلم

        Returns:
            نص منسق بالعربية جاهز للإرسال
        """
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"📡 *حالة النظام* — {now}",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        # ── قسم المحفظة ──
        if portfolio_snapshot:
            ps = portfolio_snapshot
            lines.extend([
                "",
                "💼 *المحفظة*",
                f"  💰 الرصيد: `{ps.balance:,.2f} USDT`",
                f"  📈 حقوق الملكية: `{ps.equity:,.2f}`",
                f"  🔓 صفقات مفتوحة: {ps.open_positions}",
                f"  📊 نسبة الربح: {ps.win_rate:.1f}%",
                f"  📉 السحب: {ps.drawdown:.2f}%",
                f"  ⚡ الحالة: {ps.status}",
            ])
            # مؤشر صحة إضافي
            if ps.drawdown > 5:
                lines.append(f"  ⚠️ *تحذير:* السحب مرتفع ({ps.drawdown:.2f}%)!")
            if ps.win_rate > 60 and ps.balance > 0:
                lines.append(f"  🔥 *أداء إيجابي* — نسبة نجاح ممتازة!")

        # ── قسم الصحة ──
        if health:
            lines.extend([
                "",
                "⚙️ *صحة المحركات*",
            ])
            system_state = health.get('system_state', 'غير معروف')
            state_icon = {
                'HEALTHY': '🟢',
                'DEGRADED': '🟡',
                'FAILED': '🔴',
                'SAFE_MODE': '🟠',
            }.get(system_state, '⚪')
            lines.append(f"  {state_icon} حالة النظام: {system_state}")

            # تفاصيل المحركات
            engines_health = health.get('engines', {})
            if engines_health:
                for eng_name, eng_data in engines_health.items():
                    if isinstance(eng_data, dict):
                        eng_status = eng_data.get('status', 'غير معروف')
                        eng_icon = '🟢' if eng_status == 'HEALTHY' else '🔴'
                        eng_latency = eng_data.get('latency_ms', 0)
                        eng_errors = eng_data.get('error_rate', 0)
                        lines.append(
                            f"  {eng_icon} {eng_name}: {eng_status} "
                            f"| ⏱ {eng_latency}ms | ❌ {eng_errors}"
                        )
            else:
                lines.append(f"  ℹ️ لا توجد بيانات تفصيلية للمحركات.")

        # ── قسم التوصيات ──
        if recommendations:
            lines.extend([
                "",
                "💡 *توصيات*",
            ])
            for i, rec in enumerate(recommendations[:5], 1):
                lines.append(f"  {i}. {rec}")
            if len(recommendations) > 5:
                lines.append(f"  ... و {len(recommendations) - 5} توصية إضافية.")
        else:
            lines.extend([
                "",
                "💡 *توصيات*",
                "  ℹ️ لا توجد توصيات حالياً. كل شيء يسير بشكل طبيعي.",
            ])

        # ── قسم الصفقات المفتوحة ──
        if telegram_id:
            try:
                async for session in get_session():
                    open_trades = await TradeRepository.get_open_trades_for_user(
                        session, telegram_id
                    )
                    if open_trades:
                        lines.extend([
                            "",
                            "🔓 *صفقات مفتوحة*",
                        ])
                        for t in open_trades:
                            side = "شراء" if t.side.upper() == "BUY" else "بيع"
                            lines.append(
                                f"  📌 `{t.symbol}` {side} @ `{t.entry_price}` "
                                f"| 🧠 {t.strategy_used or '—'}"
                            )
            except Exception:
                pass  # تجاهل خطأ جلب الصفقات المفتوحة في تقرير الحالة

        # ── تذييل ──
        lines.extend([
            "",
            "━━━━━━━━━━━━━━━━━━━━",
            "🕐 _يتم تحديثه تلقائياً كل 5 دقائق_",
        ])

        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────
    #  تنسيق تنبيه صفقة — V4.0
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def format_trade_alert(symbol: str, pnl: float, result: str,
                           reason: str = "", strategy: str = "",
                           confidence: float = 0, timeframe: str = "",
                           entry_price: float = 0, exit_price: float = 0) -> str:
        """
        تنسيق رسالة تنبيه عند إغلاق صفقة. V4.0 — دعم الإطار الزمني.

        Args:
            symbol: رمز العملة
            pnl: الربح/الخسارة
            result: النتيجة (WON/LOST)
            reason: سبب الإغلاق
            strategy: اسم الاستراتيجية
            confidence: نسبة الثقة
            timeframe: الإطار الزمني (V4.0)
            entry_price: سعر الدخول
            exit_price: سعر الخروج

        Returns:
            نص منسق بالعربية
        """
        icon = "✅" if result == "WON" else "❌"
        pnl_str = f"+{pnl:.2f}" if pnl > 0 else f"{pnl:.2f}"
        emotion = "🎉" if pnl > 0 else ("😐" if pnl == 0 else "💔")

        lines = [
            f"{icon} *صفقة مغلقة* {emotion}",
            f"━━━━━━━━━━━━━━━━━━━━",
            f"🪙 العملة: `{symbol}`",
            f"💰 النتيجة: `{pnl_str} USDT`",
        ]

        if entry_price:
            lines.append(f"📥 سعر الدخول: `{entry_price}`")
        if exit_price:
            lines.append(f"📤 سعر الخروج: `{exit_price}`")

        if reason:
            lines.append(f"📝 السبب: {reason}")
        if strategy:
            lines.append(f"🧠 الاستراتيجية: {strategy}")
        if timeframe:
            lines.append(f"⏱ الإطار الزمني: {timeframe}")
        if confidence:
            conf_emoji = "🔥" if confidence >= 70 else ("👍" if confidence >= 50 else "⚠️")
            lines.append(f"🎯 الثقة: {confidence:.0f}% {conf_emoji}")

        return "\n".join(lines)

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
