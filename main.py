import os
import re
import threading
import time
from datetime import datetime
import pytz
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove

# ⚙️ Environment Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8827007370:AAHzXIHLHqzft4Arn_ZxTrrb9YN0NzyxQyA").strip()
DB_URI = os.environ.get("DATABASE_URL").strip()
ADMIN_ID = os.environ.get("ADMIN_ID", "8383532004").strip()

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

@app.route('/')
def home():
    return "KBKh Holiday Bot is Alive & Running!", 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# 🕒 BD Time Helper
BD_TZ = pytz.timezone("Asia/Dhaka")
def get_bd_time():
    return datetime.now(BD_TZ)
def get_today_bd():
    return get_bd_time().date()

# 🔌 Database Connection
def get_db_connection():
    uri = DB_URI
    if uri.startswith("postgres://"):
        uri = uri.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(uri)

# 🛠️ Database Setup (New Table for Holidays)
def init_db():
    try:
        conn = get_db_connection()
        conn.autocommit = True
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS holiday_leaves (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT,
                reason TEXT,
                start_date DATE,
                end_date DATE,
                days_count INT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.close()
    except Exception as e:
        print(f"DB Init Error: {e}")

init_db()

INFO_TEAMS = ["Team Alpha", "Team Beta", "Team Gamma"]
MEME_TEAMS = ["Team Electron", "Team Proton", "Team Neutron"]
user_states = {}

# 🛡️ Member Verification Function
def get_member_info(tg_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT fb_name, unique_id, team_name, status, is_blocked, is_removed FROM members WHERE telegram_id = %s", (tg_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    except Exception:
        return None

# 📱 Keyboards
def get_main_keyboard(tg_id):
    markup = ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    if str(tg_id) == ADMIN_ID:
        markup.row(KeyboardButton("Leave Applications"), KeyboardButton("Reset Data"))
    else:
        markup.add(KeyboardButton("Apply for Leave"))
        markup.add(KeyboardButton("Cancel ongoing leave"))
        markup.add(KeyboardButton("Latest Application Receipt"))
    return markup

def get_cancel_form_keyboard():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Cancel Application"))
    return markup

# 📌 Enforce Registration
def enforce_registration(message):
    tg_id = message.from_user.id
    if str(tg_id) == ADMIN_ID: return False
    
    user = get_member_info(tg_id)
    if not user or user['status'] != 'Approved' or user['is_removed']:
        bot.send_message(message.chat.id, "You are not registered❌\nPlease complete the registration first!", reply_markup=ReplyKeyboardRemove())
        return True
    if user['is_blocked']:
        bot.send_message(message.chat.id, "Access Blocked⛔", reply_markup=ReplyKeyboardRemove())
        return True
    return False

# 🚀 /start Command
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if enforce_registration(message): return
    tg_id = message.from_user.id
    user_states.pop(tg_id, None)
    
    if str(tg_id) == ADMIN_ID:
        msg = "Welcome Admin Control Panel!\nSelect an option below:"
    else:
        msg = "Welcome to KBKh Leave Portal!\n\nYour quick assistant for managing time off and leave applications.\nChoose an option from the menu below to proceed✅"
    
    bot.send_message(message.chat.id, msg, reply_markup=get_main_keyboard(tg_id))

# ❌ Cancel Application Flow
@bot.message_handler(func=lambda msg: msg.text == "Cancel Application")
def cancel_application_form(message):
    tg_id = message.from_user.id
    bot.clear_step_handler_by_chat_id(message.chat.id)
    user_states.pop(tg_id, None)
    bot.send_message(message.chat.id, "Your leave application has been successfully cancelled✅", reply_markup=get_main_keyboard(tg_id))

# 📝 1. APPLY FOR LEAVE
@bot.message_handler(func=lambda msg: msg.text == "Apply for Leave")
def apply_leave_start(message):
    if enforce_registration(message): return
    tg_id = message.from_user.id
    today = get_today_bd()
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT end_date FROM holiday_leaves WHERE telegram_id = %s AND status = 'active' AND end_date >= %s ORDER BY id DESC LIMIT 1", (tg_id, today))
    active_leave = cursor.fetchone()
    conn.close()
    
    if active_leave:
        end_str = active_leave['end_date'].strftime('%d/%m/%Y')
        bot.send_message(message.chat.id, f"আপনার {end_str} তারিখ পর্যন্ত ছুটি চলমান আছে। এটি শেষ হলে এরপর পুনরায় আবেদন করতে পারবেন।", reply_markup=get_main_keyboard(tg_id))
        return

    msg = bot.send_message(message.chat.id, "Please briefly state the primary reason for taking leave (in Bengali):", reply_markup=get_cancel_form_keyboard())
    user_states[tg_id] = {}
    bot.register_next_step_handler(msg, step_get_reason)

def step_get_reason(message):
    if message.text == "Cancel Application": return cancel_application_form(message)
    tg_id = message.from_user.id
    user_states[tg_id]['reason'] = message.text.strip()
    
    msg = bot.send_message(message.chat.id, "Please specify the leave Start and End dates\n( ⚠️ Must be in 'DD.MM-DD.MM' format)", reply_markup=get_cancel_form_keyboard())
    bot.register_next_step_handler(msg, step_get_dates)

def step_get_dates(message):
    if message.text == "Cancel Application": return cancel_application_form(message)
    tg_id = message.from_user.id
    text = message.text.strip()
    
    # 🕒 Core Date Logic (Unchanged and Protected!)
    bangla_digits = "০১২৩৪৫৬৭৮৯"
    english_digits = "0123456789"
    for b, e in zip(bangla_digits, english_digits): text = text.replace(b, e)

    pattern = r'(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}|\d{2}(?![./-]\d)))?'
    matches = re.findall(pattern, text)

    if len(matches) < 2:
        msg = bot.send_message(message.chat.id, "⚠️ তারিখ সঠিকভাবে পাওয়া যায়নি! অনুগ্রহ করে আবার সঠিক ফরম্যাটে দিন\n\n( ⚠️ Must be in 'DD.MM-DD.MM' format)", reply_markup=get_cancel_form_keyboard())
        return bot.register_next_step_handler(msg, step_get_dates)

    today = get_today_bd()
    today_year = today.year

    def create_date_obj(match_tuple, default_year):
        d, m, y = match_tuple
        try:
            day, month = int(d), int(m)
            if y:
                year = int(y)
                if year < 100: year += 2000
            else: year = default_year
            return datetime(year, month, day).date()
        except ValueError: return None

    start_d = create_date_obj(matches[0], today_year)
    end_d = create_date_obj(matches[1], today_year)

    if not start_d or not end_d:
        msg = bot.send_message(message.chat.id, "⚠️ তারিখের ফরম্যাট সঠিক নয়! অনুগ্রহ করে পুনরায় সঠিক তারিখ লিখুন:", reply_markup=get_cancel_form_keyboard())
        return bot.register_next_step_handler(msg, step_get_dates)

    if start_d.month == 12 and end_d.month == 1 and end_d < start_d: end_d = end_d.replace(year=today_year + 1)
    if start_d < today and end_d < start_d:
        msg = bot.send_message(message.chat.id, "⚠️ শুরুর এবং শেষ তারিখ সঠিক করুন!", reply_markup=get_cancel_form_keyboard())
        return bot.register_next_step_handler(msg, step_get_dates)
    elif start_d < today:
        msg = bot.send_message(message.chat.id, "⚠️ভুল তারিখ প্রদান করেছেন!\n\n(শুরুর তারিখ সঠিক করুন)", reply_markup=get_cancel_form_keyboard())
        return bot.register_next_step_handler(msg, step_get_dates)
    elif end_d < start_d:
        msg = bot.send_message(message.chat.id, "⚠️ভুল তারিখ প্রদান করেছেন!\n\n(শেষ তারিখ সঠিক করুন)", reply_markup=get_cancel_form_keyboard())
        return bot.register_next_step_handler(msg, step_get_dates)

    days_count = (end_d - start_d).days + 1
    if days_count > 60:
        msg = bot.send_message(message.chat.id, "⚠️ভুল তারিখ প্রদান করেছেন!\n\n(একবারে সর্বোচ্চ ৬০ দিনের বেশি ছুটি আবেদন করা যাবে না)", reply_markup=get_cancel_form_keyboard())
        return bot.register_next_step_handler(msg, step_get_dates)

    # Everything verified, save and output receipt!
    user_data = get_member_info(tg_id)
    reason = user_states[tg_id]['reason']
    month_name = get_bd_time().strftime("%B")
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Save Leave
        cursor.execute("INSERT INTO holiday_leaves (telegram_id, reason, start_date, end_date, days_count) VALUES (%s, %s, %s, %s, %s)", (tg_id, reason, start_d, end_d, days_count))
        
        # Calculate Total Leaves for Receipt
        cursor.execute("SELECT SUM(days_count) FROM holiday_leaves WHERE telegram_id = %s", (tg_id,))
        total_days = cursor.fetchone()[0] or days_count
        
        # 🔥 Update 0Days in Task Records Central DB 🔥
        cursor.execute("INSERT INTO task_records (telegram_id, month, holiday_days) VALUES (%s, %s, %s) ON CONFLICT (telegram_id, month) DO UPDATE SET holiday_days = task_records.holiday_days + %s", (tg_id, month_name, days_count, days_count))
        
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving leave: {e}")
        bot.send_message(message.chat.id, "System Error. Please try again.", reply_markup=get_main_keyboard(tg_id))
        return

    # Success Messages
    ack_msg = (
        "✔️আপনার ছুটির আবেদন গৃহীত হয়েছে\n\n"
        "✔️এখন আপনার মূল কাজ: ডিসকাশন গ্রুপে আপনার নামের পাশে \"📍(Absent)\" যুক্ত করে নিন। "
        "আশা করি, নির্ধারিত সময়ের মধ্যে আপনি পুনরায় কাজে যোগ দেবেন। কাজে ফেরার পর নিজ দায়িত্বে নামের পাশ থেকে ‘Absent’ চিহ্নটি সরিয়ে নেবেন।\n\n"
        "⚠️ঘন ঘন বা দীর্ঘমেয়াদী ছুটি আপনার ৪ মাসের ইন্টার্নশিপ সফলভাবে সম্পন্ন হওয়ার ক্ষেত্রে বাধা হতে পারে। "
        "নিয়ম অনুযায়ী, এমন ক্ষেত্রে ইন্টার্নশিপের মেয়াদ বর্ধিত হতে পারে। তাই ঘন ঘন বা দীর্ঘদিনের ছুটি না নেওয়ার অনুরোধ রইল। "
        "ছুটি চলাকালীনও সম্ভব হলে প্রতিদিন কিছু কাজ সম্পন্ন করার চেষ্টা করবেন।"
    )
    
    t_disp = str(user_data['team_name']).replace("Team ", "")
    receipt = (
        "- Application receipt 🧾\n\n"
        f"Applicant Name: {user_data['fb_name']}\n"
        f"✓Unique ID: {user_data['unique_id']}\n"
        f"✓Holiday end date: {end_d.strftime('%d/%m/%Y')}\n"
        f"✓Number of holidays: {days_count}\n"
        f"✓The Total number of holidays, including all previous holidays, is: {total_days}\n"
        f"✓Team: {t_disp}"
    )
    
    bot.send_message(message.chat.id, ack_msg, reply_markup=get_main_keyboard(tg_id))
    bot.send_message(message.chat.id, receipt)
    user_states.pop(tg_id, None)

# 🛑 2. CANCEL ONGOING LEAVE
@bot.message_handler(func=lambda msg: msg.text == "Cancel ongoing leave")
def cancel_ongoing_leave_start(message):
    if enforce_registration(message): return
    tg_id = message.from_user.id
    today = get_today_bd()
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM holiday_leaves WHERE telegram_id = %s AND status = 'active' AND end_date >= %s ORDER BY id DESC LIMIT 1", (tg_id, today))
    active_leave = cursor.fetchone()
    conn.close()
    
    if not active_leave:
        return bot.send_message(message.chat.id, "Your ongoing leave remains active (not cancelled).✅", reply_markup=get_main_keyboard(tg_id))
        
    markup = ReplyKeyboardMarkup(row_width=2, resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("Yes"), KeyboardButton("No"))
    bot.send_message(message.chat.id, "Are you sure you want to cancel your ongoing leave?", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text in ["Yes", "No"])
def cancel_ongoing_leave_confirm(message):
    if enforce_registration(message): return
    tg_id = message.from_user.id
    
    if message.text == "No":
        return bot.send_message(message.chat.id, "Your ongoing leave remains active (not cancelled).✅", reply_markup=get_main_keyboard(tg_id))
        
    today = get_today_bd()
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM holiday_leaves WHERE telegram_id = %s AND status = 'active' AND end_date >= %s ORDER BY id DESC LIMIT 1", (tg_id, today))
    active_leave = cursor.fetchone()
    
    if not active_leave:
        conn.close()
        return bot.send_message(message.chat.id, "Your ongoing leave remains active (not cancelled).✅", reply_markup=get_main_keyboard(tg_id))
        
    # Logic to calculate used days and refund unused days
    start_date = active_leave['start_date']
    actual_days = 0 if today < start_date else (today - start_date).days + 1
    refund_days = active_leave['days_count'] - actual_days
    month_name = get_bd_time().strftime("%B")
    
    try:
        # Update Leave
        cursor.execute("UPDATE holiday_leaves SET status = 'cancelled', end_date = %s, days_count = %s WHERE id = %s", (today, actual_days, active_leave['id']))
        
        # Calculate Total Leaves for Receipt
        cursor.execute("SELECT SUM(days_count) FROM holiday_leaves WHERE telegram_id = %s", (tg_id,))
        total_days = cursor.fetchone()[0] or actual_days
        
        # 🔥 Refund Days to Task Records Central DB 🔥
        cursor.execute("UPDATE task_records SET holiday_days = holiday_days - %s WHERE telegram_id = %s AND month = %s", (refund_days, tg_id, month_name))
        cursor.execute("UPDATE task_records SET holiday_days = 0 WHERE telegram_id = %s AND month = %s AND holiday_days < 0", (tg_id, month_name))
        conn.commit()
    except Exception as e:
        print(f"Error refunding leave: {e}")
    
    conn.close()
    user_data = get_member_info(tg_id)
    t_disp = str(user_data['team_name']).replace("Team ", "")
    
    receipt = (
        "Your ongoing leave has been successfully cancelled.☑️\n"
        "Please collect the latest receipt\n\n"
        "- Application receipt 🧾\n\n"
        f"Applicant Name: {user_data['fb_name']}\n"
        f"✓Unique ID: {user_data['unique_id']}\n"
        f"✓Holiday end date: {today.strftime('%d/%m/%Y')}\n"
        f"✓Number of holidays: {actual_days}\n"
        f"✓The Total number of holidays, including all previous holidays, is: {total_days}\n"
        f"✓Team: {t_disp}"
    )
    
    bot.send_message(message.chat.id, receipt, reply_markup=get_main_keyboard(tg_id))

# 🧾 3. LATEST RECEIPT
@bot.message_handler(func=lambda msg: msg.text == "Latest Application Receipt")
def show_latest_receipt(message):
    if enforce_registration(message): return
    tg_id = message.from_user.id
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM holiday_leaves WHERE telegram_id = %s ORDER BY id DESC LIMIT 1", (tg_id,))
    latest = cursor.fetchone()
    if not latest:
        conn.close()
        return bot.send_message(message.chat.id, "No Application receipt found!", reply_markup=get_main_keyboard(tg_id))
        
    cursor.execute("SELECT SUM(days_count) FROM holiday_leaves WHERE telegram_id = %s", (tg_id,))
    total_days = cursor.fetchone()[0] or latest['days_count']
    conn.close()
    
    user_data = get_member_info(tg_id)
    t_disp = str(user_data['team_name']).replace("Team ", "")
    
    receipt = (
        "- Application receipt 🧾\n\n"
        f"Applicant Name: {user_data['fb_name']}\n"
        f"✓Unique ID: {user_data['unique_id']}\n"
        f"✓Holiday end date: {latest['end_date'].strftime('%d/%m/%Y')}\n"
        f"✓Number of holidays: {latest['days_count']}\n"
        f"✓The Total number of holidays, including all previous holidays, is: {total_days}\n"
        f"✓Team: {t_disp}"
    )
    bot.send_message(message.chat.id, receipt, reply_markup=get_main_keyboard(tg_id))

# 👑 ADMIN: PANEL
@bot.message_handler(func=lambda msg: msg.text == "Leave Applications")
def admin_leave_menu(message):
    if str(message.from_user.id) != ADMIN_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Info Team", callback_data="leave_view_info"), InlineKeyboardButton("Meme Team", callback_data="leave_view_meme"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="admin_cancel"))
    bot.send_message(message.chat.id, "Select team to view leaves:", reply_markup=markup)

@bot.message_handler(func=lambda msg: msg.text == "Reset Data")
def admin_reset_menu(message):
    if str(message.from_user.id) != ADMIN_ID: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("Info Team", callback_data="reset_ask_info"), InlineKeyboardButton("Meme Team", callback_data="reset_ask_meme"))
    markup.add(InlineKeyboardButton("Cancel", callback_data="admin_cancel"))
    bot.send_message(message.chat.id, "Select team to reset leaves:", reply_markup=markup)

# 🔘 ADMIN: INLINE CALLBACKS
@bot.callback_query_handler(func=lambda call: True)
def handle_admin_callbacks(call):
    if str(call.from_user.id) != ADMIN_ID: return
    data = call.data
    try: bot.answer_callback_query(call.id)
    except: pass

    if data == "admin_cancel":
        bot.delete_message(call.message.chat.id, call.message.message_id)

    elif data == "admin_back":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Info Team", callback_data="leave_view_info"), InlineKeyboardButton("Meme Team", callback_data="leave_view_meme"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="admin_cancel"))
        bot.edit_message_text("Select team to view leaves:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data == "admin_reset_back":
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Info Team", callback_data="reset_ask_info"), InlineKeyboardButton("Meme Team", callback_data="reset_ask_meme"))
        markup.add(InlineKeyboardButton("Cancel", callback_data="admin_cancel"))
        bot.edit_message_text("Select team to reset leaves:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("leave_view_"):
        cat = "Info" if "info" in data else "Meme"
        teams = INFO_TEAMS if cat == "Info" else MEME_TEAMS
        today = get_today_bd()
        
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        msg_text = f"📊 {cat} Team Current Leaves\n\n"
        for team in teams:
            t_disp = str(team).replace("Team ", "")
            msg_text += f"Team {t_disp} -\n"
            
            cursor.execute("""
                SELECT h.days_count, m.fb_name 
                FROM holiday_leaves h 
                JOIN members m ON h.telegram_id = m.telegram_id 
                WHERE m.team_name = %s AND h.status = 'active' AND h.end_date >= %s
            """, (team, today))
            leaves = cursor.fetchall()
            
            if not leaves:
                msg_text += "- None -\n\n"
            else:
                for l in leaves:
                    msg_text += f"{l['fb_name']} - {l['days_count']} Days\n"
                msg_text += "\n"
        conn.close()
        
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Back", callback_data="admin_back"), InlineKeyboardButton("Cancel", callback_data="admin_cancel"))
        bot.edit_message_text(msg_text.strip(), call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("reset_ask_"):
        cat = "Info Team" if "info" in data else "Meme Team"
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("Yes", callback_data=f"do_reset_{'info' if 'info' in data else 'meme'}"), InlineKeyboardButton("No", callback_data="admin_reset_back"))
        bot.edit_message_text(f"Are you sure you want to delete {cat}'s vacation data?", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("do_reset_"):
        cat = "Info Team" if "info" in data else "Meme Team"
        teams = INFO_TEAMS if "info" in data else MEME_TEAMS
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Delete Leaves
        cursor.execute("DELETE FROM holiday_leaves WHERE telegram_id IN (SELECT telegram_id FROM members WHERE team_name = ANY(%s))", (teams,))
        
        # Reset 0Days in Central DB safely!
        month_name = get_bd_time().strftime("%B")
        cursor.execute("UPDATE task_records SET holiday_days = 0 WHERE telegram_id IN (SELECT telegram_id FROM members WHERE team_name = ANY(%s)) AND month = %s", (teams, month_name))
        
        conn.commit()
        conn.close()
        
        bot.edit_message_text(f"All leave data for the {cat} has been deleted Successfully✅", call.message.chat.id, call.message.message_id)

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    print("🤖 KBKh Holiday Bot is Active & Running...")
    
    try: bot.remove_webhook()
    except Exception: pass
        
    while True:
        try: bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
        except Exception as e: time.sleep(5)
