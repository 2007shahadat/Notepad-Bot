import os
import logging
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
# dotenv is usually handled by Vercel's environment variables directly,
# but can be useful for local testing.
# from dotenv import load_dotenv

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment variable (Vercel sets this)
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set.")

# --- DANGER: THIS IS STILL EPHEMERAL FOR VERVEL! ---
# FOR REAL PERSISTENCE, YOU NEED AN EXTERNAL DATABASE.
# The `user_data` will reset on every new function invocation/cold start.
# This structure is kept for demonstration based on your original request,
# but it WILL NOT WORK for persistent notes on Vercel.
user_data = {
    'notes': {},
    'settings': {}
}

# --- Mock Persistence for Vercel Demo (NOT RECOMMENDED) ---
# In a real Vercel deployment with persistence, you would connect to an external DB here.
# For example:
# from your_database_module import connect_to_db, save_note_to_db, get_notes_from_db
#
# Since this is a Vercel serverless function, we can't 'load_user_data' on startup
# from a file. If you insist on file-based, you'd need a separate storage service
# (e.g., S3, Google Cloud Storage) and load/save from there on each invocation.
# This makes it very complex and slow.

# For demonstration purposes, we'll initialize with empty data
# and warn that notes WILL NOT PERSIST.
logger.warning("Vercel deployment detected. Notes will NOT persist without an external database!")
# --------------------------------------------------------

# --- Helper functions for note management (modified for stateless context) ---
# These functions will operate on the `user_data` dictionary,
# but remember this dictionary is recreated on each Vercel invocation.

NOTES_PER_PAGE = 5
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "YOUR_VERCEL_APP_URL/api/webhook") # Set this in Vercel env vars

def get_user_notes(user_id):
    return sorted(
        user_data['notes'].get(str(user_id), []),
        key=lambda x: datetime.fromisoformat(x['created_at']),
        reverse=True
    )

def add_user_note(user_id, title, content, category='General'):
    user_id_str = str(user_id)
    if user_id_str not in user_data['notes']:
        user_data['notes'][user_id_str] = []
    if user_id_str not in user_data['settings']:
        user_data['settings'][user_id_str] = {'next_note_id': 1}

    user_settings = user_data['settings'][user_id_str]
    note_id = user_settings['next_note_id']
    user_settings['next_note_id'] += 1

    note = {
        'title': title,
        'content': content,
        'category': category,
        'created_at': datetime.now().isoformat(),
        'note_id': note_id
    }
    user_data['notes'][user_id_str].append(note)
    # No save_user_data() call here, as file persistence is not possible.
    return note['note_id']

def delete_user_note(user_id, note_id):
    user_id_str = str(user_id)
    if user_id_str in user_data['notes']:
        initial_len = len(user_data['notes'][user_id_str])
        user_data['notes'][user_id_str] = [note for note in user_data['notes'][user_id_str] if note['note_id'] != note_id]
        if len(user_data['notes'][user_id_str]) < initial_len:
            # No save_user_data() call here
            return True
    return False

def get_user_note(user_id, note_id):
    user_id_str = str(user_id)
    if user_id_str in user_data['notes']:
        for note in user_data['notes'][user_id_str]:
            if note['note_id'] == note_id:
                return note
    return None

def update_user_note_category(user_id, note_id, new_category):
    user_id_str = str(user_id)
    if user_id_str in user_data['notes']:
        for note in user_data['notes'][user_id_str]:
            if note['note_id'] == note_id:
                note['category'] = new_category
                # No save_user_data() call here
                return True
    return False

def search_user_notes(user_id, query):
    user_id_str = str(user_id)
    if user_id_str not in user_data['notes']:
        return []
    query = query.lower()
    results = []
    for note in user_data['notes'][user_id_str]:
        if (query in note['title'].lower() or
            query in note['content'].lower() or
            query in note['category'].lower()):
            results.append(note)
    return sorted(results, key=lambda x: datetime.fromisoformat(x['created_at']), reverse=True)

def get_user_categories(user_id):
    user_id_str = str(user_id)
    if user_id_str not in user_data['notes']:
        return []
    categories = set()
    for note in user_data['notes'][user_id_str]:
        categories.add(note['category'])
    return sorted(list(categories))

