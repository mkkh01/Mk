# تصميم نظام سجلات سير العمل (Workflow Logs Render)

الهدف هو توفير سجلات واضحة في واجهة Render تتبع الصفقة من البداية (التحليل) إلى النهاية (النتيجة والأسباب).

## 1. سجلات التحليل (Analysis Logs)
يجب أن تظهر السجلات بشكل متسلسل:
- `[ANALYSIS] START`: بداية تحليل العملة.
- `[ANALYSIS] COMPONENT`: تفاصيل كل مكون (SMC, Trend, Volume).
- `[ANALYSIS] GATES`: حالة البوابات (Regime, HTF, Confidence).

## 2. سجلات القرار (Decision Logs)
- `[DECISION] APPROVED`: في حال الموافقة مع ملخص الخطة (Entry, SL, TP).
- `[DECISION] REJECTED`: في حال الرفض مع ذكر السبب بوضوح (Detailed Reason).

## 3. سجلات النتائج (Results Logs)
- `[TRADE] OPENED`: تفاصيل الصفقة المفتوحة.
- `[TRADE] CLOSED`: النتيجة النهائية (PnL) والسبب (TP, SL, Manual).

## 4. سجلات الأسباب (Reasoning Logs)
- ربط كل رفض بسبب فني محدد من الـ Orchestrator.
- توفير ملخص دوري لأكثر الأسباب تكراراً للرفض.
