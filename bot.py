import logging
import asyncio
import pandas as pd
from datetime import datetime, timedelta
import os
import sys

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- ⚙️ CONFIGURATION ---
# ON RENDER: Set these in the "Environment" tab!
API_TOKEN = os.getenv("TELEGRAM_TOKEN")
# If not set in Render, it crashes to warn you
if not API_TOKEN:
    sys.exit("Error: TELEGRAM_TOKEN is not set in environment variables!")

# We try to get Admin ID from environment, or default to None (you must claim it)
MASTER_ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

# Webhook Config for Render
WEBHOOK_PATH = "/webhook"
# Render gives you a URL like https://your-app.onrender.com
# You must set this in Render Environment variables as 'EXTERNAL_URL'
WEB_SERVER_URL = os.getenv("EXTERNAL_URL", "") 

# --- 💾 DATABASE (WARNING FOR FREE TIER) ---
# On Render Free Tier, this data WIPES every time the server sleeps (15 mins inactivity).
# For a real product, you MUST use a database (Postgres/Redis).
# For testing, this is fine, but data will be lost on restart.
CLIENT_CONFIG = {
    'channel_id': None, # We will set this dynamically!
    'business_name': "Crypto Signals VIP",
    'trial_enabled': True,
    'price': 20
}
users_db = {} 
SAAS_LICENSES = {}

# --- 🌍 LANGUAGE SETTINGS ---
LANG_DATA = {
    'en': {
        'welcome': "👋 Welcome! Select an option:",
        'trial_btn': "⏳ Get 24h Free Trial",
        'buy_btn': "💎 Buy VIP Membership",
        'support_btn': "📞 Support",
        'trial_success': "✅ **Trial Activated!**\nLink: ",
        'trial_disabled': "❌ Free trials are disabled.",
        'trial_used': "⚠️ Trial already used.",
        'payment_info': "💰 Send $20 USDT (TRC20) to: `TJxxxxxxxx`\nThen click check.",
        'payment_check': "🔍 Payment Accepted (Simulated)",
        'joined': "🎉 **Welcome to VIP!**",
        'kicked_trial': "❌ Trial over.",
        'setup_needed': "⚠️ Bot is not configured! Admin must run /set_channel"
    },
    'fa': {
        'welcome': "👋 خوش آمدید! انتخاب کنید:",
        'trial_btn': "⏳ تست ۲۴ ساعته",
        'buy_btn': "💎 خرید VIP",
        'support_btn': "📞 پشتیبانی",
        'trial_success': "✅ **تست فعال شد!**\nلینک: ",
        'trial_disabled': "❌ تست غیرفعال است.",
        'trial_used': "⚠️ قبلا استفاده کردید.",
        'payment_info': "💰 ۲۰ تتر به `TJxxxxxxxx` بزنید و تایید را بزنید.",
        'payment_check': "🔍 پرداخت تایید شد (تستی)",
        'joined': "🎉 **خوش آمدید!**",
        'kicked_trial': "❌ مهلت تست تمام شد.",
        'setup_needed': "⚠️ ربات تنظیم نشده! ادمین باید /set_channel بزند"
    }
}

# Initialize Bot
logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

# --- 📊 EXCEL LOGGING ---
EXCEL_FILE = "vip_customers.xlsx"
def log_to_excel(user_id, amount, status):
    # Note: This file will also disappear on Render restart!
    new_data = {
        'User': [user_id], 'Amount': [amount], 'Status': [status],
        'Date': [datetime.now().strftime("%Y-%m-%d")]
    }
    # (Existing excel logic skipped for brevity - keeps it simple)
    pass

# --- ⚙️ DYNAMIC SETUP COMMANDS ---

@dp.message(Command("set_channel"))
async def cmd_set_channel(message: types.Message):
    """
    Admin command to link the bot to the channel where this command is sent.
    Usage: Add bot to channel -> Make Admin -> Type /set_channel in the channel.
    """
    # Security check: Only allow the Master Admin to configure
    if message.from_user.id != MASTER_ADMIN_ID:
        await message.answer("⛔ You are not the bot owner.")
        return

    chat_type = message.chat.type
    if chat_type in ["group", "supergroup", "channel"]:
        CLIENT_CONFIG['channel_id'] = message.chat.id
        await message.answer(f"✅ **Configured!**\nTarget Channel ID set to: `{message.chat.id}`")
        
        # Try to delete the setup message to keep channel clean
        try: await message.delete()
        except: pass
    else:
        await message.answer("❌ Please run this command INSIDE the target channel/group.")

@dp.message(Command("set_admin"))
async def cmd_claim_admin(message: types.Message):
    """If ADMIN_ID wasn't set in env, the first user to type this becomes admin."""
    global MASTER_ADMIN_ID
    if MASTER_ADMIN_ID == 0:
        MASTER_ADMIN_ID = message.from_user.id
        await message.answer(f"✅ You are now the Master Admin ({MASTER_ADMIN_ID})")
    else:
        await message.answer("❌ Admin already claimed.")

# --- 🤖 STANDARD HANDLERS ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    # Select Language
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="English 🇺🇸", callback_data="lang_en"),
         InlineKeyboardButton(text="فارسی 🇮🇷", callback_data="lang_fa")]
    ])
    await message.answer("Please select language:", reply_markup=kb)

@dp.callback_query(F.data.startswith("lang_"))
async def set_language(callback: types.CallbackQuery):
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id
    if user_id not in users_db: users_db[user_id] = {}
    users_db[user_id]['lang'] = lang
    
    await callback.message.delete()
    await callback.message.answer(
        LANG_DATA[lang]['welcome'],
        reply_markup=get_main_keyboard(lang)
    )

def get_main_keyboard(lang):
    kb = [[KeyboardButton(text=LANG_DATA[lang]['buy_btn']), KeyboardButton(text=LANG_DATA[lang]['trial_btn'])]]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

@dp.message(F.text.in_([LANG_DATA['en']['trial_btn'], LANG_DATA['fa']['trial_btn']]))
async def process_trial(message: types.Message):
    # Check if channel is configured
    if not CLIENT_CONFIG['channel_id']:
        await message.answer("⚠️ Bot not configured yet.")
        return

    user_id = message.from_user.id
    lang = users_db.get(user_id, {}).get('lang', 'en')
    
    # (Trial Logic Here - Simplified)
    try:
        invite = await bot.create_chat_invite_link(
            chat_id=CLIENT_CONFIG['channel_id'],
            name=f"Trial_{user_id}",
            member_limit=1
        )
        await message.answer(LANG_DATA[lang]['trial_success'] + f"\n{invite.invite_link}")
    except Exception as e:
        await message.answer(f"Error: {e}")

# --- 🚀 WEBHOOK SERVER (REQUIRED FOR RENDER) ---

async def on_startup(bot: Bot):
    # Set the webhook when the app starts
    if WEB_SERVER_URL:
        await bot.set_webhook(f"{WEB_SERVER_URL}{WEBHOOK_PATH}")
        logging.info(f"Webhook set to {WEB_SERVER_URL}{WEBHOOK_PATH}")

def main():
    # Start Scheduler
    scheduler.start()

    # Webhook Setup
    dp.startup.register(on_startup)
    
    # Create Web App
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    
    # Render provides the PORT environment variable
    port = int(os.environ.get("PORT", 3000))
    web.run_app(app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()