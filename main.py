import os
import json
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
GOOGLE_CREDENTIALS_JSON = '''
{
  "type": "service_account",
  "project_id": "rosatom-quiz-bot",
  "private_key_id": "562b02abc9d66a76ad8d6928bde2f17ba8fe48a6",
  "private_key": "-----BEGIN PRIVATE KEY-----\\nYOUR_PRIVATE_KEY_HERE\\n-----END PRIVATE KEY-----\\n",
  "client_email": "rosatom-bot@rosatom-quiz-bot.iam.gserviceaccount.com",
  "client_id": "123456789012345678901",
  "auth_uri": "https://accounts.google.com/o/oauth2/auth",
  "token_uri": "https://oauth2.googleapis.com/token",
  "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
  "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/rosatom-bot%40rosatom-quiz-bot.iam.gserviceaccount.com"
}
'''

SHEET_ID = "108345771575623727353"

def get_sheet():
    try:
        creds_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        creds = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).sheet1
        
        # Создаем заголовки если лист пустой
        if not sheet.get_all_records():
            sheet.append_row(["User ID", "Name", "Email", "Language", "Score", "Timestamp"])
        
        return sheet
    except Exception as e:
        logging.error(f"Google Sheets error: {e}")
        raise

def append_result(user_id, name, email, language, score):
    try:
        sheet = get_sheet()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([str(user_id), name, email, language, str(score), timestamp])
        logging.info(f"Result saved: {name}, score: {score}")
    except Exception as e:
        logging.error(f"Failed to save result: {e}")
        raise

def user_exists(user_id):
    try:
        sheet = get_sheet()
        ids = sheet.col_values(1)
        return str(user_id) in ids
    except Exception as e:
        logging.error(f"Error checking user existence: {e}")
        return False

