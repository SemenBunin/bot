import os
import logging
import asyncio
import tempfile
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Message, CallbackQuery, FSInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

import gspread
from google.oauth2.service_account import Credentials

import qrcode
from PIL import Image
from aiohttp import web

# ============== НАСТРОЙКИ ==============
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else None
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", 8000))
TARGET_URL = "https://rosatom.ru"

# ============== GOOGLE SHEETS КОНФИГУРАЦИЯ ==============
def get_sheet():
    try:
        # Читаем credentials из Secret Files
        creds_path = "/etc/secrets/google-credentials.json"
        if not os.path.exists(creds_path):
            logging.error("❌ Google credentials file not found in /etc/secrets/")
            # Попробуем альтернативный путь
            creds_path = "google-credentials.json"
            if not os.path.exists(creds_path):
                logging.error("❌ Google credentials file not found anywhere")
                raise FileNotFoundError("Google credentials file not found")
        
        logging.info(f"✅ Using credentials from: {creds_path}")
        creds = Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        
        # ID вашей таблицы
        SHEET_ID = "108345771575623727353"
        sheet = client.open_by_key(SHEET_ID).sheet1
        
        # Создаем заголовки если лист пустой
        if not sheet.get_all_records():
            sheet.append_row(["User ID", "Name", "Email", "Language", "Score", "Timestamp"])
        
        logging.info("✅ Google Sheets connection successful")
        return sheet
        
    except Exception as e:
        logging.error(f"❌ Google Sheets error: {e}")
        raise

def append_result(user_id, name, email, language, score):
    try:
        sheet = get_sheet()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([str(user_id), name, email, language, str(score), timestamp])
        logging.info(f"✅ Result saved: {name}, score: {score}")
        return True
    except Exception as e:
        logging.error(f"❌ Failed to save result: {e}")
        return False

def user_exists(user_id):
    try:
        sheet = get_sheet()
        ids = sheet.col_values(1)
        return str(user_id) in ids
    except Exception as e:
        logging.error(f"❌ Error checking user existence: {e}")
        return False

# ============== ВОПРОСЫ ==============
QUESTIONS = {
    "ru": [
        {"text": "В каком году была создана Госкорпорация «Росатом»?", "options": ["2000", "2007", "2010", "1995"], "correct_option_index": 1, "explanation": "Росатом был образован указом Президента РФ в 2007 году."},
        {"text": "Сколько стран сотрудничают с Росатомом в строительстве АЭС?", "options": ["5", "8", "12", "20"], "correct_option_index": 2, "explanation": "Росатом реализует проекты АЭС в 12 странах мира."},
        {"text": "Как называется первая в мире плавучая атомная станция?", "options": ["ПАТЭС «Ломоносов»", "«Академик Ломоносов»", "«Севморатом»", "«Арктическая энергия»"], "correct_option_index": 1, "explanation": "Плавучая АЭС носит имя Михаила Ломоносова."},
    ],
    "en": [
        {"text": "In what year was Rosatom State Corporation established?", "options": ["2000", "2007", "2010", "1995"], "correct_option_index": 1, "explanation": "Rosatom was established by presidential decree in 2007."},
        {"text": "How many countries collaborate with Rosatom in nuclear power plant construction?", "options": ["5", "8", "12", "20"], "correct_option_index": 2, "explanation": "Rosatom is building NPPs in 12 countries worldwide."},
        {"text": "What is the name of the world's first floating nuclear power plant?", "options": ["FNPP Lomonosov", "Akademik Lomonosov", "Sevmoratom", "Arctic Energy"], "correct_option_index": 1, "explanation": "The floating NPP is named after Mikhail Lomonosov."},
    ]
}

