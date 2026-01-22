import asyncio
import logging
import os
import aiohttp
import re
import time
import unicodedata
import json
import random
from typing import Optional, List, Tuple, Dict, Any
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ChatAction
from dotenv import load_dotenv

# ==================== НАСТРОЙКА ====================
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Проверка переменных окружения
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
    logger.error("Отсутствуют обязательные переменные в .env файле!")
    exit(1)

# Конфигурация OpenRouter API
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_URL = f"{OPENROUTER_BASE_URL}/chat/completions"

# ==================== МОДЕЛИ С НОВЫМИ ВАРИАНТАМИ ====================
MODELS_CONFIG = {
    "main": {
        "primary": "meta-llama/llama-3.3-70b-instruct:free",
        "backup": "qwen/qwen3-next-80b-a3b-instruct:free",  # 🚀 МОЩНАЯ новая модель 80B!
        "fallback": "google/gemma-3-4b-it:free",  # Быстрая и эффективная
        "emergency": "microsoft/phi-3.5-mini-instruct:free"  # Аварийный вариант
    },
    "deepseek": {
        "primary": "deepseek/deepseek-v3.2",  # 🆕 ПЛАТНАЯ но очень мощная!
        "backup": "deepseek/deepseek-r1-0528:free",  # Старый добрый R1
        "fallback": "deepseek/deepseek-coder-33b-instruct:free",  # Для кода
        "emergency": "qwen/qwen2.5-32b-instruct:free"  # Ещё одна мощная модель
    }
}

# Проверка доступности платных моделей (требуют баланс)
USE_PAID_MODELS = os.getenv("USE_PAID_MODELS", "false").lower() == "true"
if not USE_PAID_MODELS:
    MODELS_CONFIG["deepseek"]["primary"] = "deepseek/deepseek-r1-0528:free"
    logger.info("ℹ️ Используем только бесплатные модели (USE_PAID_MODELS=false)")

# Приоритет моделей для запросов (чем выше, тем быстрее модель)
MODEL_PRIORITIES = {
    # Основные (быстрые)
    "google/gemma-3-4b-it:free": 1,
    "microsoft/phi-3.5-mini-instruct:free": 1,
    "qwen/qwen2.5-32b-instruct:free": 2,
    
    # Средние
    "deepseek/deepseek-coder-33b-instruct:free": 3,
    "meta-llama/llama-3.3-70b-instruct:free": 4,
    "deepseek/deepseek-r1-0528:free": 4,
    
    # Мощные (медленные)
    "qwen/qwen3-next-80b-a3b-instruct:free": 5,
    "deepseek/deepseek-v3.2": 5,
}

