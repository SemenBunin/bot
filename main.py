import os
import logging
import asyncio
import tempfile
import random
from datetime import datetime
from aiohttp import web
import ipaddress

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

# ============== НАСТРОЙКИ ==============
logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{RENDER_EXTERNAL_URL}{WEBHOOK_PATH}" if RENDER_EXTERNAL_URL else None
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("PORT", 8000))
TARGET_URL = "https://rosatom.ru"

# ============== IP-ФИЛЬТР ДЛЯ UPTIMEROBOT ==============
async def ip_middleware(app, handler):
    async def middleware_handler(request):
        # Разрешенные IP-адреса UptimeRobot (Северная Америка)
        allowed_ips = [
            "34.210.0.0/17",
            "35.155.0.0/16", 
            "52.11.0.0/16",
            "52.24.0.0/16",
            "54.68.0.0/16",
            "54.70.0.0/16",
            "54.189.0.0/16",
            "34.215.0.0/16",
            "35.163.0.0/16",
            "52.32.0.0/16",
            "52.36.0.0/16",
            "52.88.0.0/16",
            "54.148.0.0/16",
            "54.184.0.0/16",
            "54.200.0.0/16",
            "54.218.0.0/16"
        ]
        
        client_ip = request.headers.get('X-Forwarded-For', request.remote)
        
        # Если это запрос к /ping - проверяем IP
        if request.path == '/ping':
            ip_allowed = False
            try:
                for ip_range in allowed_ips:
                    if ipaddress.ip_address(client_ip) in ipaddress.ip_network(ip_range):
                        ip_allowed = True
                        break
            except Exception as e:
                logging.warning(f"⚠️ IP check error for {client_ip}: {e}")
                ip_allowed = True  # В случае ошибки разрешаем доступ
            
            if not ip_allowed:
                logging.warning(f"⛔ Blocked ping from unauthorized IP: {client_ip}")
                return web.Response(text="Unauthorized", status=403)
        
        return await handler(request)
    
    return middleware_handler

# ============== GOOGLE SHEETS КОНФИГУРАЦИЯ ==============
def get_sheet():
    try:
        # Читаем credentials из Secret Files
        creds_path = "/etc/secrets/google-credentials.json"
        if not os.path.exists(creds_path):
            logging.error("❌ Google credentials file not found in /etc/secrets/")
            raise FileNotFoundError("Google credentials file not found")
        
        logging.info(f"✅ Using credentials from: {creds_path}")
        creds = Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(creds)
        
        # ПРАВИЛЬНЫЙ ID ТАБЛИЦЫ
        SHEET_ID = "1Nc3nDzPyie0qgOwHRn4QuHeGBRhXA5L5sJ4SGqaFFJ8"
        sheet = client.open_by_key(SHEET_ID).sheet1
        
        # Создаем заголовки если лист пустой
        if not sheet.get_all_records():
            sheet.append_row(["User ID", "Name", "Email", "Language", "Category", "Difficulty", "Score", "Timestamp"])
        
        logging.info("✅ Google Sheets connection successful")
        return sheet
        
    except Exception as e:
        logging.error(f"❌ Google Sheets error: {e}")
        raise