# ============== QR ==============
def generate_qr(url):
    qr = qrcode.QRCode(
        version=5,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert('RGB')

# ============== БОТ ==============
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

class QuizStates(StatesGroup):
    choosing_language = State()
    entering_name = State()
    entering_email = State()
    confirming_consent = State()
    answering = State()

TEXTS = {
    "ru": {
        "start": "⚛️ Добро пожаловать в опрос о Росатоме!\n\nВыберите язык:",
        "name_prompt": "📝 Пожалуйста, укажите ваше имя:",
        "email_prompt": "📧 Укажите ваш email:",
        "consent": '🛡️ Нажимая «Подтверждаю», вы даёте согласие на обработку персональных данных в соответствии с <a href="https://rosatom.ru/privacy">политикой конфиденциальности</a>.',
        "already_done": "Вы уже прошли опрос. Спасибо за интерес к Росатому!",
        "quiz_start": "Вопрос {num} из 3:\n\n{question}",
        "correct": "✅ Верно!",
        "incorrect": "❌ Неверно.\nПравильный ответ: <b>{answer}</b>",
        "explanation": "\nℹ️ {explanation}",
        "final": "🎉 Поздравляем! Вы ответили правильно на <b>{score}</b> из 3 вопросов.",
        "qr_text": "Отсканируйте QR-код, чтобы узнать больше о Росатоме:",
        "error_saving": "⚠️ Результат не сохранен из-за технической ошибки."
    },
    "en": {
        "start": "⚛️ Welcome to the Rosatom quiz!\n\nChoose your language:",
        "name_prompt": "📝 Please enter your first name:",
        "email_prompt": "📧 Please provide your email:",
        "consent": '🛡️ By clicking "I Agree", you consent to the processing of personal data in accordance with the <a href="https://rosatom.ru/privacy">privacy policy</a>.',
        "already_done": "You've already completed the quiz. Thank you for your interest in Rosatom!",
        "quiz_start": "Question {num} out of 3:\n\n{question}",
        "correct": "✅ Correct!",
        "incorrect": "❌ Incorrect.\nCorrect answer: <b>{answer}</b>",
        "explanation": "\nℹ️ {explanation}",
        "final": "🎉 Congratulations! You answered <b>{score}</b> out of 3 questions correctly.",
        "qr_text": "Scan the QR code to learn more about Rosatom:",
        "error_saving": "⚠️ Result not saved due to technical error."
    }
}

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton("🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]
    ])

def consent_kb(lang):
    txt = "✅ Подтверждаю" if lang == "ru" else "✅ I Agree"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(txt, callback_data="consent_yes")]])

def opts_kb(opts, lang):
    letters = ["A", "B", "C", "D"]
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(f"{letters[i]}) {opt}", callback_data=f"ans_{i}")] for i, opt in enumerate(opts)
    ])

# ============== ХЕНДЛЕРЫ ==============
@dp.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    uid = message.from_user.id
    try:
        if user_exists(uid):
            await message.answer(TEXTS["ru"]["already_done"])
            return
    except Exception as e:
        logging.error(f"Error checking user: {e}")
        # Продолжаем даже если проверка не удалась
        await message.answer("⚠️ Временные технические неполадки. Начинаем опрос...")
        
    await state.set_state(QuizStates.choosing_language)
    await message.answer(TEXTS["ru"]["start"], reply_markup=lang_kb())

