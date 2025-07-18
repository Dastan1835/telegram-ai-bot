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
# И что ffmpeg установлен в вашей системе и доступен в PATH
# Путь к FFmpeg. Убедитесь, что это ПРАВИЛЬНЫЙ путь к папке 'bin' вашего FFmpeg.
# Например: r"C:\ffmpeg\bin" или r"C:\Users\NoutSpace\Desktop\ffmpeg-master-latest-win64-gpl-shared\bin"
os.environ["PATH"] += os.pathsep + r"C:\ffmpeg\bin"  # <-- УКАЖИТЕ АКТУАЛЬНЫЙ ПУТЬ К ВАШЕМУ BIN FFmpeg

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)  # Получаем логгер для использования в функциях

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_SHEET_COURSES_ID = os.getenv("SHEET_ID_COURSES", "1XeTe3Ihvi2N8bvo6P-yBZL2j_8L2IlvN6bOPYmCu5z8")
GOOGLE_DOC_ID = os.getenv("DOC_ID")

SERVICE_ACCOUNT_FILE = 'service_account_key.json'

try:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/documents.readonly']
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    sheets_client = gspread.authorize(creds)
    docs_service = build("docs", "v1", credentials=creds)
    sheet_courses = sheets_client.open_by_key(GOOGLE_SHEET_COURSES_ID).worksheet("Лист1")
    logger.info("Подключение к Google Sheets и Docs успешно установлено")
except Exception as e:
    logger.error(f"Ошибка подключения к Google API: {str(e)}")
    raise

course_cache = {'ru': [], 'ky': []}
knowledge_base_cache = {'ru': '', 'ky': ''}
last_cache_update = datetime.min
CACHE_LIFETIME = timedelta(minutes=30)  # Время жизни кэша

COURSE_SYNONYMS = {
    'ru': {
        'python': ['питон', 'пайтон', 'основы python', 'программирование на python', 'python курс', 'изучение python'],
        'backend': ['бэкенд', 'серверная разработка', 'backend разработка', 'создание серверов'],
        'frontend': ['фронтенд', 'интерфейс', 'веб-разработка', 'дизайн сайтов'],
        'scratch': ['скретч', 'программирование для детей', 'скретч для начинающих', 'игры на скретч'],
        'data science': ['данные', 'аналитика данных', 'наука о данных', 'анализ данных', 'data анализ']
    },
    'ky': {
        'python': ['питон', 'пайтон', 'python негиздери', 'python боюнча программалоо', 'python курсу',
                   'python үйрөнүү'],
        'backend': ['бэкенд', 'сервердик иштеп чыгуу', 'backend иштеп чыгуу', 'сервер түзүү', 'бекенд'],
        'frontend': ['фронтенд', 'интерфейс', 'веб-иштеп чыгуу', 'сайттардын дизайны', 'фронтенд'],
        'scratch': ['скретч', 'балдар үчүн программалоо', 'скретч башталгычтар үчүн', 'скретч менен оюндар'],
        'data science': ['маалыматтар', 'маалыматтар аналитикасы', 'маалымат илими', 'маалыматтарды талдоо',
                         'data талдоо']
    }
}

