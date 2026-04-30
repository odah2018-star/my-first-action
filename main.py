import re
import logging
import os
import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict
from contextlib import contextmanager

from dotenv import load_dotenv

# ==================== إعدادات Proxy ====================
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

# ==================== تحميل الإعدادات ====================
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

# ==================== قراءة المتغيرات ====================
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

if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
    raise ValueError("❌ لم يتم تعيين BOT_TOKEN بشكل صحيح في ملف .env!")

print(f"✅ تم تحميل التوكن: {BOT_TOKEN[:10]}...")
print(f"👑 عدد المشرفين: {len(ADMIN_IDS)}")
print(f"📢 قناة الاشتراك: {CHANNEL_LINK or 'غير مفعلة'}")

# ==================== التخزين المؤقت ====================
_replies_cache: Dict[str, str] = {}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== إدارة قاعدة البيانات ====================
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS auto_replies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL UNIQUE,
                response TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_text TEXT NOT NULL,
                interval_minutes INTEGER DEFAULT 120,
                last_sent TIMESTAMP,
                is_active BOOLEAN DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_groups (
                chat_id INTEGER PRIMARY KEY,
                chat_title TEXT,
                is_active BOOLEAN DEFAULT 1,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS violators_db (
                user_id INTEGER PRIMARY KEY,
                warnings INTEGER DEFAULT 0,
                last_violation TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS custom_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                button_text TEXT NOT NULL,
                button_url TEXT NOT NULL,
                menu_name TEXT DEFAULT 'main',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS button_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text_content TEXT,
                buttons_data TEXT,
                cols INTEGER DEFAULT 2,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
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
            print(f"📝 تم تحميل {len(_replies_cache)} رد تلقائي إلى الذاكرة")
    except Exception as e:
        logger.error(f"خطأ في تحميل الذاكرة المؤقتة: {e}")
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
            count = cur.fetchone()[0]
            if count == 0:
                for keyword, response in default_replies:
                    cur.execute("INSERT OR IGNORE INTO auto_replies (keyword, response) VALUES (?, ?)", (keyword, response))
                conn.commit()
                refresh_caches()
                print(f"✅ تم إضافة {len(default_replies)} رد افتراضي")
    except Exception as e:
        logger.error(f"خطأ في إضافة الردود الافتراضية: {e}")

# ==================== دوال الأزرار ====================
def get_custom_buttons(menu_name: str = 'main') -> List[Tuple[str, str]]:
    with get_db() as conn:
        return conn.execute(
            "SELECT button_text, button_url FROM custom_buttons WHERE menu_name = ? ORDER BY id",
            (menu_name,)
        ).fetchall()

def add_custom_button(button_text: str, button_url: str, menu_name: str = 'main') -> bool:
    with get_db() as conn:
        try:
            conn.execute(
                "INSERT INTO custom_buttons (button_text, button_url, menu_name) VALUES (?, ?, ?)",
                (button_text.strip(), button_url.strip(), menu_name)
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"خطأ في إضافة الزر: {e}")
            return False

def delete_custom_button(button_text: str, menu_name: str = 'main') -> bool:
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM custom_buttons WHERE button_text = ? AND menu_name = ?",
            (button_text.strip(), menu_name)
        )
        conn.commit()
        return result.rowcount > 0

# ==================== دوال القوائم المضمنة ====================
def parse_buttons(data_str: str, cols: int):
    buttons = []
    pairs = [p.strip() for p in data_str.split('+')]
    for pair in pairs:
        if '=' in pair:
            name, url = pair.split('=', 1)
            buttons.append(InlineKeyboardButton(name.strip(), url=url.strip()))
    return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]

def save_button_post(text_content: str, buttons_data: str, cols: int) -> int:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO button_posts (text_content, buttons_data, cols) VALUES (?, ?, ?)",
            (text_content, buttons_data, cols)
        )
        conn.commit()
        return cur.lastrowid

