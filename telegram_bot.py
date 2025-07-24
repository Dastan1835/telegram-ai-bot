import os
import json
import asyncio
import logging
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import sys
import urllib.parse
import functools
import tempfile  # Для создания временных файлов
from pydub import AudioSegment  # Для работы с аудиофайлами, требует ffmpeg

# Убедитесь, что pydub установлен: pip install pydub
# На сервере FFmpeg должен быть доступен в PATH или установлен через менеджер пакетов.
# Для Render, скорее всего, эта строка не нужна или должна быть другой,
# так как FFmpeg обычно предустановлен или легко добавляется через настройки сервиса.
# Пока закомментируем её или изменим для общей совместимости.
# os.environ["PATH"] += os.pathsep + r"C:\\ffmpeg\\bin" # Исходная строка для Windows

# На Linux-подобных системах (как Render), ffmpeg обычно уже в PATH или доступен напрямую
# Если возникнут проблемы с pydub, вернемся к этому.

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения из .env файла
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
SHEET_ID_COURSES = os.getenv('SHEET_ID_COURSES')
DOC_ID = os.getenv('DOC_ID')

# Проверка, что все необходимые переменные окружения загружены
if not all([TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, SHEET_ID_COURSES, DOC_ID]):
    logger.critical("One or more environment variables are missing. Please check your .env file or Render environment settings.")
    sys.exit(1) # Выходим, если переменные не загружены

# Инициализация OpenAI клиента
client = OpenAI(api_key=OPENAI_API_KEY)

# Google Sheets API setup
# SCOPES = ['https://www.googleapis.com/auth/spreadsheets.readonly', 'https://www.googleapis.com/auth/documents.readonly']
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/documents'] # Добавил 'write' scope, если боту нужно изменять таблицы

# Проверяем, есть ли переменная окружения GOOGLE_SERVICE_ACCOUNT_KEY
# Если есть, используем ее содержимое. Иначе - ищем файл (только для локальной разработки).
service_account_info_json = os.getenv('GOOGLE_SERVICE_ACCOUNT_KEY')

if service_account_info_json:
    try:
        service_account_info = json.loads(service_account_info_json)
        creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
        logger.info("Credentials loaded from GOOGLE_SERVICE_ACCOUNT_KEY environment variable.")
    except json.JSONDecodeError as e:
        logger.critical(f"Error decoding GOOGLE_SERVICE_ACCOUNT_KEY JSON: {e}")
        sys.exit(1) # Выходим, если не можем загрузить ключ
else:
    # Этот блок будет выполняться ТОЛЬКО при локальном запуске, если GOOGLE_SERVICE_ACCOUNT_KEY не установлена
    SERVICE_ACCOUNT_FILE = 'service_account_key.json' # Здесь можно оставить, если для локальной разработки
    try:
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        logger.info("Credentials loaded from local service_account_key.json file.")
    except FileNotFoundError:
        logger.critical("Error: service_account_key.json not found and GOOGLE_SERVICE_ACCOUNT_KEY environment variable not set. Bot cannot start.")
        sys.exit(1) # Выходим, если не можем загрузить ключ

# Инициализация gspread
gc = gspread.authorize(creds)
# Инициализация Google Docs API
docs_service = build('docs', 'v1', credentials=creds)

# Глобальные переменные для хранения данных (кэш)
courses_data = {}
knowledge_base_data = {}
last_cache_update = None # Время последнего обновления кэша

# --- Функции для работы с Google Sheets и Docs ---

def load_courses_from_sheets():
    """Загружает данные о курсах из Google Sheets."""
    try:
        worksheet = gc.open_by_id(SHEET_ID_COURSES).worksheet("Наши курсы")
        records = worksheet.get_all_records()
        global courses_data
        courses_data = {row['Курс'].lower(): row for row in records}
        logger.info("Данные о курсах успешно загружены.")
    except Exception as e:
        logger.error(f"Ошибка загрузки данных о курсах из Google Sheets: {e}")
        raise

def get_knowledge_base(lang='ru'):
    """Загружает базу знаний из Google Docs."""
    try:
        doc_content = docs_service.documents().get(documentId=DOC_ID).execute()
        full_text = ""
        # Проходим по всем элементам тела документа
        for element in doc_content.get('body').get('content'):
            # Проверяем, есть ли параграфы
            if 'paragraph' in element:
                # Проходим по всем текстовым элементам в параграфе
                for text_run in element.get('paragraph').get('elements'):
                    if 'textRun' in text_run:
                        # Добавляем текст
                        full_text += text_run.get('textRun').get('content')

        # Разделяем на секции по ключевым словам для русского и кыргызского
        if lang == 'ru':
            start_marker = "База знаний:\n"
            end_marker = "Доступные курсы:\n"
            start_index = full_text.find(start_marker)
            end_index = full_text.find(end_marker, start_index + len(start_marker))
            if start_index != -1 and end_index != -1:
                kb_text = full_text[start_index + len(start_marker):end_index].strip()
            else:
                kb_text = full_text.strip() # Если маркеры не найдены, берем весь текст
            knowledge_base_data['ru'] = kb_text
            logger.info(f"База знаний RU успешно загружена. Длина: {len(kb_text)} символов.")

        elif lang == 'ky':
            start_marker = "База знаний:\n" # Возможно, другие маркеры для кыргызского?
            end_marker = "Доступные курсы:\n" # Или другие
            start_index = full_text.find(start_marker)
            end_index = full_text.find(end_marker, start_index + len(start_marker))
            if start_index != -1 and end_index != -1:
                kb_text = full_text[start_index + len(start_marker):end_index].strip()
            else:
                kb_text = full_text.strip() # Если маркеры не найдены, берем весь текст
            knowledge_base_data['ky'] = kb_text
            logger.info(f"База знаний KY успешно загружена. Длина: {len(kb_text)} символов.")

    except Exception as e:
        logger.error(f"Ошибка загрузки базы знаний из Google Docs для языка {lang}: {e}")
        raise