def append_result(user_id, name, email, language, category, difficulty, score):
    try:
        sheet = get_sheet()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sheet.append_row([str(user_id), name, email, language, category, difficulty, str(score), timestamp])
        logging.info(f"✅ Result saved: {name}, category: {category}, score: {score}")
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
    "ru": {
        "Экологическое просвещение": {
            "Полегче": [
                {"text": "Что такое экосистема?", "options": [
                    "Часть бизнес-стратегии", 
                    "Совокупность совместно обитающих организмов и условий их существования, находящихся в закономерной взаимосвязи друг с другом", 
                    "Группа людей, которые занимаются охраной окружающей среды", 
                    "Система, основанная на учете потребностей природы"
                ], "correct_option_index": 1, "explanation": "Экосистема - это буквально всё, что нас окружает. Мы, люди, так же являемся частью местной экосистемы, поскольку находимся во взаимодействии с другими живыми организмами и природой."},
                
                {"text": "Невозобновляемые ресурсы - это ресурсы, которые...", "options": [
                    "Восполняются самостоятельно в течение короткого периода времени", 
                    "Которые получаются путем переработки отходов и их использования в промышленности", 
                    "Невозможно восстановить без определенных технологий", 
                    "Которые исчерпываются в результате их длительной добычи и использования"
                ], "correct_option_index": 3, "explanation": "Невозобновляемые ресурсы планеты имеют конечный запас. Яркий пример - ископаемое топливо (нефть, уголь, газ), применяемое в энергетике. Подбирая альтернативные источники выработки энергии, мы уменьшаем негативное воздействие на окружающую среду."},
                
                {"text": "Сколько экопривычек следует применять среднестатистическому горожанину?", "options": [
                    "от 10 до 15", 
                    "Всё индивидуально", 
                    "от 30 до 40", 
                    "от 20 до 30"
                ], "correct_option_index": 1, "explanation": "Есть много примеров, как сделать свою жизнь экологичнее, но это не означает, что существуют стандарты, сколько у каждого должно быть привычек. Для того чтобы закрепить экологичный паттерн поведения и встроить в жизнь, важно подбирать привычки исходя из образа жизни."},
                
                {"text": "Что собой подразумевает день экологического долга?", "options": [
                    "День, когда все жители планеты останавливают использование любых природных ресурсов",
                    "День, когда наступает глобальный кризис в экологии и человечество сталкивается с необратимыми последствиями",
                    "Международный день обращения внимания на потребность в экологически устойчивом поведении и уменьшении углеродного следа",
                    "День в году, когда человечество исчерпывает ресурсы, способные восстановиться за один год, и начинает использовать ресурсы будущих поколений"
                ], "correct_option_index": 3, "explanation": "Да, оказывается, мы можем жить в долг у ресурсов планеты! Дата рассчитывается независимым аналитическом центром Global Footprint Network по средним показателям всех стран."},
                
                {"text": "Почему бумажные стаканчики не промокают?", "options": [
                    "Из-за двойного бумажного слоя", 
                    "Из-за слоя полиэтилена", 
                    "Из-за слоя поливинилхлорида"
                ], "correct_option_index": 1, "explanation": "К счастью, поливинилхлорид (ПВХ) тут не при чем, зато тонкий слой полиэтилена является тем самым защитным слоем, который препятствует размоканию бумажных стаканов! Однако, именно он и препятствует их переработке."},
                
                {"text": "Если бы мы отдельно сортировали этот вид отходов, у нас было бы меньше проблем с переработкой, а также уменьшилось бы выделение метана в атмосферу", "options": [
                    "Пластик", 
                    "Бумага", 
                    "Металл", 
                    "Пищевые отходы"
                ], "correct_option_index": 3, "explanation": "Из-за пищевых отходов больше сложностей, чем может показаться! Они могут препятствовать переработке вторичного сырья из-за загрязнения, а на свалках в процессе разложения являются причиной выделения метана в атмосферу."}
            ],
            "Посложнее": [
                {"text": "Какой объём космических отходов сейчас на орбитах?", "options": [
                    "До 30 тысяч единиц", 
                    "До 1млн единиц", 
                    "До 15млн единиц", 
                    "До 130млн единиц"
                ], "correct_option_index": 3, "explanation": "Невероятно, но факт. По средним оценкам, на данный момент на различных орбитах находится 120-130млн обломков малых и средних размеров."},
                
                {"text": "Какой невозобновляемый природный ресурс является одним из самых добываемых в мире?", "options": [
                    "Золото", 
                    "Железная руда", 
                    "Песок", 
                    "Уголь"
                ], "correct_option_index": 2, "explanation": "Помимо того, что песок является одним из самых распространенных природных ресурсов на Земле и используется во многих отраслях, его ресурсы начинают заканчиваться!"},
                
                {"text": "Что является главным производителем кислорода?", "options": [
                    "Деревья", 
                    "Небольшие растения", 
                    "Воздух", 
                    "Фитопланктон"
                ], "correct_option_index": 3, "explanation": "Именно эти морские микроорганизмы являются производителем от 40 до 60% кислорода на Земле! Конечно, все земные растения тоже вырабатывают кислород, но в меньших масштабах."},
                
                {"text": "Что такое экономика замкнутого цикла?", "options": [
                    "Экономика, где цепочка «добыча ресурсов — производство — потребление — утилизация» замкнута в непрерывный возобновляемый цикл",
                    "Модель, в которой доходы и расходы организаций взаимно связаны между собой",
                    "Вид экономической системы, характеризующийся периодическими колебаниями национального производства, доходов и занятости",
                    "Модель, при которой не происходит никаких колебаний национального производства и занятости, а все системы работают стабильно без изменений"
                ], "correct_option_index": 0, "explanation": "Её ещё называют 'экономикой будущего', поскольку она является альтернативой линейной экономике, при которой жизненный цикл товара не учитывает его вторичное использование."},
                
                {"text": "На этот вид отходов приходится до 10% мирового выброса углекислого газа - больше, чем все международные рейсы и перевозки вместе взятые", "options": [
                    "Бытовые отходы", 
                    "Отходы ресторанной деятельности", 
                    "Отходы производства автомобилей", 
                    "Отходы производства одежды"
                ], "correct_option_index": 3, "explanation": "Именно такую цифру предоставила Программа ООН по окружающей среде, в рамках которой учитывается не только одежда в виде бытового отхода, но и весь цикл производства."},
                
                {"text": "Это явление в океане иногда называют 'новым континентом', однако оно не имеет точных границ и существенно вредит морской экосистеме. Что это?", "options": [
                    "Размножение фитопланктонов", 
                    "Место с захоронением кораблей, вышедших из строя", 
                    "Большое тихоокеанское мусорное пятно", 
                    "Старые нефтяные платформы"
                ], "correct_option_index": 2, "explanation": "Название - серьёзное, ситуация - неприятная. Это централизованное скопление мусора с различных континентов в верхних слоях Тихого океана."}
            ]
        },
        "Природа России": {
            "Полегче": [
                {"text": "Как называется самая длинная река России?", "options": [
                    "Волга", 
                    "Лена", 
                    "Обь", 
                    "Амур"
                ], "correct_option_index": 1, "explanation": "Лена — крупнейшая из рек, чей бассейн полностью лежит в пределах России. Её длина составляет около 4400км, протекая через всю Иркутскую область и Якутию."},
                
                {"text": "Какая глубина у озера Байкал?", "options": [
                    "1100 метров", 
                    "2050 метров", 
                    "898 метров", 
                    "1642 метра"
                ], "correct_option_index": 3, "explanation": "Помимо того, что озеро Байкал является самым глубоким озером в мире, это также и крупнейшее пресноводное озеро, в котором сосредоточено до 19% мирового запаса пресной воды."},
                
                {"text": "Как называется самая высокая горная вершина России?", "options": [
                    "Эльбрус", 
                    "Белуха", 
                    "Пик Шота Руставели", 
                    "Казбек"
                ], "correct_option_index": 0, "explanation": "Эльбрус является самой высокой точкой России с двумя вершинами-пятетысячниками: 5642 и 5621м."},
                
                {"text": "Сколько субъектов насчитывает Российская Федерация?", "options": [
                    "64", 
                    "89", 
                    "80", 
                    "91"
                ], "correct_option_index": 1, "explanation": "Российская Федерация состоит из 89 субъектов."},
                
                {"text": "Как называется крупнейший город за полярным кругом?", "options": [
                    "Апатиты", 
                    "Мурманск", 
                    "Североморск", 
                    "Тромсё"
                ], "correct_option_index": 1, "explanation": "Мурманск считается самым крупным городом в мире за полярным кругом с численностью населения в 267 тысяч человек."},
                
                {"text": "Что такое золотое кольцо России?", "options": [
                    "Туристический маршрут, проходящий через несколько исторических городов Золотого кольца Центральной России",
                    "Термин, используемый для обозначения круговой системы дорог, соединяющих города-миллионники России",
                    "Ежегодный фестиваль ремесленников, проходящий на территории Ярославской области",
                    "Название российского футбольного турнира, который проводится среди сильнейших команд из разных регионов страны"
                ], "correct_option_index": 0, "explanation": "Золотое кольцо России — туристический маршрут, проходящий по древним городам-центрам народных ремёсел."}
            ],
            "Посложнее": [
                {"text": "Какой город России считается самым холодным?", "options": [
                    "Мурманск", 
                    "Верхоянск", 
                    "Норильск", 
                    "Воркута"
                ], "correct_option_index": 1, "explanation": "В 1982 году гидрометеостанция в Верхоянске зафиксировала абсолютный температурный минимум — 67,8 градуса ниже нуля."},
                
                {"text": "В какой части России расположено наибольшее число геотермальных электростанций?", "options": [
                    "Алтайский край", 
                    "Камчатский край", 
                    "Краснодарский край", 
                    "Дагестан"
                ], "correct_option_index": 1, "explanation": "Геотермальная энергетика - перспективное направление. Камчатский край славится Долиной Гейзеров."},
                
                {"text": "Как называется самый северный природный заповедник России, известный как 'дом белых медведей'?", "options": [
                    "Остров Врангеля", 
                    "Кузнецкий Алатау", 
                    "Кивач", 
                    "Тебердинский заповедник"
                ], "correct_option_index": 0, "explanation": "Заповедник 'Остров Врангеля' расположен в районе Чукотского моря и является ключевым местом обитания белого медведя."},
                
                {"text": "Многие называют этот парк 'птичьим мостом', так как он является частью миграционного пути десятка миллионов птиц ежегодно.", "options": [
                    "Остров Врангеля", 
                    "Куршская коса", 
                    "Берег Азовского моря", 
                    "Григорьевская коса"
                ], "correct_option_index": 1, "explanation": "Местные называют Куршскую косу 'птичьим мостом' не просто так - в пик миграции на её территории фиксируется от 1.5 до 2млн особей в день."},
                
                {"text": "Семь великанов, мансийские болваны и священная гора. О чём идет речь?", "options": [
                    "Маньпупунёр", 
                    "Стоунхендж", 
                    "Плато Бермамыт", 
                    "Плато Путорана"
                ], "correct_option_index": 0, "explanation": "Уникальные по своей природе столбы выветривания Манпупунёр на Северном Урале ежегодно привлекают внимание десятков тысяч туристов."},
                
                {"text": "Каким был первый российский природный объект, включенный в список объектов Всемирного природного наследия ЮНЕСКО?", "options": [
                    "Куршская коса", 
                    "Золотые горы Алтая", 
                    "Озеро Байкал", 
                    "Девственные леса Коми"
                ], "correct_option_index": 3, "explanation": "Девственные леса Коми - нетронутые леса, простирающиеся на территории 32 600 км²."}
            ]
        },
        "Атомная промышленность": {
            "Полегче": [
                {"text": "Этот город – база атомного ледокольного флота России", "options": [
                    "Мурманск", 
                    "Санкт-Петербург", 
                    "Владивосток", 
                    "Архангельск"
                ], "correct_option_index": 0, "explanation": "Мурманск является базой атомного ледокольного флота России."},
                
                {"text": "Из соображений секретности здание первой в мире Обнинской АЭС построили похожим на…", "options": [
                    "Цирк", 
                    "Библиотеку", 
                    "Кафе", 
                    "Жилой дом"
                ], "correct_option_index": 3, "explanation": "Первая в мире АЭС в Обнинске была построена похожей на жилой дом из соображений секретности."},
                
                {"text": "Правда или миф? На Земле скоро иссякнет запас урана", "options": [
                    "Правда", 
                    "Миф"
                ], "correct_option_index": 1, "explanation": "Это миф! Урана на нашей планете в 600 раз больше, чем золота. Эксперты считают, что его хватит еще на пятьсот лет."},
                
                {"text": "Какое количество выбросов CO2 предотвращает работа российских АЭС, построенных в мире в настоящий момент?", "options": [
                    "20 млн тонн", 
                    "95 млн тонн", 
                    "145 млн тонн", 
                    "217 млн тонн"
                ], "correct_option_index": 3, "explanation": "Российские АЭС предотвращают выброс 217 млн тонн CO2."},
                
                {"text": "Где построена первая в мире АЭС?", "options": [
                    "Россия", 
                    "Япония", 
                    "Франция"
                ], "correct_option_index": 0, "explanation": "В мае 1950 года в Обнинске (Калужская область) началось строительство первой в мире АЭС."},
                
                {"text": "Правда или миф? Вся радиация — вредная", "options": [
                    "Правда", 
                    "Миф"
                ], "correct_option_index": 1, "explanation": "А вот и нет! На самом деле человека всегда окружает радиационный фон. Но не вся радиация опасна, вопрос в дозах излучения."}
            ],
            "Посложнее": [
                {"text": "По каким морям проходит Северный морской путь?", "options": [
                    "Карское море, море Лаптевых, Восточно-Сибирское море, Чукотское море, Берингово море",
                    "Море Бофорта, море Линкольна, Гренландское море",
                    "Баренцево море, Белое море, Охотское море",
                    "Балтийское море, Черное море, Каспийское море"
                ], "correct_option_index": 0, "explanation": "Северный морской путь проходит по Карскому морю, морю Лаптевых, Восточно-Сибирскому морю, Чукотскому морю и Берингову морю."},
                
                {"text": "Это первая в мире атомная электростанция, расположенная в зоне вечной мерзлоты", "options": [
                    "Белоярская АЭС", 
                    "Балтийская АЭС", 
                    "Балаковская АЭС", 
                    "Билибинская АЭС"
                ], "correct_option_index": 3, "explanation": "Билибинская АЭС - первая в мире атомная электростанция, расположенная в зоне вечной мерзлоты."},
                
                {"text": "Выберите, какими крупными проектами в сфере ликвидации накопленного вреда занимается Росатом?", "options": [
                    "Городская свалка в Челябинске, Промышленная площадка в г. Усолье-Сибирское, ОАО «Байкальский целлюлозно-бумажный комбинат», Полигон 'Красный бор'",
                    "Мусорный полигон 'Ядрово', Промышленная площадка в г. Кемерово",
                    "Все перечисленные варианты",
                    "Ни один из перечисленных вариантов"
                ], "correct_option_index": 0, "explanation": "Росатом занимается ликвидацией накопленного вреда на объектах: Городская свалка в Челябинске, Промышленная площадка в г. Усолье-Сибирское, ОАО «Байкальский целлюлозно-бумажный комбинат», Полигон 'Красный бор'."},
                
                {"text": "Какой элемент лишний? Выберите один вариант:", "options": [
                    "Индий", 
                    "Уран", 
                    "Литий", 
                    "Радий", 
                    "Барий"
                ], "correct_option_index": 2, "explanation": "Лишний элемент — литий, он единственный в этой компании не радиоактивный."},
                
                {"text": "Правда или миф? Рядом с АЭС опасно возводить жилые дома.", "options": [
                    "Правда", 
                    "Миф"
                ], "correct_option_index": 1, "explanation": "Мы живем при постоянном радиационном фоне. Возле АЭС жить не опасно, уже на расстоянии 80 км человек получает дозу облучения 0,01 миллизиверта в год."},
                
                {"text": "В состав Росатома входит единственная в мире плавучая атомная станция. Чем она занимается?", "options": [
                    "Снабжает электроэнергией порт Певек на Чукотке",
                    "Служит перевалочным пунктом для кораблей Атомфлота",
                    "Используется для продажи электроэнергии другим странам",
                    "Служит плавучей стоянкой для ледоколов"
                ], "correct_option_index": 0, "explanation": "Единственную в мире плавучую атомную станцию «Академик Ломоносов» снабжает электроэнергией Чукотский автономный округ, в первую очередь город Певек."}
            ]
        }
    },
    "en": {
        "Environmental Education": {
            "Easy": [
                {"text": "What is an ecosystem?", "options": [
                    "Part of a business strategy",
                    "A set of cohabiting organisms and their living conditions that are in a natural relationship with each other",
                    "A group of people engaged in environmental protection",
                    "A system based on accounting for the needs of nature"
                ], "correct_option_index": 1, "explanation": "An ecosystem is literally everything that surrounds us. We humans are also part of the local ecosystem as we interact with other living organisms and nature."},
                
                {"text": "Non-renewable resources are resources that...", "options": [
                    "Replenish themselves within a short period of time",
                    "Are obtained by recycling waste and using it in industry",
                    "Cannot be restored without certain technologies",
                    "Are depleted as a result of their long-term extraction and use"
                ], "correct_option_index": 3, "explanation": "The planet's non-renewable resources have a finite supply. A prime example is fossil fuels (oil, coal, gas) used in energy."},
                
                {"text": "How many eco-habits should an average city dweller apply?", "options": [
                    "from 10 to 15",
                    "Everything is individual",
                    "from 30 to 40",
                    "from 20 to 30"
                ], "correct_option_index": 1, "explanation": "There are many examples of how to make your life more eco-friendly, but this does not mean that there are standards for how many habits each person should have."},
                
                {"text": "What does Ecological Debt Day imply?", "options": [
                    "The day when all inhabitants of the planet stop using any natural resources",
                    "The day when a global ecological crisis occurs and humanity faces irreversible consequences",
                    "International day to draw attention to the need for environmentally sustainable behavior and reducing carbon footprint",
                    "The day of the year when humanity exhausts the resources capable of recovering in one year and begins to use the resources of future generations"
                ], "correct_option_index": 3, "explanation": "Yes, it turns out we can live on credit from the planet's resources! The date is calculated by the independent analytical center Global Footprint Network."},
                
                {"text": "Why don't paper cups get wet?", "options": [
                    "Due to the double paper layer",
                    "Due to the polyethylene layer",
                    "Due to the polyvinyl chloride layer"
                ], "correct_option_index": 1, "explanation": "Fortunately, polyvinyl chloride (PVC) has nothing to do with it, but a thin layer of polyethylene is the very protective layer that prevents paper cups from getting wet!"},
                
                {"text": "If we separately sorted this type of waste, we would have fewer problems with recycling, and methane emissions into the atmosphere would also decrease", "options": [
                    "Plastic",
                    "Paper",
                    "Metal",
                    "Food waste"
                ], "correct_option_index": 3, "explanation": "Food waste causes more difficulties than it might seem! They can interfere with the recycling of secondary raw materials due to contamination."}
            ],
            "Difficult": [
                {"text": "What volume of space debris is currently in orbit?", "options": [
                    "Up to 30 thousand units",
                    "Up to 1 million units",
                    "Up to 15 million units",
                    "Up to 130 million units"
                ], "correct_option_index": 3, "explanation": "Incredible but true. According to average estimates, there are currently 120-130 million small and medium-sized debris in various orbits."},
                
                {"text": "Which non-renewable natural resource is one of the most mined in the world?", "options": [
                    "Gold",
                    "Iron ore",
                    "Sand",
                    "Coal"
                ], "correct_option_index": 2, "explanation": "In addition to being one of the most common natural resources on Earth and used in many industries, sand resources are starting to run out!"},
                
                {"text": "What is the main producer of oxygen?", "options": [
                    "Trees",
                    "Small plants",
                    "Air",
                    "Phytoplankton"
                ], "correct_option_index": 3, "explanation": "It is these marine microorganisms that produce 40 to 60% of oxygen on Earth! Of course, all terrestrial plants also produce oxygen, but on a smaller scale."},
                
                {"text": "What is a circular economy?", "options": [
                    "An economy where the chain 'resource extraction — production — consumption — disposal' is closed in a continuous renewable cycle",
                    "A model in which the income and expenses of organizations are mutually related to each other",
                    "A type of economic system characterized by periodic fluctuations in national production, income and employment",
                    "A model in which there are no fluctuations in national production and employment, and all systems work stably without changes"
                ], "correct_option_index": 0, "explanation": "It is also called the 'economy of the future' because it is an alternative to the linear economy."},
                
                {"text": "This type of waste accounts for up to 10% of global carbon dioxide emissions - more than all international flights and transportation combined", "options": [
                    "Household waste",
                    "Restaurant waste",
                    "Car production waste",
                    "Clothing production waste"
                ], "correct_option_index": 3, "explanation": "This figure was provided by the UN Environment Programme, which takes into account not only clothing as household waste, but the entire production cycle."},
                
                {"text": "This phenomenon in the ocean is sometimes called the 'new continent', but it has no clear boundaries and significantly harms the marine ecosystem. What is it?", "options": [
                    "Phytoplankton reproduction",
                    "A place with the burial of decommissioned ships",
                    "Great Pacific Garbage Patch",
                    "Old oil platforms"
                ], "correct_option_index": 2, "explanation": "The name is serious, the situation is unpleasant. This is a centralized accumulation of garbage from various continents in the upper layers of the Pacific Ocean."}
            ]
        },
        "Nature of Russia": {
            "Easy": [
                {"text": "What is the longest river in Russia?", "options": [
                    "Volga",
                    "Lena",
                    "Ob",
                    "Amur"
                ], "correct_option_index": 1, "explanation": "Lena is the largest river whose basin lies entirely within Russia. Its length is about 4400km."},
                
                {"text": "What is the depth of Lake Baikal?", "options": [
                    "1100 meters",
                    "2050 meters",
                    "898 meters",
                    "1642 meters"
                ], "correct_option_index": 3, "explanation": "In addition to being the deepest lake in the world, Lake Baikal is also the largest freshwater lake, containing up to 19% of the world's fresh water supply."},
                
                {"text": "What is the name of the highest mountain peak in Russia?", "options": [
                    "Elbrus",
                    "Belukha",
                    "Shota Rustaveli Peak",
                    "Kazbek"
                ], "correct_option_index": 0, "explanation": "Elbrus is the highest point in Russia with two five-thousander peaks: 5642 and 5621m."},
                
                {"text": "How many subjects does the Russian Federation have?", "options": [
                    "64",
                    "89",
                    "80",
                    "91"
                ], "correct_option_index": 1, "explanation": "The Russian Federation consists of 89 subjects."},
                
                {"text": "What is the name of the largest city beyond the Arctic Circle?", "options": [
                    "Apatity",
                    "Murmansk",
                    "Severomorsk",
                    "Tromsø"
                ], "correct_option_index": 1, "explanation": "Murmansk is considered the largest city in the world beyond the Arctic Circle with a population of 267 thousand people."},
                
                {"text": "What is the Golden Ring of Russia?", "options": [
                    "A tourist route passing through several historical cities of the Golden Ring of Central Russia",
                    "A term used to denote a circular system of roads connecting million-plus cities of Russia",
                    "An annual festival of artisans held in the Yaroslavl region",
                    "The name of the Russian football tournament held among the strongest teams from different regions of the country"
                ], "correct_option_index": 0, "explanation": "The Golden Ring of Russia is a tourist route passing through ancient cities-centers of folk crafts."}
            ],
            "Difficult": [
                {"text": "Which city in Russia is considered the coldest?", "options": [
                    "Murmansk",
                    "Verkhoyansk",
                    "Norilsk",
                    "Vorkuta"
                ], "correct_option_index": 1, "explanation": "In 1982, the weather station in Verkhoyansk recorded an absolute temperature minimum of -67.8 degrees below zero."},
                
                {"text": "In which part of Russia are the largest number of geothermal power plants located?", "options": [
                    "Altai Territory",
                    "Kamchatka Territory",
                    "Krasnodar Territory",
                    "Dagestan"
                ], "correct_option_index": 1, "explanation": "Geothermal energy is a promising direction. Kamchatka Territory is famous for the Valley of Geysers."},
                
                {"text": "What is the name of the northernmost nature reserve in Russia, known as the 'home of polar bears'?", "options": [
                    "Wrangel Island",
                    "Kuznetsk Alatau",
                    "Kivach",
                    "Teberdinsky Reserve"
                ], "correct_option_index": 0, "explanation": "The Wrangel Island Reserve is located in the Chukchi Sea area and is a key habitat for the polar bear."},
                
                {"text": "Many call this park a 'bird bridge' as it is part of the migration route of tens of millions of birds annually.", "options": [
                    "Wrangel Island",
                    "Curonian Spit",
                    "Coast of the Azov Sea",
                    "Grigorievskaya Spit"
                ], "correct_option_index": 1, "explanation": "Locals call the Curonian Spit a 'bird bridge' for a reason - during peak migration, from 1.5 to 2 million individuals are recorded on its territory per day."},
                
                {"text": "Seven giants, Mansi blockheads and a sacred mountain. What are we talking about?", "options": [
                    "Manpupuner",
                    "Stonehenge",
                    "Bermamyt Plateau",
                    "Putorana Plateau"
                ], "correct_option_index": 0, "explanation": "The unique weathering pillars of Manpupuner in the Northern Urals attract the attention of tens of thousands of tourists every year."},
                
                {"text": "What was the first Russian natural object included in the UNESCO World Natural Heritage List?", "options": [
                    "Curonian Spit",
                    "Golden Mountains of Altai",
                    "Lake Baikal",
                    "Virgin Komi Forests"
                ], "correct_option_index": 3, "explanation": "The Virgin Komi Forests are untouched forests stretching over an area of 32,600 km²."}
            ]
        },
        "Nuclear Industry": {
            "Easy": [
                {"text": "This city is the base of Russia's nuclear icebreaker fleet", "options": [
                    "Murmansk",
                    "St. Petersburg",
                    "Vladivostok",
                    "Arkhangelsk"
                ], "correct_option_index": 0, "explanation": "Murmansk is the base of Russia's nuclear icebreaker fleet."},
                
                {"text": "For secrecy reasons, the building of the world's first Obninsk NPP was made similar to...", "options": [
                    "Circus",
                    "Library",
                    "Cafe",
                    "Residential building"
                ], "correct_option_index": 3, "explanation": "The world's first nuclear power plant in Obninsk was built to resemble a residential building for secrecy reasons."},
                
                {"text": "True or myth? The Earth will soon run out of uranium", "options": [
                    "True",
                    "Myth"
                ], "correct_option_index": 1, "explanation": "This is a myth! There is 600 times more uranium on our planet than gold. Experts believe that it will last for another five hundred years."},
                
                {"text": "How much CO2 emissions are prevented by the operation of Russian nuclear power plants built in the world at the moment?", "options": [
                    "20 million tons",
                    "95 million tons",
                    "145 million tons",
                    "217 million tons"
                ], "correct_option_index": 3, "explanation": "Russian nuclear power plants prevent the emission of 217 million tons of CO2."},
                
                {"text": "Where was the world's first nuclear power plant built?", "options": [
                    "Russia",
                    "Japan",
                    "France"
                ], "correct_option_index": 0, "explanation": "In May 1950, construction of the world's first nuclear power plant began in Obninsk (Kaluga region)."},
                
                {"text": "True or myth? All radiation is harmful", "options": [
                    "True",
                    "Myth"
                ], "correct_option_index": 1, "explanation": "But no! In fact, humans are always surrounded by background radiation. But not all radiation is dangerous, the question is in the doses of radiation."}
            ],
            "Difficult": [
                {"text": "Which seas does the Northern Sea Route pass through?", "options": [
                    "Kara Sea, Laptev Sea, East Siberian Sea, Chukchi Sea, Bering Sea",
                    "Beaufort Sea, Lincoln Sea, Greenland Sea",
                    "Barents Sea, White Sea, Okhotsk Sea",
                    "Baltic Sea, Black Sea, Caspian Sea"
                ], "correct_option_index": 0, "explanation": "The Northern Sea Route passes through the Kara Sea, Laptev Sea, East Siberian Sea, Chukchi Sea and Bering Sea."},
                
                {"text": "This is the world's first nuclear power plant located in the permafrost zone", "options": [
                    "Beloyarsk NPP",
                    "Baltic NPP",
                    "Balakovo NPP",
                    "Bilibino NPP"
                ], "correct_option_index": 3, "explanation": "Bilibino NPP is the world's first nuclear power plant located in the permafrost zone."},
                
                {"text": "Choose which major projects in the field of accumulated harm elimination Rosatom is engaged in?", "options": [
                    "City landfill in Chelyabinsk, Industrial site in Usolye-Sibirskoye, Baikal Pulp and Paper Mill, Krasny Bor landfill",
                    "Yadrovo landfill, Industrial site in Kemerovo",
                    "All of the above options",
                    "None of the above options"
                ], "correct_option_index": 0, "explanation": "Rosatom is engaged in the elimination of accumulated harm at the facilities: City landfill in Chelyabinsk, Industrial site in Usolye-Sibirskoye, Baikal Pulp and Paper Mill, Krasny Bor landfill."},
                
                {"text": "Which element is extra? Choose one option:", "options": [
                    "Indium",
                    "Uranium",
                    "Lithium",
                    "Radium",
                    "Barium"
                ], "correct_option_index": 2, "explanation": "The extra element is lithium, it is the only one in this company that is not radioactive."},
                
                {"text": "True or myth? It is dangerous to build residential buildings near nuclear power plants.", "options": [
                    "True",
                    "Myth"
                ], "correct_option_index": 1, "explanation": "We live with constant background radiation. It is not dangerous to live near a nuclear power plant; already at a distance of 80 km a person receives a radiation dose of 0.01 millisievert per year."},
                
                {"text": "Rosatom includes the world's only floating nuclear power plant. What does it do?", "options": [
                    "Supplies electricity to the port of Pevek in Chukotka",
                    "Serves as a transshipment point for Atomflot ships",
                    "Used to sell electricity to other countries",
                    "Serves as a floating parking lot for icebreakers"
                ], "correct_option_index": 0, "explanation": "The world's only floating nuclear power plant 'Akademik Lomonosov' supplies electricity to the Chukotka Autonomous Okrug, primarily the city of Pevek."}
            ]
        }
    }
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
    choosing_category = State()
    choosing_difficulty = State()
    answering = State()