# Обновленные сообщения для бота
MESSAGES = {
    'ru': {
        'welcome': "Привет! Я виртуальный менеджер IT Run Academy. Я здесь, чтобы помочь вам узнать о наших курсах и возможностях. Чем могу помочь?",
        'course_data_error': "Внимание: Не удалось загрузить данные о курсах. Я буду отвечать на основе общих знаний, но точная информация о курсах может быть недоступна.",
        'openai_connect_error': "Извините, сейчас не могу связаться с сервером. Попробуйте позже.",
        'openai_rate_limit_error': "Извините, слишком много запросов. Подождите немного.",
        'openai_auth_error': "Извините, произошла ошибка аутентификации. Сообщите администратору.",
        'openai_timeout_error': "Извините, запрос к серверу занял слишком много времени. Попробуйте еще раз.",
        'openai_unknown_error': "Извините, произошла внутренняя ошибка. Попробуйте еще раз или свяжитесь с поддержкой.",
        'no_course_info_available': "Информация о курсах пока недоступна в полном объеме. Сообщите администратору, чтобы он добавил курсы или уточните позже.",
        'off_topic_response': "К сожалению, я не могу помочь с этим вопросом, но могу рассказать о наших курсах в IT Run Academy. У нас есть отличные программы, которые могут вас заинтересовать. Хотите, чтобы я рассказала подробнее?",
        # Новая фраза для записи через форму
        'enrollment_form_prompt': "Отлично! Чтобы записаться или получить подробную консультацию, пожалуйста, заполните эту форму:[ https://forms.gle/QkyZyuPm1SLdfEa8A ]. Также Вы можете лично посетить нашу академию по адресу: [Адрес академии из базы знаний]. Мы работаем [График работы из базы знаний]. Будем рады Вас видеть!",
        # Новая фраза для запроса формы при отсутствии информации
        'no_info_form_prompt': "Извините, у меня нет точной информации по вашему вопросу прямо сейчас, или я не нашла такой курс. Чтобы наши менеджеры могли связаться с вами и предоставить подробную информацию, пожалуйста, заполните эту форму: [ https://forms.gle/QkyZyuPm1SLdfEa8A ].",
        # Новая фраза для приглашения на пробный урок (GPT будет использовать ее как пример)
        'trial_lesson_invite': "Также приглашаем вас на наш бесплатный пробный урок, который проводится каждую субботу. Это отличная возможность познакомиться с нами ближе!",
        # Новая фраза для вопроса после консультации
        'post_consultation_prompt': "Если вы хотите записаться, пожалуйста, скажите об этом.",
        # Новая фраза для ответа на вопросы о сотрудничестве/практике
        'cooperation_contact_prompt': "По вопросам сотрудничества или практики, пожалуйста, свяжитесь с нашим менеджером по номеру: [НОМЕР_МЕНЕДЖЕРА_ДЛЯ_СОТРУДНИЧЕСТВА].",
    },
    'ky': {
        'welcome': "Салам! Мен IT Run Academyнин виртуалдык менеджеримин. Мен сизге курстарыбыз жана мүмкүнчүлүктөрүбүз жөнүндө маалымат берүүгө даярмын. Кантип жардам бере алам?",
        'course_data_error': "Эскертүү: Курстар жөнүндө маалымат жүктөлбөй калды. Мен жалпы билимдин негизинде жооп берем, бирок курстар жөнүндө так маалымат жеткиликсиз болушу мүмкүн.",
        'openai_connect_error': "Кечиресиз, учурда сервер менен байланша албай жатам. Кийинчерээк кайра аракет кылыңыз.",
        'openai_rate_limit_error': "Кечиресиз, суроо-талаптар көп. Бир аз күтүңүз.",
        'openai_auth_error': "Кечиресиз, аутентификация катасы кетти. Администраторго билдириңиз.",
        'openai_timeout_error': "Кечиресиз, серверге суроо-талап узак убакытты алды. Кайра аракет кылыңыз.",
        'openai_unknown_error': "Кечиресиз, ички ката кетти. Кайра аракет кылыңыз же колдоо кызматына кайрылыңыз.",
        'no_course_info_available': "Курстар жөнүндө маалымат толук жеткиликсиз. Администраторго билдириңиз же кийинчерээк тактаңыз.",
        'off_topic_response': "Кечиресиз, бул суроого жардам бере албайм, бирок IT Run Academyдеги курстарыбыз жөнүндө айта алам. Бизде сизди кызыктыра турган сонун программалар бар. Кененирээк айтып берейинби?",
        'enrollment_form_prompt': "Абдан сонун! Катталуу же кененирээк кеңеш алуу үчүн, сураныч, бул форманы толтуруңуз: [ https://forms.gle/QkyZyuPm1SLdfEa8A ]. Ошондой эле сиз биздин академияга жеке өзүңүз келип кайрылсаңыз болот: [Академиянын дареги базадан]. Биз [Иш убактысы базадан] иштейбиз. Сизди күтөбүз!",
        'no_info_form_prompt': "Кечиресиз, учурда менин сурооңуз боюнча так маалыматым жок, же мен андай курсту тапкан жокмун. Биздин менеджерлер сизге байланышып, толук маалымат бере алышы үчүн, сураныч, бул форманы толтуруңуз: [ https://forms.gle/QkyZyuPm1SLdfEa8A ].",
        'trial_lesson_invite': "Ошондой эле сизди ар ишемби сайын өтүүчү акысыз сыноо сабагыбызга чакырабыз. Бул биз менен жакындан таанышууга эң сонун мүмкүнчүлүк!",
        'post_consultation_prompt': "Эгер сиз жазылгыңыз келсе, айтыңыз.",
        'cooperation_contact_prompt': "Кызматташуу же практика боюнча суроолор үчүн, биздин менеджер менен бул номер аркылуу байланышыңыз: [НОМЕР_МЕНЕДЖЕРА_ДЛЯ_СОТРУДНИЧЕСТВА].",
    }
}