def get_button_post(post_id: int):
    with get_db() as conn:
        return conn.execute(
            "SELECT text_content, buttons_data, cols FROM button_posts WHERE id = ?",
            (post_id,)
        ).fetchone()

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
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
            return cur.fetchone() is not None
    except:
        return False

# ==================== دالة التحقق من الاشتراك (المعدلة جذرياً) ====================
async def check_subscription(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    # 1. إذا لم تكن هناك قناة محددة في ملف .env
    if not CHANNEL_LINK:
        return True
    
    # 2. 🔥 استثناء المعرفات السالبة (القنوات والمجموعات) - هذا يحل المشكلة الأساسية
    if user_id < 0:
        logger.info(f"✅ استثناء: معرف سالب {user_id} (قناة أو مجموعة)")
        return True
    
    # 3. استثناء البوت نفسه
    bot_user = await context.bot.get_me()
    if user_id == bot_user.id:
        logger.info(f"✅ استثناء: البوت نفسه {user_id}")
        return True
    
    # 4. استثناء المشرفين
    if await is_admin(user_id):
        logger.info(f"✅ استثناء: مشرف {user_id}")
        return True

    # 5. التحقق من العضو العادي
    try:
        channel = CHANNEL_LINK.replace('@', '').replace('https://t.me/', '').strip()
        member = await context.bot.get_chat_member(chat_id=f"@{channel}", user_id=user_id)
        is_member = member.status in ['member', 'administrator', 'creator']
        if not is_member:
            logger.info(f"⚠️ المستخدم {user_id} غير مشترك في القناة")
        return is_member
    except Exception as e:
        logger.error(f"خطأ في التحقق من الاشتراك: {e}")
        return True

async def delete_after_delay(message, delay: int):
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception as e:
        logger.error(f"خطأ في حذف الرسالة: {e}")

async def send_subscription_warning(update: Update, context: ContextTypes.DEFAULT_TYPE, user, warning_count: int):
    bot_user = await context.bot.get_me()
    if user.id == bot_user.id:
        return
    
    if warning_count == 1:
        delete_time = 60
    elif warning_count == 2:
        delete_time = 30
    else:
        delete_time = 20

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 اضغط للاشتراك في قناة الجامعة 📢", url=f"https://t.me/{CHANNEL_LINK.replace('@', '')}")
    ]])

    warning_text = (
        f"⚠️ *تحذير {warning_count}* ⚠️\n\n"
        f"عذراً {user.first_name}، أنت غير مشترك في قناة الجامعة.\n"
        f"❌ سيتم حذف رسالتك بعد *{delete_time}* ثانية.\n\n"
        f"✨ يرجى الاشتراك ثم إعادة المحاولة ✨"
    )

    await update.message.reply_text(
        warning_text,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN
    )
    asyncio.create_task(delete_after_delay(update.message, delete_time))

