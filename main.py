import os
import re
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
import pytz
import psycopg2
from psycopg2.extras import RealDictCursor
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

# Simple HTTP Handler for Render Health Check
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"KBKh Holiday Bot is running!")

    def log_message(self, format, *args):
        return  # Suppress default HTTP logging

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# Start HTTP server in background thread
threading.Thread(target=run_health_check_server, daemon=True).start()

# Logging Setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Environment Variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
BACKUP_GROUP_ID = int(os.getenv("BACKUP_GROUP_ID"))

BD_TZ = pytz.timezone("Asia/Dhaka")

# Conversation States
NAME, UNIQUE_ID, REASON, DATES, GROUP_NAME, CONFIRM_CANCEL = range(6)

# Main Buttons
BTN_APPLY = "ছুটির আবেদন করুন!"
BTN_CANCEL = "চলমান ছুটি এখনই বাতিল করুন!"
BTN_RECEIPT = "সর্বশেষ ছুটির আবেদন Receipt!"
BTN_CANCEL_FORM = "আবেদন বাতিল করুন ❌"

# Confirmation Buttons
BTN_YES = "হ্যাঁ✅"
BTN_NO = "না❌"

def get_db():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS leaves (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                short_name TEXT NOT NULL,
                unique_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                group_name TEXT NOT NULL,
                days_count INT NOT NULL,
                token_number INT NOT NULL,
                total_days INT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        logging.error(f"DB Init Error: {e}")

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[BTN_APPLY], [BTN_CANCEL], [BTN_RECEIPT]],
        resize_keyboard=True
    )

def get_form_cancel_keyboard():
    return ReplyKeyboardMarkup(
        [[BTN_CANCEL_FORM]],
        resize_keyboard=True
    )

