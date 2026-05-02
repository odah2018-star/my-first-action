import re
import logging
import os
import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict
from contextlib import contextmanager

from dotenv import load_dotenv

# Proxy settings (لـ PythonAnywhere - لا تؤثر على GitHub Actions)
os.environ['HTTP_PROXY'] = 'http://proxy.pythonanywhere.com:8080'
os.environ['HTTPS_PROXY'] = 'http://proxy.pythonanywhere.com:8080'
os.environ['NO_PROXY'] = 'localhost,127.0.0.1'

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    InlineQueryHandler,
)
from telegram.constants import ParseMode
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

# ==================== تحميل البيئة ====================
possible_paths = [
    '/home/1fu300/.env',
    '/home/motaz2026/telegram-bot/.env',
    os.path.join(os.path.dirname(__file__), '.env'),
    '.env'
]

env_loaded = False
for path in possible_paths:
    if os.path.exists(path):
        load_dotenv(path)
        print(f"✅ تم تحميل .env من: {path}")
        env_loaded = True
        break

if not env_loaded:
    load_dotenv()
    print("⚠️ تم تحميل .env من المسار الافتراضي")

# ==================== المتغيرات الأساسية ====================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "").strip()
ADMIN_IDS = []
admin_ids_str = os.getenv("ADMIN_IDS", "")

if admin_ids_str:
    for id_str in admin_ids_str.split(","):
        try:
            ADMIN_IDS.append(int(id_str.strip()))
        except ValueError:
            print(f"⚠️ تحذير: لا يمكن تحويل '{id_str}' إلى رقم")

AUTO_SEND_INTERVAL = int(os.getenv("AUTO_SEND_INTERVAL", "5"))
DB_PATH = os.path.join(os.path.dirname(__file__), "bot_data.db")

# 🔥 معرف القناة المرتبطة (غير مهم الآن لكن موجود)
LINKED_CHANNEL_ID = -1002882265751

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    raise ValueError("❌ لم يتم تعيين BOT_TOKEN بشكل صحيح في ملف .env!")

print(f"✅ تم تحميل التوكن: {BOT_TOKEN[:10]}...")
print(f"👑 عدد المشرفين: {len(ADMIN_IDS)}")
print(f"📢 قناة الاشتراك: {CHANNEL_LINK or 'غير مفعلة'}")
print(f"🔗 معرف القناة المرتبطة: {LINKED_CHANNEL_ID}")