# --- Bot Handlers (mostly same as before, but adapted for webhook context) ---

BACK_TO_MAIN_MENU_BUTTON = InlineKeyboardButton("🔙 Main Menu", callback_data='back_to_main')

def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ New Note", callback_data='new_note')],
        [InlineKeyboardButton("📋 My Notes", callback_data='view_notes_page_0')],
        [InlineKeyboardButton("🔍 Search Notes", callback_data='search_notes')],
        [InlineKeyboardButton("🗂️ Categories", callback_data='view_categories')],
        [InlineKeyboardButton("📊 Statistics", callback_data='stats')],
        [InlineKeyboardButton("❓ Help Guide", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)

# Initialize the Application outside the handler for efficiency, but not ideal for state
# A cleaner way would be to create and pass the `Application` and `CallbackContext` around
# For Vercel, `user_data` needs to be part of `context.user_data` if state should persist
# within a request. For cross-request state, an external DB is mandatory.

# We need to create the Application instance once per handler invocation.
# This makes user_data ephemeral.
# To handle this properly, `python-telegram-bot` usually wants to manage the
# user_data across requests, which is hard in a serverless model without a DB.
# For this Vercel example, `context.user_data` will reset on each new incoming webhook.
# If you want state across different webhook calls, you MUST use an external database.

# The Application itself can be instantiated once, but its `run_webhook` loop is what manages state.
# We will create Application and use `process_update` for each incoming webhook.

# Global application instance (to be used within the handler)
# It's usually better to create this inside the handler if state per invocation is desired,
# but for some `python-telegram-bot` internal workings, it's often global.
# We'll use a `LazyApplication` or instantiate on demand.
# For simplicity, let's make it a factory function to be called on each request.
application_instance = None
async def get_application():
    global application_instance
    if application_instance is None:
        application_instance = Application.builder().token(BOT_TOKEN).build()
        # Add handlers here so they are registered once
        application_instance.add_handler(CommandHandler("start", start))
        application_instance.add_handler(CommandHandler("new", new_note))
        application_instance.add_handler(CommandHandler("mynotes", my_notes))
        application_instance.add_handler(CommandHandler("search", search_command))
        application_instance.add_handler(CommandHandler("categories", categories_command))
        application_instance.add_handler(CommandHandler("help", help_command))
        application_instance.add_handler(CommandHandler("stats", stats_command))
        application_instance.add_handler(CommandHandler("clear", clear_command))
        application_instance.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application_instance.add_handler(CallbackQueryHandler(button_handler))
        logger.info("Telegram Application handlers initialized.")
    return application_instance

# --- Vercel's entry point for serverless functions ---
from http.server import BaseHTTPRequestHandler
import asyncio # Vercel's Python runtime uses asyncio

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    welcome_text = f"""
👋 Hello {user.first_name}! Welcome to *Notepad++ Bot*! 📝

✨ *Features:*
• 📝 Create and save notes with titles
• 📋 View and organize notes by categories (with *pagination*!)
• 🔍 Search through your notes
• 🗂️ Categorize your notes (and *edit them*!)
• ⚡ Quick inline navigation
• 🗑️ Delete notes
• 📈 User Statistics
• 🚀 Markdown support for formatted text

*Quick Commands:*
`/new` - Create a new note
`/mynotes` - View your notes
`/search` - Search notes
`/categories` - Manage categories
`/help` - Show help guide
`/stats` - Show statistics
`/clear` - Clear all notes

Simply send me any text to save it as a quick note! 🚀
"""
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard())

