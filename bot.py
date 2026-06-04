import os
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Загружаем переменные окружения (только если файл .env существует)
if os.path.exists('.env'):
    from dotenv import load_dotenv
    load_dotenv()
    print("📁 Загружен .env файл", flush=True)
else:
    print("🐳 Запуск в Docker, используем переменные окружения", flush=True)

# Подключаем psycopg (версия 3, не требует компиляции)
try:
    import psycopg
    print("✅ psycopg (v3) импортирован", flush=True)
except ImportError:
    print("❌ psycopg не установлен!", flush=True)
    raise

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Подключение к БД
def get_db_connection():
    try:
        conn = psycopg.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432'),
            dbname=os.getenv('DB_NAME', 'task_tracker'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD')
        )
        return conn
    except Exception as e:
        print(f"❌ DB connection error: {e}", flush=True)
        raise

# Создание таблицы при первом запуске
def init_db():
    try:
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
        print("✅ Таблица tasks проверена/создана", flush=True)
    except Exception as e:
        print(f"❌ init_db error: {e}", flush=True)
        raise

# Потоки
STREAMS = {
    'n8n': '🤖 n8n и Codex',
    'linux': '🐧 Linux + Bash + Python',
    'portfolio': '📁 Portfolio'
}

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сбрасываем состояние при старте
    context.user_data.clear()

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
        f"Используй кнопки ниже для управления задачами.\n\n"
        f"Или команды:\n"
        f"/add n8n задача\n"
        f"/add linux задача\n"
        f"/add portfolio задача\n"
        f"/list\n"
        f"/stats\n"
        f"/done ID\n"
        f"/delete ID",
        reply_markup=reply_markup
    )

# Добавление задачи через команду /add
async def add_task_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "Использование: /add <поток> <задача>\n\n"
            "Потоки: n8n, linux, portfolio\n\n"
            "Пример: /add n8n Изучить Docker"
        )
        return

    stream = context.args[0].lower()
    task_text = ' '.join(context.args[1:])

    if stream not in STREAMS:
        await update.message.reply_text(f"❌ Неизвестный поток. Доступны: n8n, linux, portfolio")
        return

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

# Добавление задачи - выбор потока (кнопки)
async def add_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Отвечаем на callback

    print("📞 add_task_start вызвана!", flush=True)

    keyboard = [
        [InlineKeyboardButton(STREAMS['n8n'], callback_data='stream_n8n')],
        [InlineKeyboardButton(STREAMS['linux'], callback_data='stream_linux')],
        [InlineKeyboardButton(STREAMS['portfolio'], callback_data='stream_portfolio')],
        [InlineKeyboardButton("« Назад", callback_data='back_to_menu')]  # Кнопка назад
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Выбери поток, в который добавить задачу:",
        reply_markup=reply_markup
    )

# Выбор потока
async def select_stream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()  # Отвечаем на callback

    print("📞 select_stream вызвана!", flush=True)

    stream_map = {
        'stream_n8n': 'n8n',
        'stream_linux': 'linux',
        'stream_portfolio': 'portfolio'
    }
    stream = stream_map.get(query.data)
    if stream:
        context.user_data['selected_stream'] = stream
        context.user_data['waiting_for_task'] = True
        await query.edit_message_text(
            f"Выбран поток: {STREAMS[stream]}\n\n"
            f"Теперь отправь текст задачи одним сообщением:\n\n"
            f"(или отправь /cancel чтобы отменить)"
        )

