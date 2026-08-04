# CT System Dashboard - دليل الاستخدام

## نظرة عامة

تم تطوير لوحة تحكم (Dashboard) شاملة لنظام CT تعرض سير عمل النظام بشكل مباشر وحي. توفر اللوحة رؤية شاملة عن:

- **حالة النظام**: حالة المحرك، العملات المفعلة، آخر بيانات مستلمة، عدد الأخطاء
- **الأسعار الحية**: أسعار جميع العملات المفعلة مع الطوابع الزمنية
- **الأداء الكلي**: إجمالي الصفقات، نسبة الفوز، إجمالي الربح/الخسارة، الحد الأقصى للانخفاض
- **أداء الاستراتيجيات**: أداء كل عملة على حدة مع معايير مفصلة
- **الثوابت والإعدادات**: جميع معاملات النظام والعتبات المستخدمة في التحليل

## الوصول إلى لوحة التحكم

يمكن الوصول إلى لوحة التحكم عبر:
```
http://localhost:8000/dashboard
```

أو في بيئة الإنتاج:
```
https://your-render-url.onrender.com/dashboard
```

## مسارات API الجديدة

### 1. حالة النظام
**المسار**: `/api/dashboard/system_health`
**الطريقة**: GET
**الوصف**: يعيد معلومات شاملة عن حالة النظام

**الاستجابة**:
```json
{
  "scan_cycles": 150,
  "pairs_analyzed": 450,
  "strategies_run": 1350,
  "opportunities_found": 45,
  "opportunities_rejected": 405,
  "rejection_reasons": {
    "confidence_below_threshold": 200,
    "risk_rejected": 100,
    "regime_check_failed": 105
  },
  "errors": 2,
  "last_data_at": "2024-01-15T10:30:45.123456Z",
  "total_score_sum": 28.5,
  "total_confidence_sum": 22.3,
  "total_analysis_time_ms": 4500.25,
  "db_writes": 45,
  "telegram_sent": 45,
  "engine_running": true,
  "active_coins": ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
}
```

### 2. الأسعار الحية
**المسار**: `/api/dashboard/live_prices/{symbol}`
**الطريقة**: GET
**الوصف**: يعيد السعر الحي لعملة معينة

**الاستجابة**:
```json
{
  "symbol": "BTCUSDT",
  "price": 42500.50,
  "timestamp": "2024-01-15T10:30:45.123456Z"
}
```

### 3. الثوابت والإعدادات
**المسار**: `/api/dashboard/thresholds`
**الطريقة**: GET
**الوصف**: يعيد جميع الثوابت والعتبات المستخدمة في النظام

**الاستجابة** (عينة):
```json
{
  "SWING_LOOKBACK": 5,
  "MIN_SWING_SIZE_PCT": 0.15,
  "TREND_EMA_FAST": 9,
  "TREND_EMA_SLOW": 21,
  "CONFIDENCE_THRESHOLD": 0.65,
  "HTF_ALIGNMENT_WEIGHT": 0.25,
  "STRUCTURE_WEIGHT": 0.30,
  "MOMENTUM_WEIGHT": 0.20,
  "LIQUIDITY_WEIGHT": 0.15,
  "SESSION_WEIGHT": 0.10,
  ...
}
```

### 4. الأداء الكلي
**المسار**: `/api/dashboard/overall_performance`
**الطريقة**: GET
**الوصف**: يعيد معايير الأداء الكلي لجميع الصفقات المغلقة

**الاستجابة**:
```json
{
  "total_trades": 150,
  "winning_trades": 95,
  "losing_trades": 55,
  "win_rate": 0.6333,
  "total_pnl": 2500.75,
  "average_pnl": 16.67,
  "max_drawdown": -450.25,
  "max_drawdown_percent": -2.15,
  "sharpe_ratio": 1.25,
  "profit_factor": 2.15,
  "consecutive_wins": 8,
  "consecutive_losses": 3
}
```

### 5. أداء الاستراتيجية حسب العملة
**المسار**: `/api/dashboard/strategy_performance/{symbol}`
**الطريقة**: GET
**الوصف**: يعيد معايير الأداء لعملة معينة

**الاستجابة**:
```json
{
  "symbol": "BTCUSDT",
  "total_trades": 50,
  "winning_trades": 32,
  "losing_trades": 18,
  "win_rate": 0.64,
  "total_pnl": 850.50,
  "average_pnl": 17.01,
  "max_drawdown": -200.25,
  "max_drawdown_percent": -1.95,
  "sharpe_ratio": 1.45,
  "profit_factor": 2.35,
  "consecutive_wins": 6,
  "consecutive_losses": 2
}
```

## المقاييس الرئيسية المعروضة