async def new_note_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['awaiting_note'] = True
    context.user_data.pop('awaiting_search', None)
    context.user_data.pop('awaiting_category_for_note_id', None)

    target_object = update.message if update.message else update.callback_query
    reply_func = target_object.reply_text if update.message else target_object.edit_message_text

    await reply_func(
        "📝 *Let's create a new note!*\n\n"
        "Please send me the text for your note. You can also include a title and category by formatting it like:\n"
        "`Title: Your Title Here\nCategory: Your Category Name\nContent: Your content here`\n\n"
        "Or just send the content, and I'll auto-generate a title and assign it to the 'General' category!",
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    if 'awaiting_category_for_note_id' in context.user_data:
        note_id = context.user_data.pop('awaiting_category_for_note_id')
        new_category = text.strip()

        note = get_user_note(user_id, note_id)
        if note and update_user_note_category(user_id, note_id, new_category):
            keyboard = [
                [InlineKeyboardButton("📄 View Note", callback_data=f'view_note_{note_id}')],
                [InlineKeyboardButton("📋 My Notes", callback_data='view_notes_page_0')],
                [InlineKeyboardButton("➕ New Note", callback_data='new_note')],
                [BACK_TO_MAIN_MENU_BUTTON]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"✅ *Category for Note #{note_id} updated to '{new_category}' successfully!*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text("❌ Failed to update category. Note might not exist or an error occurred.")
        return

    if context.user_data.get('awaiting_note'):
        context.user_data['awaiting_note'] = False

        title = None
        category = 'General'
        content = text

        lines = text.split('\n')
        parsed_content_lines = []
        is_content_explicitly_set = False

        for line in lines:
            line_lower = line.lower()
            if line_lower.startswith('title:'):
                title = line.split(':', 1)[1].strip()
            elif line_lower.startswith('category:'):
                category = line.split(':', 1)[1].strip()
            elif line_lower.startswith('content:'):
                content = line.split(':', 1)[1].strip()
                is_content_explicitly_set = True
                parsed_content_lines = []
            else:
                if not is_content_explicitly_set and not (line_lower.startswith('title:') or line_lower.startswith('category:')):
                    parsed_content_lines.append(line)

        if not is_content_explicitly_set:
            content = "\n".join(parsed_content_lines).strip()
            if not content:
                content = text

        if not title:
            title = content[:50] + '...' if len(content) > 50 else content
            if not title:
                title = "Untitled Note"

        note_id = add_user_note(user_id, title, content, category)

        keyboard = [
            [InlineKeyboardButton("📋 View All Notes", callback_data='view_notes_page_0')],
            [InlineKeyboardButton("➕ Another Note", callback_data='new_note')],
            [InlineKeyboardButton("📄 View This Note", callback_data=f'view_note_{note_id}')],
            [BACK_TO_MAIN_MENU_BUTTON]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"✅ *Note saved successfully!* (`#{note_id}`)\n\n"
            f"📌 *Title:* {title}\n"
            f"🗂️ *Category:* {category}\n"
            f"📄 *Content:* {content[:150]}{'...' if len(content) > 150 else ''}\n\n"
            f"You can view, edit category, or delete this note using buttons!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )
        return

    elif context.user_data.get('awaiting_search'):
        context.user_data['awaiting_search'] = False
        query = text
        results = search_user_notes(user_id, query)

        context.user_data['last_search_results'] = results
        context.user_data['last_search_query'] = query

        await send_search_results_page_handler(update.message, context, query, 0)
        return

    else:
        title = text[:50] + '...' if len(text) > 50 else text
        if not title:
            title = "Untitled Quick Note"

        note_id = add_user_note(user_id, title, text, category='Quick Notes')

        keyboard = [
            [InlineKeyboardButton("📋 View All Notes", callback_data='view_notes_page_0')],
            [InlineKeyboardButton("➕ Another Note", callback_data='new_note')],
            [InlineKeyboardButton("📄 View This Note", callback_data=f'view_note_{note_id}')],
            [BACK_TO_MAIN_MENU_BUTTON]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"✍️ *Quick Note saved!* (`#{note_id}`)\n\n"
            f"📌 *Title:* {title}\n"
            f"🗂️ *Category:* Quick Notes\n"
            f"📄 *Content:* {text[:100]}{'...' if len(text) > 100 else ''}\n\n"
            f"You can use the `/new` command or the '➕ New Note' button for more detailed options!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )


async def send_notes_page_handler(target_message, context, page: int, category: str = None):
    user_id = target_message.chat.id
    all_notes = get_user_notes(user_id)

    if category and category != 'All':
        all_notes = [note for note in all_notes if note['category'] == category]
        
    if not all_notes:
        text = f"📭 You don't have any notes yet {'in the category *'+category+'*' if category else ''}. Use /new to create one!"
        reply_func = target_message.reply_text if target_message.from_user else target_message.edit_message_text
        await reply_func(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard())
        return

    total_pages = (len(all_notes) + NOTES_PER_PAGE - 1) // NOTES_PER_PAGE
    current_page = max(0, min(page, total_pages - 1))
    start_index = current_page * NOTES_PER_PAGE
    end_index = start_index + NOTES_PER_PAGE
    notes_on_page = all_notes[start_index:end_index]

    message_lines = [f"📋 *Your Notes ({'Category: *' + category + '*' if category else 'All Notes'} - Page {current_page + 1}/{total_pages}):*\n"]
    keyboard = []

    for note in notes_on_page:
        message_lines.append(f"• #{note['note_id']}: *{note['title']}* ({note['category']})")
        keyboard.append([
            InlineKeyboardButton(f"📄 View #{note['note_id']}", callback_data=f'view_note_{note["note_id"]}'),
            InlineKeyboardButton(f"❌ Delete #{note['note_id']}", callback_data=f'delete_note_{note["note_id"]}')
        ])

    pagination_buttons = []
    if current_page > 0:
        pagination_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'view_notes_page_{current_page-1}{f"_cat_{category}" if category else ""}'))
    if current_page < total_pages - 1:
        pagination_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'view_notes_page_{current_page+1}{f"_cat_{category}" if category else ""}'))
    if pagination_buttons:
        keyboard.append(pagination_buttons)

    keyboard.extend([
        [InlineKeyboardButton("🔍 Search Notes", callback_data='search_notes')],
        [InlineKeyboardButton("🗂️ View Categories", callback_data='view_categories')],
        [InlineKeyboardButton("➕ New Note", callback_data='new_note')],
        [BACK_TO_MAIN_MENU_BUTTON]
    ])

    reply_markup = InlineKeyboardMarkup(keyboard)
    text_to_send = "\n".join(message_lines)

    if target_message.from_user:
        await target_message.reply_text(text_to_send, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await target_message.edit_message_text(text_to_send, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def my_notes_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_notes_page_handler(update.message, context, 0)

async def send_search_results_page_handler(target_message, context, query: str, page: int):
    user_id = target_message.chat.id
    results = context.user_data.get('last_search_results', [])
    
    if not results:
        text = "🔍 No notes found matching your search."
        reply_func = target_message.reply_text if target_message.from_user else target_message.edit_message_text
        await reply_func(text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard())
        return

    total_pages = (len(results) + NOTES_PER_PAGE - 1) // NOTES_PER_PAGE
    current_page = max(0, min(page, total_pages - 1))
    start_index = current_page * NOTES_PER_PAGE
    end_index = start_index + NOTES_PER_PAGE
    notes_on_page = results[start_index:end_index]

    message_lines = [f"🔍 *Search Results for '{query}' (Page {current_page + 1}/{total_pages}):*\n"]
    keyboard = []

    for note in notes_on_page:
        message_lines.append(f"• #{note['note_id']}: *{note['title']}* ({note['category']})")
        keyboard.append([
            InlineKeyboardButton(f"📄 View #{note['note_id']}", callback_data=f'view_note_{note["note_id"]}'),
            InlineKeyboardButton(f"❌ Delete #{note['note_id']}", callback_data=f'delete_note_{note["note_id"]}')
        ])

    pagination_buttons = []
    if current_page > 0:
        pagination_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f'search_results_page_{current_page-1}'))
    if current_page < total_pages - 1:
        pagination_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f'search_results_page_{current_page+1}'))
    if pagination_buttons:
        keyboard.append(pagination_buttons)

    keyboard.extend([
        [InlineKeyboardButton("📋 Back to Notes", callback_data='view_notes_page_0')],
        [InlineKeyboardButton("➕ New Note", callback_data='new_note')],
        [BACK_TO_MAIN_MENU_BUTTON]
    ])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text_to_send = "\n".join(message_lines)
    reply_func = target_message.reply_text if target_message.from_user else target_message.edit_message_text
    await reply_func(text_to_send, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)


