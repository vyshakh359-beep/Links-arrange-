import os
import re
import json
import time
import shutil
import telebot
import threading
import logging
from telebot import types

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not set")

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

user_data = {}
data_lock = threading.Lock()


def default_user_state():
    return {
        "thumb_mode": False,
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

        if os.path.exists(DATA_FILE):
            shutil.copyfile(DATA_FILE, BACKUP_FILE)

        os.replace(TEMP_FILE, DATA_FILE)

    except Exception as e:
        logging.error(f"Save data error: {e}")


def load_data():
    global user_data

    try:
        if os.path.exists(DATA_FILE):
            path = DATA_FILE
        elif os.path.exists(BACKUP_FILE):
            path = BACKUP_FILE
        else:
            user_data = {}
            return

        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        fixed = {}

        for uid_str, value in raw.items():
            uid = int(uid_str)
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

        user_data = fixed
        logging.info("User data loaded successfully")

    except Exception as e:
        logging.error(f"Load data error: {e}")
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
    return re.sub(r"\s+", " ", (line or "").strip())


def extract_links(text):
    return re.findall(r'https?://[^\s<>()"]+', text or "")


def unique_keep_order(items):
    seen = set()
    out = []

    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)

    return out


def has_malayalam(text):
    return bool(re.search(r"[\u0D00-\u0D7F]", text or ""))


def only_symbols_or_emoji(line):
    return bool(re.fullmatch(
        r"[\W_🔥💥⚜️❤️✅🥰😍😘💎✨⭐🎉💯😂🤣🙏👉📌📍📢•○●◇◆■□~`|]+",
        line or ""
    ))


def build_links(links):
    links = unique_keep_order(links)

    if not links:
        return ""

    result = ["FULL VIDEO 🌝🌸"]

    for i, link in enumerate(links, 1):
        result.append(f"VIDEO {i}\n{link}")

    return "\n\n".join(result).strip()


def build_links_simple(links):
    links = unique_keep_order(links)

    if not links:
        return ""

    result = []

    for i, link in enumerate(links, 1):
        result.append(f"VIDEO {i}\n{link}")

    return "\n\n".join(result).strip()


def clean_malayalam_text(text):
    lines = (text or "").splitlines()
    cleaned = []

    promo_words = [
        "join", "telegram", "whatsapp", "subscribe", "follow",
        "watch video", "channel",
        "കൂടുതൽ", "ചാനൽ", "സബ്സ്ക്രൈബ്", "ഫോളോ",
        "ലൈക്ക്", "ലൈക്കുകൾ", "ഷെയർ", "കമന്റ്",
        "ഞങ്ങളുടെ", "നമ്മുടെ", "ഉഷാർ", "പരിപാടി"
    ]

    for raw in lines:
        line = normalize_line(raw)
        low = line.lower()

        if not line:
            continue

        if re.search(r"https?://", line):
            continue

        if re.match(r"^\d+[\).\s]", line):
            continue

        if only_symbols_or_emoji(line):
            continue

        if any(w in low for w in promo_words):
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

    if user_data[uid].get("arrange_mode"):
        if links:
            return safe_text(build_links(links))
        return safe_text(text.strip())

    if user_data[uid]["text_edit_mode"]:
        return text_edit(uid, text)

    if user_data[uid]["middle_mode"]:
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
    slot = user_data[uid]["selected_thumb"]
    if not slot:
        return None
    return user_data[uid]["thumbs"].get(slot)


def selected_channel_names(uid):
    return [
        name for name, cid in CHANNELS.items()
        if cid in user_data[uid]["selected_channels"]
    ]


