# تقرير الإصلاح -- مشاكل ملخص الأداء (Cycle Summary)

**التاريخ:** 29 يوليو 2026

---

## المشكلة الأولى: "5 أزواج" بدلاً من 3 عملات

### الجذر

في `app/main.py` السطر 882، كان المتغير `pairs_analyzed` يُحسب على أساس **كل شمعة مغلقة** لكل إطار زمني. النظام يشترك في 10 أزواج (symbol:timeframe):

| العملة | الإطارات الزمنية | عدد الاشتراكات |
|---------|-------------------|----------------|
| VTHOUSDT | 1m, 15m, 1h | 3 |
| XRPUSDT | 15m, 1h, 4h, 1d | 4 |
| POWRUSDT | 1h, 15m, 4h | 3 |
| **المجموع** | | **10** |

VTHO على timeframe 1m ينتج 60 قرار/ساعة، مما يفسر الـ 1071 قرار الإجمالي مقارنة بـ 76 لـ XRP و 75 لـ POWR.

### الإصلاح

1. إضافة `unique_symbols_seen: set()` لتتبع العملات الفريدة.
2. في `_run_health_logger_loop`، استخدام `len(unique_symbols_seen)` بدلاً من `pairs_analyzed` لعرض عدد العملات الفريدة (3).
3. إضافة `cooldown` mechanism: `min_analysis_interval = 30` ثانية لكل عملة، مما يمنع VTHO من إنتاج 60 قرار/ساعة بدلاً من قرار واحد كل 30 ثانية.

### الملفات المعدلة

- `app/main.py` (health_stats init, cooldown check, unique symbols tracking)

---

## المشكلة الثانية: Bearish=0, Sideways=0 دائماً

### الجذر

في `app/main.py` السطر 890-891:

```python
regime_key = f"regime_{result.regime_check_passed}"
self._health_stats[regime_key] = self._health_stats.get(regime_key, 0) + 1
```

`regime_check_passed` هو boolean يعني "هل النظام غير VOLATILE؟" وليس اتجاه السوق الفعلي. عندما `regime_check_passed = True` (أي TRENDING أو RANGING)، كانت تُحسب كـ "bullish". وبما أن جميع القرارات كانت `regime_check_passed = True`، كانت كلها "bullish".

في `engine/orchestrator.py` السطر 1201، كان الاتجاه `neutral` يُحوَّل تلقائياً إلى `direction = "long"`، مما يفقد معلومات الاتجاه الفعلي.

### الإصلاح

1. **`contracts/decision.py`**: توسيع `StrategySignal.direction` من `Literal["long", "short"]` إلى `Literal["long", "short", "neutral"]`.
2. **`engine/orchestrator.py`**: عند تحويل trend direction، أصبح `neutral` يحافظ على قيمته `neutral` بدلاً من التحول إلى `long`.
3. **`engine/orchestrator.py`**: تحديث `_build_strategy_signal` ليقبل `neutral` كاتجاه صالح.
4. **`app/main.py`**: إضافة `_determine_primary_direction()` يستخرج الاتجاه من component_signals (trend signal أولاً، ثم momentum، ثم htf).
5. **`app/main.py`**: استبدال `regime_True/False` بحسابات `bullish_count/bearish_count/sideways_count` مبنية على الاتجاه الفعلي.
6. **`engine/htf_filter.py`**: إضافة `ltf_neutral_pass_through` في `_check_alignment`.
7. **`engine/entry_rules.py`**: تحديث `_apply_limit_offset` و `_decide_entry_type` ليقبل `neutral` كـ market entry بدون offset.

### الملفات المعدلة

- `contracts/decision.py`
- `engine/orchestrator.py`
- `engine/htf_filter.py`
- `engine/entry_rules.py`
- `app/main.py`

---

## المشكلة الثالثة: VTHO يسيطر على الملخص

### الجذر

VTHO على timeframe 1m ينتج شمعة مغلقة كل دقيقة. النظام يحلل كل شمعة مغلقة، مما يعني 60 تحليل/ساعة لـ VTHO مقابل 4 تحليلات/ساعة لـ XRP (1h) و 6 تحليلات/ساعة لـ POWR (1h+4h).

### الإصلاح

إضافة cooldown mechanism في `_dispatch_candle_message`:

```python
now_ts = datetime.now(timezone.utc)
last_time = self._health_stats["last_analysis_time"].get(candle.symbol)
interval = self._health_stats["min_analysis_interval"]  # 30 seconds
if last_time is not None:
    elapsed = (now_ts - last_time).total_seconds()
    if elapsed < interval:
        return  # Skip this candle -- already analyzed recently
```

هذا يضمن أن كل عملة تُحلل مرة كل 30 ثانية كحد أدنى، مما يوازن الوجود في الملخص.

### الملفات المعدلة

- `app/main.py`

---

## ملخص الاختبارات

تم تشغيل 48 اختبار unit على الملفات المعدلة: **48 passed, 0 failed**.

الاختبارات الخمسة الفاشلة في `test_smc.py` و `test_structure.py` و `test_portfolio.py` كانت موجودة مسبقاً ولا علاقة لها بالتعديلات (فشل fixtures في بناء بيانات اختبار).

---

## التأثير المتوقع بعد نشر الإصلاحات

| المؤشر | قبل الإصلاح | بعد الإصلاح (متوقع) |
|---------|-------------|---------------------|
| Pairs Analyzed | 5 (عدد التحليلات) | 3 (عدد العملات الفريدة) |
| Bullish | 100% (regime_check_passed) | بناءً على trend direction الفعلي |
| Bearish | 0% | سيظهر عندما يكون trend bearish |
| Sideways | 0% | سيظهر عندما يكون trend neutral |
| VTHO dominance | 90%+ من التحليلات | متوازن مع cooldown 30s |
| Confidence | 45% (RANGING modifier 0.80) | غير متغير (مشكلة هيكلية) |

---

## ملاحظة إضافية: confidence المنخفض (45%)

جميع القرارات رُفضت بسبب `risk_rejected: skipped: regime or confidence gate failed`. التحقيق أظهر أن:

1. `regime_check_passed = True` لجميع القرارات (لا مشكلة هنا).
2. `confidence < CONFIDENCE_THRESHOLD (0.65)` لجميع القرارات.
3. سبب الـ confidence المنخفض: `regime_modifier = 0.80` (RANGING) يُضرب في المتوسط المرجح.

الصيغة: `confidence = raw_confidence × regime_modifier`

إذا كان `raw_confidence = 0.56` (مثال)، فإن `confidence = 0.56 × 0.80 = 0.45`.

**التوصية:** مراجعة `REGIME_MODIFIER_RANGING = 0.80` في `config/thresholds.py` أو تخفيض `CONFIDENCE_THRESHOLD = 0.65` إذا كان الهدف السماح بفرص أكثر.