# ==================== الذاكرة المؤقتة والإعدادات ====================
_replies_cache: Dict[str, str] = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== قاعدة البيانات ====================
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    global _replies_cache
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS auto_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            response TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_text TEXT NOT NULL,
            interval_minutes INTEGER DEFAULT 120,
            last_sent TIMESTAMP,
            is_active BOOLEAN DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS bot_groups (
            chat_id INTEGER PRIMARY KEY,
            chat_title TEXT,
            is_active BOOLEAN DEFAULT 1,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS violators_db (
            user_id INTEGER PRIMARY KEY,
            warnings INTEGER DEFAULT 0,
            last_violation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS custom_buttons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            button_text TEXT NOT NULL,
            button_url TEXT NOT NULL,
            menu_name TEXT DEFAULT 'main',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS button_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text_content TEXT,
            buttons_data TEXT,
            cols INTEGER DEFAULT 2,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.commit()
        for admin_id in ADMIN_IDS:
            cur.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
        conn.commit()
    refresh_caches()
    add_default_replies()
    print("✅ تم تهيئة قاعدة البيانات بنجاح")

def refresh_caches():
    global _replies_cache
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT keyword, response FROM auto_replies")
            rows = cur.fetchall()
            _replies_cache = {row['keyword']: row['response'] for row in rows}
            print(f"📝 تم تحميل {len(_replies_cache)} رد تلقائي")
    except Exception as e:
        logger.error(f"خطأ في التحميل: {e}")
        _replies_cache = {}

def add_default_replies():
    default_replies = [
        ("مرحبا", "أهلاً بك! 🌟 كيف يمكنني مساعدتك؟"),
        ("السلام عليكم", "وعليكم السلام ورحمة الله وبركاته 🌸"),
        ("شكرا", "العفو! 🌹 أهلًا بك دائمًا"),
        ("شكراً", "العفو! 🌹 أهلًا بك دائمًا"),
        ("مساعدة", "📋 الأوامر المتاحة:\n/addreply - إضافة رد\n/delreply - حذف رد\n/replies - عرض الردود\n/stats - إحصائيات\n/schedule - إضافة رسالة مجدولة"),
        ("كيف حالك", "الحمد لله، أنا بخير! شكراً لسؤالك 😊"),
        ("بوت", "أنا بوت خدمي، أرد على الكلمات المفتاحية 🤖"),
    ]
    try:
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM auto_replies")
            if cur.fetchone()[0] == 0:
                for kw, resp in default_replies:
                    cur.execute("INSERT OR IGNORE INTO auto_replies (keyword, response) VALUES (?, ?)", (kw, resp))
                conn.commit()
                refresh_caches()
                print(f"✅ تم إضافة {len(default_replies)} رد افتراضي")
    except Exception as e:
        logger.error(f"خطأ: {e}")

# ==================== الأزرار والقوائم ====================
def get_custom_buttons(menu_name: str = 'main') -> List[Tuple[str, str]]:
    with get_db() as conn:
        return conn.execute("SELECT button_text, button_url FROM custom_buttons WHERE menu_name = ? ORDER BY id", (menu_name,)).fetchall()

def add_custom_button(button_text: str, button_url: str, menu_name: str = 'main') -> bool:
    with get_db() as conn:
        try:
            conn.execute("INSERT INTO custom_buttons (button_text, button_url, menu_name) VALUES (?, ?, ?)", (button_text.strip(), button_url.strip(), menu_name))
            conn.commit()
            return True
        except:
            return False

def delete_custom_button(button_text: str, menu_name: str = 'main') -> bool:
    with get_db() as conn:
        result = conn.execute("DELETE FROM custom_buttons WHERE button_text = ? AND menu_name = ?", (button_text.strip(), menu_name))
        conn.commit()
        return result.rowcount > 0

def parse_buttons(data_str: str, cols: int):
    buttons = []
    for pair in [p.strip() for p in data_str.split('+')]:
        if '=' in pair:
            name, url = pair.split('=', 1)
            buttons.append(InlineKeyboardButton(name.strip(), url=url.strip()))
    return [buttons[i:i+cols] for i in range(0, len(buttons), cols)]

def save_button_post(text_content: str, buttons_data: str, cols: int) -> int:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO button_posts (text_content, buttons_data, cols) VALUES (?, ?, ?)", (text_content, buttons_data, cols))
        conn.commit()
        return cur.lastrowid

def get_button_post(post_id: int):
    with get_db() as conn:
        return conn.execute("SELECT text_content, buttons_data, cols FROM button_posts WHERE id = ?", (post_id,)).fetchone()

# ==================== دوال المساعدة ====================
def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'[^\w\s\u0600-\u06FF]', ' ', text.lower())
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

async def is_admin(user_id: int) -> bool:
    if user_id in ADMIN_IDS:
        return True
    try:
        with get_db() as conn:
            return conn.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)).fetchone() is not None
    except:
        return False

async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if user_id == LINKED_CHANNEL_ID or user_id < 0:
        return True
    if not CHANNEL_LINK:
        return True
    if user_id in ADMIN_IDS:
        return True
    bot_user = await context.bot.get_me()
    if user_id == bot_user.id:
        return True
    try:
        channel = CHANNEL_LINK.replace('@', '').replace('https://t.me/', '').strip()
        member = await context.bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return True