def get_knowledge_base(lang_code):
    """Синхронная функция для загрузки базы знаний из Google Docs."""
    doc = docs_service.documents().get(documentId=GOOGLE_DOC_ID).execute()
    content = doc.get("body").get("content")
    text = ""
    for element in content:
        if "paragraph" in element:
            for text_run in element["paragraph"]["elements"]:
                if "textRun" in text_run:
                    text += text_run["textRun"]["content"]
    knowledge_base_cache[lang_code] = text
    return text


async def refresh_cache():
    global last_cache_update
    if datetime.now() - last_cache_update > CACHE_LIFETIME:
        logger.info("Обновление кэша: загрузка курсов и базы знаний.")
        await asyncio.to_thread(load_courses_from_sheets)
        for lang in ['ru', 'ky']:
            knowledge_base_cache[lang] = await asyncio.to_thread(get_knowledge_base, lang)
        last_cache_update = datetime.now()
        logger.info("Кэш успешно обновлен.")
    else:
        logger.info("Кэш актуален, обновление не требуется.")


async def refresh_cache_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Плановое обновление кэша...")
    await refresh_cache()
    logger.info("Плановое обновление кэша завершено.")


def load_courses_from_sheets():
    """Синхронная функция для загрузки курсов из Google Sheets."""
    global course_cache
    try:
        records = sheet_courses.get_all_records()
        courses_ru, courses_ky = [], []
        for record in records:
            course_ru_name = record.get('Название курса', '').strip()
            if course_ru_name:
                courses_ru.append({
                    'Название курса': course_ru_name,
                    'Описание': record.get('Описание', 'Описание отсутствует.'),
                    'Цена / на месяц': record.get('Цена / на месяц', 'Цена не указана.'),
                    'Продолжительность': record.get('Продолжительность', 'Продолжительность не указана.'),
                    'график учебы': record.get('график учебы', 'График не указан.'),
                    'возрастное ограничение': record.get('возрастное ограничение', 'Возраст не указан.')
                })
            course_ky_name = record.get('Название курса (Кырг.)', course_ru_name).strip()
            if course_ky_name:
                courses_ky.append({
                    'Название курса': course_ky_name,
                    'Описание': record.get('Описание (Кырг.)', record.get('Описание', 'Сүрөттөмө жок.')),
                    'Цена / на месяц': record.get('Цена / на месяц', 'Баасы көрсөтүлгөн эмес.'),
                    'Продолжительность': record.get('Продолжительность', 'Узактыгы көрсөтүлгөн эмес.'),
                    'график учебы': record.get('график учебы (Кырг.)',
                                               record.get('график учебы', 'График көрсөтүлгөн эмес.')),
                    'возрастное ограничение': record.get('возрастное ограничение (Кырг.)',
                                                         record.get('возрастное ограничение', 'Жашы көрсөтүлгөн эмес.'))
                })
        course_cache['ru'] = courses_ru
        course_cache['ky'] = courses_ky
        logger.info(f"Загружено {len(courses_ru)} курсов для RU и {len(courses_ky)} для KY из Google Sheets.")
    except Exception as e:
        logger.error(f"Ошибка при загрузке курсов из Google Sheets: {str(e)}")
        pass


