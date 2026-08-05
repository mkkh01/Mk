# تقرير إصلاح: عدم إرسال رسالة تلجرام عند تغيير Stop Loss

## الملف المعدّل
- `app/main.py` — الدالة `_run_paper_trader_guarded`

## المشكلة الجذرية

في الإصدار السابق، كانت آلية اكتشاف تغييرات trailing stop تعتمد على مقارنة الصفقات المفتوحة **قبل** و**بعد** تنفيذ `scan_and_close_open_trades()`. المشكلة ظهرت عندما يتم تحديث trailing stop ثم تُغلق الصفقة في نفس دورة الفحص (لأن السعر وصل إلى الـ SL الجديد). في هذه الحالة:

1. `open_trades_before` تحتوي على الصفقة بـ SL القديم
2. `scan_and_close_open_trades` يحدّث SL ثم يُغلق الصفقة
3. `open_trades_after` لا تحتوي على الصفقة (لأنها أصبحت `closed`)
4. النتيجة: **لا إشعار trailing stop update ولا إشعار إغلاق** في بعض الحالات

## الإصلاح المطبّق

تم تعديل `_run_paper_trader_guarded` ليتحقق من تغييرات SL لجميع الصفقات التي كانت مفتوحة في بداية الدورة، بغض النظر عن حالتها النهائية:

```python
# قبل: مقارنة open_trades_after فقط
# بعد: مقارنة open_trades_before بالكامل مع closed_trades_map

for trade in open_trades_before:
    if trade_id in closed_trades_map:
        current_trade = closed_trades_map[trade_id]  # تستخدم بيانات الإغلاق
    else:
        current_trade = trade  # لا تزال مفتوحة
```

هذا يضمن:
- إشعار trailing stop update حتى لو أُغلقت الصفقة في نفس الدورة
- إشعار الإغلاق يُرسَل بشكل مستقل عن إشعار trailing stop
- إذا تغير SL ثم أُغلقت الصفقة، يُرسل إشعاران: الأول لتحديث trailing stop والثاني للإغلاق

## نتائج الاختبارات

- جميع الاختبارات (220) اجتازت بنجاح
- لا تغييرات في `simulation/paper_trade.py` أو `bot/telegram_bot.py`
- الاستيراد يعمل بشكل صحيح

## ملاحظات إضافية

- `format_trailing_stop_update` موجود في `bot/telegram_bot.py` (السطر 2087) ويعمل بشكل صحيح
- `SimulatedTrade` تم إضافتها إلى `TYPE_CHECKING` imports في `app/main.py`
- لا حاجة لتعديلات في قاعدة البيانات أو العقود