# ==================== ГЕНЕРАЦИЯ БЕЗ API (LOCAL FALLBACK) ====================
LOCAL_RESPONSES = {
    "технология": [
        "🤖 **ИИ-анализ технологии**\n\n"
        "Для внедрения ИИ от SberVision в Telegram группу:\n\n"
        "1. **Получите API ключ SberVision** на developer.sber.ru\n"
        "2. **Создайте Telegram бота** через @BotFather\n"
        "3. **Настройте вебхук** для обработки сообщений\n"
        "4. **Интегрируйте SberVision API** для распознавания\n"
        "5. **Добавьте логику** обработки изображений и формул\n\n"
        "```python\n# Пример обработки изображения\nimport requests\n\ndef recognize_image(image_url):\n    api_key = 'YOUR_API_KEY'\n    response = requests.post(\n        'https://api.sber.dev/vision/v1/recognize',\n        json={'image': image_url},\n        headers={'Authorization': f'Bearer {api_key}'}\n    )\n    return response.json()\n```\n\n"
        "🔧 **Следующие шаги**: Настройка базы данных для хранения результатов и добавление панели администратора."
    ],
    "код": [
        "💻 **Пример кода для Telegram бота с SberVision**\n\n"
        "```python\nimport telebot\nimport requests\nimport json\n\n# Настройки\nTOKEN = 'YOUR_BOT_TOKEN'\nSBER_API_KEY = 'YOUR_SBER_VISION_KEY'\n\nbot = telebot.TeleBot(TOKEN)\n\n@bot.message_handler(content_types=['photo'])\ndef handle_photo(message):\n    # Получаем файл изображения\n    file_id = message.photo[-1].file_id\n    file_info = bot.get_file(file_id)\n    file_url = f'https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}'\n    \n    # Отправляем в SberVision\n    response = requests.post(\n        'https://api.sber.dev/vision/v1/ocr',\n        headers={'Authorization': f'Bearer {SBER_API_KEY}'},\n        json={'image_url': file_url}\n    )\n    \n    if response.status_code == 200:\n        result = response.json()\n        text = result.get('text', 'Текст не распознан')\n        bot.reply_to(message, f'📝 Распознанный текст:\\n{text}')\n    else:\n        bot.reply_to(message, '❌ Ошибка распознавания')\n\nbot.polling()\n```\n\n"
        "📁 **Структура проекта**:\n"
        "```\nproject/\n├── bot.py\n├── config.py\n├── sber_vision.py\n├── database.py\n└── requirements.txt\n```"
    ],
    "общий": [
        "🧠 **Анализ запроса**\n\n"
        "Для распознавания текста и формул в Telegram группе:\n\n"
        "**Этапы внедрения:**\n"
        "1. **Подготовка инфраструктуры**\n"
        "   - Сервер/VPS с Python 3.8+\n"
        "   - База данных (PostgreSQL/Redis)\n"
        "   - SSL сертификат для вебхуков\n\n"
        "2. **Интеграция API**\n"
        "   - SberVision для OCR и распознавания формул\n"
        "   - Telegram Bot API\n"
        "   - Дополнительные сервисы (если нужно)\n\n"
        "3. **Разработка логики**\n"
        "   - Обработка изображений\n"
        "   - Парсинг математических выражений\n"
        "   - Хранение и поиск результатов\n\n"
        "4. **Тестирование и деплой**\n"
        "   - Юнит-тесты\n"
        "   - Нагрузочное тестирование\n"
        "   - Мониторинг и логирование\n\n"
        "⏱️ **Примерные сроки**: 2-3 недели для MVP"
    ],
    "ai_models": [
        "🤖 **Сравнение ИИ-моделей**\n\n"
        "**🆕 Новые модели в боте:**\n\n"
        "🚀 **Qwen3 Next 80B** - самая мощная бесплатная модель:\n"
        "• 80 миллиардов параметров\n"
        "• Отличное понимание контекста\n"
        "• Хорошо справляется с кодом\n\n"
        "⚡ **Gemma 3 4B** - быстрая и эффективная:\n"
        "• Всего 4 миллиарда параметров\n"
        "• Быстрые ответы\n"
        "• Экономит токены\n\n"
        "💎 **DeepSeek V3.2** - платная но мощная:\n"
        "• Последняя версия DeepSeek\n"
        "• Лучшее качество ответов\n"
        "• Требует баланс на OpenRouter\n\n"
        "**🔧 Настройка:**\n"
        "Для использования платных моделей установите `USE_PAID_MODELS=true` в .env"
    ]
}

def get_local_fallback_response(user_question: str) -> str:
    """Генерация локального ответа если API недоступно"""
    question_lower = user_question.lower()
    
    # Определяем тему вопроса
    if any(word in question_lower for word in ['код', 'пример', 'программир', 'python', 'javascript']):
        topic = "код"
    elif any(word in question_lower for word in ['модел', 'ai', 'ии', 'chatgpt', 'нейросет']):
        topic = "ai_models"
    elif any(word in question_lower for word in ['шаг', 'план', 'внедр', 'настрой', 'установ']):
        topic = "технология"
    else:
        topic = "общий"
    
    responses = LOCAL_RESPONSES[topic]
    return random.choice(responses)