### حالة النظام
| المقياس | الوصف |
|---------|-------|
| **Engine Running** | حالة المحرك (تشغيل/إيقاف) |
| **Active Coins** | قائمة العملات المفعلة للتحليل |
| **Last Data At** | آخر وقت تم فيه استقبال بيانات |
| **Errors** | عدد الأخطاء التي حدثت |

### الأسعار الحية
| المقياس | الوصف |
|---------|-------|
| **Symbol** | رمز العملة (مثل BTCUSDT) |
| **Price** | السعر الحالي |
| **Timestamp** | وقت آخر تحديث للسعر |

### الأداء
| المقياس | الوصف |
|---------|-------|
| **Total Trades** | إجمالي عدد الصفقات |
| **Win Rate** | نسبة الصفقات الرابحة |
| **Total PnL** | إجمالي الربح/الخسارة |
| **Max Drawdown** | أقصى انخفاض في رأس المال |
| **Sharpe Ratio** | نسبة العائد المعدل بالمخاطر |
| **Profit Factor** | نسبة الأرباح الإجمالية إلى الخسائر |
| **Consecutive Wins/Losses** | أطول سلسلة من الانتصارات/الخسائر |

## التحديثات التلقائية

تتحدث لوحة التحكم البيانات تلقائياً بالفترات التالية:

- **حالة النظام**: كل 5 ثوان
- **الأسعار الحية**: كل 3 ثوان
- **الأداء الكلي**: كل 10 ثوان
- **الثوابت**: مرة واحدة عند التحميل الأولي

## الملفات المضافة/المعدلة

### ملفات جديدة:
1. **app/dashboard_endpoints.py** - مسارات API الجديدة
2. **app/static/index.html** - واجهة لوحة التحكم

### ملفات معدلة:
1. **app/main.py** - دمج مسارات API الجديدة وتثبيت الملفات الثابتة
2. **config/thresholds.py** - إضافة `__all__` للتصدير

## المتطلبات

جميع المتطلبات موجودة بالفعل في `requirements.txt`:
- FastAPI
- Uvicorn
- Pydantic
- Redis
- Asyncpg

## الاستخدام

### التشغيل المحلي:
```bash
cd CT
python -m app.main
```

ثم افتح المتصفح على: `http://localhost:8000/dashboard`

### التشغيل على Render:
اللوحة متاحة تلقائياً على: `https://your-app.onrender.com/dashboard`

## الميزات الرئيسية

✅ **عرض حي للبيانات**: تحديثات فورية لحالة النظام والأسعار
✅ **مراقبة الأداء**: تتبع أداء الاستراتيجيات والصفقات
✅ **معايير شاملة**: عرض جميع المعايير المالية المهمة
✅ **إعدادات النظام**: عرض جميع الثوابت والعتبات المستخدمة
✅ **واجهة سهلة الاستخدام**: تصميم حديث وسهل الاستخدام
✅ **تحديثات تلقائية**: تحديثات دورية بدون تدخل يدوي

## استكشاف الأخطاء

### لا تظهر البيانات:
1. تأكد من أن المحرك يعمل (`Engine Running: Running`)
2. تحقق من وجود عملات مفعلة (`Active Coins`)
3. افتح أدوات المطور (F12) وتحقق من رسائل الخطأ في Console

### الأسعار الحية لا تظهر:
1. تأكد من أن Redis يعمل بشكل صحيح
2. تأكد من أن WebSocket يستقبل البيانات
3. تحقق من أن العملات مفعلة في قاعدة البيانات

### الأداء لا يظهر:
1. تأكد من أن هناك صفقات مغلقة في قاعدة البيانات
2. تحقق من اتصال Supabase
3. تأكد من أن `PerformanceCalculator` تم تهيئته بشكل صحيح

## التطوير المستقبلي

يمكن إضافة الميزات التالية في المستقبل:

- 📊 رسوم بيانية متقدمة لتتبع الأداء
- 🔔 تنبيهات فورية للأحداث المهمة
- 📈 تحليل تفصيلي للصفقات
- 🔍 بحث متقدم وتصفية البيانات
- 💾 تصدير البيانات إلى CSV/Excel
- 🌙 وضع مظلم/فاتح
- 📱 دعم الأجهزة المحمولة المتقدم

## الدعم والمساعدة

للمزيد من المعلومات، راجع:
- [WORKFLOW_LOGS_GUIDE.md](WORKFLOW_LOGS_GUIDE.md) - دليل سجلات سير العمل
- [README.md](README.md) - ملف التعريف الرئيسي
- [INVESTIGATION_REPORT.md](INVESTIGATION_REPORT.md) - تقرير التحقيق
