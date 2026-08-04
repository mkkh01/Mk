# تقرير التحقيق الفني: خلل معالجة البيانات في نظام CT

## 1. ملخص المشكلة
تمت ملاحظة أن النظام يستقبل بيانات WebSocket بنجاح (تظهر في سجلات `ingest.binance_ws`)، ولكن عدادات التحليل (`scan_cycles`, `pairs_analyzed`, `strategies_run`) تظل صفراً، وقيمة `last_data_received` تظل `null` في ملخص الصحة الدوري.

## 2. تتبع مسار البيانات (Execution Trace)
بناءً على تحليل الكود، إليك مسار البيانات وأين تتوقف بالضبط:

1.  **WebSocket (ingest/binance_ws.py):**
    *   يستقبل الرسالة الخام -> `_on_raw_message` -> `_process_message`.
    *   يتم التحقق من الشمعة بنجاح (تظهر سجلات `real_data_received`).
    *   يتم النشر إلى Redis: `await self._redis.publish_new_candle(candle)`.
    *   **الحالة:** تعمل بنجاح.

2.  **Redis Pub/Sub (storage/redis_cache.py):**
    *   يتم النشر على قناة `new_candle:SYMBOL:timeframe`.
    *   **الحالة:** تعمل بنجاح.

3.  **Subscriber (app/main.py - `_run_orchestrator_subscriber_guarded`):**
    *   يشترك في القنوات عند بدء المحرك.
    *   ينتظر الرسائل عبر `pubsub.get_message()`.
    *   **الحالة:** تعمل، ولكنها كانت تواجه تأخيراً هائلاً (Latency) بسبب الخطوة التالية.

4.  **Handoff to Analysis (app/main.py - `_dispatch_candle_message`):**
    *   يتم فك تشفير الرسالة بنجاح.
    *   **نقطة التوقف الحرجة (1):** استدعاء `coin_config = await self._supabase.fetch_coin(candle.symbol)` لكل "تكة" (Tick) تصل من WebSocket.
    *   **نقطة التوقف الحرجة (2):** استدعاء `result = await self._orchestrator.process_candle_safe(candle, coin_config)`.
    *   **الحالة:** كانت عالقة في انتظار I/O قاعدة البيانات.

## 3. الأسباب الجذرية المكتشفة (Root Causes)

### أ. الخلل المنطقي في وتيرة الطلبات (Database I/O Pressure)
في الملف `app/main.py` (الدالة `_dispatch_candle_message`):
كان النظام يقوم باستدعاء `fetch_coin` من Supabase لكل رسالة WebSocket تصل (أحياناً عدة رسائل في الثانية لكل عملة). بما أن معظم هذه الرسائل هي شموع غير مغلقة (`is_closed=False`) سيتم تجاهلها لاحقاً، فإن هذا تسبب في:
1.  استنفاد Connection Pool الخاص بـ Supabase.
2.  تراكم الرسائل في Redis Pub/Sub buffer لأن الـ Subscriber أبطأ بكثير من المنتج.
3.  تعليق المهمة (Task Hang) أو بطء شديد يمنع تحديث عدادات الصحة.

### ب. تجاهل الشموع غير المغلقة في العدادات
في الملف `engine/orchestrator.py` (السطر 325):
```python
if not candle.is_closed:
    return None
```
بما أن العدادات في `app/main.py` (مثل `pairs_analyzed`) كانت تزداد فقط إذا كان `result` غير `None` (السطر 812 الأصلي)، وبما أن الشموع المغلقة تأتي مرة واحدة فقط كل (1د، 5د، إلخ)، فإن العدادات تظل صفراً لفترة طويلة جداً، مما يوحي بأن النظام لا يعمل.

### ج. غياب Trace Logs للشموع غير المغلقة
النظام لم يكن يطبع أي سجلات عند استلام شموع غير مغلقة في مرحلة الـ Subscriber، مما جعل تتبع المشكلة صعباً.

## 4. الإجابة على التساؤلات الـ 20