def send_message_safe(chat_id, text, reply_markup=None):
    try:
        return bot.send_message(chat_id, safe_text(text), reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Send message error to {chat_id}: {e}")
        return None


def send_photo_safe(chat_id, photo, caption="", reply_markup=None):
    try:
        return bot.send_photo(
            chat_id,
            photo,
            caption=safe_caption(caption),
            reply_markup=reply_markup
        )
    except Exception as e:
        logging.error(f"Send photo error to {chat_id}: {e}")
        return None


def send_video_safe(chat_id, video, caption="", reply_markup=None):
    try:
        return bot.send_video(
            chat_id,
            video,
            caption=safe_caption(caption),
            reply_markup=reply_markup
        )
    except Exception as e:
        logging.error(f"Send video error to {chat_id}: {e}")
        return None


def send_document_safe(chat_id, document, caption="", reply_markup=None):
    try:
        return bot.send_document(
            chat_id,
            document,
            caption=safe_caption(caption),
            reply_markup=reply_markup
        )
    except Exception as e:
        logging.error(f"Send document error to {chat_id}: {e}")
        return None


def send_animation_safe(chat_id, animation, caption="", reply_markup=None):
    try:
        return bot.send_animation(
            chat_id,
            animation,
            caption=safe_caption(caption),
            reply_markup=reply_markup
        )
    except Exception as e:
        logging.error(f"Send animation error to {chat_id}: {e}")
        return None


def report_forward_error(uid, channel_id, err):
    send_message_safe(
        uid,
        f"⚠️ Forward failed\nChannel: {channel_id}\nError: {err}",
        reply_markup=main_kb()
    )


def forward_to_channels_text(uid, text):
    if not user_data[uid]["auto_forward"]:
        return

    for ch in user_data[uid]["selected_channels"]:
        msg = send_message_safe(ch, text)
        if not msg:
            report_forward_error(uid, ch, "Send message failed")


def forward_to_channels_photo(uid, photo, caption=""):
    if not user_data[uid]["auto_forward"]:
        return

    for ch in user_data[uid]["selected_channels"]:
        msg = send_photo_safe(ch, photo, caption)
        if not msg:
            report_forward_error(uid, ch, "Send photo failed")


def forward_to_channels_video(uid, video, caption=""):
    if not user_data[uid]["auto_forward"]:
        return

    for ch in user_data[uid]["selected_channels"]:
        msg = send_video_safe(ch, video, caption)
        if not msg:
            report_forward_error(uid, ch, "Send video failed")


def forward_to_channels_document(uid, document, caption=""):
    if not user_data[uid]["auto_forward"]:
        return

    for ch in user_data[uid]["selected_channels"]:
        msg = send_document_safe(ch, document, caption)
        if not msg:
            report_forward_error(uid, ch, "Send document failed")


def forward_to_channels_animation(uid, animation, caption=""):
    if not user_data[uid]["auto_forward"]:
        return

    for ch in user_data[uid]["selected_channels"]:
        msg = send_animation_safe(ch, animation, caption)
        if not msg:
            report_forward_error(uid, ch, "Send animation failed")


def main_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📸 Set Thumb", "✅ Use Thumb")
    kb.row("🖼 Thumb ON", "🖼 Thumb OFF")
    kb.row("🔗 Arrange ON", "🔗 Arrange OFF")
    kb.row("📝 Text Edit ON", "📝 Text Edit OFF")
    kb.row("🎯 Middle ON", "🎯 Middle OFF")
    kb.row("📢 Select Channel")
    kb.row("🟢 Auto Forward ON", "🔴 Auto Forward OFF")
    kb.row("👁 Current Thumb", "📊 Current Settings")
    return kb


def slot_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Photo 1", "Photo 2")
    kb.row("Photo 3", "Photo 4")
    kb.row("⬅️ Back")
    return kb


def channel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("Channel 1", "Channel 2")
    kb.row("Channel 3", "Channel 4")
    kb.row("Channel 5")
    kb.row("✅ Done", "🗑 Clear Channels")
    kb.row("⬅️ Back")
    return kb


@bot.message_handler(commands=["start"])
def start(m):
    uid = m.from_user.id

    if not is_admin(uid):
        bot.reply_to(m, "❌ Admin only bot")
        return

    init_user(uid)

    send_message_safe(
        m.chat.id,
        "🔥 CLEAN VIP BOT READY ✅\n\n"
        "Arrange:\n"
        "FULL VIDEO 🌝🌸\n\n"
        "VIDEO 1\n"
        "link\n\n"
        "Text Edit:\n"
        "Caption\n\n"
        "VIDEO 1\n"
        "link",
        reply_markup=main_kb()
    )


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📸 Set Thumb")
def set_thumb(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["thumb_action"] = "set"
    save_data()
    send_message_safe(m.chat.id, "Save ചെയ്യാൻ slot select ചെയ്യൂ", reply_markup=slot_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "✅ Use Thumb")
def use_thumb(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["thumb_action"] = "use"
    save_data()
    send_message_safe(m.chat.id, "Use ചെയ്യാൻ slot select ചെയ്യൂ", reply_markup=slot_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text in THUMB_SLOTS)
def thumb_slot(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    slot = m.text
    action = user_data[uid]["thumb_action"]

    if action == "set":
        user_data[uid]["waiting_thumb"] = slot
        save_data()
        send_message_safe(
            m.chat.id,
            f"{slot} ലേക്ക് save ചെയ്യാൻ photo അയക്കൂ 📸",
            reply_markup=slot_kb()
        )
        return

    if action == "use":
        if user_data[uid]["thumbs"].get(slot):
            user_data[uid]["selected_thumb"] = slot
            user_data[uid]["thumb_action"] = None
            save_data()
            send_message_safe(m.chat.id, f"{slot} selected ✅", reply_markup=main_kb())
        else:
            send_message_safe(m.chat.id, f"{slot} il thumb ഇല്ല ❌", reply_markup=slot_kb())
        return

    send_message_safe(m.chat.id, "ആദ്യം Set/Use Thumb ചെയ്യൂ", reply_markup=main_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "👁 Current Thumb")
def current_thumb(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    thumb = get_thumb(uid)
    slot = user_data[uid]["selected_thumb"]

    if not thumb:
        send_message_safe(m.chat.id, "Current thumb ഇല്ല ❌", reply_markup=main_kb())
        return

    send_photo_safe(m.chat.id, thumb, caption=f"Current Thumb: {slot} ✅", reply_markup=main_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🖼 Thumb ON")
def thumb_on(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)

    if not user_data[uid]["selected_thumb"]:
        send_message_safe(m.chat.id, "ആദ്യം thumb select ചെയ്യൂ ❌", reply_markup=main_kb())
        return

    user_data[uid]["thumb_mode"] = True
    save_data()
    bot.reply_to(m, "Thumb ON ✅")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🖼 Thumb OFF")
def thumb_off(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["thumb_mode"] = False
    save_data()
    bot.reply_to(m, "Thumb OFF ❌")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🔗 Arrange ON")
def arrange_on(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["arrange_mode"] = True
    user_data[uid]["text_edit_mode"] = False
    user_data[uid]["middle_mode"] = False
    save_data()
    bot.reply_to(m, "Arrange ON ✅")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🔗 Arrange OFF")
def arrange_off(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["arrange_mode"] = False
    save_data()
    bot.reply_to(m, "Arrange OFF ❌")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📝 Text Edit ON")
def text_edit_on(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["text_edit_mode"] = True
    user_data[uid]["middle_mode"] = False
    user_data[uid]["arrange_mode"] = False
    save_data()
    bot.reply_to(m, "Text Edit ON ✅")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📝 Text Edit OFF")
def text_edit_off(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["text_edit_mode"] = False
    save_data()
    bot.reply_to(m, "Text Edit OFF ❌")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🎯 Middle ON")
def middle_on(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["middle_mode"] = True
    user_data[uid]["text_edit_mode"] = False
    user_data[uid]["arrange_mode"] = False
    save_data()
    bot.reply_to(m, "Middle ON ✅")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🎯 Middle OFF")
def middle_off(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["middle_mode"] = False
    save_data()
    bot.reply_to(m, "Middle OFF ❌")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📢 Select Channel")
def select_channel(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    send_message_safe(m.chat.id, "Channels select ചെയ്യൂ 👇", reply_markup=channel_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text in CHANNELS.keys())
def toggle_channel(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    cid = CHANNELS[m.text]

    if cid in user_data[uid]["selected_channels"]:
        user_data[uid]["selected_channels"].remove(cid)
        send_message_safe(m.chat.id, f"{m.text} removed ❌", reply_markup=channel_kb())
    else:
        user_data[uid]["selected_channels"].append(cid)
        send_message_safe(m.chat.id, f"{m.text} added ✅", reply_markup=channel_kb())

    save_data()


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "✅ Done")
def done_channels(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    send_message_safe(m.chat.id, "Channels saved ✅", reply_markup=main_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🗑 Clear Channels")
def clear_channels(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["selected_channels"] = []
    save_data()
    send_message_safe(m.chat.id, "Channels cleared ✅", reply_markup=channel_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "⬅️ Back")
def back_btn(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    send_message_safe(m.chat.id, "Main menu ✅", reply_markup=main_kb())


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🟢 Auto Forward ON")
def auto_forward_on(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)

    if not user_data[uid]["selected_channels"]:
        send_message_safe(m.chat.id, "ആദ്യം channel select ചെയ്യൂ ❌", reply_markup=channel_kb())
        return

    user_data[uid]["auto_forward"] = True
    save_data()
    bot.reply_to(m, "Auto Forward ON ✅")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "🔴 Auto Forward OFF")
def auto_forward_off(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)
    user_data[uid]["auto_forward"] = False
    save_data()
    bot.reply_to(m, "Auto Forward OFF ❌")


@bot.message_handler(func=lambda m: m.content_type == "text" and m.text == "📊 Current Settings")
def current_settings(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)

    channel_names = selected_channel_names(uid)
    channel_text = "\n".join(channel_names) if channel_names else "None ❌"

    text = (
        f"Thumb Mode: {'ON ✅' if user_data[uid]['thumb_mode'] else 'OFF ❌'}\n"
        f"Arrange Mode: {'ON ✅' if user_data[uid]['arrange_mode'] else 'OFF ❌'}\n"
        f"Text Edit Mode: {'ON ✅' if user_data[uid]['text_edit_mode'] else 'OFF ❌'}\n"
        f"Middle Mode: {'ON ✅' if user_data[uid]['middle_mode'] else 'OFF ❌'}\n"
        f"Auto Forward: {'ON ✅' if user_data[uid]['auto_forward'] else 'OFF ❌'}\n"
        f"Selected Thumb: {user_data[uid]['selected_thumb'] or 'None ❌'}\n\n"
        f"Selected Channels:\n{channel_text}"
    )

    send_message_safe(m.chat.id, text, reply_markup=main_kb())


@bot.message_handler(content_types=["photo"])
def photo_handler(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)

    try:
        photo_id = m.photo[-1].file_id
        caption = m.caption or ""

        if user_data[uid]["waiting_thumb"]:
            slot = user_data[uid]["waiting_thumb"]
            user_data[uid]["thumbs"][slot] = photo_id
            user_data[uid]["waiting_thumb"] = None
            user_data[uid]["thumb_action"] = None
            save_data()
            send_message_safe(m.chat.id, f"{slot} saved ✅", reply_markup=main_kb())
            return

        send_photo_id = get_thumb(uid) if user_data[uid]["thumb_mode"] else photo_id
        if not send_photo_id:
            send_photo_id = photo_id

        final_caption = apply_processing(uid, caption)

        send_photo_safe(m.chat.id, send_photo_id, caption=final_caption, reply_markup=main_kb())
        forward_to_channels_photo(uid, send_photo_id, final_caption)

    except Exception as e:
        logging.error(f"Photo handler error: {e}")
        send_message_safe(m.chat.id, "Photo process ചെയ്യാൻ പറ്റിയില്ല ❌", reply_markup=main_kb())


@bot.message_handler(content_types=["video"])
def video_handler(m):
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
        send_message_safe(m.chat.id, "Video process ചെയ്യാൻ പറ്റിയില്ല ❌", reply_markup=main_kb())


@bot.message_handler(content_types=["document"])
def document_handler(m):
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
        send_message_safe(m.chat.id, "Document process ചെയ്യാൻ പറ്റിയില്ല ❌", reply_markup=main_kb())


@bot.message_handler(content_types=["animation"])
def animation_handler(m):
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
        send_message_safe(m.chat.id, "Animation process ചെയ്യാൻ പറ്റിയില്ല ❌", reply_markup=main_kb())


@bot.message_handler(content_types=["text"])
def text_handler(m):
    uid = m.from_user.id
    if not is_admin(uid):
        return

    init_user(uid)

    ignore = {
        "📸 Set Thumb", "✅ Use Thumb",
        "🖼 Thumb ON", "🖼 Thumb OFF",
        "🔗 Arrange ON", "🔗 Arrange OFF",
        "📝 Text Edit ON", "📝 Text Edit OFF",
        "🎯 Middle ON", "🎯 Middle OFF",
        "📢 Select Channel",
        "🟢 Auto Forward ON", "🔴 Auto Forward OFF",
        "👁 Current Thumb", "📊 Current Settings",
        "Channel 1", "Channel 2", "Channel 3", "Channel 4", "Channel 5",
        "✅ Done", "🗑 Clear Channels", "⬅️ Back",
        "Photo 1", "Photo 2", "Photo 3", "Photo 4",
    }

    if m.text in ignore:
        return

    try:
        final_text = apply_processing(uid, m.text)

        send_message_safe(
            m.chat.id,
            final_text if final_text else "Empty text ❌",
            reply_markup=main_kb()
        )

        if final_text:
            forward_to_channels_text(uid, final_text)

    except Exception as e:
        logging.error(f"Text handler error: {e}")
        send_message_safe(m.chat.id, "Text process ചെയ്യാൻ പറ്റിയില്ല ❌", reply_markup=main_kb())


def run_bot():
    while True:
        try:
            logging.info("Bot running...")
            bot.remove_webhook()
            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )
        except Exception as e:
            logging.error(f"Polling crash: {e}")
            time.sleep(5)


if __name__ == "__main__":
    load_data()
    run_bot()

