import os
import re
import json
import time
import shutil
import telebot
import threading
import logging
import gc
from telebot import types
from telebot.apihelper import ApiTelegramException

# --- ENVIRONMENT & CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set!")

telebot.apihelper.CONNECT_TIMEOUT = 15
telebot.apihelper.READ_TIMEOUT = 30

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)

ADMIN_IDS = [6630347046, 7194569468]

CHANNELS = {
    "Channel 1": -1002674664027,
    "Channel 2": -1002514181198,
    "Channel 3": -1002427180742,
    "Channel 4": -1003590340901,
    "Channel 5": -1002852893991,
}

THUMB_SLOTS = ["Photo 1", "Photo 2", "Photo 3", "Photo 4"]

DATA_FILE = "user_data.json"
BACKUP_FILE = "user_data_backup.json"
TEMP_FILE = "user_data.tmp"

# --- LOGGING SETUP ---
log_handlers = [logging.StreamHandler()]
try:
    log_handlers.append(logging.FileHandler("bot.log", encoding="utf-8"))
except Exception as e:
    print(f"Failed to set up FileHandler: {e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=log_handlers
)

# --- GLOBAL METRICS & STATE ---
START_TIME = time.time()
user_data = {}
data_lock = threading.Lock()

processed_messages = set()
processed_lock = threading.Lock()

stats_lock = threading.Lock()
STATS = {
    "forwarded_messages": 0,
    "processed_messages": 0,
    "errors": 0
}

# Pre-compiled Regex Patterns
URL_PATTERN = re.compile(r'https?://[^\s<>()"]+')
MALAYALAM_PATTERN = re.compile(r"[\u0D00-\u0D7F]")
SYMBOLS_EMOJI_PATTERN = re.compile(r"[\W_]+")
PROMO_DIGIT_PREFIX = re.compile(r"^\d+[\).\s]")
WHITESPACE_PATTERN = re.compile(r"\s+")

PROMO_WORDS = frozenset([
    "join", "telegram", "whatsapp", "subscribe", "follow",
    "watch video", "channel",
    "കൂടുതൽ", "ചാനൽ", "സബ്സ്ക്രൈബ്", "ഫോളോ",
    "ലൈക്‌ക്", "ലൈക്‌കുകൾ", "ഷെയർ", "കമന്റ്",
    "ഞങ്‌ങളുടെ", "നമ്‌മുടെ", "ഉഷാർ", "പരിപാടി"
])

IGNORE_COMMANDS = frozenset({
    "⚙️ Set Thumb", "🖼️ Use Thumb",
    "🖼️ Thumb ON", "❌ Thumb OFF",
    "🖼️ Photo ON", "❌ Photo OFF",
    "🔄 Arrange ON", "❌ Arrange OFF",
    "📝 Text Edit ON", "❌ Text Edit OFF",
    "✂️ Middle ON", "❌ Middle OFF",
    "📢 Select Channel", "📤 Auto Forward ON", "❌ Auto Forward OFF",
    "🖼️ Current Thumb", "📌 Current Settings",
    "Channel 1", "Channel 2", "Channel 3", "Channel 4", "Channel 5",
    "✅ Done", "🧹 Clear Channels", "🔙 Back",
    "Photo 1", "Photo 2", "Photo 3", "Photo 4",
    "🧪 Test Channels"
})

def increment_stat(key, count=1):
    with stats_lock:
        STATS[key] = STATS.get(key, 0) + count

def is_duplicate_msg(m):
    if not m or not hasattr(m, 'message_id') or not hasattr(m, 'chat'):
        return False
    msg_key = (m.chat.id, m.message_id)
    with processed_lock:
        if msg_key in processed_messages:
            return True
        processed_messages.add(msg_key)
        if len(processed_messages) > 10000:
            to_remove = list(processed_messages)[:5000]
            for k in to_remove:
                processed_messages.discard(k)
    increment_stat("processed_messages")
    return False

def auto_cleanup():
    with processed_lock:
        if len(processed_messages) > 5000:
            to_remove = list(processed_messages)[:2500]
            for k in to_remove:
                processed_messages.discard(k)
    gc.collect()