# ============== ВОПРОСЫ ==============
QUESTIONS = {
    "ru": [
        {"text": "В каком году была создана Госкорпорация «Росатом»?", "options": ["2000", "2007", "2010", "1995"], "correct_option_index": 1, "explanation": "Росатом был образован указом Президента РФ в 2007 году."},
        {"text": "Сколько стран сотрудничают с Росатомом в строительстве АЭС (по состоянию на 2025 г.)?", "options": ["5", "8", "12", "20"], "correct_option_index": 2, "explanation": "Росатом реализует проекты АЭС в 12 странах мира."},
        {"text": "Как называется первая в мире плавучая атомная станция?", "options": ["ПАТЭС «Ломоносов»", "«Академик Ломоносов»", "«Севморатом»", "«Арктическая энергия»"], "correct_option_index": 1, "explanation": "Плавучая АЭС носит имя Михаила Ломоносова."},
        {"text": "Является ли Росатом лидером мирового рынка по обогащению урана?", "options": ["Нет", "Да, но только в Европе", "Да, контролирует ~40% рынка", "Нет, лидер — США"], "correct_option_index": 2, "explanation": "Росатом — крупнейший в мире поставщик услуг по обогащению урана."},
        {"text": "Входит ли ядерная медицина в сферу деятельности Росатома?", "options": ["Нет", "Только исследования", "Да, через дивизион «Русатом Хэлскеа»", "Только за рубежом"], "correct_option_index": 2, "explanation": "Росатом развивает ядерную медицину через специализированный дивизион."},
        {"text": "В каком международном проекте по термоядерному синтезу участвует Росатом?", "options": ["DEMO", "ITER", "FusionX", "SunCore"], "correct_option_index": 1, "explanation": "ITER — крупнейший международный проект по управляемому термояду."},
        {"text": "Есть ли у Росатома проекты в области водородной энергетики?", "options": ["Нет", "Только лабораторные", "Да, разрабатывает «зелёный» водород", "Только в партнёрстве с Китаем"], "correct_option_index": 2, "explanation": "Росатом активно развивает технологии производства «зелёного» водорода."},
        {"text": "Как называется корпоративный университет Росатома?", "options": ["АтомВУЗ", "Росатом Академия", "НИЯУ МИФИ", "ТехноАтом"], "correct_option_index": 1, "explanation": "«Росатом Академия» отвечает за обучение сотрудников и студентов."},
        {"text": "Где находится штаб-квартира Росатома?", "options": ["Санкт-Петербург", "Новосибирск", "Москва", "Димитровград"], "correct_option_index": 2, "explanation": "Центральный офис расположен в Москве."},
        {"text": "Какой дивизион Росатома отвечает за ветроэнергетику?", "options": ["Росэнерго", "НоваВинд", "АтомВетер", "Экоатом"], "correct_option_index": 1, "explanation": "Дивизион «НоваВинд» развивает ветроэнергетические проекты."},
        {"text": "Что такое проект «Прорыв»?", "options": ["Запуск спутников", "Замкнутый ядерный топливный цикл", "Строительство подлодок", "Медицинский проект"], "correct_option_index": 1, "explanation": "Проект «Прорыв» направлен на создание замкнутого ядерного топливного цикла."},
        {"text": "Есть ли у Росатома образовательные программы для школьников?", "options": ["Нет", "Только в Москве", "Да, например, «Атомный класс»", "Только для победителей олимпиад"], "correct_option_index": 2, "explanation": "Проекты вроде «Атомный класс» и «Кванториумы» работают по всей России."},
        {"text": "Кто возглавляет Росатом в 2025 году?", "options": ["Сергей Кириенко", "Алексей Лихачёв", "Дмитрий Медведев", "Игорь Сечин"], "correct_option_index": 1, "explanation": "Генеральный директор — Алексей Евгеньевич Лихачёв."},
        {"text": "Имеет ли Росатом собственные научные центры?", "options": ["Нет", "Только в Москве", "Да, например, в Димитровграде и Обнинске", "Только за рубежом"], "correct_option_index": 2, "explanation": "Научные центры Росатома расположены в нескольких городах России."},
        {"text": "Разрабатывает ли Росатом ядерные установки для космоса?", "options": ["Нет", "Только с 2030 года", "Да", "Только в теории"], "correct_option_index": 2, "explanation": "Росатом участвует в создании ядерных энергоустановок для космических аппаратов."},
        {"text": "Сколько энергоблоков АЭС построил Росатом за рубежом (по состоянию на 2025 г.)?", "options": ["10", "22", "37", "50"], "correct_option_index": 2, "explanation": "Росатом построил 37 реакторов в 12 странах."},
        {"text": "Есть ли у Росатома стартап-акселератор?", "options": ["Нет", "Только в Сколково", "Да, «StartRosatom»", "Только для сотрудников"], "correct_option_index": 2, "explanation": "Программа «StartRosatom» поддерживает инновационные проекты."},
        {"text": "Снижает ли атомная энергетика выбросы CO₂?", "options": ["Нет", "Да, значительно", "Только в Европе", "Это миф"], "correct_option_index": 1, "explanation": "АЭС не выбрасывают CO₂ при генерации электроэнергии."},
        {"text": "Производит ли Росатом оборудование для нефтегазовой отрасли?", "options": ["Нет", "Только для России", "Да, через дивизион «Русатом Аутдор»", "Только детали"], "correct_option_index": 2, "explanation": "Дивизион «Русатом Аутдор» поставляет оборудование для нефтегаза."},
        {"text": "Какова миссия Росатома?", "options": ["Максимальная прибыль", "Экспорт технологий", "Безопасная ядерная энергетика для устойчивого развития", "Военное превосходство"], "correct_option_index": 2, "explanation": "Миссия Росатома — обеспечение устойчивого развития через ядерные технологии."}
    ],
    "en": [
        {"text": "In what year was Rosatom State Corporation established?", "options": ["2000", "2007", "2010", "1995"], "correct_option_index": 1, "explanation": "Rosatom was established by presidential decree in 2007."},
        {"text": "How many countries collaborate with Rosatom in nuclear power plant construction (as of 2025)?", "options": ["5", "8", "12", "20"], "correct_option_index": 2, "explanation": "Rosatom is building NPPs in 12 countries worldwide."},
        {"text": "What is the name of the world's first floating nuclear power plant?", "options": ["FNPP Lomonosov", "Akademik Lomonosov", "Sevmoratom", "Arctic Energy"], "correct_option_index": 1, "explanation": "The floating NPP is named after Mikhail Lomonosov."},
        {"text": "Is Rosatom the global leader in uranium enrichment?", "options": ["No", "Yes, but only in Europe", "Yes, controls ~40% of the market", "No, the USA is the leader"], "correct_option_index": 2, "explanation": "Rosatom is the world's largest uranium enrichment service provider."},
        {"text": "Does nuclear medicine fall under Rosatom's activities?", "options": ["No", "Research only", "Yes, via Rusatom Healthcare", "Only abroad"], "correct_option_index": 2, "explanation": "Rosatom develops nuclear medicine through a dedicated division."},
        {"text": "Which international fusion project does Rosatom participate in?", "options": ["DEMO", "ITER", "FusionX", "SunCore"], "correct_option_index": 1, "explanation": "ITER is the world's largest fusion energy project."},
        {"text": "Does Rosatom have hydrogen energy projects?", "options": ["No", "Lab-scale only", "Yes, develops green hydrogen", "Only with China"], "correct_option_index": 2, "explanation": "Rosatom actively develops green hydrogen production technologies."},
        {"text": "What is the name of Rosatom's corporate university?", "options": ["AtomUni", "Rosatom Academy", "MEPhI", "TechAtom"], "correct_option_index": 1, "explanation": "Rosatom Academy trains employees and students."},
        {"text": "Where is Rosatom's headquarters located?", "options": ["Saint Petersburg", "Novosibirsk", "Moscow", "Dimitrovgrad"], "correct_option_index": 2, "explanation": "The central office is in Moscow."},
        {"text": "Which Rosatom division is responsible for wind energy?", "options": ["RosEnergo", "NovaWind", "AtomWind", "EcoAtom"], "correct_option_index": 1, "explanation": "NovaWind develops wind energy projects."},
        {"text": "What is the 'Breakthrough' project?", "options": ["Satellite launch", "Closed nuclear fuel cycle", "Submarine construction", "Medical program"], "correct_option_index": 1, "explanation": "The Breakthrough project aims to create a closed nuclear fuel cycle."},
        {"text": "Does Rosatom run educational programs for schoolchildren?", "options": ["No", "Only in Moscow", "Yes, e.g., Atom Class", "Only for Olympiad winners"], "correct_option_index": 2, "explanation": "Programs like Atom Class and Quantoriums operate across Russia."},
        {"text": "Who leads Rosatom in 2025?", "options": ["Sergey Kiriyenko", "Alexey Likhachev", "Dmitry Medvedev", "Igor Sechin"], "correct_option_index": 1, "explanation": "CEO: Alexey Yevgenyevich Likhachev."},
        {"text": "Does Rosatom have its own research centers?", "options": ["No", "Only in Moscow", "Yes, e.g., in Dimitrovgrad and Obninsk", "Only abroad"], "correct_option_index": 2, "explanation": "Rosatom's research centers are located in several Russian cities."},
        {"text": "Does Rosatom develop nuclear power systems for space?", "options": ["No", "Only from 2030", "Yes", "Only in theory"], "correct_option_index": 2, "explanation": "Rosatom participates in creating nuclear power systems for spacecraft."},
        {"text": "How many NPP units has Rosatom built abroad (as of 2025)?", "options": ["10", "22", "37", "50"], "correct_option_index": 2, "explanation": "Rosatom has built 37 reactor units in 12 countries."},
        {"text": "Does Rosatom have a startup accelerator?", "options": ["No", "Only in Skolkovo", "Yes, StartRosatom", "Only for employees"], "correct_option_index": 2, "explanation": "StartRosatom supports innovation projects."},
        {"text": "Does nuclear power reduce CO₂ emissions?", "options": ["No", "Yes, significantly", "Only in Europe", "It's a myth"], "correct_option_index": 1, "explanation": "NPPs produce electricity without CO₂ emissions."},
        {"text": "Does Rosatom produce oil & gas equipment?", "options": ["No", "Only for Russia", "Yes, via Rusatom Overseas", "Only parts"], "correct_option_index": 2, "explanation": "Rusatom Overseas supplies equipment to the oil & gas industry."},
        {"text": "What is Rosatom's mission?", "options": ["Maximize profit", "Export technologies", "Safe nuclear energy for sustainable development", "Military dominance"], "correct_option_index": 2, "explanation": "Rosatom's mission is to enable sustainable development through nuclear technologies."}
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
        "quiz_start": "Вопрос {num} из 20:\n\n{question}",
        "correct": "✅ Верно!",
        "incorrect": "❌ Неверно.\nПравильный ответ: <b>{answer}</b>",
        "explanation": "\nℹ️ {explanation}",
        "final": "Вы ответили правильно на <b>{score}</b> из 20 вопросов.",
        "qr_text": "Отсканируйте QR-код, чтобы узнать больше о Росатоме:"
    },
    "en": {
        "start": "⚛️ Welcome to the Rosatom quiz!\n\nChoose your language:",
        "name_prompt": "📝 Please enter your first name:",
        "email_prompt": "📧 Please provide your email:",
        "consent": '🛡️ By clicking "I Agree", you consent to the processing of personal data in accordance with the <a href="https://rosatom.ru/privacy">privacy policy</a>.',
        "already_done": "You've already completed the quiz. Thank you for your interest in Rosatom!",
        "quiz_start": "Question {num} out of 20:\n\n{question}",
        "correct": "✅ Correct!",
        "incorrect": "❌ Incorrect.\nCorrect answer: <b>{answer}</b>",
        "explanation": "\nℹ️ {explanation}",
        "final": "You answered <b>{score}</b> out of 20 questions correctly.",
        "qr_text": "Scan the QR code to learn more about Rosatom:"
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
        await message.answer("⚠️ Временные технические неполадки. Попробуйте начать опрос через /start")
        return
        
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
    if q_idx >= 20:
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
    if q_idx >= 20:
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
    
    try:
        append_result(uid, name, email, lang, score)
        await message.answer(TEXTS[lang]["final"].format(score=score))
        
        # Генерация QR-кода
        qr_img = generate_qr(TARGET_URL)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            qr_img.save(tmp.name)
            await message.answer_photo(FSInputFile(tmp.name), caption=TEXTS[lang]["qr_text"])
            os.unlink(tmp.name)
            
    except Exception as e:
        logging.error(f"Error finishing quiz: {e}")
        await message.answer(f"✅ Викторина завершена! Ваш результат: {score}/20\n\n⚠️ Результат не сохранен из-за технической ошибки.")
    
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
        # Тестируем подключение к Google Sheets при старте
        get_sheet()
        logging.info("✅ Google Sheets connection successful")
        
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