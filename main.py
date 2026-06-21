import os
import sys
import asyncio
from keep_alive import keep_alive
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler
from config import TELEGRAM_TOKEN, ADMIN_ID
from database import init_db
from bot.handlers import (
    start, handle_message, process_add_symbol, process_add_capital, 
    process_add_risk, process_add_tf,
    ADD_SYMBOL, ADD_CAPITAL, ADD_RISK, ADD_TF
)
from Core.trade_monitor import TradeMonitor

async def start_background_tasks(app):
    """تشغيل الرادار المؤسسي والمراقبة"""
    await asyncio.sleep(5)
    monitor = TradeMonitor(bot=app.bot)
    asyncio.create_task(monitor.check_prices())
    print("📡 [SYSTEM] تم إطلاق الرادار المؤسسي والمراقبة اللحظية.")

async def post_init(app: Application):
    await start_background_tasks(app)

async def main():
    # تشغيل خادم Keep-Alive أولاً
    keep_alive()
    
    print("🚀 جاري إقلاع نظام التداول المؤسسي CT V4.0...")
    
    # تهيئة قاعدة البيانات
    await init_db()
    
    # بناء التطبيق باستخدام الطريقة القياسية لـ python-telegram-bot v20+
    application = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    
    # إعداد المحادثة المؤسسية لإضافة عملة
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ إضافة عملة$"), handle_message)],
        states={
            ADD_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_symbol)],
            ADD_CAPITAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_capital)],
            ADD_RISK: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_risk)],
            ADD_TF: [CallbackQueryHandler(process_add_tf, pattern='^tf_')],
        },
        fallbacks=[CommandHandler('start', start)],
        per_message=False
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ النظام المؤسسي جاهز بالكامل.")
    
    # استخدام run_polling وهي الطريقة الأكثر استقراراً وبساطة
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    
    # إبقاء البرنامج يعمل للأبد
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await application.stop()
        await application.shutdown()

if __name__ == "__main__":
    # تشغيل الـ Loop الرئيسي
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