async def delete_after_delay(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception as e:
        logger.error(f"خطأ في حذف الرسالة: {e}")

async def send_subscription_warning(update: Update, context: ContextTypes.DEFAULT_TYPE, user, warning_count: int):
    delete_time = 60 if warning_count == 1 else 30 if warning_count == 2 else 20
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 اضغط للاشتراك في قناة الجامعة 📢", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")
    ]])
    warning_text = (
        f"⚠️ *تحذير {warning_count}* ⚠️\n\n"
        f"عذراً {user.first_name}، أنت غير مشترك في قناة الجامعة.\n"
        f"❌ سيتم حذف رسالتك بعد *{delete_time}* ثانية.\n\n"
        f"✨ يرجى الاشتراك ثم إعادة المحاولة ✨"
    )
    await update.message.reply_text(warning_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    asyncio.create_task(delete_after_delay(update.message, delete_time))

# ==================== معالجة الرسائل (الأهم) ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # استثناء القنوات تماماً
        if update.channel_post:
            return
        if update.message and update.message.sender_chat:
            return
        if update.effective_chat and update.effective_chat.type == "channel":
            return

        if not update.message or not update.message.text or not update.effective_user:
            return

        bot_user = await context.bot.get_me()
        if update.effective_user.id == bot_user.id or update.effective_user.id < 0:
            return

        message_text = update.message.text.strip()
        if message_text.startswith('/'):
            return

        # الاشتراك الإجباري مع تحذير
        if update.effective_chat.type in ["group", "supergroup"]:
            user_id = update.effective_user.id
            if user_id not in ADMIN_IDS:
                if not await check_subscription(user_id, context):
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT OR IGNORE INTO violators_db (user_id, warnings) VALUES (?, 0)", (user_id,))
                        cur.execute("UPDATE violators_db SET warnings = warnings + 1 WHERE user_id = ?", (user_id,))
                        conn.commit()
                        row = cur.execute("SELECT warnings FROM violators_db WHERE user_id = ?", (user_id,)).fetchone()
                        warning_count = row[0] if row else 1
                    await send_subscription_warning(update, context, update.effective_user, warning_count)
                    return
                else:
                    with get_db() as conn:
                        conn.execute("DELETE FROM violators_db WHERE user_id = ?", (user_id,))
                        conn.commit()

        # الردود التلقائية
        norm_text = normalize_text(message_text)
        if not _replies_cache:
            refresh_caches()
        for kw, resp in _replies_cache.items():
            if normalize_text(kw) in norm_text:
                await update.message.reply_text(resp, parse_mode=ParseMode.MARKDOWN)
                return
    except Exception as e:
        logger.error(f"خطأ في handle_message: {e}")

# ==================== الأوامر الأساسية (مع تشخيص) ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        print(f"📢 تم استلام أمر /start من {update.effective_user.id}")
        buttons = get_custom_buttons()
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url)] for text, url in buttons]) if buttons else None
        start_text = (
            f"🤖 *مرحباً! أنا بوت الخدمات الجامعية*\n\n"
            f"📋 *الأوامر المتاحة:*\n"
            f"/addreply كلمة : رد\n/delreply كلمة\n/replies\n"
            f"/addbutton نص : رابط\n/delbutton نص\n/buttons\n"
            f"/new 2\n/publish 1 @chat_id\n"
            f"/schedule 120 نص\n/delschedule 1\n/schedules\n"
            f"/stats\n\n🔒 الاشتراك: {'✅ مفعل' if CHANNEL_LINK else '❌ غير مفعل'}"
        )
        if keyboard:
            await update.message.reply_text(start_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(start_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"خطأ في start: {e}")
        await update.message.reply_text("البوت يعمل الآن! استخدم /replies لعرض الردود.")

# ==================== بقية الأوامر (الردود، الأزرار، المجدول) ====================
async def add_reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    if not context.args:
        return await update.message.reply_text("❌ الصيغة: `/addreply كلمة : الرد`", parse_mode=ParseMode.MARKDOWN)
    try:
        text = ' '.join(context.args)
        if ':' not in text:
            return await update.message.reply_text("❌ استخدم : للفصل")
        keyword, response = text.split(':', 1)
        keyword, response = keyword.strip().lower(), response.strip()
        if not keyword or not response:
            return await update.message.reply_text("❌ الكلمة والرد مطلوبان")
        with get_db() as conn:
            if conn.execute("SELECT 1 FROM auto_replies WHERE keyword = ?", (keyword,)).fetchone():
                return await update.message.reply_text(f"⚠️ الكلمة `{keyword}` موجودة مسبقاً!", parse_mode=ParseMode.MARKDOWN)
            conn.execute("INSERT INTO auto_replies (keyword, response) VALUES (?, ?)", (keyword, response))
            conn.commit()
        refresh_caches()
        await update.message.reply_text(f"✅ تم إضافة الرد: `{keyword}`")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

async def del_reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    if not context.args:
        return await update.message.reply_text("❌ مثال: `/delreply مرحبا`")
    keyword = ' '.join(context.args).strip().lower()
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM auto_replies WHERE keyword = ?", (keyword,)).fetchone():
            return await update.message.reply_text(f"❌ لا يوجد رد للكلمة `{keyword}`")
        conn.execute("DELETE FROM auto_replies WHERE keyword = ?", (keyword,))
        conn.commit()
    refresh_caches()
    await update.message.reply_text(f"✅ تم حذف الرد: `{keyword}`")

async def replies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _replies_cache:
        refresh_caches()
    if not _replies_cache:
        return await update.message.reply_text("📭 لا توجد ردود بعد.")
    text = "📋 *قائمة الردود:*\n\n"
    for i, (kw, resp) in enumerate(list(_replies_cache.items())[:30], 1):
        short = resp[:40] + "..." if len(resp) > 40 else resp
        text += f"{i}. `{kw}` → {short}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def add_button_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    if len(context.args) < 2 or ':' not in ' '.join(context.args):
        return await update.message.reply_text("❌ مثال: `/addbutton فيسبوك : https://facebook.com`")
    text = ' '.join(context.args)
    bt, url = text.split(':', 1)
    if add_custom_button(bt.strip(), url.strip()):
        await update.message.reply_text(f"✅ تم إضافة الزر: `{bt.strip()}`")
    else:
        await update.message.reply_text("❌ حدث خطأ")

async def del_button_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    bt = ' '.join(context.args)
    if delete_custom_button(bt):
        await update.message.reply_text(f"✅ تم حذف الزر: `{bt}`")
    else:
        await update.message.reply_text(f"❌ لا يوجد زر بهذا الاسم")

async def buttons_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    btns = get_custom_buttons()
    if not btns:
        return await update.message.reply_text("📭 لا توجد أزرار.\nاستخدم `/addbutton`")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(t, url=u)] for t, u in btns])
    await update.message.reply_text("🔗 *الأزرار المتاحة:*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        groups = conn.execute("SELECT COUNT(*) FROM bot_groups").fetchone()[0]
        replies = conn.execute("SELECT COUNT(*) FROM auto_replies").fetchone()[0]
        buttons = conn.execute("SELECT COUNT(*) FROM custom_buttons").fetchone()[0]
        scheduled = conn.execute("SELECT COUNT(*) FROM scheduled_messages WHERE is_active = 1").fetchone()[0]
    await update.message.reply_text(
        f"📊 *إحصائيات*\n📢 مجموعات: {groups}\n📝 ردود: {replies}\n🔘 أزرار: {buttons}\n⏰ مجدولة: {scheduled}\n👑 مشرفون: {len(ADMIN_IDS)}",
        parse_mode=ParseMode.MARKDOWN
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"خطأ: {context.error}")

# ==================== المجدول والجداول ====================
async def send_scheduled_messages(app: Application):
    with get_db() as conn:
        msgs = conn.execute("SELECT * FROM scheduled_messages WHERE is_active = 1").fetchall()
        groups = conn.execute("SELECT chat_id FROM bot_groups WHERE is_active = 1").fetchall()
    if not msgs or not groups:
        return
    now = datetime.now()
    for msg in msgs:
        last = msg['last_sent']
        last_sent = datetime.strptime(last, "%Y-%m-%d %H:%M:%S") if last else datetime.min
        if now - last_sent < timedelta(minutes=msg['interval_minutes']):
            continue
        for grp in groups:
            try:
                await app.bot.send_message(grp['chat_id'], msg['message_text'], parse_mode=ParseMode.MARKDOWN)
                await asyncio.sleep(0.5)
            except:
                pass
        with get_db() as conn:
            conn.execute("UPDATE scheduled_messages SET last_sent = ? WHERE id = ?", (now.strftime("%Y-%m-%d %H:%M:%S"), msg['id']))
            conn.commit()

async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    if len(context.args) < 2:
        return await update.message.reply_text("📝 `/schedule 120 نص الرسالة`")
    try:
        interval = int(context.args[0])
        text = ' '.join(context.args[1:])
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO scheduled_messages (message_text, interval_minutes, is_active, last_sent) VALUES (?, ?, 1, NULL)", (text, interval))
            conn.commit()
            msg_id = cur.lastrowid
        await update.message.reply_text(f"✅ تمت الإضافة! المعرف: `{msg_id}`", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("❌ الفاصل الزمني يجب أن يكون رقماً")

async def delschedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    if not context.args:
        return await update.message.reply_text("❌ `/delschedule 1`")
    try:
        msg_id = int(context.args[0])
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM scheduled_messages WHERE id = ?", (msg_id,)).fetchone():
                return await update.message.reply_text(f"❌ لا توجد رسالة رقم {msg_id}")
            conn.execute("DELETE FROM scheduled_messages WHERE id = ?", (msg_id,))
            conn.commit()
        await update.message.reply_text(f"✅ تم حذف الرسالة رقم {msg_id}")
    except:
        await update.message.reply_text("❌ يجب إدخال رقم صحيح")

async def schedules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    with get_db() as conn:
        msgs = conn.execute("SELECT id, message_text, interval_minutes, is_active FROM scheduled_messages").fetchall()
    if not msgs:
        return await update.message.reply_text("📭 لا توجد رسائل مجدولة.")
    text = "📋 *الرسائل المجدولة:*\n"
    for m in msgs:
        status = "✅" if m['is_active'] else "❌"
        text += f"{status} `{m['id']}` | كل {m['interval_minutes']} دقيقة\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

async def create_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    if not update.message.text:
        return
    try:
        parts = update.message.text.split('\n', 2)
        if len(parts) < 3:
            raise ValueError
        cols = int(parts[0].split()[1]) if len(parts[0].split()) > 1 else 2
        post_id = save_button_post(parts[1], parts[2], cols)
        await update.message.reply_text(f"✅ تم الإنشاء! الرقم: `{post_id}`\nشاركه: `@{ (await context.bot.get_me()).username } {post_id}`", parse_mode=ParseMode.MARKDOWN)
    except:
        await update.message.reply_text("❌ صيغة خاطئة")

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query.isdigit():
        return
    post = get_button_post(int(query))
    if post:
        text, btns, cols = post
        reply_markup = InlineKeyboardMarkup(parse_buttons(btns, cols))
        results = [InlineQueryResultArticle(id=query, title=f"نشر القائمة {query}", description=text[:50], input_message_content=InputTextMessageContent(text), reply_markup=reply_markup)]
        await update.inline_query.answer(results)

async def publish_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ خاص بالمشرفين.")
    if len(context.args) < 2:
        return await update.message.reply_text("❌ `/publish رقم @chat_id`")
    try:
        post_id = int(context.args[0])
        target = context.args[1]
        post = get_button_post(post_id)
        if not post:
            return await update.message.reply_text(f"❌ لا توجد قائمة {post_id}")
        text, btns, cols = post
        await context.bot.send_message(chat_id=target, text=text, reply_markup=InlineKeyboardMarkup(parse_buttons(btns, cols)), parse_mode=ParseMode.MARKDOWN)
        await update.message.reply_text(f"✅ تم نشر القائمة {post_id}")
    except:
        await update.message.reply_text("❌ خطأ")

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members or update.channel_post or update.effective_chat.type == "channel":
        return
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            continue
        welcome = f"🎉 *أهلاً وسهلاً بك يا {member.first_name}!* 🎉\nنرحب بك في {update.effective_chat.title or 'المجموعة'} 🌸"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📘 فيسبوك", url="https://www.facebook.com/groups/qou202")],
            [InlineKeyboardButton("📷 إنستغرام", url="https://www.instagram.com/qou_TM1/")],
        ])
        await update.message.reply_text(welcome, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        break

# ==================== تشغيل البوت ====================
async def run_bot():
    print("\n🚀 جاري تشغيل البوت...")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة المعالجات
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("addreply", add_reply_cmd))
    app.add_handler(CommandHandler("delreply", del_reply_cmd))
    app.add_handler(CommandHandler("replies", replies_cmd))
    app.add_handler(CommandHandler("addbutton", add_button_cmd))
    app.add_handler(CommandHandler("delbutton", del_button_cmd))
    app.add_handler(CommandHandler("buttons", buttons_cmd))
    app.add_handler(CommandHandler("new", create_list_command))
    app.add_handler(CommandHandler("publish", publish_list))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("delschedule", delschedule_cmd))
    app.add_handler(CommandHandler("schedules", schedules_cmd))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome_new_member))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    # المجدول
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_scheduled_messages, IntervalTrigger(minutes=AUTO_SEND_INTERVAL), args=[app])
    scheduler.start()

    print("✅ البوت يعمل الآن!")

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        await app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except Exception as e:
        print(f"❌ خطأ: {e}")
