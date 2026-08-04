import os
import re
import logging
import threading
import calendar
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
        return

def run_health_check_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_health_check_server, daemon=True).start()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
BACKUP_GROUP_ID = int(os.getenv("BACKUP_GROUP_ID"))
ADMIN_ID = os.getenv("ADMIN_ID")

BD_TZ = pytz.timezone("Asia/Dhaka")

# Conversation States
NAME, UNIQUE_ID, REASON, DATES, GROUP_NAME, CONFIRM_CANCEL_FORM = range(6)
ADMIN_GROUP, ADMIN_MONTH = range(6, 8)

# Main Buttons
BTN_APPLY = "ছুটির আবেদন করুন!"
BTN_CANCEL = "চলমান ছুটি এখনই বাতিল করুন!"
BTN_RECEIPT = "সর্বশেষ ছুটির আবেদন Receipt!"
BTN_CANCEL_FORM = "আবেদন বাতিল করুন ❌"

# Form Cancel Confirmation Buttons
BTN_FORM_YES_CANCEL = "হ্যাঁ, নিশ্চিত ❌"
BTN_FORM_NO_CANCEL = "না, বাতিল করবেন না ↩️"

# Admin Buttons
BTN_ADMIN_LEAVE = "📊 Leave Applications"
BTN_ADMIN_RESET = "⚙️ Reset Data"
BTN_RESET_YES = "হ্যাঁ, রিসেট করুন ⚠️"
BTN_RESET_NO = "না ❌"

# Confirmation Buttons
BTN_YES = "হ্যাঁ✅"
BTN_NO = "না❌"

MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12
}

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

def get_main_keyboard(user_id):
    if ADMIN_ID and str(user_id) == str(ADMIN_ID):
        buttons = [[BTN_ADMIN_LEAVE], [BTN_ADMIN_RESET]]
    else:
        buttons = [[BTN_APPLY], [BTN_CANCEL], [BTN_RECEIPT]]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_form_cancel_keyboard():
    return ReplyKeyboardMarkup([[BTN_CANCEL_FORM]], resize_keyboard=True)

def parse_date(date_str, today_year):
    date_str = date_str.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            pass
            
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
    user_id = update.effective_user.id
    if ADMIN_ID and str(user_id) == str(ADMIN_ID):
        msg = "Welcome Admin Control Panel!\n\nআপনার জন্য অপশনগুলো নিচে দেওয়া হলো:"
    else:
        msg = "Welcome to KBKh Leave Portal!\n\nYour quick assistant for managing time off and leave applications.\nChoose an option from the menu below to proceed✅"
    
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(user_id))
    return ConversationHandler.END

# Start Leave Application
async def apply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    if active_leave:
        end_str = active_leave['end_date'].strftime('%d/%m/%Y')
        await update.message.reply_text(
            f"আপনার {end_str} তারিখ পর্যন্ত ছুটি চলমান আছে। এটি শেষ হলে এরপর পুনরায় আবেদন করতে পারবেন।",
            reply_markup=get_main_keyboard(user_id)
        )
        return ConversationHandler.END

    context.user_data['current_state'] = NAME
    await update.message.reply_text(
        "আপনার সংক্ষিপ্ত নাম লিখুন:",
        reply_markup=get_form_cancel_keyboard()
    )
    return NAME

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['short_name'] = update.message.text
    context.user_data['current_state'] = UNIQUE_ID
    await update.message.reply_text("আপনার ইউনিক আইডি (Unique ID) লিখুন:", reply_markup=get_form_cancel_keyboard())
    return UNIQUE_ID

async def get_unique_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['unique_id'] = update.message.text
    context.user_data['current_state'] = REASON
    await update.message.reply_text("ছুটি নেওয়ার মূল কারণ (সংক্ষেপে উল্লেখ করুন):", reply_markup=get_form_cancel_keyboard())
    return REASON

async def get_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['reason'] = update.message.text
    context.user_data['current_state'] = DATES
    await update.message.reply_text(
        "ছুটি শুরু এবং শেষ হওয়ার তারিখ উল্লেখ করুন\n\n(⚠️অব্যশই এই ফরম্যাট এ দেবেন: 15/08 - 20/09)",
        reply_markup=get_form_cancel_keyboard()
    )
    return DATES