async def refresh_cache():
    """Обновляет кэш данных из Google Sheets и Docs."""
    global last_cache_update
    if last_cache_update is None or (datetime.now() - last_cache_update) > timedelta(hours=1):
        logger.info("Обновление кэша данных...")
        try:
            load_courses_from_sheets()
            for lang in ['ru', 'ky']:
                get_knowledge_base(lang)
            last_cache_update = datetime.now()
            logger.info("Кэш данных успешно обновлен.")
        except Exception as e:
            logger.error(f"Ошибка при обновлении кэша: {e}")

# --- Функции для работы с OpenAI ---

def generate_response(prompt, lang='ru'):
    """Генерирует ответ с помощью OpenAI."""
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo", # Или "gpt-4-turbo" если есть доступ
            messages=[
                {"role": "system", "content": open(f'system_prompt_{lang}.txt', 'r', encoding='utf-8').read().format(
                    knowledge_base=knowledge_base_data.get(lang, ''),
                    courses_str="\n".join([f"- {k}: {v['Описание']}" for k, v in courses_data.items()]),
                    post_consultation_prompt_example="Готовы ли вы начать обучение с IT Run Academy?"
                )},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Ошибка генерации ответа OpenAI: {e}")
        return f"Извините, произошла ошибка при обработке вашего запроса ({e}). Пожалуйста, попробуйте еще раз."

# --- Функции для работы с Telegram API ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает команду /start."""
    user_name = update.effective_user.first_name if update.effective_user else "пользователь"
    await update.message.reply_text(f"Привет, {user_name}! Я бот IT Run Academy. Спрашивайте меня о курсах и нашей академии.")
    logger.info(f"Получена команда /start от {user_name}.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает текстовые сообщения."""
    await refresh_cache() # Обновляем кэш при каждом сообщении (или по таймеру)
    user_text = update.message.text
    user_id = update.effective_user.id
    logger.info(f"Получено сообщение от {user_id}: {user_text}")

    detected_lang = detect_language(user_text)
    response_text = generate_response(user_text, lang=detected_lang)
    await update.message.reply_text(response_text)

async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обрабатывает голосовые сообщения."""
    await refresh_cache() # Обновляем кэш при каждом сообщении (или по таймеру)
    user_id = update.effective_user.id
    voice_file_id = update.message.voice.file_id
    logger.info(f"Получено голосовое сообщение от {user_id}. File ID: {voice_file_id}")

    try:
        # Скачиваем голосовое сообщение
        file = await context.bot.get_file(voice_file_id)
        # Используем tempfile для безопасной работы с временными файлами
        with tempfile.TemporaryDirectory() as tmpdir:
            ogg_path = os.path.join(tmpdir, f"{voice_file_id}.ogg")
            wav_path = os.path.join(tmpdir, f"{voice_file_id}.wav")

            await file.download_to_drive(ogg_path)
            logger.info(f"Голосовое сообщение сохранено как {ogg_path}")

            # Конвертируем OGG в WAV с помощью pydub
            # Это требует FFmpeg. Убедитесь, что FFmpeg установлен и доступен.
            audio = AudioSegment.from_ogg(ogg_path)
            audio.export(wav_path, format="wav")
            logger.info(f"Голосовое сообщение сконвертировано в {wav_path}")

            # Отправляем аудио на транскрибацию в OpenAI Whisper
            with open(wav_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    response_format="text"
                )
            user_text = transcript.strip()
            logger.info(f"Транскрибированный текст: {user_text}")

            if not user_text:
                await update.message.reply_text("Не удалось распознать речь. Пожалуйста, попробуйте еще раз или напишите текст.")
                return

            detected_lang = detect_language(user_text)
            response_text = generate_response(user_text, lang=detected_lang)
            await update.message.reply_text(response_text)

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}", exc_info=True)
        await update.message.reply_text("Извините, произошла ошибка при обработке голосового сообщения. Пожалуйста, попробуйте еще раз.")


# --- Вспомогательные функции ---

def detect_language(text):
    """
    Определяет язык текста (русский или кыргызский) на основе наличия кыргызских букв
    или ключевых слов.
    """
    text_lower = text.lower()
    kyrgyz_letters = "өңүчзг" # Простые кыргызские буквы для быстрого определения
    kyrgyz_keywords = ["саламатсызбы", "рахмат", "кечиресиз", "кандай", "рахмат", "жок"] # Более полные слова

    # Проверка на наличие уникальных кыргызских букв
    if any(char in text_lower for char in kyrgyz_letters):
        logger.info(f"Язык определен как KY (по кыргызским буквам): '{text}'")
        return 'ky'

    kyrgyz_word_count = sum(1 for keyword in kyrgyz_keywords if keyword in text_lower)

    if kyrgyz_word_count > 0 or "саламатсызбы" in text_lower:
        logger.info(f"Язык определен как KY (по ключевым словам): '{text}')")
        return 'ky'

    logger.info(f"Язык определен как RU (по умолчанию): '{text}')")
    return 'ru'


def main():
    logger.info("Инициализация: загрузка курсов и базы знаний.")
    try:
        load_courses_from_sheets()
        for lang in ['ru', 'ky']:
            get_knowledge_base(lang)
        global last_cache_update
        last_cache_update = datetime.now()
    except Exception as e:
        logger.critical(f"Критическая ошибка при первоначальной загрузке данных: {e}. Бот не может быть запущен.")
        sys.exit(1)

    logger.info("Бот запущен")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message)) # Добавляем обработчик для голосовых сообщений

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()