def get_system_prompt(lang_code):
    knowledge_base = knowledge_base_cache.get(lang_code, '')
    if not knowledge_base:
        logger.warning(f"База знаний для языка {lang_code} пуста в кэше. Попытка загрузки.")
        knowledge_base = get_knowledge_base(lang_code)
        if not knowledge_base:
            logger.error(f"Не удалось загрузить базу знаний для языка {lang_code}.")
            knowledge_base = "Информация об академии временно недоступна."

    courses = course_cache.get(lang_code, [])
    courses_str = "\n".join([
        f"- {c['Название курса']}: {c.get('Описание', 'Описание отсутствует.')}, "
        f"Цена: {c.get('Цена / на месяц', 'Цена не указана.')}, "
        f"Продолжительность: {c.get('Продолжительность', 'Продолжительность не указана.')}, "
        f"График учебы: {c.get('график учебы', 'График не указан.')}, "
        f"Возрастное ограничение: {c.get('возрастное ограничение', 'Возраст не указан.')}"
        for c in courses
    ])
    if not courses:
        courses_str = MESSAGES[lang_code]['no_course_info_available']
        logger.warning(f"Информация о курсах для языка {lang_code} пуста в кэше.")

    off_topic_response_example = MESSAGES[lang_code]['off_topic_response']
    enrollment_form_prompt_example = MESSAGES[lang_code]['enrollment_form_prompt']
    no_info_form_prompt_example = MESSAGES[lang_code]['no_info_form_prompt']
    trial_lesson_invite_example = MESSAGES[lang_code]['trial_lesson_invite']
    post_consultation_prompt_example = MESSAGES[lang_code]['post_consultation_prompt']
    cooperation_contact_prompt_example = MESSAGES[lang_code]['cooperation_contact_prompt']

    prompt_file_path = f"system_prompt_{lang_code}.txt"
    try:
        with open(prompt_file_path, 'r', encoding='utf-8') as f:
            base_prompt = f.read()
    except FileNotFoundError:
        logger.error(f"Файл промпта не найден: {prompt_file_path}. Используется резервный промпт.")
        base_prompt = (
            "You are an IT Run Academy virtual manager. Provide information about courses and academy. "
            "Use provided knowledge base and course info. If information is missing, ask to fill the form."
        )

    try:
        prompt = base_prompt.format(
            knowledge_base=knowledge_base,
            courses_str=courses_str,
            off_topic_response_example=off_topic_response_example,
            enrollment_form_prompt_example=enrollment_form_prompt_example,
            no_info_form_prompt_example=no_info_form_prompt_example,
            trial_lesson_invite_example=trial_lesson_invite_example,
            post_consultation_prompt_example=post_consultation_prompt_example,
            cooperation_contact_prompt_example=cooperation_contact_prompt_example
        )
    except KeyError as e:
        logger.error(
            f"Ошибка форматирования промпта для языка {lang_code}: не найден ключ {e}. Проверьте файл промпта.")
        prompt = (
            "You are an IT Run Academy virtual manager. Provide information about courses and academy. "
            f"Knowledge Base: {knowledge_base}\nCourses: {courses_str}\n"
            "If information is missing, ask to fill the form."
        )
    return prompt


async def get_gpt_response(user_message, chat_history, lang_code):
    messages = [{"role": "system", "content": get_system_prompt(lang_code)}]
    messages.extend(chat_history)

    MAX_HISTORY_LENGTH = 10
    if len(messages) > MAX_HISTORY_LENGTH + 1:
        messages = [messages[0]] + messages[-(MAX_HISTORY_LENGTH):]

    messages.append({"role": "user", "content": user_message})

    logger.info(f"Сообщения для GPT: {messages}")

    try:
        response = await asyncio.to_thread(
            openai_client.chat.completions.create,
            model="gpt-4o",
            messages=messages,
            max_tokens=500,
            temperature=0.7
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Ошибка OpenAI: {str(e)}", exc_info=True)
        if "rate limit" in str(e).lower():
            return MESSAGES[lang_code]['openai_rate_limit_error']
        elif "authentication error" in str(e).lower() or "invalid api key" in str(e).lower():
            return MESSAGES[lang_code]['openai_auth_error']
        elif "timeout" in str(e).lower():
            return MESSAGES[lang_code]['openai_timeout_error']
        return MESSAGES[lang_code]['openai_unknown_error']


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await refresh_cache()
    lang_code = detect_language(update.message.text)
    context.user_data['lang'] = lang_code
    context.user_data['chat_history'] = []
    logger.info(f"Запуск команды /start для пользователя {update.effective_user.id}, язык: {lang_code}")
    await update.message.reply_text(MESSAGES[lang_code]['welcome'])


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str = None):
    # Если message_text передан (например, из голосового сообщения), используем его.
    # В противном случае, используем текст из update.message.
    user_message = message_text if message_text is not None else update.message.text.strip()
    user_id = update.effective_user.id

    # Определяем язык, используя либо уже установленный в user_data, либо детектируя из сообщения
    lang_code = context.user_data.get('lang', detect_language(user_message))
    logger.info(f"Обработка сообщения от {user_id}: '{user_message}', язык: {lang_code}")

    if 'chat_history' not in context.user_data:
        context.user_data['chat_history'] = []
    if 'lang' not in context.user_data:
        context.user_data['lang'] = lang_code

    chat_history = context.user_data.get('chat_history', [])
    chat_history.append({"role": "user", "content": user_message})

    await update.message.reply_chat_action("typing")

    response_text = await get_gpt_response(user_message, chat_history, lang_code)
    chat_history.append({"role": "assistant", "content": response_text})
    context.user_data['chat_history'] = chat_history

    await update.message.reply_text(response_text)