def parse_date(date_str, today_year):
    date_str = date_str.strip()
    # Try full date formats (with year)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
            
    # Try short date formats (day/month)
    for fmt in ("%d/%m", "%d-%m", "%d.%m"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(year=today_year).date()
        except ValueError:
            pass
            
    return None

def get_today_bd():
    return datetime.now(BD_TZ).date()

# /start Command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to KBKh Leave Portal!\n\nYour quick assistant for managing time off and leave applications.\nChoose an option from the menu below to proceed✅",
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# Start Leave Application
async def apply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = get_today_bd()

    # Check active ongoing leave
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT * FROM leaves 
        WHERE user_id = %s AND status = 'active' AND end_date >= %s 
        ORDER BY id DESC LIMIT 1;
    """, (user_id, today))
    active_leave = cur.fetchone()
    cur.close()
    conn.close()

    if active_leave:
        end_str = active_leave['end_date'].strftime('%d/%m/%Y')
        await update.message.reply_text(
            f"আপনার {end_str} তারিখ পর্যন্ত ছুটি চলমান আছে। এটি শেষ হলে এরপর পুনরায় আবেদন করতে পারবেন।",
            reply_markup=get_main_keyboard()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "আপনার সংক্ষিপ্ত নাম লিখুন:",
        reply_markup=get_form_cancel_keyboard()
    )
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['short_name'] = update.message.text
    await update.message.reply_text("আপনার ইউনিক আইডি (Unique ID) লিখুন:", reply_markup=get_form_cancel_keyboard())
    return UNIQUE_ID

async def get_unique_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['unique_id'] = update.message.text
    await update.message.reply_text("ছুটি নেওয়ার মূল কারণ (সংক্ষেপে উল্লেখ করুন):", reply_markup=get_form_cancel_keyboard())
    return REASON

async def get_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reason'] = update.message.text
    await update.message.reply_text(
        "ছুটি শুরু এবং শেষ হওয়ার তারিখ উল্লেখ করুন\n\n(⚠️অব্যশই এই ফরম্যাট এ দেবেন: 15/08 to 20/09)",
        reply_markup=get_form_cancel_keyboard()
    )
    return DATES

async def get_dates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    dates = re.findall(r'\d{1,2}[-/\.]\d{1,2}(?:[-/\.]\d{2,4})?', text)
    if len(dates) < 2:
        await update.message.reply_text(
            "⚠️ তারিখ সঠিকভাবে পাওয়া যায়নি! অনুগ্রহ করে আবার সঠিক ফরম্যাটে দিন\n\n(⚠️অব্যশই এই ফরম্যাট এ দেবেন: 15/08 to 20/09)",
            reply_markup=get_form_cancel_keyboard()
        )
        return DATES

    today = get_today_bd()
    today_year = today.year

    start_d = parse_date(dates[0], today_year)
    end_d = parse_date(dates[1], today_year)

    if not start_d or not end_d:
        await update.message.reply_text(
            "⚠️ তারিখের ফরম্যাট সঠিক নয়! অনুগ্রহ করে পুনরায় সঠিক তারিখ লিখুন:",
            reply_markup=get_form_cancel_keyboard()
        )
        return DATES

    # Handle short format crossing year boundary e.g., 28/12 to 05/01
    if end_d < start_d and end_d.year == start_d.year:
        if len(dates[1].replace('.','/').replace('-','/').split('/')) == 2:
            end_d = end_d.replace(year=today_year + 1)

    if end_d < start_d:
        await update.message.reply_text(
            "⚠️ শেষ তারিখ শুরু তারিখের আগের হতে পারে না! অনুগ্রহ করে পুনরায় সঠিক তারিখ লিখুন:",
            reply_markup=get_form_cancel_keyboard()
        )
        return DATES

    days_count = (end_d - start_d).days + 1
    context.user_data['start_date'] = start_d
    context.user_data['end_date'] = end_d
    context.user_data['days_count'] = days_count

    group_keyboard = ReplyKeyboardMarkup(
        [["কি...বিজ্ঞান খুঁজছেন?"], ["বিজ্ঞান খুঁজে লাভ নাই!"], [BTN_CANCEL_FORM]],
        resize_keyboard=True
    )
    await update.message.reply_text(
        'Group Name নির্বাচন করুন বা লিখে দিন:\n(যেমন: "কি...বিজ্ঞান খুঁজছেন?/বিজ্ঞান খুঁজে লাভ নাই!")',
        reply_markup=group_keyboard
    )
    return GROUP_NAME

async def get_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    group_name = update.message.text
    user_id = update.effective_user.id
    short_name = context.user_data['short_name']
    unique_id = context.user_data['unique_id']
    reason = context.user_data['reason']
    start_date = context.user_data['start_date']
    end_date = context.user_data['end_date']
    days_count = context.user_data['days_count']

    # Database Calculation
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT COUNT(*) as cnt FROM leaves WHERE user_id = %s;", (user_id,))
    token_number = cur.fetchone()['cnt'] + 1

    cur.execute("SELECT SUM(days_count) as total FROM leaves WHERE user_id = %s;", (user_id,))
    prev_total = cur.fetchone()['total'] or 0
    total_days = prev_total + days_count

    cur.execute("""
        INSERT INTO leaves (user_id, short_name, unique_id, reason, start_date, end_date, group_name, days_count, token_number, total_days, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active');
    """, (user_id, short_name, unique_id, reason, start_date, end_date, group_name, days_count, token_number, total_days))
    conn.commit()
    cur.close()
    conn.close()

    msg_ack = (
        "✔️আপনার ছুটির আবেদন গৃহীত হয়েছে。\n\n"
        "✔️এখন আপনার মূল কাজ: দয়া করে ডিসকাশন গ্রুপে আপনার নামের পাশে \"📍(Absent)\" যুক্ত করে নিন। "
        "আশা করি, নির্ধারিত সময়ের মধ্যে আপনি পুনরায় কাজে যোগ দেবেন। কাজে ফেরার পর নিজ দায়িত্বে নামের পাশ থেকে ‘Absent’ চিহ্নটি সরিয়ে নেবেন।\n\n"
        "⚠️[ঘন ঘন বা দীর্ঘমেয়াদী ছুটি আপনার ৪ মাসের ইন্টার্নশিপ সফলভাবে সম্পন্ন হওয়ার ক্ষেত্রে বাধা হতে পারে। "
        "নিয়ম অনুযায়ী, এমন ক্ষেত্রে ইন্টার্নশিপের মেয়াদ বর্ধিত হতে পারে। তাই ঘন ঘন বা দীর্ঘদিনের ছুটি না নেওয়ার অনুরোধ রইল। "
        "ছুটি চলাকালীনও সম্ভব হলে প্রতিদিন কিছু কাজ সম্পন্ন করার চেষ্টা করবেন।]"
    )

    receipt = (
        "-Application receipt 🧾\n\n"
        f"✅আবেদনকারীর নাম: {short_name}\n"
        f"✅Unique ID: {unique_id}\n"
        f"✅ছুটির শেষ তারিখ: {end_date.strftime('%d/%m/%Y')}\n"
        f"✅ছুটির দিন সংখ্যা: {days_count}\n"
        f"✅পূর্বের সকল ছুটির দিন মিলিয়ে মোট ছুটির দিন সংখ্যা দাঁড়ালো: {total_days}\n"
        f"✅Group Name: {group_name}\n"
        f"✔️Token Number: {token_number}"
    )

    await update.message.reply_text(msg_ack, reply_markup=get_main_keyboard())
    await update.message.reply_text(receipt)

    try:
        await context.bot.send_message(
            chat_id=BACKUP_GROUP_ID,
            text=f"📢 **নতুন ছুটির আবেদন:**\n\n{receipt}"
        )
    except Exception as e:
        logging.error(f"Backup group notify error: {e}")

    return ConversationHandler.END

async def cancel_form_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("আপনার ছুটির আবেদন বাতিল হয়েছে✅", reply_markup=get_main_keyboard())
    return ConversationHandler.END

# Cancel Ongoing Leave Handler (Step 1: Ask Confirmation)
async def ask_cancel_ongoing_leave(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = get_today_bd()

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT * FROM leaves 
        WHERE user_id = %s AND status = 'active' AND end_date >= %s 
        ORDER BY id DESC LIMIT 1;
    """, (user_id, today))
    active_leave = cur.fetchone()
    cur.close()
    conn.close()

    if not active_leave:
        await update.message.reply_text("আপনার কোনো চলমান ছুটি নেই।", reply_markup=get_main_keyboard())
        return

    confirm_keyboard = ReplyKeyboardMarkup(
        [[BTN_YES, BTN_NO]],
        resize_keyboard=True,
        one_time_keyboard=True
    )
    await update.message.reply_text(
        "আপনি নিশ্চিতভাবে আপনার চলমান ছুটি বাতিল করতে চান?",
        reply_markup=confirm_keyboard
    )

# Cancel Ongoing Leave Handler (Step 2: Confirm YES)
async def confirm_cancel_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today = get_today_bd()

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT * FROM leaves 
        WHERE user_id = %s AND status = 'active' AND end_date >= %s 
        ORDER BY id DESC LIMIT 1;
    """, (user_id, today))
    active_leave = cur.fetchone()

    if not active_leave:
        await update.message.reply_text("আপনার কোনো চলমান ছুটি নেই।", reply_markup=get_main_keyboard())
        cur.close()
        conn.close()
        return

    start_date = active_leave['start_date']
    if today < start_date:
        actual_days = 0
    else:
        actual_days = (today - start_date).days + 1

    cur.execute("""
        UPDATE leaves 
        SET status = 'cancelled', end_date = %s, days_count = %s 
        WHERE id = %s;
    """, (today, actual_days, active_leave['id']))

    cur.execute("SELECT SUM(days_count) as total FROM leaves WHERE user_id = %s;", (user_id,))
    new_total_days = cur.fetchone()['total'] or 0

    cur.execute("UPDATE leaves SET total_days = %s WHERE id = %s;", (active_leave['id'],))
    cur.execute("UPDATE leaves SET total_days = %s WHERE id = %s;", (new_total_days, active_leave['id']))
    conn.commit()

    receipt = (
        "-Application receipt 🧾\n\n"
        f"✅আবেদনকারীর নাম: {active_leave['short_name']}\n"
        f"✅Unique ID: {active_leave['unique_id']}\n"
        f"✅ছুটির দিন সংখ্যা: {actual_days}\n"
        f"✅পূর্বের সকল ছুটির দিন মিলিয়ে মোট ছুটির দিন সংখ্যা দাঁড়ালো: {new_total_days}\n"
        f"✅Group Name: {active_leave['group_name']}\n"
        f"✔️Token Number: {active_leave['token_number']}"
    )

    cur.close()
    conn.close()

    await update.message.reply_text("আপনার চলমান ছুটি সফলভাবে বাতিল হয়েছে। সর্বশেষ Receipt সংগ্রহ করুন।", reply_markup=get_main_keyboard())
    await update.message.reply_text(receipt)

    try:
        await context.bot.send_message(
            chat_id=BACKUP_GROUP_ID,
            text=f"⚠️ **ছুটি বাতিল করা হয়েছে:**\n\n{receipt}"
        )
    except Exception as e:
        logging.error(f"Backup group notify error: {e}")

# Cancel Ongoing Leave Handler (Step 2: Confirm NO)
async def confirm_cancel_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "আপনার চলমান ছুটি অব্যাহত আছে(বাতিল হয়নি)।✅",
        reply_markup=get_main_keyboard()
    )

# Fetch Latest Receipt
async def get_latest_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM leaves WHERE user_id = %s ORDER BY id DESC LIMIT 1;", (user_id,))
    latest = cur.fetchone()
    cur.close()
    conn.close()

    if not latest:
        await update.message.reply_text("আপনার কোনো পূর্বের ছুটির আবেদন পাওয়া যায়নি।", reply_markup=get_main_keyboard())
        return

    if latest['status'] == 'cancelled':
        receipt = (
            "-Application receipt 🧾\n\n"
            f"✅আবেদনকারীর নাম: {latest['short_name']}\n"
            f"✅Unique ID: {latest['unique_id']}\n"
            f"✅ছুটির দিন সংখ্যা: {latest['days_count']}\n"
            f"✅পূর্বের সকল ছুটির দিন মিলিয়ে মোট ছুটির দিন সংখ্যা দাঁড়ালো: {latest['total_days']}\n"
            f"✅Group Name: {latest['group_name']}\n"
            f"✔️Token Number: {latest['token_number']}"
        )
    else:
        receipt = (
            "-Application receipt 🧾\n\n"
            f"✅আবেদনকারীর নাম: {latest['short_name']}\n"
            f"✅Unique ID: {latest['unique_id']}\n"
            f"✅ছুটির শেষ তারিখ: {latest['end_date'].strftime('%d/%m/%Y')}\n"
            f"✅ছুটির দিন সংখ্যা: {latest['days_count']}\n"
            f"✅পূর্বের সকল ছুটির দিন মিলিয়ে মোট ছুটির দিন সংখ্যা দাঁড়ালো: {latest['total_days']}\n"
            f"✅Group Name: {latest['group_name']}\n"
            f"✔️Token Number: {latest['token_number']}"
        )

    await update.message.reply_text(receipt, reply_markup=get_main_keyboard())

# Main Function
def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_APPLY}$"), apply_start)],
        states={
            NAME: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), cancel_form_action),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)
            ],
            UNIQUE_ID: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), cancel_form_action),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_unique_id)
            ],
            REASON: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), cancel_form_action),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_reason)
            ],
            DATES: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), cancel_form_action),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_dates)
            ],
            GROUP_NAME: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), cancel_form_action),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_group_name)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), cancel_form_action),
            CommandHandler("cancel", cancel_form_action)
        ]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    
    # Cancel Ongoing Leave Confirmation Handlers
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_CANCEL}$"), ask_cancel_ongoing_leave))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_YES}$"), confirm_cancel_yes))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_NO}$"), confirm_cancel_no))
    
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_RECEIPT}$"), get_latest_receipt))

    logging.info("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