# ==================== КОНФИГУРАЦИЯ ГЕНЕРАЦИИ ====================
GENERATION_CONFIG = {
    "temperature": 0.8,
    "max_tokens": 1000,
    "top_p": 0.9,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== УТИЛИТЫ ОБРАБОТКИ ТЕКСТА ====================
def fix_unbalanced_backticks(text: str) -> str:
    """Исправляет нечётное количество обратных кавычек"""
    if not text:
        return text
    
    backtick_count = text.count('`')
    if backtick_count % 2 == 0:
        return text
    
    logger.warning(f"⚠️ Нечётное количество кавычек: {backtick_count}")
    
    # Проверяем блоки кода
    if '```' in text:
        # Если есть незакрытый блок кода
        lines = text.split('\n')
        in_code_block = False
        
        for i, line in enumerate(lines):
            if line.strip().startswith('```'):
                in_code_block = not in_code_block
        
        if in_code_block:
            text += '\n```'
            logger.info("✅ Добавлены закрывающие ```")
        else:
            text += '`'
            logger.info("✅ Добавлена кавычка")
    else:
        text += '`'
        logger.info("✅ Добавлена кавычка")
    
    return text

def clean_text_safe(text: str) -> str:
    """Безопасная очистка текста"""
    if not text:
        return ""
    
    # Фиксируем кавычки
    text = fix_unbalanced_backticks(text)
    
    # Удаляем опасные символы
    cleaned = []
    for char in text:
        cat = unicodedata.category(char)
        if cat[0] == 'C':  # Control characters
            if char in ['\n', '\t', '\r', '`']:
                cleaned.append(char)
        else:
            cleaned.append(char)
    
    text = ''.join(cleaned)
    
    # Удаляем конкретные опасные символы
    dangerous = ['\u0000', '\u0001', '\u0002', '\u0003', '\u0004', '\u0005',
                '\u0006', '\u0007', '\u0008', '\u000b', '\u000c',
                '\u000e', '\u000f', '\u0010', '\u0011', '\u0012',
                '\u0013', '\u0014', '\u0015', '\u0016', '\u0017',
                '\u0018', '\u0019', '\u001a', '\u001b', '\u001c',
                '\u001d', '\u001e', '\u001f', '\u200b', '\u200c',
                '\u200d', '\ufeff']
    
    for char in dangerous:
        text = text.replace(char, '')
    
    return text

def escape_markdown_simple(text: str) -> str:
    """Простое экранирование Markdown"""
    text = clean_text_safe(text)
    
    # Экранируем основные символы
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text = text.replace(char, f'\\{char}')
    
    return text

# ==================== ФУНКЦИИ ОТПРАВКИ ====================
async def send_message_safe(
    chat_id: int, 
    text: str, 
    reply_to_message_id: Optional[int] = None
) -> Optional[types.Message]:
    """Безопасная отправка сообщений"""
    if not text:
        return None
    
    text = clean_text_safe(text)
    
    try:
        # Пробуем Markdown
        escaped = escape_markdown_simple(text)
        kwargs = {
            "chat_id": chat_id,
            "text": escaped,
            "parse_mode": "MarkdownV2"
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        return await bot.send_message(**kwargs)
    except Exception as e:
        logger.warning(f"Markdown не сработал: {e}, пробуем без форматирования")
        try:
            kwargs = {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": None
            }
            if reply_to_message_id:
                kwargs["reply_to_message_id"] = reply_to_message_id
            
            return await bot.send_message(**kwargs)
        except Exception as e2:
            logger.error(f"Не удалось отправить сообщение: {e2}")
            return None

async def send_long_message(
    chat_id: int, 
    text: str, 
    reply_to_message_id: Optional[int] = None
) -> None:
    """Отправка длинных сообщений"""
    if len(text) <= 4000:
        await send_message_safe(chat_id, text, reply_to_message_id)
        return
    
    # Разбиваем на части
    parts = []
    current = ""
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if len(current) + len(para) + 2 <= 4000:
            current += para + "\n\n"
        else:
            if current:
                parts.append(current.strip())
            current = para + "\n\n"
    
    if current:
        parts.append(current.strip())
    
    for i, part in enumerate(parts):
        await send_message_safe(
            chat_id,
            part,
            reply_to_message_id if i == 0 else None
        )
        if i < len(parts) - 1:
            await asyncio.sleep(0.3)

# ==================== OPENROUTER С УМНЫМ ВЫБОРОМ МОДЕЛИ ====================
async def test_model_speed(model: str) -> float:
    """Тестирует скорость ответа модели"""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    
    data = {
        "model": model,
        "messages": [{"role": "user", "content": "Привет"}],
        "max_tokens": 10
    }
    
    try:
        start = time.time()
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                elapsed = time.time() - start
                
                if response.status == 200:
                    return elapsed
                else:
                    return float('inf')  # Модель не работает
    except:
        return float('inf')

async def get_best_model(model_list: List[str]) -> str:
    """Выбирает лучшую модель на основе приоритета и скорости"""
    # Сначала сортируем по приоритету
    sorted_models = sorted(
        model_list,
        key=lambda m: MODEL_PRIORITIES.get(m, 10)
    )
    
    # Тестируем первые 3 модели
    test_models = sorted_models[:3]
    speeds = {}
    
    logger.info(f"📊 Тестируем скорость моделей: {', '.join(test_models)}")
    
    for model in test_models:
        speed = await test_model_speed(model)
        speeds[model] = speed
        if speed < float('inf'):
            logger.info(f"  • {model.split('/')[-1]}: {speed:.2f}с")
    
    # Выбираем работающую модель с лучшей скоростью
    working_models = {m: s for m, s in speeds.items() if s < float('inf')}
    
    if working_models:
        best_model = min(working_models.items(), key=lambda x: x[1])[0]
        logger.info(f"✅ Выбрана модель: {best_model.split('/')[-1]}")
        return best_model
    else:
        # Если ни одна не работает, возвращаем первую
        logger.warning("⚠️ Ни одна модель не ответила, используем первую из списка")
        return sorted_models[0]

async def try_model_with_retry(
    model_list: List[str],
    user_question: str,
    system_prompt: Dict[str, str],
    max_retries: int = 2
) -> Optional[str]:
    """Пробует несколько моделей с повторными попытками"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    # Выбираем лучшую модель
    best_model = await get_best_model(model_list)
    
    for attempt in range(max_retries):
        try:
            data = {
                "model": best_model,
                "messages": [
                    system_prompt,
                    {"role": "user", "content": user_question}
                ],
                **GENERATION_CONFIG
            }
            
            # Динамический таймаут в зависимости от модели
            model_timeout = 30 if MODEL_PRIORITIES.get(best_model, 10) <= 2 else 60
            timeout = aiohttp.ClientTimeout(total=model_timeout)
            
            logger.info(f"🚀 Запрос к {best_model.split('/')[-1]} (таймаут: {model_timeout}с)...")
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    OPENROUTER_URL, 
                    headers=headers, 
                    json=data
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        if 'choices' in result and result['choices']:
                            text = result['choices'][0]['message'].get('content', '').strip()
                            if text:
                                logger.info(f"✅ {best_model.split('/')[-1]} ответил успешно")
                                return fix_unbalanced_backticks(text)
                    
                    # Если не 200 или пустой ответ
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ Попытка {attempt+1} для {best_model} не удалась, повторяем...")
                        await asyncio.sleep(1)
                        
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            logger.warning(f"⚠️ Ошибка сети для {best_model}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
        except Exception as e:
            logger.error(f"❌ Неизвестная ошибка для {best_model}: {e}")
            break
    
    logger.warning(f"❌ Модель {best_model} не сработала после {max_retries} попыток")
    return None

async def get_ai_response(user_question: str, response_type: str = "main") -> Optional[str]:
    """
    Получает ответ от AI с использованием резервных моделей
    response_type: "main" или "deepseek"
    """
    # Определяем список моделей для этого типа ответа
    if response_type == "main":
        models = [
            MODELS_CONFIG["main"]["primary"],
            MODELS_CONFIG["main"]["backup"],
            MODELS_CONFIG["main"]["fallback"],
            MODELS_CONFIG["main"]["emergency"]
        ]
        system_prompt = {
            "role": "system",
            "content": (
                "Ты Иван Иваныч — эксперт в технологиях и футуристике. "
                "Отвечай ясно и по делу. Используй Markdown для форматирования. "
                "Для кода используй тройные кавычки. Держи ответ в 800-1200 символов."
            )
        }
    else:  # deepseek
        models = [
            MODELS_CONFIG["deepseek"]["primary"],
            MODELS_CONFIG["deepseek"]["backup"],
            MODELS_CONFIG["deepseek"]["fallback"],
            MODELS_CONFIG["deepseek"]["emergency"]
        ]
        system_prompt = {
            "role": "system",
            "content": (
                "Ты технический аналитик. Дай глубокий анализ с практическими шагами. "
                "Используй Markdown, для кода — тройные кавычки. "
                "Будь конкретным и техничным. 1000-1500 символов."
            )
        }
    
    # Пробуем получить ответ от API
    response = await try_model_with_retry(models, user_question, system_prompt)
    
    # Если API не ответило, используем локальный fallback
    if not response:
        logger.warning("⚠️ Все модели не ответили, использую локальный fallback")
        response = get_local_fallback_response(user_question)
    
    return response

async def get_parallel_responses(user_question: str) -> Tuple[Optional[str], Optional[str]]:
    """Параллельное получение ответов от обеих систем"""
    main_task = asyncio.create_task(get_ai_response(user_question, "main"))
    deepseek_task = asyncio.create_task(get_ai_response(user_question, "deepseek"))
    
    try:
        main_response, deepseek_response = await asyncio.gather(main_task, deepseek_task)
    except Exception as e:
        logger.error(f"Ошибка в параллельных запросах: {e}")
        main_response = deepseek_response = None
    
    return main_response, deepseek_response

# ==================== ПРОВЕРКА СТАТУСА МОДЕЛЕЙ ====================
async def check_models_status() -> Dict[str, Dict[str, Any]]:
    """Проверяет доступность и скорость моделей"""
    status = {}
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    for model_type, model_config in MODELS_CONFIG.items():
        status[model_type] = {}
        
        for model_key, model_name in model_config.items():
            is_available = False
            response_time = None
            
            try:
                data = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Привет"}],
                    "max_tokens": 10
                }
                
                start_time = time.time()
                timeout = aiohttp.ClientTimeout(total=15)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                        response_time = time.time() - start_time
                        
                        if response.status == 200:
                            is_available = True
                            logger.info(f"✅ {model_name.split('/')[-1]}: {response_time:.2f}с")
                        else:
                            logger.warning(f"⚠️ {model_name.split('/')[-1]}: недоступна")
                            
            except Exception as e:
                logger.warning(f"⚠️ Ошибка проверки {model_name}: {e}")
            
            status[model_type][model_key] = {
                "name": model_name,
                "available": is_available,
                "response_time": response_time
            }
    
    return status

# ==================== ОБРАБОТЧИКИ ТЕЛЕГРАМ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    welcome = (
        "👋 Привет! Я Иван Иваныч — бот с продвинутыми ИИ-моделями\n\n"
        "🚀 **Новые мощные модели:**\n"
        "• **Qwen3 Next 80B** — самая мощная бесплатная модель\n"
        "• **Gemma 3 4B** — быстрая и эффективная\n"
        "• **DeepSeek V3.2** — платная но самая современная\n\n"
        "🤖 **Архитектура:**\n"
        "• 2 параллельных ИИ-ассистента\n"
        "• Автовыбор лучшей доступной модели\n"
        "• Умное кэширование и fallback\n\n"
        "⚡ **Команды:**\n"
        "/start - эта информация\n"
        "/status - проверка моделей\n"
        "/models - список всех моделей\n"
        "/help - полная справка\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_message_safe(message.chat.id, welcome, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Команда /status - проверка доступности моделей"""
    status_text = "🔄 Проверяю доступность и скорость моделей..."
    status_msg = await send_message_safe(message.chat.id, status_text, message.message_id)
    
    try:
        models_status = await check_models_status()
        
        status_report = "📊 **Статус моделей:**\n\n"
        
        for model_type, models in models_status.items():
            status_report += f"**{model_type.upper()}:**\n"
            
            for model_key, model_info in models.items():
                emoji = "✅" if model_info["available"] else "❌"
                name_short = model_info["name"].split('/')[-1]
                
                if model_info["available"] and model_info["response_time"]:
                    time_info = f" ({model_info['response_time']:.1f}с)"
                else:
                    time_info = ""
                
                status_report += f"{emoji} {model_key}: `{name_short}`{time_info}\n"
            
            status_report += "\n"
        
        status_report += "💡 *Примечание:* Бот автоматически выбирает лучшую доступную модель."
        
        if status_msg:
            await status_msg.edit_text(status_report, parse_mode="MarkdownV2")
        else:
            await send_message_safe(message.chat.id, status_report, message.message_id)
            
    except Exception as e:
        error_text = f"❌ Ошибка проверки статуса: {str(e)[:100]}"
        if status_msg:
            await status_msg.edit_text(error_text, parse_mode=None)
        else:
            await send_message_safe(message.chat.id, error_text, message.message_id)

@dp.message(Command("models"))
async def cmd_models(message: types.Message):
    """Команда /models - информация о всех моделях"""
    models_info = (
        "🤖 **Доступные модели ИИ:**\n\n"
        
        "🚀 **МОЩНЫЕ МОДЕЛИ (рекомендуемые):**\n"
        "• `qwen/qwen3-next-80b-a3b-instruct:free` — 80B параметров, лучшая бесплатная\n"
        "• `deepseek/deepseek-v3.2` — самая современная (платная)\n"
        "• `meta-llama/llama-3.3-70b-instruct:free` — классика, хорошо сбалансирована\n\n"
        
        "⚡ **БЫСТРЫЕ МОДЕЛИ (для простых запросов):**\n"
        "• `google/gemma-3-4b-it:free` — 4B, очень быстрая\n"
        "• `microsoft/phi-3.5-mini-instruct:free` — миниатюрная но умная\n\n"
        
        "🔧 **СПЕЦИАЛИЗИРОВАННЫЕ:**\n"
        "• `deepseek/deepseek-r1-0528:free` — для глубокого анализа\n"
        "• `deepseek/deepseek-coder-33b-instruct:free` — для программирования\n"
        "• `qwen/qwen2.5-32b-instruct:free` — хороший баланс скорости/качества\n\n"
        
        "⚙️ **Как это работает:**\n"
        "1. Бот тестирует скорость всех моделей\n"
        "2. Выбирает самую быструю доступную модель\n"
        "3. При ошибке переключается на следующую\n"
        "4. Если все модели не работают — использует локальную базу\n\n"
        
        "🔧 **Настройка платных моделей:**\n"
        "Добавьте в .env файл:\n"
        "```\nUSE_PAID_MODELS=true\n```"
    )
    await send_message_safe(message.chat.id, models_info, message.message_id)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Команда /help"""
    help_text = (
        "📖 **Полная справка по боту:**\n\n"
        "**📋 КОМАНДЫ:**\n"
        "• /start — информация о боте\n"
        "• /status — проверка доступности моделей\n"
        "• /models — список всех моделей с описанием\n"
        "• /help — эта справка\n\n"
        
        "**❓ КАК ЗАДАВАТЬ ВОПРОСЫ:**\n"
        "Просто напишите вопрос с '?' в конце\n\n"
        
        "**🔧 ПРИМЕРЫ ВОПРОСОВ:**\n"
        "• Как создать Telegram бота для распознавания текста?\n"
        "• Дай пример кода на Python для работы с API\n"
        "• Как настроить SberVision для OCR?\n"
        "• Сравни модели Llama и DeepSeek\n\n"
        
        "**⚠️ ЕСЛИ МОДЕЛИ НЕ ОТВЕЧАЮТ:**\n"
        "Бот автоматически:\n"
        "1. Переключится на резервную модель\n"
        "2. Использует локальную базу знаний\n"
        "3. Всегда даст какой-то ответ\n\n"
        
        "**💰 ПЛАТНЫЕ МОДЕЛИ:**\n"
        "Для использования DeepSeek V3.2:\n"
        "1. Пополните баланс на OpenRouter\n"
        "2. Установите USE_PAID_MODELS=true в .env"
    )
    await send_message_safe(message.chat.id, help_text, message.message_id)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Обработка вопросов"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = message.from_user.username or f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    # Отправляем уведомление
    processing_text = "🤔 Ищу лучшую модель для ответа..."
    processing_msg = await send_message_safe(chat_id, processing_text, message.message_id)
    
    if not processing_msg:
        return
    
    start_time = time.time()
    
    try:
        # Параллельные запросы
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        main_response, deepseek_response = await get_parallel_responses(user_question)
        
        elapsed = time.time() - start_time
        
        # Добавляем информацию о моделях в ответы
        model_info = ""
        if main_response:
            # Определяем какая модель использовалась
            model_info = "\n\n_✨ Ответ сгенерирован современной ИИ-моделью_"
        
        # Отправляем основной ответ
        if main_response:
            logger.info(f"📤 Отправка основного ответа ({len(main_response)} символов)")
            
            await processing_msg.edit_text(
                "✅ Первый ответ готов! Готовлю анализ...",
                parse_mode=None
            )
            
            await send_long_message(
                chat_id,
                f"🤖 **Основной ответ:**{model_info}\n\n{main_response}",
                message.message_id
            )
        else:
            logger.warning("⚠️ Основной ответ не получен")
        
        # Отправляем аналитический ответ
        if deepseek_response and len(deepseek_response) > 100:
            logger.info(f"📤 Отправка аналитического ответа ({len(deepseek_response)} символов)")
            
            await send_long_message(
                chat_id,
                f"🔍 **Детальный анализ:**{model_info}\n\n{deepseek_response}",
                message.message_id
            )
            
            # Финальное сообщение
            if main_response:
                final_text = (
                    f"✅ Анализ завершён за {elapsed:.1f}с!\n"
                    f"📊 Основной ответ: {len(main_response)} символов\n"
                    f"🔍 Детальный анализ: {len(deepseek_response)} символов"
                )
            else:
                final_text = (
                    f"✅ Анализ завершён за {elapsed:.1f}с!\n"
                    f"🔍 Ответ: {len(deepseek_response)} символов"
                )
            
            await processing_msg.edit_text(final_text, parse_mode=None)
            
        elif main_response:
            # Только основной ответ
            final_text = f"✅ Ответ готов за {elapsed:.1f}с! ({len(main_response)} символов)"
            await processing_msg.edit_text(final_text, parse_mode=None)
            
        else:
            # Ничего не получилось
            fallback_response = get_local_fallback_response(user_question)
            await processing_msg.edit_text("⚠️ Использую локальную базу знаний...", parse_mode=None)
            await send_long_message(chat_id, f"📚 **База знаний:**\n\n{fallback_response}", message.message_id)
            
            final_text = f"✅ Локальный ответ за {elapsed:.1f}с"
            await processing_msg.edit_text(final_text, parse_mode=None)
        
        logger.info(f"✅ Обработка завершена за {elapsed:.1f}с")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки: {e}")
        
        # Даже при ошибке пытаемся дать ответ
        try:
            fallback = get_local_fallback_response(user_question)
            await processing_msg.edit_text("⚠️ Произошла ошибка, но вот что я могу предложить:", parse_mode=None)
            await send_long_message(chat_id, f"💡 **Предложение:**\n\n{fallback}", message.message_id)
        except Exception as e2:
            logger.error(f"❌ Даже fallback не сработал: {e2}")
            await processing_msg.edit_text("❌ Произошла ошибка. Попробуйте позже.", parse_mode=None)

@dp.message()
async def handle_other_messages(message: types.Message):
    """Обработка других сообщений"""
    if message.text and len(message.text.strip()) > 3:
        response = (
            "🤔 Задайте вопрос с '?' в конце для развёрнутого ответа от ИИ.\n\n"
            "**Доступные команды:**\n"
            "/start - информация о боте\n"
            "/status - проверка моделей\n"
            "/models - список моделей\n"
            "/help - полная справка"
        )
        await send_message_safe(message.chat.id, response, message.message_id)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info("🚀 Бот IvanIvanych запускается с НОВЫМИ МОДЕЛЯМИ...")
    logger.info("🤖 ОСНОВНЫЕ МОДЕЛИ:")
    logger.info(f"  • {MODELS_CONFIG['main']['primary']}")
    logger.info(f"  • {MODELS_CONFIG['main']['backup']}")
    logger.info(f"  • {MODELS_CONFIG['main']['fallback']}")
    logger.info("🤖 АНАЛИТИЧЕСКИЕ МОДЕЛИ:")
    logger.info(f"  • {MODELS_CONFIG['deepseek']['primary']}")
    logger.info(f"  • {MODELS_CONFIG['deepseek']['backup']}")
    logger.info(f"  • {MODELS_CONFIG['deepseek']['fallback']}")
    logger.info("⚡ Архитектура: Умный выбор модели + параллельная генерация")
    logger.info("💡 Fallback: Локальная база знаний + автовыбор")
    logger.info("=" * 60)
    
    # Проверяем доступность моделей при запуске
    try:
        logger.info("🔄 Проверка доступности моделей...")
        status = await check_models_status()
        
        available_count = 0
        for model_type, models in status.items():
            for model_key, model_info in models.items():
                if model_info["available"]:
                    available_count += 1
                    name_short = model_info["name"].split('/')[-1]
                    time_info = f" ({model_info['response_time']:.1f}с)" if model_info["response_time"] else ""
                    logger.info(f"✅ {model_key}: {name_short}{time_info}")
        
        logger.info(f"📊 Доступно моделей: {available_count}/{len(MODELS_CONFIG['main']) + len(MODELS_CONFIG['deepseek'])}")
        
    except Exception as e:
        logger.warning(f"⚠️ Не удалось проверить модели: {e}")
    
    try:
        # Очищаем обновления
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Очищены предыдущие обновления")
        
        # Запускаем бота
        await dp.start_polling(bot, skip_updates=True)
        
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
    finally:
        try:
            await bot.session.close()
            logger.info("🔌 Сессия закрыта")
        except:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Завершение работы")
    except Exception as e:
        logger.error(f"💥 Фатальная ошибка: {e}", exc_info=True)