TEXTS = {
    "ru": {
        "start": "⚛️ Добро пожаловать в опрос о Росатоме!\n\nВыберите язык:",
        "name_prompt": "📝 Пожалуйста, укажите ваше имя:",
        "email_prompt": "📧 Укажите ваш email:",
        "consent": '🛡️ Нажимая «Подтверждаю», вы даёте согласие на обработку персональных данных в соответствии с политикой конфиденциальности (<a href="https://www.consultant.ru/document/cons_doc_LAW_61801/315f051396c88f1e4f827ba3f2ae313d999a1873/">Федеральный закон от 27.07.2006 N 152-ФЗ</a>).',
        "already_done": "Вы уже прошли опрос. Спасибо за интерес к Росатому!",
        "choose_category": "🎯 Выберите направление:",
        "choose_difficulty": "📊 Выберите уровень сложности:",
        "quiz_start": "Вопрос {num} из 6:\n\n{question}",
        "correct": "✅ Верно!\n\nℹ️ {explanation}",
        "incorrect": "❌ Неверно.\nПравильный ответ: <b>{answer}</b>\n\nℹ️ {explanation}",
        "explanation": "",
        "final": "🎉 Поздравляем вас с завершением викторины!\n\nНам было важно сделать викторину максимально разносторонней, чтобы через разъяснения к ответам сделать ее еще и познавательной. Надеемся, вам понравилось.\n\nИ, конечно, мы не могли вас оставить без подарков!\nЧтобы получить приз за участие в викторине, вам необходимо подписаться на телеграм-канал Михи Атомова https://t.me/mixaatomov\n\nДо встречи!",
        "qr_text": "",
        "error_saving": "⚠️ Результат не сохранен из-за технической ошибки."
    },
    "en": {
        "start": "⚛️ Welcome to the Rosatom quiz!\n\nChoose your language:",
        "name_prompt": "📝 Please enter your first name:",
        "email_prompt": "📧 Please provide your email:",
        "consent": '🛡️ By clicking "I Agree", you consent to the processing of personal data in accordance with the privacy policy (<a href="https://www.consultant.ru/document/cons_doc_LAW_61801/315f051396c88f1e4f827ba3f2ae313d999a1873/">Federal Law No. 152-FZ of 27.07.2006</a>).',
        "already_done": "You've already completed the quiz. Thank you for your interest in Rosatom!",
        "choose_category": "🎯 Choose direction:",
        "choose_difficulty": "📊 Choose difficulty level:",
        "quiz_start": "Question {num} out of 6:\n\n{question}",
        "correct": "✅ Correct!\n\nℹ️ {explanation}",
        "incorrect": "❌ Incorrect.\nCorrect answer: <b>{answer}</b>\n\nℹ️ {explanation}",
        "explanation": "",
        "final": "🎉 Congratulations on completing the quiz!\n\nIt was important for us to make the quiz as versatile as possible, so that through explanations of the answers it would also be educational. We hope you enjoyed it.\n\nAnd of course, we couldn't leave you without gifts!\nTo receive a prize for participating in the quiz, you need to subscribe to the Misha Atmov Telegram channel https://t.me/mixaatomov\n\nSee you!",
        "qr_text": "",
        "error_saving": "⚠️ Result not saved due to technical error."
    }
}