# ==================== معالجة الرسائل الرئيسية (المعدلة بالكامل) ====================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # ========== الاستثناءات الأساسية ==========
        
        # 1. تجاهل أي رسالة ليست مرسلة من مستخدم حقيقي (مثل القنوات)
        if update.message and update.message.sender_chat:
            logger.info(f"✅ استثناء: رسالة من قناة عبر sender_chat: {update.message.sender_chat.id}")
            return
        
        # 2. استثناء رسائل القنوات المباشرة
        if update.channel_post:
            logger.info("✅ استثناء: channel_post")
            return
        
        # 3. التأكد من وجود مستخدم فعال
        if not update.effective_user:
            logger.info("✅ استثناء: لا يوجد effective_user")
            return
        
        user_id = update.effective_user.id
        
        # 4. 🔥 إذا كان المعرف سالباً، فهو ليس مستخدماً بشرياً (قناة/مجموعة)، نتجاهله
        if user_id < 0:
            logger.info(f"✅ استثناء: معرف سالب {user_id} (ليس مستخدم بشري)")
            return
        
        # 5. استثناء البوت نفسه
        bot_user = await context.bot.get_me()
        if user_id == bot_user.id:
            logger.info("✅ استثناء: البوت نفسه")
            return
        
        # ========== معالجة المستخدمين العاديين فقط ==========
        
        # التأكد من وجود رسالة نصية
        if not update.message or not update.message.text:
            return
        
        message_text = update.message.text.strip()
        
        # استثناء الأوامر
        if message_text.startswith('/'):
            return
        
        # تسجيل المجموعة إذا كانت جديدة
        chat_id = update.effective_chat.id
        if update.effective_chat.type != "private":
            try:
                with get_db() as conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO bot_groups (chat_id, chat_title) VALUES (?, ?)",
                        (chat_id, update.effective_chat.title or "بدون عنوان")
                    )
                    conn.commit()
            except Exception as e:
                logger.error(f"خطأ في تسجيل المجموعة: {e}")
        
        user = update.effective_user
        
        # تطبيق الاشتراك الإجباري فقط على المستخدمين العاديين (معرف موجب)
        if user_id > 0 and update.effective_chat.type != "private":
            if not await check_subscription(user_id, context):
                try:
                    with get_db() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT OR IGNORE INTO violators_db (user_id, warnings) VALUES (?, 0)", (user_id,))
                        cur.execute("UPDATE violators_db SET warnings = warnings + 1 WHERE user_id = ?", (user_id,))
                        conn.commit()
                        cur.execute("SELECT warnings FROM violators_db WHERE user_id = ?", (user_id,))
                        row = cur.fetchone()
                        warning_count = row[0] if row else 1
                    await send_subscription_warning(update, context, user, warning_count)
                    return
                except Exception as e:
                    logger.error(f"خطأ في نظام الاشتراك: {e}")
            else:
                try:
                    with get_db() as conn:
                        conn.execute("DELETE FROM violators_db WHERE user_id = ?", (user_id,))
                        conn.commit()
                except:
                    pass
        
        # ========== الردود التلقائية ==========
        norm_text = normalize_text(message_text)
        if len(_replies_cache) == 0:
            refresh_caches()
        for keyword, response in _replies_cache.items():
            if normalize_text(keyword) in norm_text:
                try:
                    await update.message.reply_text(response, parse_mode=ParseMode.MARKDOWN)
                    return
                except:
                    try:
                        await update.message.reply_text(response)
                        return
                    except:
                        pass
                        
    except Exception as e:
        logger.error(f"خطأ عام في handle_message: {e}")