async def get_dates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    dates = re.findall(r'\d{1,2}[-/\.]\d{1,2}(?:[-/\.]\d{2,4})?', text)
    if len(dates) < 2:
        await update.message.reply_text(
            "⚠️ তারিখ সঠিকভাবে পাওয়া যায়নি! অনুগ্রহ করে আবার সঠিক ফরম্যাটে দিন\n\n(⚠️অব্যশই এই ফরম্যাট এ দেবেন: 15/08 - 20/09)",
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

    # Handle cross-year ONLY if start month is Dec and end month is Jan
    if start_d.month == 12 and end_d.month == 1 and end_d < start_d:
        end_d = end_d.replace(year=today_year + 1)

    # Strict Validation Checks
    is_start_invalid = start_d < today
    is_end_invalid = end_d < start_d

    if is_start_invalid and is_end_invalid:
        await update.message.reply_text(
            "⚠️ শুরুর এবং শেষ তারিখ সঠিক করুন!",
            reply_markup=get_form_cancel_keyboard()
        )
        return DATES
    elif is_start_invalid:
        await update.message.reply_text(
            "⚠️ভুল তারিখ প্রদান করেছেন!\n\n(শুরুর তারিখ সঠিক করুন)",
            reply_markup=get_form_cancel_keyboard()
        )
        return DATES
    elif is_end_invalid:
        await update.message.reply_text(
            "⚠️ভুল তারিখ প্রদান করেছেন!\n\n(শেষ তারিখ সঠিক করুন)",
            reply_markup=get_form_cancel_keyboard()
        )
        return DATES

    days_count = (end_d - start_d).days + 1

    # Max Limit Check (Max 60 days per single application)
    if days_count > 60:
        await update.message.reply_text(
            "⚠️ভুল তারিখ প্রদান করেছেন!\n\n(একবারে সর্বোচ্চ ৬০ দিনের বেশি ছুটি আবেদন করা যাবে না)",
            reply_markup=get_form_cancel_keyboard()
        )
        return DATES

    context.user_data['start_date'] = start_d
    context.user_data['end_date'] = end_d
    context.user_data['days_count'] = days_count

    context.user_data['current_state'] = GROUP_NAME
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

    await update.message.reply_text(msg_ack, reply_markup=get_main_keyboard(user_id))
    await update.message.reply_text(receipt)

    try:
        await context.bot.send_message(
            chat_id=BACKUP_GROUP_ID,
            text=f"📢 **নতুন ছুটির আবেদন:**\n\n{receipt}"
        )
    except Exception as e:
        logging.error(f"Backup group notify error: {e}")

    return ConversationHandler.END

# Form Cancel Confirmation
async def prompt_cancel_form(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = ReplyKeyboardMarkup([[BTN_FORM_YES_CANCEL, BTN_FORM_NO_CANCEL]], resize_keyboard=True)
    await update.message.reply_text("আপনি কি নিশ্চিতভাবে এই ছুটির আবেদনটি বাতিল করতে চান?", reply_markup=kb)
    return CONFIRM_CANCEL_FORM

async def confirm_cancel_form_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    if text == BTN_FORM_YES_CANCEL:
        await update.message.reply_text("আপনার ছুটির আবেদন প্রক্রিয়া বাতিল করা হলো।✅", reply_markup=get_main_keyboard(user_id))
        return ConversationHandler.END
    else:
        prev_state = context.user_data.get('current_state', NAME)
        await update.message.reply_text("আবেদন প্রক্রিয়া বাতিল করা হয়নি। অনুগ্রহ করে তথ্যটি প্রবেশ করুন:", reply_markup=get_form_cancel_keyboard())
        return prev_state

# Ongoing Leave Cancellation Logic
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
        await update.message.reply_text("আপনার কোনো চলমান ছুটি নেই।", reply_markup=get_main_keyboard(user_id))
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
        await update.message.reply_text("আপনার কোনো চলমান ছুটি নেই।", reply_markup=get_main_keyboard(user_id))
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

    await update.message.reply_text("আপনার চলমান ছুটি সফলভাবে বাতিল হয়েছে। সর্বশেষ Receipt সংগ্রহ করুন।", reply_markup=get_main_keyboard(user_id))
    await update.message.reply_text(receipt)

    try:
        await context.bot.send_message(
            chat_id=BACKUP_GROUP_ID,
            text=f"⚠️ **ছুটি বাতিল করা হয়েছে:**\n\n{receipt}"
        )
    except Exception as e:
        logging.error(f"Backup group notify error: {e}")

async def confirm_cancel_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        "আপনার চলমান ছুটি অব্যাহত আছে(বাতিল হয়নি)।✅",
        reply_markup=get_main_keyboard(user_id)
    )

async def get_latest_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT * FROM leaves WHERE user_id = %s ORDER BY id DESC LIMIT 1;", (user_id,))
    latest = cur.fetchone()
    cur.close()
    conn.close()

    if not latest:
        await update.message.reply_text("আপনার কোনো পূর্বের ছুটির আবেদন পাওয়া যায়নি।", reply_markup=get_main_keyboard(user_id))
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

    await update.message.reply_text(receipt, reply_markup=get_main_keyboard(user_id))

# Admin Flow: Leave Applications
async def admin_leave_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ADMIN_ID or str(user_id) != str(ADMIN_ID):
        return ConversationHandler.END

    group_keyboard = ReplyKeyboardMarkup(
        [["কি...বিজ্ঞান খুঁজছেন?"], ["বিজ্ঞান খুঁজে লাভ নাই!"], [BTN_CANCEL_FORM]],
        resize_keyboard=True
    )
    await update.message.reply_text("অনুগ্রহ করে গ্রুপ সিলেক্ট করুন:", reply_markup=group_keyboard)
    return ADMIN_GROUP

async def admin_group_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['admin_selected_group'] = update.message.text
    month_keyboard = ReplyKeyboardMarkup([
        ["January", "February", "March"],
        ["April", "May", "June"],
        ["July", "August", "September"],
        ["October", "November", "December"],
        [BTN_CANCEL_FORM]
    ], resize_keyboard=True)
    
    await update.message.reply_text("মাসের নাম নির্বাচন করুন:", reply_markup=month_keyboard)
    return ADMIN_MONTH

async def admin_month_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    selected_month_name = update.message.text
    selected_group = context.user_data.get('admin_selected_group')

    if selected_month_name not in MONTH_MAP:
        await update.message.reply_text("⚠️ সঠিক মাসের নাম নির্বাচন করুন।", reply_markup=get_main_keyboard(user_id))
        return ConversationHandler.END

    month_num = MONTH_MAP[selected_month_name]
    year = get_today_bd().year

    _, last_day = calendar.monthrange(year, month_num)
    m_start = datetime(year, month_num, 1).date()
    m_end = datetime(year, month_num, last_day).date()

    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT short_name, start_date, end_date 
        FROM leaves 
        WHERE group_name = %s;
    """, (selected_group,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    user_days = {}
    for r in rows:
        st = r['start_date']
        en = r['end_date']
        
        overlap_start = max(st, m_start)
        overlap_end = min(en, m_end)

        if overlap_start <= overlap_end:
            days_in_month = (overlap_end - overlap_start).days + 1
            name = r['short_name']
            user_days[name] = user_days.get(name, 0) + days_in_month

    if not user_days:
        msg = f"📊 **{selected_month_name} Month Leave Summary**\nGroup: {selected_group}\n\nএই মাসে কোনো ছুটির রেকর্ড পাওয়া যায়নি।"
    else:
        msg = f"📊 **{selected_month_name} Month Leave Summary**\nGroup: {selected_group}\n\n"
        for name, days in user_days.items():
            msg += f"{name} - {days}Days\n"

    await update.message.reply_text(msg, reply_markup=get_main_keyboard(user_id))
    return ConversationHandler.END

# Admin Flow: Reset Data
async def admin_reset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ADMIN_ID or str(user_id) != str(ADMIN_ID):
        return

    confirm_kb = ReplyKeyboardMarkup([[BTN_RESET_YES, BTN_RESET_NO]], resize_keyboard=True)
    await update.message.reply_text(
        "আপনি কি নিশ্চিতভাবে সকল ছুটির রেকর্ড রিসেট করতে চান? এটি করলে পূর্বের সকল ডাটা স্থায়ীভাবে মুছে যাবে!",
        reply_markup=confirm_kb
    )

async def admin_reset_confirm_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not ADMIN_ID or str(user_id) != str(ADMIN_ID):
        return

    conn = get_db()
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE leaves;")
    conn.commit()
    cur.close()
    conn.close()

    await update.message.reply_text("✅ সকল ছুটির রেকর্ড সফলভাবে রিসেট করা হয়েছে।", reply_markup=get_main_keyboard(user_id))

async def admin_reset_confirm_no(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text("রিসেট প্রক্রিয়া বাতিল করা হয়েছে।✅", reply_markup=get_main_keyboard(user_id))

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Apply Conversation
    conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_APPLY}$"), apply_start)],
        states={
            NAME: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_name)
            ],
            UNIQUE_ID: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_unique_id)
            ],
            REASON: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_reason)
            ],
            DATES: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_dates)
            ],
            GROUP_NAME: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_group_name)
            ],
            CONFIRM_CANCEL_FORM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, confirm_cancel_form_handler)
            ]
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
            CommandHandler("cancel", prompt_cancel_form)
        ]
    )

    # Admin Summary Conversation
    admin_summary_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{BTN_ADMIN_LEAVE}$"), admin_leave_start)],
        states={
            ADMIN_GROUP: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_group_selected)
            ],
            ADMIN_MONTH: [
                MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_month_selected)
            ],
        },
        fallbacks=[
            MessageHandler(filters.Regex(f"^{BTN_CANCEL_FORM}$"), prompt_cancel_form),
            CommandHandler("cancel", prompt_cancel_form)
        ]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(admin_summary_handler)
    
    # Handlers for Ongoing Leave Cancellation & Confirmation Buttons
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_CANCEL}$"), ask_cancel_ongoing_leave))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_YES}$"), confirm_cancel_yes))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_NO}$"), confirm_cancel_no))
    
    # Handlers for Admin Reset
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_ADMIN_RESET}$"), admin_reset_start))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_RESET_YES}$"), admin_reset_confirm_yes))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_RESET_NO}$"), admin_reset_confirm_no))

    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_RECEIPT}$"), get_latest_receipt))

    logging.info("Bot is running...")
    app.run_polling()

if __name__ == '__main__':
    main()