# Клавиатуры
def lang_kb():
    buttons = [
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang_ru")],
        [InlineKeyboardButton(text="🇬🇧 English", callback_data="lang_en")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def category_kb(lang):
    if lang == "ru":
        buttons = [
            [InlineKeyboardButton(text="🌿 Экологическое просвещение", callback_data="cat_eco")],
            [InlineKeyboardButton(text="🏔️ Природа России", callback_data="cat_nature")],
            [InlineKeyboardButton(text="⚛️ Атомная промышленность", callback_data="cat_atom")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="🌿 Environmental Education", callback_data="cat_eco")],
            [InlineKeyboardButton(text="🏔️ Nature of Russia", callback_data="cat_nature")],
            [InlineKeyboardButton(text="⚛️ Nuclear Industry", callback_data="cat_atom")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def difficulty_kb(lang):
    if lang == "ru":
        buttons = [
            [InlineKeyboardButton(text="🟢 Полегче", callback_data="diff_easy")],
            [InlineKeyboardButton(text="🔴 Посложнее", callback_data="diff_hard")]
        ]
    else:
        buttons = [
            [InlineKeyboardButton(text="🟢 Easy", callback_data="diff_easy")],
            [InlineKeyboardButton(text="🔴 Difficult", callback_data="diff_hard")]
        ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def consent_kb(lang):
    txt = "✅ Подтверждаю" if lang == "ru" else "✅ I Agree"
    button = InlineKeyboardButton(text=txt, callback_data="consent_yes")
    return InlineKeyboardMarkup(inline_keyboard=[[button]])

def opts_kb(opts, lang):
    letters = ["A", "B", "C", "D"]
    buttons = []
    for i, opt in enumerate(opts):
        if i < len(letters):
            buttons.append([InlineKeyboardButton(text=f"{letters[i]}) {opt}", callback_data=f"ans_{i}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

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
    await state.set_state(QuizStates.choosing_category)
    lang = (await state.get_data())["language"]
    await callback.message.edit_text(TEXTS[lang]["choose_category"], reply_markup=category_kb(lang))
    await callback.answer()

@dp.callback_query(F.data.startswith("cat_"))
async def category_cb(callback: CallbackQuery, state: FSMContext):
    category_map = {
        "cat_eco": "Экологическое просвещение" if (await state.get_data())["language"] == "ru" else "Environmental Education",
        "cat_nature": "Природа России" if (await state.get_data())["language"] == "ru" else "Nature of Russia", 
        "cat_atom": "Атомная промышленность" if (await state.get_data())["language"] == "ru" else "Nuclear Industry"
    }
    
    category = category_map[callback.data]
    await state.update_data(category=category)
    await state.set_state(QuizStates.choosing_difficulty)
    lang = (await state.get_data())["language"]
    await callback.message.edit_text(TEXTS[lang]["choose_difficulty"], reply_markup=difficulty_kb(lang))
    await callback.answer()

@dp.callback_query(F.data.startswith("diff_"))
async def difficulty_cb(callback: CallbackQuery, state: FSMContext):
    difficulty_map = {
        "diff_easy": "Полегче" if (await state.get_data())["language"] == "ru" else "Easy",
        "diff_hard": "Посложнее" if (await state.get_data())["language"] == "ru" else "Difficult"
    }
    
    difficulty = difficulty_map[callback.data]
    await state.update_data(difficulty=difficulty, answers=[], current_q=0)
    await state.set_state(QuizStates.answering)
    await send_question(callback.message, state)
    await callback.answer()

async def send_question(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data["language"]
    category = data["category"]
    difficulty = data["difficulty"]
    q_idx = data.get("current_q", 0)
    
    # Получаем вопросы для выбранной категории и сложности
    if lang == "ru":
        category_key = category
        difficulty_key = difficulty
    else:
        category_key = {
            "Environmental Education": "Environmental Education",
            "Nature of Russia": "Nature of Russia", 
            "Nuclear Industry": "Nuclear Industry"
        }[category]
        difficulty_key = {
            "Easy": "Easy",
            "Difficult": "Difficult"
        }[difficulty]
    
    questions_list = QUESTIONS[lang][category_key][difficulty_key]
    
    if q_idx >= len(questions_list):
        await finish_quiz(message, state, lang)
        return
        
    q = questions_list[q_idx]
    txt = TEXTS[lang]["quiz_start"].format(num=q_idx + 1, question=q["text"])
    await message.answer(txt, reply_markup=opts_kb(q["options"], lang))

@dp.callback_query(F.data.startswith("ans_"))
async def answer_cb(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    lang = data["language"]
    category = data["category"]
    difficulty = data["difficulty"]
    q_idx = data.get("current_q", 0)
    
    if lang == "ru":
        category_key = category
        difficulty_key = difficulty
    else:
        category_key = {
            "Environmental Education": "Environmental Education",
            "Nature of Russia": "Nature of Russia",
            "Nuclear Industry": "Nuclear Industry"
        }[category]
        difficulty_key = {
            "Easy": "Easy",
            "Difficult": "Difficult"
        }[difficulty]
    
    questions_list = QUESTIONS[lang][category_key][difficulty_key]
    
    if q_idx >= len(questions_list):
        await callback.answer()
        return
        
    sel = int(callback.data.split("_", 1)[1])
    q = questions_list[q_idx]
    correct = sel == q["correct_option_index"]
    correct_text = q["options"][q["correct_option_index"]]
    answers = data.get("answers", [])
    answers.append({"selected": sel, "correct": correct})
    await state.update_data(answers=answers, current_q=q_idx + 1)
    
    if correct:
        fb = TEXTS[lang]["correct"].format(explanation=q["explanation"])
    else:
        fb = TEXTS[lang]["incorrect"].format(answer=correct_text, explanation=q["explanation"])
    
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
    category = data["category"]
    difficulty = data["difficulty"]
    
    # Сохраняем результат
    success = append_result(uid, name, email, lang, category, difficulty, score)
    
    if success:
        final_text = TEXTS[lang]["final"]
    else:
        final_text = f"{TEXTS[lang]['final']}\n\n{TEXTS[lang]['error_saving']}"
    
    await message.answer(final_text)
    
    # Отправка статического QR-кода
    try:
        # Загружаем ваш файл qrcode1.png (должен быть в той же директории)
        qr_file = FSInputFile("qrcode1.png")
        await message.answer_photo(qr_file)
    except Exception as e:
        logging.error(f"Error sending QR code: {e}")
        # Fallback: генерируем QR код если файл не найден
        try:
            qr_img = generate_qr(TARGET_URL)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                qr_img.save(tmp.name)
                await message.answer_photo(FSInputFile(tmp.name))
                os.unlink(tmp.name)
        except Exception as e2:
            logging.error(f"Error generating QR code: {e2}")
    
    await state.clear()

# ============== WEBHOOK + HEALTH + PING ==============
async def health_check(request):
    return web.Response(text="OK", status=200)

# ЭНДПОИНТ ДЛЯ UPTIMEROBOT
async def ping_handler(request):
    return web.Response(text="pong", status=200)

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
        
        # ДОБАВЛЕНО: IP-фильтр для UptimeRobot
        app.middlewares.append(ip_middleware)
        
        app.router.add_get("/health", health_check)
        app.router.add_get("/ping", ping_handler)
        SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
        app.on_startup.append(on_startup)
        app.on_shutdown.append(on_shutdown)
        
        logging.info(f"🚀 Starting bot on port {WEB_SERVER_PORT}")
        logging.info("✅ UptimeRobot monitoring enabled at /ping endpoint")
        logging.info("🔒 IP filtering enabled for North America regions")
        web.run_app(app, host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
        
    except Exception as e:
        logging.exception("❌ CRITICAL ERROR")
        raise

if __name__ == "__main__":
    main()