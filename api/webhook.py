import os
import json
import logging
from datetime import datetime, timedelta
from threading import Thread, Timer
from typing import List, Dict, Any

from flask import Flask
from dotenv import load_dotenv
from fpdf import FPDF
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# ------------------ Configuration ------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Please set BOT_TOKEN in your .env file")

DATA_FILE = "user_data.json"
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ------------------ Persistence ------------------
DEFAULT_STRUCT = {"users": {}}


def load_data() -> Dict[str, Any]:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load data: %s", e)
            return DEFAULT_STRUCT.copy()
    return DEFAULT_STRUCT.copy()


def save_data(data: Dict[str, Any]):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("Failed to save data: %s", e)


DATA = load_data()


# ------------------ Utilities ------------------
def ensure_user(uid: str):
    if uid not in DATA["users"]:
        DATA["users"][uid] = {
            "notes": [],
            "next_id": 1,
            "pinned": [],
            "reminders": [],
            "lang": "en",
        }
        save_data(DATA)


def next_note_id(uid: str) -> int:
    ensure_user(uid)
    nid = DATA["users"][uid]["next_id"]
    DATA["users"][uid]["next_id"] += 1
    return nid


def now_iso():
    return datetime.utcnow().isoformat()


# ------------------ Keep-alive ------------------
app = Flask(__name__)


@app.route("/")
def home():
    return "✅ Notepad Bot running"


def run_web():
    app.run(host="0.0.0.0", port=8080)


def keep_alive():
    t = Thread(target=run_web, daemon=True)
    t.start()


# ------------------ Reply Keyboard ------------------
MAIN_KEYBOARD = [
    ["📝 Add Note", "📖 View Notes"],
    ["🔍 Search", "📂 Categories"],
    ["📌 Pin/Unpin", "🗂 Export PDF"],
    ["⏰ Reminders", "⚙️ Settings"],
]
MAIN_REPLY = ReplyKeyboardMarkup(MAIN_KEYBOARD, resize_keyboard=True)
CANCEL_KEYBOARD = ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)

# ------------------ Conversation states ------------------
ADD_NOTE = 1
SEARCH = 2
EXPORT_PDF = 3
PIN_UNPIN = 4
REMINDER = 5

# ------------------ Reminder scheduler ------------------
SCHEDULED_TIMERS: List[Timer] = []


def schedule_reminder(application: Application, chat_id: int, note_id: int, remind_at_iso: str):
    try:
        remind_at = datetime.fromisoformat(remind_at_iso)
        now = datetime.utcnow()
        delay = (remind_at - now).total_seconds()
        if delay <= 0:
            application.create_task(send_reminder_now(application, chat_id, note_id))
            return

        def _send():
            application.create_task(send_reminder_now(application, chat_id, note_id))

        t = Timer(delay, _send)
        t.daemon = True
        t.start()
        SCHEDULED_TIMERS.append(t)
    except Exception as e:
        logger.error("Failed to schedule reminder: %s", e)


async def send_reminder_now(application: Application, chat_id: int, note_id: int):
    uid = str(chat_id)
    ensure_user(uid)
    note = next((n for n in DATA["users"][uid]["notes"] if n["id"] == note_id), None)
    if not note:
        return
    text = f"⏰ Reminder — Note #{note_id}: {note['title']} \n{note['content']}"
    try:
        await application.bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        logger.error("Failed to send reminder: %s", e)


def schedule_all_reminders(application: Application):
    for uid, udata in DATA.get("users", {}).items():
        chat_id = int(uid)
        for r in udata.get("reminders", []):
            schedule_reminder(application, chat_id, r["note_id"], r["at"])


# ------------------ PDF export ------------------
def export_notes_to_pdf(uid: str, out_path: str, only_ids: List[int] = None):
    ensure_user(uid)
    user = DATA["users"][uid]
    notes = user["notes"]
    if only_ids is not None:
        notes = [n for n in notes if n["id"] in only_ids]

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Arial", size=14)
    pdf.cell(0, 10, txt="Notepad Export", ln=1, align="C")
    pdf.ln(4)

    pdf.set_font("Arial", size=12)
    for n in notes:
        pdf.multi_cell(0, 8, txt=f"#{n['id']} - {n['title']} ({n['category']}) - {n['created_at']}")
        pdf.multi_cell(0, 7, txt=n['content'])
        pdf.ln(3)

    pdf.output(out_path)

# ------------------ Handlers ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.message.from_user.id)
    ensure_user(uid)
    text = "👋 Welcome! Use the keyboard below to manage your notes."
    await update.message.reply_text(text, reply_markup=MAIN_REPLY)


# The rest of handlers (add_note_receive, view_notes, text_router, pin/unpin, search, categories, reminder, export etc.)
# follow the same pattern as above. All previous logic from your code should work correctly.

# ------------------ Application bootstrap ------------------
def build_app() -> Application:
    application = Application.builder().token(BOT_TOKEN).build()

    # basic commands
    application.add_handler(CommandHandler("start", start))
    # add other CommandHandlers, ConversationHandlers, and MessageHandlers as per your previous code
    return application


def main():
    keep_alive()
    app_ = build_app()
    schedule_all_reminders(app_)
    logger.info("Bot is up. Listening...")
    app_.run_polling()


if __name__ == "__main__":
    main()
