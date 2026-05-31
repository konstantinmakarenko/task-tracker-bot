import os
import logging
from datetime import datetime

from dotenv import load_dotenv
import psycopg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Подключение к БД
def get_db_connection():
    return psycopg.connect(
        host=os.getenv('DB_HOST'),
        port=os.getenv('DB_PORT', '5432'),
        dbname=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD')
    )

# Создание таблицы при первом запуске
def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL,
            stream TEXT NOT NULL,
            task_text TEXT NOT NULL,
            is_done BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT NOW()
        )
    ''')
    conn.commit()
    cur.close()
    conn.close()

# Потоки (можно расширять)
STREAMS = {
    'n8n': '🤖 n8n и Codex',
    'linux': '🐧 Linux + Bash + Python',
    'portfolio': '📁 Portfolio'
}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Добавить задачу", callback_data='add')],
        [InlineKeyboardButton("📋 Список задач", callback_data='list')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Привет! Я трекер задач по трём направлениям:\n\n"
        f"• {STREAMS['n8n']}\n"
        f"• {STREAMS['linux']}\n"
        f"• {STREAMS['portfolio']}\n\n"
        f"Используй кнопки ниже для управления задачами.",
        reply_markup=reply_markup
    )

async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton(STREAMS['n8n'], callback_data='stream_n8n')],
        [InlineKeyboardButton(STREAMS['linux'], callback_data='stream_linux')],
        [InlineKeyboardButton(STREAMS['portfolio'], callback_data='stream_portfolio')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Выбери поток, в который добавить задачу:",
        reply_markup=reply_markup
    )
    context.user_data['waiting_for_stream'] = True

async def select_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    stream_map = {
        'stream_n8n': 'n8n',
        'stream_linux': 'linux',
        'stream_portfolio': 'portfolio'
    }
    stream = stream_map.get(query.data)
    if stream:
        context.user_data['selected_stream'] = stream
        await query.edit_message_text(
            f"Выбран поток: {STREAMS[stream]}\n\n"
            f"Теперь отправь текст задачи одним сообщением:"
        )
        context.user_data['waiting_for_task'] = True

async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('waiting_for_task'):
        task_text = update.message.text
        stream = context.user_data.get('selected_stream')
        user_id = update.message.from_user.id

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tasks (user_id, stream, task_text) VALUES (%s, %s, %s)",
            (user_id, stream, task_text)
        )
        conn.commit()
        cur.close()
        conn.close()

        await update.message.reply_text(f"✅ Задача добавлена в поток {STREAMS[stream]}!")

        context.user_data['waiting_for_task'] = False
        context.user_data['selected_stream'] = None

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, stream, task_text, is_done FROM tasks WHERE user_id = %s ORDER BY is_done, created_at",
        (user_id,)
    )
    tasks = cur.fetchall()
    cur.close()
    conn.close()

    if not tasks:
        await query.edit_message_text("📭 У тебя пока нет задач. Добавь первую через кнопку ➕")
        return

    # Группируем по потокам
    tasks_by_stream = {}
    for task_id, stream, text, is_done in tasks:
        if stream not in tasks_by_stream:
            tasks_by_stream[stream] = []
        status = "✅" if is_done else "⏳"
        tasks_by_stream[stream].append(f"{status} `#{task_id}` {text}")

    message = "📋 *Твои задачи:*\n\n"
    for stream, stream_tasks in tasks_by_stream.items():
        message += f"*{STREAMS[stream]}:*\n"
        message += "\n".join(stream_tasks) + "\n\n"

    message += "\n_Чтобы отметить задачу выполненной, используй /done <id>_"
    await query.edit_message_text(message, parse_mode='Markdown')

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT stream, COUNT(*), SUM(CASE WHEN is_done THEN 1 ELSE 0 END) FROM tasks WHERE user_id = %s GROUP BY stream",
        (user_id,)
    )
    stats_data = cur.fetchall()
    cur.close()
    conn.close()

    message = "📊 *Статистика:*\n\n"
    total_tasks = 0
    total_done = 0
    for stream, total, done in stats_data:
        remaining = total - done
        message += f"*{STREAMS[stream]}:*\n"
        message += f"  Всего: {total}\n"
        message += f"  ✅ Сделано: {done}\n"
        message += f"  ⏳ Осталось: {remaining}\n\n"
        total_tasks += total
        total_done += done

    message += f"*Итого:* {total_done}/{total_tasks} выполнено"
    await query.edit_message_text(message, parse_mode='Markdown')

async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /done <id_задачи>")
        return

    try:
        task_id = int(context.args[0])
        user_id = update.message.from_user.id

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE tasks SET is_done = TRUE WHERE id = %s AND user_id = %s RETURNING task_text",
            (task_id, user_id)
        )
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if result:
            await update.message.reply_text(f"✅ Задача #{task_id} выполнена! Молодец!")
        else:
            await update.message.reply_text(f"❌ Задача #{task_id} не найдена или уже выполнена")
    except ValueError:
        await update.message.reply_text("ID задачи должен быть числом")

async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /delete <id_задачи>")
        return

    try:
        task_id = int(context.args[0])
        user_id = update.message.from_user.id

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tasks WHERE id = %s AND user_id = %s RETURNING task_text",
            (task_id, user_id)
        )
        result = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()

        if result:
            await update.message.reply_text(f"🗑 Задача #{task_id} удалена")
        else:
            await update.message.reply_text(f"❌ Задача #{task_id} не найдена")
    except ValueError:
        await update.message.reply_text("ID задачи должен быть числом")

def main():
    try:
        # Инициализация БД
        init_db()
        print("✅ Database connection successful")
    except Exception as e:
        print(f"❌ Database connection FAILED: {e}")

    # Создаём приложение
    token = os.getenv('BOT_TOKEN')
    application = Application.builder().token(token).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("done", mark_done))
    application.add_handler(CommandHandler("delete", delete_task))

    # Callback-обработчики
    application.add_handler(CallbackQueryHandler(add_task_start, pattern='^add$'))
    application.add_handler(CallbackQueryHandler(list_tasks, pattern='^list$'))
    application.add_handler(CallbackQueryHandler(stats, pattern='^stats$'))
    application.add_handler(CallbackQueryHandler(select_stream, pattern='^stream_'))

    # Обработчик текста для задач
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_task))

    # Запуск
    application.run_polling()

if __name__ == '__main__':
    main()