# ==================== الترحيب بالأعضاء الجدد ====================
async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.new_chat_members:
        return
    if update.channel_post:
        return
    chat_title = update.effective_chat.title or "المجموعة"
    for member in update.message.new_chat_members:
        if member.id == context.bot.id:
            continue
        welcome_text = f"""🎉 *أهلاً وسهلاً بك يا {member.first_name}!* 🎉

نرحب بك في {chat_title} 🌸
نتمنى لك قضاء وقت ممتع ومفيد معنا.

📌 *القوانين:*
• النقاش في الأمور التي تخص الجامعة فقط
• عدم نشر الإعلانات
• احترام الأعضاء

🤖 يمكنك استخدام /start لمعرفة أوامر البوت"""
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📘 فيسبوك", url="https://www.facebook.com/groups/qou202")],
            [InlineKeyboardButton("📷 إنستغرام", url="https://www.instagram.com/qou_TM1/")],
        ])
        await update.message.reply_text(welcome_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        logger.info(f"✅ تم الترحيب بالعضو: {member.first_name}")
        break

# ==================== نظام الرسائل المجدولة ====================
async def send_scheduled_messages(app: Application):
    try:
        with get_db() as conn:
            messages = conn.execute("SELECT * FROM scheduled_messages WHERE is_active = 1").fetchall()
            groups = conn.execute("SELECT chat_id FROM bot_groups WHERE is_active = 1").fetchall()
        if not messages or not groups:
            return
        now = datetime.now()
        for msg in messages:
            last_sent = None
            if msg['last_sent']:
                try:
                    last_sent = datetime.strptime(msg['last_sent'], "%Y-%m-%d %H:%M:%S")
                except:
                    last_sent = datetime.min
            else:
                last_sent = datetime.min
            if now - last_sent < timedelta(minutes=msg['interval_minutes']):
                continue
            for group in groups:
                try:
                    await app.bot.send_message(group['chat_id'], msg['message_text'], parse_mode=ParseMode.MARKDOWN)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.error(f"فشل إرسال رسالة مجدولة: {e}")
            with get_db() as conn:
                conn.execute(
                    "UPDATE scheduled_messages SET last_sent = ? WHERE id = ?",
                    (now.strftime("%Y-%m-%d %H:%M:%S"), msg['id'])
                )
                conn.commit()
    except Exception as e:
        logger.error(f"خطأ في send_scheduled_messages: {e}")

# ==================== أوامر الرسائل المجدولة ====================
async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    if len(context.args) < 2:
        return await update.message.reply_text(
            "📝 *إضافة رسالة مجدولة*\n\nالصيغة: `/schedule 120 نص الرسالة`",
            parse_mode=ParseMode.MARKDOWN
        )
    try:
        interval = int(context.args[0])
        message_text = ' '.join(context.args[1:])
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO scheduled_messages (message_text, interval_minutes, is_active, last_sent) VALUES (?, ?, 1, NULL)",
                (message_text, interval)
            )
            conn.commit()
            msg_id = cur.lastrowid
        await update.message.reply_text(
            f"✅ *تم إضافة الرسالة المجدولة!*\n\n🆔 المعرف: `{msg_id}`\n⏰ كل {interval} دقيقة",
            parse_mode=ParseMode.MARKDOWN
        )
    except ValueError:
        await update.message.reply_text("❌ الفاصل الزمني يجب أن يكون رقماً")

async def delschedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    if not context.args:
        return await update.message.reply_text("❌ مثال: `/delschedule 1`", parse_mode=ParseMode.MARKDOWN)
    try:
        msg_id = int(context.args[0])
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT message_text FROM scheduled_messages WHERE id = ?", (msg_id,))
            msg = cur.fetchone()
            if not msg:
                return await update.message.reply_text(f"❌ لا توجد رسالة رقم `{msg_id}`", parse_mode=ParseMode.MARKDOWN)
            cur.execute("DELETE FROM scheduled_messages WHERE id = ?", (msg_id,))
            conn.commit()
        await update.message.reply_text(f"✅ تم حذف الرسالة رقم `{msg_id}`", parse_mode=ParseMode.MARKDOWN)
    except ValueError:
        await update.message.reply_text("❌ يجب إدخال رقم صحيح")

async def schedules_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    with get_db() as conn:
        messages = conn.execute("SELECT id, message_text, interval_minutes, is_active FROM scheduled_messages").fetchall()
    if not messages:
        return await update.message.reply_text("📭 لا توجد رسائل مجدولة.")
    text = "📋 *الرسائل المجدولة:*\n\n"
    for msg in messages:
        status = "✅" if msg['is_active'] else "❌"
        text += f"{status} `{msg['id']}` | كل {msg['interval_minutes']} دقيقة\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ==================== أوامر القوائم المضمنة ====================