def default_user_state():
    return {
        "thumb_mode": False,
        "photo_mode": False,
        "arrange_mode": False,
        "text_edit_mode": False,
        "middle_mode": False,
        "auto_forward": False,
        "selected_channels": [],
        "selected_thumb": None,
        "waiting_thumb": None,
        "thumb_action": None,
        "thumbs": {slot: None for slot in THUMB_SLOTS},
    }

def save_data():
    try:
        with data_lock:
            serializable = {str(k): v for k, v in user_data.items()}
            with open(TEMP_FILE, "w", encoding="utf-8") as f:
                json.dump(serializable, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            if os.path.exists(DATA_FILE):
                try:
                    shutil.copyfile(DATA_FILE, BACKUP_FILE)
                except Exception as e:
                    logging.warning(f"Backup copy failure: {e}")
            os.replace(TEMP_FILE, DATA_FILE)
    except Exception as e:
        logging.error(f"Error during save_data: {e}")
        increment_stat("errors")

def load_data():
    global user_data
    try:
        raw = None
        target_path = None
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                target_path = DATA_FILE
            except Exception as e:
                logging.error(f"Failed loading main file {DATA_FILE}: {e}")

        if raw is None and os.path.exists(BACKUP_FILE):
            try:
                with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                target_path = BACKUP_FILE
            except Exception as e:
                logging.error(f"Failed loading backup file {BACKUP_FILE}: {e}")

        if raw is None:
            with data_lock:
                user_data = {}
            return

        fixed = {}
        for uid_str, value in raw.items():
            try:
                uid = int(uid_str)
            except ValueError:
                continue
            base = default_user_state()
            if isinstance(value, dict):
                for k in base:
                    if k in value:
                        base[k] = value[k]
                if not isinstance(base.get("thumbs"), dict):
                    base["thumbs"] = {slot: None for slot in THUMB_SLOTS}
                for slot in THUMB_SLOTS:
                    base["thumbs"].setdefault(slot, None)
                if not isinstance(base.get("selected_channels"), list):
                    base["selected_channels"] = []
            fixed[uid] = base

        with data_lock:
            user_data = fixed
        logging.info(f"User data loaded from {target_path}")
    except Exception as e:
        logging.error(f"Error loading data: {e}")
        with data_lock:
            user_data = {}

def is_admin(uid):
    return uid in ADMIN_IDS

def init_user(uid):
    if uid not in user_data:
        with data_lock:
            if uid not in user_data:
                user_data[uid] = default_user_state()
        save_data()

def safe_text(text):
    return (text or "")[:4096]

def safe_caption(text):
    return (text or "")[:1024]

def normalize_line(line):
    return WHITESPACE_PATTERN.sub(" ", (line or "").strip())

def extract_links(text):
    return URL_PATTERN.findall(text or "")

def unique_keep_order(items):
    seen = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

def has_malayalam(text):
    return bool(MALAYALAM_PATTERN.search(text or ""))

def only_symbols_or_emoji(line):
    return bool(SYMBOLS_EMOJI_PATTERN.fullmatch(line or ""))

def build_links(links):
    links = unique_keep_order(links)
    if not links:
        return ""
    # കൃത്യം ഒരു ലൈൻ സ്പേസിംഗും ഫോട്ടോയിലുള്ള ഇമോജിയും
    result = ["FULL VIDEO 🤫🌸"]
    for i, link in enumerate(links, 1):
        result.append(f"VIDEO {i}\n{link}")
    return "\n\n".join(result).strip()

def build_links_simple(links):
    links = unique_keep_order(links)
    if not links:
        return ""
    result = [f"VIDEO {i}\n{link}" for i, link in enumerate(links, 1)]
    return "\n\n".join(result).strip()

def clean_malayalam_text(text):
    lines = (text or "").splitlines()
    cleaned = []
    for raw in lines:
        line = normalize_line(raw)
        low = line.lower()
        if not line:
            continue
        if URL_PATTERN.search(line):
            continue
        if PROMO_DIGIT_PREFIX.match(line):
            continue
        if only_symbols_or_emoji(line):
            continue
        if any(w in low for w in PROMO_WORDS):
            continue
        if has_malayalam(line):
            cleaned.append(line)
    return unique_keep_order(cleaned)

def middle_text_filter(text):
    mal_lines = clean_malayalam_text(text)
    if len(mal_lines) >= 2:
        return mal_lines[1:]
    return mal_lines

def text_edit(uid, text):
    mal_lines = clean_malayalam_text(text)
    links = extract_links(text)
    parts = []
    if mal_lines:
        parts.append("\n".join(mal_lines).strip())
    if links:
        parts.append(build_links_simple(links))
    final = "\n\n".join(parts).strip()
    if not final:
        final = (text or "").strip()
    return safe_text(final)

def apply_processing(uid, text):
    text = text or ""
    links = extract_links(text)
    
    with data_lock:
        state = user_data.get(uid, default_user_state())
        arrange_mode = state.get("arrange_mode")
        text_edit_mode = state.get("text_edit_mode")
        middle_mode = state.get("middle_mode")

    if arrange_mode:
        if links:
            return safe_text(build_links(links))
        return safe_text(text.strip())

    if text_edit_mode:
        return text_edit(uid, text)

    if middle_mode:
        mal = middle_text_filter(text)
        parts = []
        if mal:
            parts.append("\n".join(mal))
        if links:
            parts.append(build_links_simple(links))
        final = "\n\n".join(parts).strip()
        return safe_text(final if final else text.strip())

    return safe_text(text.strip())

def get_thumb(uid):
    with data_lock:
        state = user_data.get(uid, {})
        slot = state.get("selected_thumb")
        if not slot:
            return None
        return state.get("thumbs", {}).get(slot)

def selected_channel_names(uid):
    with data_lock:
        selected = user_data.get(uid, {}).get("selected_channels", [])
    return [name for name, cid in CHANNELS.items() if cid in selected]

def api_call_retry(func, *args, **kwargs):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except ApiTelegramException as e:
            if e.error_code == 429:
                retry_after = 3
                try:
                    retry_after = int(e.result_json.get("parameters", {}).get("retry_after", 3))
                except Exception:
                    pass
                logging.warning(f"Telegram FloodWait 429: Retrying after {retry_after}s")
                time.sleep(retry_after + 1)
            elif e.error_code in (500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
            else:
                increment_stat("errors")
                raise e
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
            else:
                increment_stat("errors")
                raise e
    return None

def send_message_safe(chat_id, text, reply_markup=None):
    try:
        return api_call_retry(bot.send_message, chat_id, safe_text(text), reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"send_message_safe failure: {e}")
        return None

def reply_safe(m, text, reply_markup=None):
    try:
        return api_call_retry(bot.reply_to, m, safe_text(text), reply_markup=reply_markup)
    except Exception as e:
        return send_message_safe(m.chat.id, text, reply_markup=reply_markup)

def send_photo_safe(chat_id, photo, caption="", reply_markup=None):
    try:
        return api_call_retry(bot.send_photo, chat_id, photo, caption=safe_caption(caption), reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"send_photo_safe failure: {e}")
        return None

def send_video_safe(chat_id, video, caption="", reply_markup=None):
    try:
        return api_call_retry(bot.send_video, chat_id, video, caption=safe_caption(caption), reply_markup=reply_markup)
    except Exception as e:
        return None

def send_document_safe(chat_id, document, caption="", reply_markup=None):
    try:
        return api_call_retry(bot.send_document, chat_id, document, caption=safe_caption(caption), reply_markup=reply_markup)
    except Exception as e:
        return None

def send_animation_safe(chat_id, animation, caption="", reply_markup=None):
    try:
        return api_call_retry(bot.send_animation, chat_id, animation, caption=safe_caption(caption), reply_markup=reply_markup)
    except Exception as e:
        return None

def report_forward_error(uid, channel_id, err):
    send_message_safe(uid, f"⚠️ Forward failed\nChannel: {channel_id}\nError: {err}", reply_markup=main_kb())

def _generic_forward(uid, media_or_text, send_fn, caption=None):
    with data_lock:
        state = user_data.get(uid, {})
        auto_forward = state.get("auto_forward", False)
        channels = list(state.get("selected_channels", []))

    if not auto_forward or not channels:
        return

    for ch in channels:
        if caption is not None:
            msg = send_fn(ch, media_or_text, caption)
        else:
            msg = send_fn(ch, media_or_text)

        if msg:
            increment_stat("forwarded_messages")
        else:
            report_forward_error(uid, ch, "Dispatch failed")

def forward_to_channels_text(uid, text):
    _generic_forward(uid, text, send_message_safe)

def forward_to_channels_photo(uid, photo, caption=""):
    _generic_forward(uid, photo, send_photo_safe, caption=caption)

def forward_to_channels_video(uid, video, caption=""):
    _generic_forward(uid, video, send_video_safe, caption=caption)

def forward_to_channels_document(uid, document, caption=""):
    _generic_forward(uid, document, send_document_safe, caption=caption)

def forward_to_channels_animation(uid, animation, caption=""):
    _generic_forward(uid, animation, send_animation_safe, caption=caption)

# --- KEYBOARDS ---
def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("⚙️ Set Thumb", "🖼️ Use Thumb")
    kb.row("🖼️ Thumb ON", "❌ Thumb OFF")
    kb.row("🖼️ Photo ON", "❌ Photo OFF")
    kb.row("🔄 Arrange ON", "❌ Arrange OFF")
    kb.row("📝 Text Edit ON", "❌ Text Edit OFF")
    kb.row("✂️ Middle ON", "❌ Middle OFF")
    kb.row("📢 Select Channel")
    kb.row("📤 Auto Forward ON", "❌ Auto Forward OFF")
    kb.row("🖼️ Current Thumb", "📌 Current Settings")
    return kb

def slot_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Photo 1", "Photo 2")
    kb.row("Photo 3", "Photo 4")
    kb.row("🔙 Back")
    return kb

def channel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Channel 1", "Channel 2")
    kb.row("Channel 3", "Channel 4")
    kb.row("Channel 5")
    kb.row("✅ Done", "🧹 Clear Channels")
    kb.row("🧪 Test Channels")
    kb.row("🔙 Back")
    return kb

# --- ADMIN COMMANDS ---
@bot.message_handler(commands=["stats"])
def stats_command(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return

    uptime_sec = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    days, hours = divmod(hours, 24)
    uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

    with stats_lock:
        f_msgs = STATS.get("forwarded_messages", 0)
        p_msgs = STATS.get("processed_messages", 0)
        errs = STATS.get("errors", 0)

    msg = (
        "📊 **Bot Operational Metrics**\n\n"
        f"⏱️ **Uptime:** {uptime_str}\n"
        f"📩 **Processed Messages:** {p_msgs}\n"
        f"📤 **Forwarded Messages:** {f_msgs}\n"
        f"⚠️ **Errors Handled:** {errs}"
    )
    send_message_safe(m.chat.id, msg, reply_markup=main_kb())

@bot.message_handler(commands=["health"])
def health_check(m):
    if is_duplicate_msg(m):
        return
    if is_admin(m.from_user.id):
        reply_safe(m, "🟢 **System Healthy**: Engine active, database synced.")

@bot.message_handler(commands=["start"])
def start(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        reply_safe(m, "❌ Admin only bot")
        return
    init_user(uid)
    send_message_safe(
        m.chat.id,
        "CLEAN VIP BOT READY ✅\n\n"
        "Arrange:\n"
        "FULL VIDEO 🤫🌸\n\n"
        "VIDEO 1\n"
        "link\n\n"
        "Text Edit:\n"
        "Caption\n\n"
        "VIDEO 1\n"
        "link",
        reply_markup=main_kb()
    )

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "⚙️ Set Thumb")
def set_thumb(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["thumb_action"] = "set"
    save_data()
    send_message_safe(m.chat.id, "Save slot select 👇", reply_markup=slot_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🖼️ Use Thumb")
def use_thumb(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["thumb_action"] = "use"
    save_data()
    send_message_safe(m.chat.id, "Use slot select 👇", reply_markup=slot_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text in THUMB_SLOTS)
def thumb_slot(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    slot = m.text
    
    with data_lock:
        action = user_data[uid].get("thumb_action")

    if action == "set":
        with data_lock:
            user_data[uid]["waiting_thumb"] = slot
        save_data()
        send_message_safe(m.chat.id, f"📸 {slot} ലേക്ക് save ചെയ്യാൻ photo അയക്കൂ", reply_markup=slot_kb())
        return
    if action == "use":
        with data_lock:
            has_slot_thumb = bool(user_data[uid]["thumbs"].get(slot))
        if has_slot_thumb:
            with data_lock:
                user_data[uid]["selected_thumb"] = slot
                user_data[uid]["thumb_action"] = None
            save_data()
            send_message_safe(m.chat.id, f"✅ {slot} selected", reply_markup=main_kb())
        else:
            send_message_safe(m.chat.id, f"❌ {slot} il thumb ഇല്ല", reply_markup=slot_kb())
        return
    send_message_safe(m.chat.id, "Set/Use Thumb തിരഞ്ഞെടുക്കൂ 👆", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🖼️ Current Thumb")
def current_thumb(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    thumb = get_thumb(uid)
    with data_lock:
        slot = user_data[uid].get("selected_thumb")
    if not thumb:
        send_message_safe(m.chat.id, "Current thumb selected: None ❌", reply_markup=main_kb())
        return
    send_photo_safe(m.chat.id, thumb, caption=f"Current Thumb: {slot} ✅", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🖼️ Thumb ON")
def thumb_on(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        selected = user_data[uid].get("selected_thumb")
    if not selected:
        send_message_safe(m.chat.id, "thumb select ചെയ്യൂ ❌", reply_markup=main_kb())
        return
    with data_lock:
        user_data[uid]["thumb_mode"] = True
    save_data()
    reply_safe(m, "Thumb ON ✅")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "❌ Thumb OFF")
def thumb_off(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["thumb_mode"] = False
    save_data()
    reply_safe(m, "Thumb OFF ❌")

# --- PHOTO MODE HANDLERS ---
@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🖼️ Photo ON")
def photo_mode_on(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        selected = user_data[uid].get("selected_thumb")
    if not selected:
        send_message_safe(m.chat.id, "thumb select ചെയ്യൂ ❌", reply_markup=main_kb())
        return
    with data_lock:
        user_data[uid]["photo_mode"] = True
    save_data()
    reply_safe(m, "Photo ON ✅")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "❌ Photo OFF")
def photo_mode_off(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["photo_mode"] = False
    save_data()
    reply_safe(m, "Photo OFF ❌")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🔄 Arrange ON")
def arrange_on(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["arrange_mode"] = True
        user_data[uid]["text_edit_mode"] = False
        user_data[uid]["middle_mode"] = False
    save_data()
    reply_safe(m, "Arrange ON ✅")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "❌ Arrange OFF")
def arrange_off(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["arrange_mode"] = False
    save_data()
    reply_safe(m, "Arrange OFF ❌")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📝 Text Edit ON")
def text_edit_on(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["text_edit_mode"] = True
        user_data[uid]["middle_mode"] = False
        user_data[uid]["arrange_mode"] = False
    save_data()
    reply_safe(m, "Text Edit ON ✅")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "❌ Text Edit OFF")
def text_edit_off(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["text_edit_mode"] = False
    save_data()
    reply_safe(m, "Text Edit OFF ❌")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "✂️ Middle ON")
def middle_on(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["middle_mode"] = True
        user_data[uid]["text_edit_mode"] = False
        user_data[uid]["arrange_mode"] = False
    save_data()
    reply_safe(m, "Middle ON ✅")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "❌ Middle OFF")
def middle_off(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["middle_mode"] = False
    save_data()
    reply_safe(m, "Middle OFF ❌")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📢 Select Channel")
def select_channel(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    send_message_safe(m.chat.id, "Channels select 👇", reply_markup=channel_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text in CHANNELS.keys())
def toggle_channel(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    cid = CHANNELS[m.text]
    with data_lock:
        if cid in user_data[uid]["selected_channels"]:
            user_data[uid]["selected_channels"].remove(cid)
            msg_text = f"❌ {m.text} removed"
        else:
            user_data[uid]["selected_channels"].append(cid)
            msg_text = f"✅ {m.text} added"
    save_data()
    send_message_safe(m.chat.id, msg_text, reply_markup=channel_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🧪 Test Channels")
def test_channels(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        selected = list(user_data[uid].get("selected_channels", []))
    if not selected:
        send_message_safe(m.chat.id, "No channels selected for testing ❌", reply_markup=channel_kb())
        return

    results = []
    for name, cid in CHANNELS.items():
        if cid in selected:
            res = send_message_safe(cid, "🧪 Test message from Auto Forward Bot")
            if res:
                results.append(f"✅ {name}: Operational")
            else:
                results.append(f"❌ {name}: Failed (Check Bot Admin Permissions)")
    send_message_safe(m.chat.id, "\n".join(results), reply_markup=channel_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "✅ Done")
def done_channels(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    send_message_safe(m.chat.id, "Channels saved ✅", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🧹 Clear Channels")
def clear_channels(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["selected_channels"] = []
    save_data()
    send_message_safe(m.chat.id, "Channels cleared 🧹", reply_markup=channel_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🔙 Back")
def back_btn(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    send_message_safe(m.chat.id, "Main menu 🔙", reply_markup=main_kb())

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📤 Auto Forward ON")
def auto_forward_on(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        has_channels = bool(user_data[uid].get("selected_channels"))
    if not has_channels:
        send_message_safe(m.chat.id, "channel select ചെയ്യൂ ❌", reply_markup=channel_kb())
        return
    with data_lock:
        user_data[uid]["auto_forward"] = True
    save_data()
    reply_safe(m, "Auto Forward ON ✅")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "❌ Auto Forward OFF")
def auto_forward_off(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    with data_lock:
        user_data[uid]["auto_forward"] = False
    save_data()
    reply_safe(m, "Auto Forward OFF ❌")

@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📌 Current Settings")
def current_settings(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    channel_names = selected_channel_names(uid)
    channel_text = "\n".join(channel_names) if channel_names else "None ❌"
    
    with data_lock:
        st = user_data[uid]
        thumb_mode = st['thumb_mode']
        photo_mode = st.get('photo_mode', False)
        arrange_mode = st['arrange_mode']
        text_edit_mode = st['text_edit_mode']
        middle_mode = st['middle_mode']
        auto_forward = st['auto_forward']
        selected_thumb = st['selected_thumb']

    text = (
        f"Thumb Mode: {'ON ✅' if thumb_mode else 'OFF ❌'}\n"
        f"Photo Mode: {'ON ✅' if photo_mode else 'OFF ❌'}\n"
        f"Arrange Mode: {'ON ✅' if arrange_mode else 'OFF ❌'}\n"
        f"Text Edit Mode: {'ON ✅' if text_edit_mode else 'OFF ❌'}\n"
        f"Middle Mode: {'ON ✅' if middle_mode else 'OFF ❌'}\n"
        f"Auto Forward: {'ON ✅' if auto_forward else 'OFF ❌'}\n"
        f"Selected Thumb: {selected_thumb or 'None ❌'}\n\n"
        f"Selected Channels:\n{channel_text}"
    )
    send_message_safe(m.chat.id, text, reply_markup=main_kb())

# --- MEDIA HANDLERS ---
@bot.message_handler(content_types=["photo"])
def photo_handler(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    try:
        photo_id = m.photo[-1].file_id
        caption = m.caption or ""

        with data_lock:
            waiting_thumb = user_data[uid].get("waiting_thumb")

        if waiting_thumb:
            slot = waiting_thumb
            with data_lock:
                user_data[uid]["thumbs"][slot] = photo_id
                user_data[uid]["waiting_thumb"] = None
                user_data[uid]["thumb_action"] = None
            save_data()
            send_message_safe(m.chat.id, f"{slot} saved ✅", reply_markup=main_kb())
            return

        with data_lock:
            thumb_mode = user_data[uid].get("thumb_mode")

        send_photo_id = get_thumb(uid) if thumb_mode else photo_id
        if not send_photo_id:
            send_photo_id = photo_id

        final_caption = apply_processing(uid, caption)
        send_photo_safe(m.chat.id, send_photo_id, caption=final_caption, reply_markup=main_kb())
        forward_to_channels_photo(uid, send_photo_id, final_caption)
    except Exception as e:
        logging.error(f"Photo handler error: {e}")
        increment_stat("errors")
        send_message_safe(m.chat.id, "Photo process error ❌", reply_markup=main_kb())

@bot.message_handler(content_types=["video"])
def video_handler(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    try:
        video_id = m.video.file_id
        caption = m.caption or ""
        final_caption = apply_processing(uid, caption)
        send_video_safe(m.chat.id, video_id, caption=final_caption, reply_markup=main_kb())
        forward_to_channels_video(uid, video_id, final_caption)
    except Exception as e:
        logging.error(f"Video handler error: {e}")
        increment_stat("errors")
        send_message_safe(m.chat.id, "Video process error ❌", reply_markup=main_kb())

@bot.message_handler(content_types=["document"])
def document_handler(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    try:
        doc_id = m.document.file_id
        caption = m.caption or ""
        final_caption = apply_processing(uid, caption)
        send_document_safe(m.chat.id, doc_id, caption=final_caption, reply_markup=main_kb())
        forward_to_channels_document(uid, doc_id, final_caption)
    except Exception as e:
        logging.error(f"Document handler error: {e}")
        increment_stat("errors")
        send_message_safe(m.chat.id, "Document process error ❌", reply_markup=main_kb())

@bot.message_handler(content_types=["animation"])
def animation_handler(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)
    try:
        anim_id = m.animation.file_id
        caption = m.caption or ""
        final_caption = apply_processing(uid, caption)
        send_animation_safe(m.chat.id, anim_id, caption=final_caption, reply_markup=main_kb())
        forward_to_channels_animation(uid, anim_id, final_caption)
    except Exception as e:
        logging.error(f"Animation handler error: {e}")
        increment_stat("errors")
        send_message_safe(m.chat.id, "Animation process error ❌", reply_markup=main_kb())

@bot.message_handler(content_types=["text"])
def text_handler(m):
    if is_duplicate_msg(m):
        return
    uid = m.from_user.id
    if not is_admin(uid):
        return
    init_user(uid)

    if m.text in IGNORE_COMMANDS:
        return

    try:
        final_text = apply_processing(uid, m.text)

        with data_lock:
            photo_mode = user_data[uid].get("photo_mode", False)

        if photo_mode:
            thumb_photo = get_thumb(uid)
            if thumb_photo:
                send_photo_safe(m.chat.id, thumb_photo, caption=final_text, reply_markup=main_kb())
                forward_to_channels_photo(uid, thumb_photo, caption=final_text)
                return
            else:
                send_message_safe(m.chat.id, "⚠️ Photo Mode ON ആണ്, പക്ഷെ Thumb Photo select ചെയ്തിട്ടില്ല! Normal text ആയി സെൻഡ് ചെയ്യുന്നു.", reply_markup=main_kb())

        send_message_safe(
            m.chat.id,
            final_text if final_text else "Empty text ❌",
            reply_markup=main_kb()
        )
        if final_text:
            forward_to_channels_text(uid, final_text)
    except Exception as e:
        logging.error(f"Text handler error: {e}")
        increment_stat("errors")
        send_message_safe(m.chat.id, "Text process error ❌", reply_markup=main_kb())

# --- RUN ENGINE ---
def run_bot():
    backoff = 1
    logging.info("Starting Telegram Auto Forward Bot Engine...")
    
    def memory_cleanup_loop():
        while True:
            time.sleep(1800)
            auto_cleanup()

    cleanup_thread = threading.Thread(target=memory_cleanup_loop, daemon=True)
    cleanup_thread.start()

    while True:
        try:
            logging.info("Bot infinity_polling initiated.")
            try:
                bot.remove_webhook()
            except Exception as e:
                logging.warning(f"Remove webhook warning: {e}")
            
            backoff = 1
            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )
        except Exception as e:
            logging.error(f"Polling Crash Detected: {e}")
            increment_stat("errors")
            logging.info(f"Reconnecting engine in {backoff} seconds...")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

if __name__ == "__main__":
    load_data()
    run_bot()
