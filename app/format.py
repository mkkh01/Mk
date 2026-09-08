# -*- coding: utf-8 -*-
"""كل النصوص العربية وتنسيق الأرقام (للتلجرام واللوحة)."""
from datetime import datetime, timedelta, timezone

LIBYA_TZ = timezone(timedelta(hours=2), "Libya")


def fmt_price(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    ax = abs(x)
    if ax >= 1000:
        return f"{x:,.2f}"
    if ax >= 100:
        return f"{x:,.3f}"
    if ax >= 1:
        return f"{x:,.4f}"
    return f"{x:,.6f}"


def fmt_money(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if x > 0 else ("−" if x < 0 else "")
    return f"{sign}{abs(x):,.2f}$"


def fmt_usd(x) -> str:
    """مبلغ بدون إشارة (للأرصدة والرسوم)."""
    try:
        return f"{float(x):,.2f}$"
    except (TypeError, ValueError):
        return "—"


def fmt_pct(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if x > 0 else ("−" if x < 0 else "")
    return f"{sign}{abs(x):,.2f}%"


def libya_str(iso_or_dt) -> str:
    try:
        dt = (datetime.fromisoformat(iso_or_dt) if isinstance(iso_or_dt, str)
              else iso_or_dt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(LIBYA_TZ).strftime("%d-%m %H:%M")
    except Exception:
        return "—"


def fmt_duration(minutes: float) -> str:
    try:
        m = int(float(minutes))
    except (TypeError, ValueError):
        return "—"
    if m < 60:
        return f"{m} دقيقة"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h} س {m} د"
    d, h = divmod(h, 24)
    return f"{d} يوم {h} س"


def held_str(entry_iso: str, now=None) -> str:
    try:
        dt = datetime.fromisoformat(entry_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = now or datetime.now(timezone.utc)
        return fmt_duration((now - dt).total_seconds() / 60)
    except Exception:
        return "—"


_SIDE_AR = {"LONG": "شراء 🟢", "SHORT": "بيع 🔴"}


def side_ar(side: str) -> str:
    return _SIDE_AR.get(side, side)


# =====================================================
#  إشعارات الصفقات (تُرسل تلقائياً)
# =====================================================

def format_trade_opened(trade: dict, equity: float) -> str:
    reasons = "\n".join(f"• {r}" for r in trade.get("entry_reasons", [])[:6])
    snap = trade.get("snapshot", {}) or {}
    return (
        f"🚀 <b>صفقة جديدة: {trade['symbol']} - {side_ar(trade['side'])}</b>\n"
        f"━━━━━━━━━━━━\n"
        f"💰 الدخول: <b>{fmt_price(trade['entry_price'])}</b>\n"
        f"🛑 الوقف: {fmt_price(trade['sl'])} | 🎯 الهدف: {fmt_price(trade['tp'])}\n"
        f"📦 الكمية: {trade['qty']:.4f} | القيمة: {fmt_usd(trade.get('notional', 0))}\n"
        f"⚠️ المخاطرة: {float(trade.get('risk_amount', 0)):,.2f}$ | العائد/المخاطرة: 1:{snap.get('rr', 0)}\n"
        f"📊 قوة الإشارة: {snap.get('strength', '')} (التقاء {snap.get('confluence', '')}/5)\n"
        f"\n<b>أسباب الدخول:</b>\n{reasons}\n"
        f"\n💼 المحفظة: {fmt_usd(equity)} | 🕒 {libya_str(trade['entry_time'])}"
    )


def format_trade_closed(trade: dict, equity: float) -> str:
    win = trade.get("result") == "WIN"
    icon = "✅ <b>صفقة رابحة</b>" if win else "❌ <b>صفقة خاسرة</b>"
    return (
        f"{icon}: {trade['symbol']} - {side_ar(trade['side'])}\n"
        f"━━━━━━━━━━━━\n"
        f"💰 الدخول: {fmt_price(trade['entry_price'])} → الخروج: <b>{fmt_price(trade['exit_price'])}</b>\n"
        f"📈 النتيجة: <b>{fmt_money(trade['pnl'])}</b> ({fmt_pct(trade.get('pnl_pct', 0))} من الهامش)\n"
        f"📏 R: {trade.get('r_multiple', 0)} | الرسوم: {fmt_usd(trade.get('fees', 0))}\n"
        f"🔚 سبب الإغلاق: {trade.get('exit_reason', '')}\n"
        f"⏱️ المدة: {fmt_duration(trade.get('duration_min', 0))}\n"
        f"\n💼 المحفظة: {fmt_usd(equity)} | 🕒 {libya_str(trade.get('exit_time', ''))}"
    )


# =====================================================
#  ردود الأزرار
# =====================================================

def format_open_trades(trades: list, perf: dict) -> str:
    if not trades:
        return "📂 <b>الصفقات المفتوحة</b>\n\nلا توجد صفقات مفتوحة حالياً.\nالنظام يفحص السوق كل دقيقة وسينبهك فور توفر فرصة. 🔍"
    lines = [f"📂 <b>الصفقات المفتوحة ({len(trades)})</b> ⚡ لحظي\n"]
    for t in trades:
        pnl = t.get("unrealized_pnl", 0) or 0
        icon = "🟢" if pnl >= 0 else "🔴"
        lines.append(
            f"{icon} <b>{t['symbol']}</b> - {side_ar(t['side'])}\n"
            f"   الدخول: {fmt_price(t['entry_price'])} | الحالي: {fmt_price(t.get('current_price'))}\n"
            f"   العائم: <b>{fmt_money(pnl)}</b> | الوقف: {fmt_price(t['sl'])} | الهدف: {fmt_price(t['tp'])}\n"
            f"   المدة: {held_str(t['entry_time'])}"
        )
    lines.append(f"\n💼 المحفظة: {fmt_usd(perf.get('equity', 0))} | العائم الكلي: {fmt_money(perf.get('open_pnl', 0))}")
    return "\n".join(lines)


def format_closed_trades(trades: list, page: int = 0, per_page: int = 8) -> tuple[str, bool, bool]:
    if not trades:
        return "📁 <b>الصفقات المغلقة</b>\n\nلا توجد صفقات مغلقة بعد.", False, False
    total_pages = max(1, (len(trades) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = trades[page * per_page:(page + 1) * per_page]
    lines = [f"📁 <b>الصفقات المغلقة</b> (صفحة {page + 1}/{total_pages})\n"]
    for t in chunk:
        icon = "✅" if t.get("result") == "WIN" else "❌"
        lines.append(
            f"{icon} <b>{t['symbol']}</b> {t['side']} | <b>{fmt_money(t.get('pnl', 0))}</b>\n"
            f"   {t.get('exit_reason', '')} | {libya_str(t.get('exit_time', ''))}"
        )
    return "\n".join(lines), page > 0, page < total_pages - 1


def format_performance(p: dict, start_balance: float) -> str:
    emo = "📈" if p.get("realized_pnl", 0) >= 0 else "📉"
    return (
        f"{emo} <b>أداء النظام</b>\n"
        f"━━━━━━━━━━━━\n"
        f"💼 المحفظة (Equity): <b>{fmt_usd(p.get('equity', 0))}</b> ({fmt_pct(p.get('return_pct', 0))})\n"
        f"💵 الرصيد المحقق: {fmt_usd(p.get('balance', 0))} | العائم: {fmt_money(p.get('open_pnl', 0))}\n"
        f"🏁 رأس المال البدائي: {fmt_usd(start_balance)}\n"
        f"\n📊 <b>الإحصائيات:</b>\n"
        f"• إجمالي الصفقات: {p.get('total_trades', 0)} (مفتوحة الآن: {p.get('open_count', 0)})\n"
        f"• الفائزة: {p.get('wins', 0)} | الخاسرة: {p.get('losses', 0)}\n"
        f"• نسبة الفوز: <b>{p.get('winrate', 0)}%</b>\n"
        f"• متوسط الربح: {fmt_usd(p.get('avg_win', 0))} | متوسط الخسارة: {fmt_usd(p.get('avg_loss', 0))}\n"
        f"• معامل الربح: {p.get('profit_factor', 0)}\n"
        f"• أفضل عملة: {p.get('best_symbol') or '—'} | أسوأ عملة: {p.get('worst_symbol') or '—'}"
    )


def format_prices(prices: dict, symbols: list, source: str, ts: str) -> str:
    if not prices:
        return "📡 <b>الأسعار الحية</b>\n\nتعذر جلب الأسعار حالياً. حاول بعد دقيقة."
    lines = [f"📡 <b>الأسعار الحية</b> ({source}) 🕒 {libya_str(ts)}\n"]
    for s in symbols:
        q = prices.get(s)
        if not q:
            lines.append(f"• {s}: —")
            continue
        ch = q.get("change_pct", 0) or 0
        icon = "🟢" if ch >= 0 else "🔴"
        lines.append(f"{icon} {s}: <b>{fmt_price(q['price'])}</b> ({fmt_pct(ch)})")
    return "\n".join(lines)


_STATUS = {"success": "✅ ناجحة", "partial": "⚠️ جزئية", "failed": "❌ فاشلة", "skipped": "⏭️ متخطاة"}


def format_cycle_summary(s: dict) -> str:
    if not s:
        return "🔄 <b>ملخص الدورة</b>\n\nلم تعمل أي دورة بعد. انتظر دقيقة ثم أعد المحاولة."
    if s.get("status") == "skipped":
        return f"🔄 <b>ملخص الدورة</b>\n\n⏭️ {s.get('reason', 'تخطي الدورة')}"
    px, kl = s.get("prices", {}), s.get("klines", {})
    ind, sig = s.get("indicators", {}), s.get("signals", {})
    tr, eq = s.get("trades", {}), s.get("equity", {})
    errs = s.get("errors", []) or []
    lines = [
        f"🔄 <b>ملخص الدورة #{s.get('cycle_id', '?')}</b>",
        f"🕒 {libya_str(s.get('started_at', ''))} (ليبيا) | ⏱️ {s.get('duration_sec', 0):.1f} ثانية",
        f"الحالة: {_STATUS.get(s.get('status'), s.get('status'))}",
        "",
        "📡 <b>البيانات:</b>",
        f"• الأسعار: {px.get('ok', 0)}/{px.get('total', 0)} عبر {s.get('data_source', {}).get('prices', '?')}",
        f"• الشموع: {kl.get('ok', 0)}/{kl.get('total', 0)}" + (
            f" (فشل: {', '.join(kl.get('failed_symbols', [])[:5])})" if kl.get("failed_symbols") else " ✅"),
        "",
        "📊 <b>المحركات:</b>",
        f"• المؤشرات محسوبة: {ind.get('computed', 0)}/{ind.get('total', 0)}",
        f"• إشارات: مفحوصة {sig.get('checked', 0)} | مقبولة {len(sig.get('accepted', []))} | مرفوضة {sig.get('rejected', 0)}",
    ]
    for a in (sig.get("accepted", []) or [])[:5]:
        lines.append(f"   ✔ {a.get('symbol')} {a.get('side')} ({a.get('strength', '')})")
    top = sig.get("top_reject") or {}
    if top:
        k = max(top, key=lambda k: top[k])
        lines.append(f"• أشهر سبب رفض ({top[k]}×): {k[:90]}")
    lines += [
        "",
        "💼 <b>الصفقات:</b>",
        f"• فُتحت: {len(tr.get('opened', []) or [])} | أُغلقت: {len(tr.get('closed', []) or [])} | المفتوحة: {tr.get('open_count', 0)}",
    ]
    for o in (tr.get("opened", []) or [])[:5]:
        lines.append(f"   🚀 {o.get('symbol')} {o.get('side')}")
    for c in (tr.get("closed", []) or [])[:5]:
        icon = "✅" if c.get("result") == "WIN" else "❌"
        lines.append(f"   {icon} {c.get('symbol')} {fmt_money(c.get('pnl', 0))} ({c.get('exit_reason', '')[:40]})")
    if tr.get("blocked_by_limit"):
        lines.append(f"• أُهملت {tr['blocked_by_limit']} إشارة لامتلاء الحد الأقصى")
    lines.append(f"• المحفظة: {fmt_usd(eq.get('equity', 0))} ({fmt_pct(eq.get('return_pct', 0))})")
    rt = s.get("realtime", {}) or {}
    ws = rt.get("ws", {}) or {}
    mon = rt.get("monitor", {}) or {}
    if ws.get("connected"):
        age = ws.get("tick_age_sec")
        age_txt = f"قبل {age} ث" if age is not None else ""
        lines += ["", f"⚡ <b>اللحظي:</b> بث متصل ✅ ({ws.get('symbols', 0)} رمز {age_txt})"]
    else:
        lines += ["", "⚡ <b>اللحظي:</b> البث متوقف ⏸️ (الأسعار من الدورة)"]
    if mon.get("last_run"):
        lines.append(f"• المراقب السريع: فحص {mon.get('checked', 0)} قبل {libya_str(mon.get('last_run'))} | إغلاق فوري: {mon.get('closed', 0)}")
    lines += ["", "⚠️ <b>الأخطاء:</b> " + ("لا يوجد ✅" if not errs else "")]
    for e in errs[:4]:
        lines.append(f"• {str(e)[:140]}")
    return "\n".join(lines)