# Сохранение задачи
async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Проверяем, ждем ли мы задачу
    if not context.user_data.get('waiting_for_task'):
        return

    task_text = update.message.text
    stream = context.user_data.get('selected_stream')
    user_id = update.message.from_user.id

    if not stream:
        await update.message.reply_text(
            "❌ Ошибка: поток не выбран.\n"
            "Используй кнопки меню для добавления задачи."
        )
        context.user_data.clear()
        return

    try:
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

        # Очищаем состояние после успешного добавления
        context.user_data.clear()

        # Показываем меню снова
        keyboard = [
            [InlineKeyboardButton("➕ Добавить задачу", callback_data='add')],
            [InlineKeyboardButton("📋 Список задач", callback_data='list')],
            [InlineKeyboardButton("📊 Статистика", callback_data='stats')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "Что хочешь сделать дальше?",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при сохранении задачи: {e}")
        await update.message.reply_text("❌ Ошибка при сохранении задачи. Попробуй еще раз.")
        context.user_data.clear()

# Отмена добавления задачи
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Добавление задачи отменено.")

    # Показываем меню
    keyboard = [
        [InlineKeyboardButton("➕ Добавить задачу", callback_data='add')],
        [InlineKeyboardButton("📋 Список задач", callback_data='list')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Возвращаемся в главное меню:",
        reply_markup=reply_markup
    )

# Список задач
async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сбрасываем состояние при просмотре списка
    context.user_data.clear()

    print("📞 list_tasks вызвана!", flush=True)

    # Поддержка как команды, так и callback
    if update.callback_query:
        query = update.callback_query
        await query.answer()  # Отвечаем на callback
        user_id = query.from_user.id
        send_message = query.edit_message_text
    else:
        user_id = update.message.from_user.id
        send_message = update.message.reply_text

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
        await send_message("📭 У тебя пока нет задач. Добавь первую через /add или кнопку ➕")
        return

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
    message += "\n_Чтобы удалить задачу, используй /delete <id>_"

    # Добавляем кнопку "Назад" для callback режима
    if update.callback_query:
        keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await send_message(message, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await send_message(message, parse_mode='Markdown')

# Статистика
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сбрасываем состояние при просмотре статистики
    context.user_data.clear()

    print("📞 stats вызвана!", flush=True)

    if update.callback_query:
        query = update.callback_query
        await query.answer()  # Отвечаем на callback
        user_id = query.from_user.id
        send_message = query.edit_message_text
    else:
        user_id = update.message.from_user.id
        send_message = update.message.reply_text

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

    # Добавляем кнопку "Назад" для callback режима
    if update.callback_query:
        keyboard = [[InlineKeyboardButton("« Назад в меню", callback_data='back_to_menu')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await send_message(message, parse_mode='Markdown', reply_markup=reply_markup)
    else:
        await send_message(message, parse_mode='Markdown')

# Отметить задачу выполненной
async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сбрасываем состояние
    context.user_data.clear()

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

# Удалить задачу
async def delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Сбрасываем состояние
    context.user_data.clear()

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

# Возврат в главное меню
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    keyboard = [
        [InlineKeyboardButton("➕ Добавить задачу", callback_data='add')],
        [InlineKeyboardButton("📋 Список задач", callback_data='list')],
        [InlineKeyboardButton("📊 Статистика", callback_data='stats')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(
        "Главное меню:",
        reply_markup=reply_markup
    )

# Единый обработчик для всех callback-запросов (кнопок)
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    print(f"🔘 Получен callback: {data}", flush=True)

    # Убираем query.answer() отсюда, так как он будет вызван в каждой функции

    if data == 'add':
        await add_task_start(update, context)
    elif data == 'list':
        await list_tasks(update, context)
    elif data == 'stats':
        await stats(update, context)
    elif data == 'back_to_menu':
        await back_to_menu(update, context)
    elif data.startswith('stream_'):
        await select_stream(update, context)
    else:
        print(f"⚠️ Неизвестный callback: {data}", flush=True)
        await query.answer("Неизвестная команда", show_alert=True)

def main():
    print("🚀 Запуск бота...", flush=True)

    # Инициализация БД
    try:
        init_db()
        print("✅ Database connection successful", flush=True)
    except Exception as e:
        print(f"❌ Database connection FAILED: {e}", flush=True)
        return

    # Создаём приложение
    token = os.getenv('BOT_TOKEN')
    if not token:
        print("❌ BOT_TOKEN не задан!", flush=True)
        return

    application = Application.builder().token(token).build()

    # Команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("add", add_task_command))
    application.add_handler(CommandHandler("list", list_tasks))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("done", mark_done))
    application.add_handler(CommandHandler("delete", delete_task))
    application.add_handler(CommandHandler("cancel", cancel))  # Добавляем команду отмены

    # Обработчик для callback-запросов (кнопок)
    application.add_handler(CallbackQueryHandler(handle_callback))

    # Обработчик текста для задач (только если не команда)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, save_task))

    print("✅ Бот запускается...", flush=True)

    # Запуск
    application.run_polling()

if __name__ == '__main__':
    main()