async def create_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    if not update.message.text:
        return
    try:
        parts = update.message.text.split('\n', 2)
        if len(parts) < 3:
            raise ValueError("الصيغة غير مكتملة")
        cmd_part = parts[0].split()
        cols = int(cmd_part[1]) if len(cmd_part) > 1 else 2
        msg_text = parts[1]
        btns_raw = parts[2]
        post_id = save_button_post(msg_text, btns_raw, cols)
        bot_username = (await context.bot.get_me()).username
        share_code = f"@{bot_username} {post_id}"
        await update.message.reply_text(
            f"✅ تم إنشاء القائمة!\n\n🔢 رقم: `{post_id}`\n🔗 كود: `{share_code}`",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query.isdigit():
        return
    post = get_button_post(int(query))
    if post:
        text_content, buttons_data, cols = post
        reply_markup = InlineKeyboardMarkup(parse_buttons(buttons_data, cols))
        results = [
            InlineQueryResultArticle(
                id=str(query),
                title=f"نشر القائمة رقم {query}",
                description=text_content[:50],
                input_message_content=InputTextMessageContent(text_content),
                reply_markup=reply_markup
            )
        ]
        await update.inline_query.answer(results)

async def publish_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    if len(context.args) < 2:
        return await update.message.reply_text("❌ استخدم: `/publish رقم @chat_id`")
    try:
        post_id = int(context.args[0])
        target = context.args[1]
        post = get_button_post(post_id)
        if not post:
            return await update.message.reply_text(f"❌ لا توجد قائمة رقم {post_id}")
        text_content, buttons_data, cols = post
        reply_markup = InlineKeyboardMarkup(parse_buttons(buttons_data, cols))
        await context.bot.send_message(
            chat_id=target,
            text=text_content,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        await update.message.reply_text(f"✅ تم نشر القائمة `{post_id}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

# ==================== الأوامر الأساسية ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        buttons = get_custom_buttons()
        keyboard = None
        if buttons:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(text, url=url)] for text, url in buttons
            ])
        start_text = (
            f"🤖 *مرحباً! أنا بوت الخدمات الجامعية*\n\n"
            f"📋 *الأوامر المتاحة:*\n\n"
            f"📝 *الردود التلقائية*\n"
            f"• `/addreply كلمة : رد` - إضافة رد\n"
            f"• `/delreply كلمة` - حذف رد\n"
            f"• `/replies` - عرض الردود\n\n"
            f"🔘 *الأزرار والقوائم*\n"
            f"• `/addbutton نص : رابط` - إضافة زر\n"
            f"• `/delbutton نص` - حذف زر\n"
            f"• `/buttons` - عرض الأزرار\n"
            f"• `/new 2` - إنشاء قائمة أزرار\n"
            f"• `/publish 1 @chat_id` - نشر قائمة\n\n"
            f"⏰ *الرسائل المجدولة*\n"
            f"• `/schedule 120 نص` - إضافة رسالة\n"
            f"• `/delschedule 1` - حذف رسالة\n"
            f"• `/schedules` - عرض الرسائل\n\n"
            f"📊 *أخرى*\n"
            f"• `/stats` - إحصائيات\n\n"
            f"🔒 الاشتراك: {'✅ مفعل' if CHANNEL_LINK else '❌ غير مفعل'}"
        )
        if keyboard:
            await update.message.reply_text(start_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        else:
            await update.message.reply_text(start_text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"خطأ في start: {e}")
        await update.message.reply_text("🤖 *مرحباً! أنا بوت الخدمات الجامعية*\n\nأنا أعمل الآن!", parse_mode=ParseMode.MARKDOWN)

# ==================== أوامر الردود ====================
async def add_reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    if not context.args:
        return await update.message.reply_text("❌ الصيغة: `/addreply كلمة : الرد`", parse_mode=ParseMode.MARKDOWN)
    try:
        text = ' '.join(context.args)
        if ':' not in text:
            return await update.message.reply_text("❌ استخدم `:` للفصل", parse_mode=ParseMode.MARKDOWN)
        parts = text.split(':', 1)
        keyword = parts[0].strip().lower()
        response = parts[1].strip()
        if not keyword or not response:
            return await update.message.reply_text("❌ الكلمة والرد مطلوبان")
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM auto_replies WHERE keyword = ?", (keyword,))
            if cur.fetchone():
                return await update.message.reply_text(f"⚠️ الكلمة `{keyword}` موجودة مسبقاً!", parse_mode=ParseMode.MARKDOWN)
            cur.execute("INSERT INTO auto_replies (keyword, response) VALUES (?, ?)", (keyword, response))
            conn.commit()
        refresh_caches()
        await update.message.reply_text(f"✅ تم إضافة الرد: `{keyword}`", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

async def del_reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    if not context.args:
        return await update.message.reply_text("❌ مثال: `/delreply مرحبا`", parse_mode=ParseMode.MARKDOWN)
    keyword = ' '.join(context.args).strip().lower()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT response FROM auto_replies WHERE keyword = ?", (keyword,))
        result = cur.fetchone()
        if not result:
            return await update.message.reply_text(f"❌ لا يوجد رد للكلمة `{keyword}`", parse_mode=ParseMode.MARKDOWN)
        cur.execute("DELETE FROM auto_replies WHERE keyword = ?", (keyword,))
        conn.commit()
    refresh_caches()
    await update.message.reply_text(f"✅ تم حذف الرد: `{keyword}`", parse_mode=ParseMode.MARKDOWN)

async def replies_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _replies_cache:
        refresh_caches()
    if not _replies_cache:
        return await update.message.reply_text("📭 لا توجد ردود حالياً.")
    reply_list = list(_replies_cache.items())[:30]
    text = "📋 *قائمة الردود:*\n\n"
    for i, (keyword, response) in enumerate(reply_list, 1):
        short_response = response[:40] + "..." if len(response) > 40 else response
        text += f"{i}. `{keyword}` → {short_response}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

# ==================== أوامر الأزرار ====================
async def add_button_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    if len(context.args) < 2 or ':' not in ' '.join(context.args):
        return await update.message.reply_text("❌ مثال: `/addbutton فيسبوك : https://facebook.com`", parse_mode=ParseMode.MARKDOWN)
    text = ' '.join(context.args)
    button_text, button_url = text.split(':', 1)
    if add_custom_button(button_text.strip(), button_url.strip()):
        await update.message.reply_text(f"✅ تم إضافة الزر: `{button_text.strip()}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("❌ حدث خطأ")

async def del_button_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ هذا الأمر خاص بالمشرفين فقط.")
    button_text = ' '.join(context.args)
    if delete_custom_button(button_text):
        await update.message.reply_text(f"✅ تم حذف الزر: `{button_text}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"❌ لا يوجد زر بهذا الاسم")

async def buttons_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    buttons = get_custom_buttons()
    if not buttons:
        return await update.message.reply_text("📭 لا توجد أزرار.")
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(text, url=url)] for text, url in buttons])
    await update.message.reply_text("🔗 *الأزرار المتاحة:*", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with get_db() as conn:
            groups = conn.execute("SELECT COUNT(*) FROM bot_groups").fetchone()[0]
            replies = conn.execute("SELECT COUNT(*) FROM auto_replies").fetchone()[0]
            buttons = conn.execute("SELECT COUNT(*) FROM custom_buttons").fetchone()[0]
            scheduled = conn.execute("SELECT COUNT(*) FROM scheduled_messages WHERE is_active = 1").fetchone()[0]
        await update.message.reply_text(
            f"📊 *إحصائيات البوت*\n\n📢 المجموعات: {groups}\n📝 الردود: {replies}\n🔘 الأزرار: {buttons}\n⏰ رسائل مجدولة: {scheduled}\n👑 المشرفون: {len(ADMIN_IDS)}",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"حدث خطأ: {context.error}")

# ==================== تشغيل البوت ====================
async def run_bot():
    print("\n" + "=" * 50)
    print("🚀 جاري تشغيل البوت...")
    print(f"👑 المشرفون: {ADMIN_IDS}")
    print(f"📢 قناة الاشتراك: {CHANNEL_LINK or 'غير مفعلة'}")
    print("=" * 50 + "\n")
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
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
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_scheduled_messages, IntervalTrigger(minutes=AUTO_SEND_INTERVAL), args=[app])
    scheduler.start()
    print(f"⏰ تم تفعيل نظام الرسائل المجدولة (فحص كل {AUTO_SEND_INTERVAL} دقيقة)")
    print("✅ البوت يعمل الآن! في انتظار الرسائل...\n")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        print("\n🛑 إيقاف البوت...")
        await app.stop()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except Exception as e:
        print(f"❌ خطأ فادح: {e}")