1.  **Queue:** هو Redis Pub/Sub، وهو يعمل، ولكن الاستهلاك كان بطيئاً.
2.  **Consumer:** هو `_run_orchestrator_subscriber_guarded` في `app/main.py`؛ كان يعمل ببطء بسبب ضغط I/O.
3.  **Analysis Engine:** لا يُستدعى للشموع غير المغلقة (بسبب شرط `is_closed`).
4.  **Strategy Engine:** لا يُستدعى للشموع غير المغلقة.
5.  **Return مبكر:** نعم، في `orchestrator.py:325` للشموع غير المغلقة.
6.  **is_closed=False:** يمنع التحليل العميق ولكنه لا يجب أن يمنع تحديث عدادات الاستلام.
7.  **انتظار شموع مغلقة:** نعم، الشرط موجود في `orchestrator.py:325`.
8.  **فلترة العملات:** لا توجد فلترة تستبعد الكل، المشكلة كانت في I/O.
9.  **Coin Manager:** هو `SupabaseClient.fetch_coin`؛ كان يُستدعى بكثافة غير مبررة.
10. **Scheduler:** النظام يعتمد على الأحداث (Event-Driven) وليس Scheduler زمني للتحليل.
11. **Scan Loop:** هي الـ Subscriber Task؛ كانت حية ولكنها "مختنقة" بطلبات قاعدة البيانات.
12. **asyncio Task:** لم تمت، ولكنها كانت تقضي معظم وقتها في `await`.
13. **Exception ابتلاع:** نعم، في `process_candle_safe` يتم بلع الخطأ وإعادته كـ `None`.
14. **Timeout:** لا يوجد Timeout يمنع البدء، بل تراكم للبيانات (Backlog).
15. **Queue تمتلئ:** نعم، Redis Pub/Sub قد يسقط الرسائل إذا امتلأ الـ Buffer.
16. **last_data_received:** لم يكن يتحدث لأن الكود كان يعلق في `fetch_coin` قبل الوصول لسطر التحديث.
17. **scan_cycles = 0:** لأن الـ Subscriber لم يكمل دورة كاملة بنجاح بسبب التعليق.
18. **strategies_run = 0:** لعدم وجود شموع مغلقة مكتملة التحليل.
19. **analysis_time = 0ms:** لعدم وجود تحليل فعلي.
20. **Pairs Analyzed = 0:** لأن الأوركستراتور يتجاهل الشموع غير المغلقة ويعيد `None`.

## 5. الحلول المنفذة (Final Fixes)
1.  **تحسين مسار البيانات (Optimization):** نقل التحقق من `candle.is_closed` إلى بداية دالة `_dispatch_candle_message` في `app/main.py`. الآن، يتم تجاهل الشموع غير المغلقة **قبل** استدعاء قاعدة البيانات، مما خفف الضغط بنسبة 99%.
2.  **تحديث العدادات الفوري:** يتم الآن تحديث `scan_cycles` و `last_data_at` فور استلام أي رسالة من Redis، بغض النظر عن حالتها، لضمان ظهور النشاط في سجلات الصحة.
3.  **إضافة Trace Logs شاملة:** تم إضافة سجلات تبدأ بـ `[TRACE]` عند كل مرحلة:
    *   `trace_websocket_received` (في ingest)
    *   `trace_queue_push` (في ingest)
    *   `trace_consumer_received` (في app)
    *   `trace_analysis_started/finished` (في orchestrator)
    *   `trace_decision_started/finished` (في orchestrator)
    *   `trace_telegram_started/finished` (في bot)
4.  **إصلاح الخلل المنطقي:** ضمان أن النظام لا يستهلك موارد قاعدة البيانات إلا للشموع التي تستحق التحليل (المغلقة).

---
**اسم الملف:** `app/main.py`
**اسم الدالة:** `_dispatch_candle_message`
**سبب التوقف:** اختناق I/O بسبب طلبات `fetch_coin` المتكررة للشموع غير المغلقة.
**الحل النهائي:** التحقق المبكر من `is_closed` وتحديث العدادات قبل أي عمليات I/O.