# НОВЫЙ ХЕНДЛЕР ДЛЯ ГОЛОСОВЫХ СООБЩЕНИЙ
async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    voice_file_id = update.message.voice.file_id
    logger.info(f"Получено голосовое сообщение от {user_id}, file_id: {voice_file_id}")

    # Используем typing, так как это более общий индикатор обработки
    await update.message.reply_chat_action("typing")
    await update.message.reply_text("Пожалуйста, подождите, я анализирую ваше голосовое сообщение...",
                                    disable_notification=True)

    ogg_path = None
    mp3_path = None
    try:
        # Создаем временный файл для сохранения аудио Telegram (обычно OGG)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as temp_ogg_file:
            ogg_path = temp_ogg_file.name
            voice_file = await context.bot.get_file(voice_file_id)
            await voice_file.download_to_drive(ogg_path)
        logger.info(f"Голосовое сообщение сохранено во временный файл: {ogg_path}")

        # Создаем временный файл для конвертированного MP3
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as temp_mp3_file:
            mp3_path = temp_mp3_file.name
            try:
                audio = AudioSegment.from_file(ogg_path, format="ogg")
                audio.export(mp3_path, format="mp3")
                logger.info(f"Голосовое сообщение конвертировано в MP3: {mp3_path}")
            except Exception as e:
                logger.error(f"Ошибка конвертации OGG в MP3 с помощью pydub/ffmpeg: {e}", exc_info=True)
                await update.message.reply_text(
                    "Извините, произошла ошибка при обработке аудио. Пожалуйста, попробуйте еще раз или напишите мне.")
                return  # Прерываем выполнение, если конвертация не удалась

        # Отправляем аудио в OpenAI Whisper для транскрибации
        with open(mp3_path, "rb") as audio_file:
            transcription_response = await asyncio.to_thread(
                openai_client.audio.transcriptions.create,
                model="whisper-1",
                file=audio_file,
                response_format="text",  # Получаем чистый текст
                language="ru"  # Указываем язык для лучшего качества распознавания
            )

        user_message_text = transcription_response.strip()
        logger.info(f"Голосовое сообщение транскрибировано в текст: '{user_message_text}'")

        # Если текст пустой, сообщаем об этом
        if not user_message_text:
            await update.message.reply_text(
                "Извините, не удалось распознать речь в вашем сообщении. Пожалуйста, повторите или напишите мне.")
            return

        # Передаем распознанный текст в существующий обработчик текстовых сообщений
        # ВАЖНО: handle_message теперь принимает message_text в качестве опционального аргумента
        await handle_message(update, context, message_text=user_message_text)

    except Exception as e:
        logger.exception(
            "Ошибка при обработке голосового сообщения:")  # Используем logger.exception для полного трейсбека
        await update.message.reply_text(
            "Извините, произошла ошибка при обработке вашего голосового сообщения. Пожалуйста, попробуйте еще раз или напишите мне.")
    finally:
        # Очистка временных файлов
        if ogg_path and os.path.exists(ogg_path):
            os.remove(ogg_path)
            logger.info(f"Временный файл удален: {ogg_path}")
        if mp3_path and os.path.exists(mp3_path):
            os.remove(mp3_path)
            logger.info(f"Временный файл удален: {mp3_path}")


def detect_language(text):
    text_lower = text.lower()
    kyrgyz_keywords = [
        'ооба', 'жок', 'салам', 'жазылайын', 'катталуу', 'тиркеме', 'кабыл алуу', 'кантип', 'эмне',
        'ким', 'бекенд', 'фронтенд', 'графолог', 'мобилография', 'орт', 'программист', 'баасы', 'узактыгы', 'графиги'
    ]

    kyrgyz_chars = "ңөү"
    if any(char in text_lower for char in kyrgyz_chars):
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
    application.add_handler(MessageHandler(filters.VOICE, handle_voice_message))  # Хендлер для голосовых сообщений

    application.job_queue.run_repeating(refresh_cache_job, interval=timedelta(minutes=20), first=0)

    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()