@dp.callback_query(F.data.startswith("lang_"))
async def lang_cb(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_", 1)[1]
    await state.update_data(language=lang)
    await state.set_state(QuizStates.entering_name)
    await callback.message.edit_text(TEXTS[lang]["name_prompt"])
    await callback.answer()

@dp.message(QuizStates.entering_name)
async def name_msg(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2:
        lang = (await state.get_data()).get("language", "ru")
        await message.answer(TEXTS[lang]["name_prompt"])
        return
    await state.update_data(name=name)
    lang = (await state.get_data())["language"]
    await state.set_state(QuizStates.entering_email)
    await message.answer(TEXTS[lang]["email_prompt"])

@dp.message(QuizStates.entering_email)
async def email_msg(message: Message, state: FSMContext):
    email = message.text.strip()
    if "@" not in email or "." not in email:
        lang = (await state.get_data())["language"]
        await message.answer(TEXTS[lang]["email_prompt"])
        return
    await state.update_data(email=email)
    lang = (await state.get_data())["language"]
    await state.set_state(QuizStates.confirming_consent)
    await message.answer(TEXTS[lang]["consent"], reply_markup=consent_kb(lang))

@dp.callback_query(F.data == "consent_yes")
async def consent_cb(callback: CallbackQuery, state: FSMContext):
    await state.update_data(answers=[], current_q=0)
    await state.set_state(QuizStates.answering)
    await send_question(callback.message, state)
    await callback.answer()

async def send_question(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data["language"]
    q_idx = data.get("current_q", 0)
    if q_idx >= len(QUESTIONS[lang]):
        await finish_quiz(message, state, lang)
        return
    q = QUESTIONS[lang][q_idx]
    txt = TEXTS[lang]["quiz_start"].format(num=q_idx + 1, question=q["text"])
    await message.answer(txt, reply_markup=opts_kb(q["options"], lang))

@dp.callback_query(F.data.startswith("ans_"))
async def answer_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data["language"]
    q_idx = data.get("current_q", 0)
    if q_idx >= len(QUESTIONS[lang]):
        await callback.answer()
        return
    sel = int(callback.data.split("_", 1)[1])
    q = QUESTIONS[lang][q_idx]
    correct = sel == q["correct_option_index"]
    correct_text = q["options"][q["correct_option_index"]]
    answers = data.get("answers", [])
    answers.append({"selected": sel, "correct": correct})
    await state.update_data(answers=answers, current_q=q_idx + 1)
    if correct:
        fb = TEXTS[lang]["correct"]
    else:
        fb = TEXTS[lang]["incorrect"].format(answer=correct_text)
    fb += TEXTS[lang]["explanation"].format(explanation=q["explanation"])
    await callback.message.edit_text(fb, reply_markup=None)
    await callback.answer()
    await asyncio.sleep(1.5)
    await send_question(callback.message, state)

async def finish_quiz(message: Message, state: FSMContext, lang: str):
    data = await state.get_data()
    score = sum(1 for a in data["answers"] if a["correct"])
    uid = message.from_user.id
    name = data["name"]
    email = data["email"]
    
    # Сохраняем результат
    success = append_result(uid, name, email, lang, score)
    
    if success:
        final_text = TEXTS[lang]["final"].format(score=score)
    else:
        final_text = f"{TEXTS[lang]['final'].format(score=score)}\n\n{TEXTS[lang]['error_saving']}"
    
    await message.answer(final_text)
    
    # Генерация QR-кода
    try:
        qr_img = generate_qr(TARGET_URL)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            qr_img.save(tmp.name)
            await message.answer_photo(FSInputFile(tmp.name), caption=TEXTS[lang]["qr_text"])
            os.unlink(tmp.name)
    except Exception as e:
        logging.error(f"Error generating QR code: {e}")
    
    await state.clear()

# ============== WEBHOOK + HEALTH ==============
async def health_check(request):
    return web.Response(text="OK", status=200)

async def on_startup(app):
    if WEBHOOK_URL:
        await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
        logging.info(f"Webhook set to {WEBHOOK_URL}")
    else:
        logging.info("Running in polling mode")

async def on_shutdown(app):
    if WEBHOOK_URL:
        await bot.delete_webhook()
    await bot.session.close()

def main():
    try:
        if not BOT_TOKEN:
            raise EnvironmentError("BOT_TOKEN environment variable is required")
        
        # Проверяем подключение к Google Sheets
        try:
            get_sheet()
            logging.info("✅ Google Sheets connection successful")
        except Exception as e:
            logging.error(f"❌ Google Sheets connection failed: {e}")
            raise
        
        app = web.Application()
        app.router.add_get("/health", health_check)
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
        
        logging.info(f"🚀 Starting bot on port {WEB_SERVER_PORT}")
        web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
        
    except Exception as e:
        logging.exception("❌ CRITICAL ERROR")
        raise

if __name__ == "__main__":
    main()