async def search_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['awaiting_search'] = True
    context.user_data.pop('awaiting_note', None)
    context.user_data.pop('awaiting_category_for_note_id', None)

    await update.message.reply_text("🔍 What would you like to search for in your notes? (Enter keywords, title, content, or category)")

async def categories_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    categories = get_user_categories(user_id)

    target_object = update.message if update.message else update.callback_query
    reply_func = target_object.reply_text if update.message else target_object.edit_message_text

    if not categories:
        text = "🗂️ You don't have any categories yet. Notes will be saved under 'General' or 'Quick Notes' by default."
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ New Note", callback_data='new_note')],
            [BACK_TO_MAIN_MENU_BUTTON]
        ])
        await reply_func(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
        return

    message = "🗂️ *Your Categories:*\n\n"
    keyboard = []
    for category in categories:
        notes_in_category = [note for note in get_user_notes(user_id) if note['category'] == category]
        message += f"• *{category}* ({len(notes_in_category)} notes)\n"
        keyboard.append([InlineKeyboardButton(f"View '{category}' Notes", callback_data=f'view_notes_page_0_cat_{category}')])

    keyboard.append([InlineKeyboardButton("📋 View All Notes", callback_data='view_notes_page_0')])
    keyboard.append([InlineKeyboardButton("➕ New Note", callback_data='new_note')])
    keyboard.append([BACK_TO_MAIN_MENU_BUTTON])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await reply_func(message, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def button_handler_func(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    context.user_data.pop('awaiting_note', None)
    context.user_data.pop('awaiting_search', None)
    context.user_data.pop('awaiting_category_for_note_id', None)

    if data == 'new_note':
        await new_note_handler(update, context)

    elif data.startswith('view_notes_page_'):
        parts = data.split('_')
        try:
            page = int(parts[3])
            category = None
            if len(parts) > 4 and parts[4] == 'cat':
                category = parts[5]
            
            await send_notes_page_handler(query.message, context, page, category)
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Invalid page or category information.", reply_markup=get_main_keyboard())

    elif data == 'search_notes':
        context.user_data['awaiting_search'] = True
        await query.edit_message_text("🔍 What would you like to search for in your notes?")
    
    elif data.startswith('search_results_page_'):
        try:
            page = int(data.split('_')[-1])
            query_text = context.user_data.get('last_search_query', '')
            if not query_text:
                await query.edit_message_text("❌ No active search query found. Please search again.", reply_markup=get_main_keyboard())
                return
            await send_search_results_page_handler(query.message, context, query_text, page)
        except (ValueError, IndexError):
            await query.edit_message_text("❌ Invalid page information for search results.", reply_markup=get_main_keyboard())

    elif data == 'view_categories':
        await categories_command_handler(update, context)

    elif data.startswith('view_note_'):
        try:
            note_id = int(data.split('_')[-1])
        except ValueError:
            await query.edit_message_text("❌ Invalid note ID format.", reply_markup=get_main_keyboard())
            return

        note = get_user_note(user_id, note_id)

        if note:
            created_date = datetime.fromisoformat(note['created_at']).strftime('%Y-%m-%d %H:%M')
            
            keyboard = [
                [InlineKeyboardButton("📋 Back to Notes", callback_data='view_notes_page_0')],
                [InlineKeyboardButton("✏️ Edit Category", callback_data=f'edit_category_{note_id}')],
                [InlineKeyboardButton("❌ Delete This Note", callback_data=f'delete_note_{note_id}')],
                [InlineKeyboardButton("➕ New Note", callback_data='new_note')],
                [BACK_TO_MAIN_MENU_BUTTON]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"📄 *Note #{note_id}*\n\n"
                f"📌 *Title:* {note['title']}\n"
                f"🗂️ *Category:* {note['category']}\n"
                f"🕒 *Created:* {created_date}\n\n"
                f"*Content:*\n{note['content']}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text("❌ Note not found or already deleted.", reply_markup=get_main_keyboard())

    elif data.startswith('edit_category_'):
        try:
            note_id = int(data.split('_')[-1])
        except ValueError:
            await query.edit_message_text("❌ Invalid note ID format.", reply_markup=get_main_keyboard())
            return
        
        note = get_user_note(user_id, note_id)
        if note:
            context.user_data['awaiting_category_for_note_id'] = note_id
            await query.edit_message_text(
                f"✏️ *Editing category for Note #{note_id}* (`{note['title'][:30]}...`)\n\n"
                "Please send me the *new category name* for this note.\n"
                f"Current category: `{note['category']}`",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await query.edit_message_text("❌ Note not found or already deleted.", reply_markup=get_main_keyboard())

    elif data.startswith('delete_note_'):
        try:
            note_id = int(data.split('_')[-1])
        except ValueError:
            await query.edit_message_text("❌ Invalid note ID format.", reply_markup=get_main_keyboard())
            return

        success = delete_user_note(user_id, note_id)

        if success:
            keyboard = [
                [InlineKeyboardButton("📋 View Notes", callback_data='view_notes_page_0')],
                [InlineKeyboardButton("➕ New Note", callback_data='new_note')],
                [BACK_TO_MAIN_MENU_BUTTON]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"✅ *Note #{note_id} deleted successfully!*",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )
        else:
            await query.edit_message_text("❌ Note not found or already deleted.", reply_markup=get_main_keyboard())

    elif data == 'stats':
        notes = get_user_notes(user_id)
        categories = get_user_categories(user_id)

        total_notes = len(notes)
        total_categories = len(categories)

        stats_text = f"""
📊 *Your Statistics*

📝 *Total Notes:* {total_notes}
🗂️ *Categories:* {total_categories}
📅 *Last Updated:* {datetime.now().strftime('%Y-%m-%d %H:%M')}

Keep adding notes to build your knowledge base! 🚀
"""
        keyboard = [[BACK_TO_MAIN_MENU_BUTTON]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

    elif data == 'help':
        help_text = """
🤖 *Notepad++ Bot Help Guide*

*Commands:*
`/start` - Start the bot and see the main menu
... (rest of help text) ...
📝 Happy note-taking!
"""
        keyboard = [[BACK_TO_MAIN_MENU_BUTTON]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(help_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

    elif data == 'back_to_main':
        user = query.from_user
        welcome_text = f"👋 *Welcome back {user.first_name}!* What would you like to do?"
        await query.edit_message_text(welcome_text, parse_mode=ParseMode.MARKDOWN, reply_markup=get_main_keyboard())


async def help_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🤖 *Notepad++ Bot Help*
... (rest of help text) ...
"""
    keyboard = [[BACK_TO_MAIN_MENU_BUTTON]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def stats_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    notes = get_user_notes(user_id)
    categories = get_user_categories(user_id)

    total_notes = len(notes)
    total_categories = len(categories)

    stats_text = f"""
📊 *Your Statistics*
... (rest of stats text) ...
"""
    keyboard = [[BACK_TO_MAIN_MENU_BUTTON]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(stats_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)

async def clear_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_id_str = str(user_id)

    if user_id_str in user_data['notes'] and user_data['notes'][user_id_str]:
        user_data['notes'][user_id_str] = []
        if user_id_str in user_data['settings']:
            user_data['settings'][user_id_str]['next_note_id'] = 1
        # No save_user_data() here
        keyboard = [[BACK_TO_MAIN_MENU_BUTTON]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("✅ All your notes have been cleared!", reply_markup=reply_markup)
    else:
        keyboard = [[BACK_TO_MAIN_MENU_BUTTON]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("📭 You don't have any notes to clear.", reply_markup=reply_markup)


# This is the actual Vercel handler function
async def handler(request: BaseHTTPRequestHandler):
    if request.method == 'POST':
        # Read the incoming JSON body from Telegram
        body = json.loads(request.body.decode('utf-8'))
        
        # Create an Update object from the incoming JSON
        update = Update.de_json(body, application_instance.bot)

        # Process the update using the application
        # context.user_data and context.chat_data here will be isolated per request.
        # This is where persistence is broken for user_data.
        # A real solution would involve fetching user data from a DB at start of handler
        # and saving it back at the end.
        
        # Instantiate application if it hasn't been already
        app = await get_application()

        # Create a ContextTypes.DEFAULT_TYPE manually (similar to how PTB creates it for webhook)
        # This part is crucial and tricky for serverless environments as context.user_data
        # needs to be managed externally if state between requests is needed.
        # For simplicity here, we're assuming context.user_data is ephemeral per request.
        # This means 'awaiting_note', 'awaiting_search' etc. WILL NOT WORK across requests.
        # To fix this, you'd store these states in your external DB.

        # To demonstrate the structure, let's create a minimal context.
        # In a real scenario, you'd load user-specific state from a DB into user_data.
        # For this example, we'll keep `user_data` global but it will be reset on cold start.

        # A more robust way would be:
        # 1. Fetch user_id from update
        # 2. Load user's state from DB (e.g., {'awaiting_note': True})
        # 3. Create context_types with this loaded state
        # 4. Pass to process_update

        # For the sake of matching previous code structure without deep DB integration:
        # We'll put context.user_data outside, but understand it's NOT persistent cross-requests.
        # A simple in-memory context for this single request.
        request_context = {
            'user_data': {}, # Will be reset per request!
            'chat_data': {}, # Will be reset per request!
            'bot_data': {},
            'job_queue': None,
            'match': None,
            'effective_user': update.effective_user,
            'effective_chat': update.effective_chat,
            'effective_message': update.effective_message,
            'args': None
        }
        # Copy the global user_data states into request_context for the current update processing.
        # This is a hacky way to prevent immediate total loss of state *within a single request chain*
        # but does NOT solve persistence between different requests.
        request_context['user_data'] = user_data['settings'].get(str(update.effective_user.id), {})
        request_context['user_data']['last_search_results'] = user_data['settings'].get(str(update.effective_user.id), {}).get('last_search_results', [])
        request_context['user_data']['last_search_query'] = user_data['settings'].get(str(update.effective_user.id), {}).get('last_search_query', '')
        
        # Make a dummy ContextTypes.DEFAULT_TYPE
        class DummyContext(ContextTypes.DEFAULT_TYPE):
            def __init__(self, data):
                super().__init__(data)
                self.user_data = data.get('user_data', {})
                self.chat_data = data.get('chat_data', {})
                self.bot_data = data.get('bot_data', {})
                self._job_queue = data.get('job_queue')
                self._match = data.get('match')
                self._effective_user = data.get('effective_user')
                self._effective_chat = data.get('effective_chat')
                self._effective_message = data.get('effective_message')
                self._args = data.get('args')
        
        context_obj = DummyContext(request_context)

        # Process the update
        await app.process_update(update, context_obj)
        
        # Update global user_data from request_context's user_data (still not persistent if next request hits cold start)
        user_data['settings'][str(update.effective_user.id)] = context_obj.user_data

        # Vercel expects a response. Telegram expects an HTTP 200 OK.
        # Note: The actual bot responses are sent asynchronously by Telegram API.
        return {
            "statusCode": 200,
            "body": "OK"
        }
    else:
        # Optional: For GET requests, return a simple status or instructions
        return {
            "statusCode": 200,
            "body": "Hello from your Telegram bot webhook! Send POST requests here."
        }

# This function is the actual entry point for Vercel's Python runtime
# It takes a request object and returns a response object
# For simplicity, we are simulating a flask-like request object and return a dict.
# In a real production setup for Vercel, you'd use a micro-framework like Flask or FastAPI
# and wrap this logic.
# For pure Vercel serverless function (without framework), the structure is a function
# that takes `request` and `response` objects, or directly returns a dictionary.

# The `vercel.json` file dictates how Vercel routes requests to this `handler` function.
# For direct Python serverless function, Vercel often expects a `handler` function.

# We'll use a basic structure that Vercel usually expects for Python functions.
async def webhook_handler(event, context): # Vercel's standard signature
    if event['httpMethod'] == 'POST':
        # Create a dummy request object to match what our `handler` function expects
        class DummyRequest:
            def __init__(self, body):
                self.method = 'POST'
                self.body = body

        request_obj = DummyRequest(event['body'])
        response = await handler(request_obj)
        return response
    elif event['httpMethod'] == 'GET':
        return {
            "statusCode": 200,
            "body": "Hello from Telegram bot webhook GET!"
        }
    else:
        return {
            "statusCode": 405,
            "body": "Method Not Allowed"
        }
