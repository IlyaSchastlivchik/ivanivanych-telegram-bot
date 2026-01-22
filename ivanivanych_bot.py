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
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_URL = f"{OPENROUTER_BASE_URL}/chat/completions"

# ==================== МОДЕЛИ ====================
MODELS_CONFIG = {
    "main": {
        "primary": "meta-llama/llama-3.3-70b-instruct:free",
        "backup": "qwen/qwen-2.5-vl-7b-instruct:free",
        "fallback": "qwen/qwen2.5-32b-instruct:free",
        "emergency": "microsoft/phi-3.5-mini-instruct:free"
    },
    "deepseek": {
        "primary": "deepseek/deepseek-r1-0528:free",
        "backup": "qwen/qwen3-coder:free",
        "fallback": "deepseek/deepseek-coder-33b-instruct:free",
        "emergency": "qwen/qwen2.5-32b-instruct:free"
    }
}

logger.info("🔧 Режим: ТОЛЬКО БЕСПЛАТНЫЕ МОДЕЛИ")

# Таймауты (в секундах)
MODEL_TIMEOUTS = {
    "fast": 45,
    "medium": 90,
    "slow": 150
}

# ==================== КОНФИГУРАЦИЯ ====================
GENERATION_CONFIG = {
    "temperature": 0.8,
    "max_tokens": 1000,
    "top_p": 0.9,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
}

DEEPSEEK_R1_CONFIG = {
    "temperature": 0.7,
    "max_tokens": 800,
    "top_p": 0.85,
    "frequency_penalty": 0.15,
    "presence_penalty": 0.1,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== УТИЛИТЫ ====================
def clean_text_safe(text: str) -> str:
    """Очистка текста от опасных символов"""
    if not text:
        return ""
    
    dangerous_chars = [
        '\u0000', '\u0001', '\u0002', '\u0003', '\u0004', '\u0005',
        '\u0006', '\u0007', '\u0008', '\u000b', '\u000c',
        '\u000e', '\u000f', '\u0010', '\u0011', '\u0012',
        '\u0013', '\u0014', '\u0015', '\u0016', '\u0017',
        '\u0018', '\u0019', '\u001a', '\u001b', '\u001c',
        '\u001d', '\u001e', '\u001f', '\u200b', '\u200c',
        '\u200d', '\ufeff'
    ]
    
    for char in dangerous_chars:
        text = text.replace(char, '')
    
    return text

def escape_markdown_v2(text: str) -> str:
    """Правильное экранирование MarkdownV2"""
    text = clean_text_safe(text)
    
    # Сначала экранируем все спецсимволы
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    return text

def format_code_blocks(text: str) -> str:
    """Форматирование блоков кода для MarkdownV2"""
    # Защищаем блоки кода с тройными кавычками
    def protect_triple_backtick(match):
        content = match.group(1)
        # Экранируем только начало и конец блока
        return f'```{content}```'
    
    # Ищем блоки кода с языком или без
    text = re.sub(r'```([\w]*)\n([\s\S]*?)\n```', 
                 lambda m: f'```{m.group(1)}\n{m.group(2)}\n```', text)
    
    return text

async def send_safe_message(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Упрощенная отправка сообщений"""
    try:
        # Сначала пробуем HTML
        html_text = text
        html_text = re.sub(r'```([\w]*)\n([\s\S]*?)\n```', 
                         r'<pre><code>\2</code></pre>', html_text)
        html_text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', html_text)
        
        kwargs = {
            "chat_id": chat_id,
            "text": html_text,
            "parse_mode": "HTML"
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Сообщение отправлено с HTML, длина: {len(text)} символов")
        return result
        
    except Exception as e:
        logger.warning(f"⚠️ HTML не сработал: {e}")
        
        try:
            # Пробуем MarkdownV2
            escaped_text = escape_markdown_v2(text)
            formatted_text = format_code_blocks(escaped_text)
            
            kwargs = {
                "chat_id": chat_id,
                "text": formatted_text,
                "parse_mode": "MarkdownV2"
            }
            if reply_to_message_id:
                kwargs["reply_to_message_id"] = reply_to_message_id
            
            result = await bot.send_message(**kwargs)
            logger.info(f"✅ Сообщение отправлено с MarkdownV2, длина: {len(text)} символов")
            return result
            
        except Exception as e2:
            logger.warning(f"⚠️ MarkdownV2 не сработал: {e2}")
            
            try:
                # Без форматирования
                cleaned_text = clean_text_safe(text)
                kwargs = {
                    "chat_id": chat_id,
                    "text": cleaned_text,
                    "parse_mode": None
                }
                if reply_to_message_id:
                    kwargs["reply_to_message_id"] = reply_to_message_id
                
                result = await bot.send_message(**kwargs)
                logger.info("✅ Сообщение отправлено без форматирования")
                return result
                
            except Exception as e3:
                logger.error(f"❌ Не удалось отправить сообщение: {e3}")
                return None

def split_message_smart(text: str, max_length: int = 3500) -> List[str]:
    """Умное разбиение сообщений"""
    if len(text) <= max_length:
        return [text]
    
    # Пробуем разбить по двойным переносам строк
    parts = []
    paragraphs = text.split('\n\n')
    
    current_part = ""
    for para in paragraphs:
        if len(current_part) + len(para) + 2 <= max_length:
            current_part += para + "\n\n"
        else:
            if current_part:
                parts.append(current_part.strip())
            current_part = para + "\n\n"
    
    if current_part:
        parts.append(current_part.strip())
    
    # Если все равно слишком длинные, разбиваем по строкам
    if any(len(p) > max_length for p in parts):
        new_parts = []
        for part in parts:
            if len(part) <= max_length:
                new_parts.append(part)
            else:
                lines = part.split('\n')
                current = ""
                for line in lines:
                    if len(current) + len(line) + 1 <= max_length:
                        current += line + "\n"
                    else:
                        if current:
                            new_parts.append(current.strip())
                        current = line + "\n"
                if current:
                    new_parts.append(current.strip())
        parts = new_parts
    
    return parts

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """Отправка длинных сообщений"""
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    parts = split_message_smart(text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        part_length = len(part)
        logger.info(f"📤 Часть {i+1}/{len(parts)}: {part_length} символов")
        
        message = await send_safe_message(
            chat_id=chat_id,
            text=part,
            reply_to_message_id=reply_to_message_id if i == 0 else None
        )
        
        if message:
            logger.info(f"✅ Часть {i+1}/{len(parts)} отправлена")
        else:
            logger.error(f"❌ Не удалось отправить часть {i+1}/{len(parts)}")
        
        if i < len(parts) - 1:
            await asyncio.sleep(0.3)

# ==================== OPENROUTER ФУНКЦИИ ====================
async def test_model_speed(model: str) -> float:
    """Тестирование скорости модели"""
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
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                elapsed = time.time() - start
                
                if response.status == 200:
                    return elapsed
                else:
                    return float('inf')
    except:
        return float('inf')

def get_model_timeout(model: str) -> int:
    """Определение таймаута для модели"""
    model_lower = model.lower()
    
    if "phi-3.5" in model_lower or "qwen-2.5-7b" in model_lower:
        return MODEL_TIMEOUTS["fast"]
    elif "qwen2.5-32b" in model_lower or "coder" in model_lower:
        return MODEL_TIMEOUTS["medium"]
    elif "llama" in model_lower or "deepseek-r1" in model_lower or "qwen3-coder" in model_lower:
        return MODEL_TIMEOUTS["slow"]
    
    return MODEL_TIMEOUTS["medium"]

async def try_model_with_retry(
    model_list: List[str],
    user_question: str,
    system_prompt: Dict[str, str],
    max_retries: int = 2
) -> Tuple[Optional[str], Optional[str], int]:
    """Попытка использовать модель с повторными попытками"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    # Тестируем модели
    speeds = {}
    for model in model_list:
        speed = await test_model_speed(model)
        speeds[model] = speed
        if speed < float('inf'):
            logger.info(f"  • {model.split('/')[-1]}: {speed:.2f}с")
    
    # Сортируем по скорости
    sorted_models = sorted(
        [m for m, s in speeds.items() if s < float('inf')],
        key=lambda x: speeds[x]
    )
    
    if not sorted_models:
        logger.warning("⚠️ Ни одна модель не ответила")
        sorted_models = model_list
    
    # Пробуем каждую модель
    for best_model in sorted_models:
        model_timeout = get_model_timeout(best_model)
        logger.info(f"🎯 Выбрана модель: {best_model.split('/')[-1]} (таймаут: {model_timeout}с)")
        
        for attempt in range(max_retries):
            try:
                # Специальные настройки для DeepSeek R1
                if "deepseek-r1" in best_model.lower():
                    config = DEEPSEEK_R1_CONFIG
                else:
                    config = GENERATION_CONFIG
                
                data = {
                    "model": best_model,
                    "messages": [
                        system_prompt,
                        {"role": "user", "content": user_question}
                    ],
                    **config
                }
                
                timeout = aiohttp.ClientTimeout(total=model_timeout)
                
                logger.info(f"🚀 Запрос к {best_model.split('/')[-1]} (попытка {attempt+1}/{max_retries})...")
                start_time = time.time()
                
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(
                        OPENROUTER_URL, 
                        headers=headers, 
                        json=data
                    ) as response:
                        
                        elapsed = time.time() - start_time
                        
                        if response.status == 200:
                            result = await response.json()
                            if 'choices' in result and result['choices']:
                                text = result['choices'][0]['message'].get('content', '').strip()
                                
                                if text and len(text) > 20 and not text.isspace():
                                    # Проверяем и корректируем блоки кода
                                    backtick_count = text.count('`')
                                    if backtick_count % 2 != 0:
                                        text += '`'
                                    
                                    code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text))
                                    logger.info(f"✅ {best_model.split('/')[-1]} ответил за {elapsed:.1f}с, {len(text)} символов, блоков кода: {code_blocks}")
                                    return text, best_model, code_blocks
                                else:
                                    logger.warning(f"⚠️ {best_model.split('/')[-1]} вернул некорректный ответ: {len(text)} символов")
                        else:
                            error_text = await response.text()
                            logger.warning(f"⚠️ {best_model.split('/')[-1]} ошибка [{response.status}]: {error_text[:200]}")
                    
                    if attempt < max_retries - 1:
                        wait_time = 1.5 * (attempt + 1)
                        logger.info(f"🔄 Повторная попытка через {wait_time} секунд...")
                        await asyncio.sleep(wait_time)
                        
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ Таймаут {best_model.split('/')[-1]} (> {model_timeout}с)")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"❌ Ошибка {best_model.split('/')[-1]}: {e}")
                break
        
        logger.warning(f"❌ Модель {best_model} не сработала после {max_retries} попыток")
    
    logger.warning("❌ Все модели не сработали")
    return None, None, 0

# ==================== ЛОКАЛЬНЫЙ FALLBACK ====================
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
        "💻 **Пример кода на Python**\n\n"
        "Вот простой пример, который читает имя пользователя и выводит приветствие:\n\n"
        "```python\nname = input('Как тебя зовут? ')  # Запрос ввода\nprint(f'Привет, {name}!')  # Вывод приветствия\n```\n\n"
        "**Объяснение:**\n"
        "1. `input()` - функция для получения ввода от пользователя\n"
        "2. `print()` - функция для вывода текста\n"
        "3. `f-строка` - удобный способ форматирования строк"
    ],
    "общий": [
        "🧠 **Анализ запроса**\n\n"
        "ИИ-модели могут генерировать код на различных языках программирования. Вот основные возможности:\n\n"
        "**Для Python:**\n"
        "• Простые скрипты и утилиты\n"
        "• Обработка данных и анализ\n"
        "• Веб-приложения и API\n"
        "• Машинное обучение и AI\n\n"
        "**Для других языков:**\n"
        "• JavaScript для веб-разработки\n"
        "• Java для Android приложений\n"
        "• C++ для системного программирования\n"
        "• SQL для работы с базами данных\n\n"
        "⏱️ **Рекомендации:** Уточните язык программирования и задачу для получения более конкретного примера."
    ]
}

def get_local_fallback_response(user_question: str) -> str:
    """Генерация локального ответа"""
    question_lower = user_question.lower()
    
    if any(word in question_lower for word in ['код', 'пример', 'программир', 'python', 'javascript']):
        topic = "код"
    elif any(word in question_lower for word in ['шаг', 'план', 'внедр', 'настрой', 'установ']):
        topic = "технология"
    else:
        topic = "общий"
    
    responses = LOCAL_RESPONSES[topic]
    return random.choice(responses)

async def get_ai_response(user_question: str, response_type: str = "main") -> Tuple[Optional[str], Optional[str], int]:
    """Получение ответа от AI"""
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
                "Ты Иван Иваныч — эксперт в технологиях. "
                "Отвечай ясно и по делу. Используй Markdown для форматирования. "
                "Для кода используй тройные кавычки с указанием языка. "
                "Всегда закрывай блок кода. "
                "Держи ответ в 800-1200 символов."
            )
        }
    else:
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
                "Используй Markdown, для кода — тройные кавычки с языком. "
                "Всегда закрывай блоки кода. "
                "Будь конкретным и техничным. 1000-1500 символов."
            )
        }
    
    response, model_used, code_blocks = await try_model_with_retry(models, user_question, system_prompt)
    
    if not response:
        logger.warning("⚠️ Все модели не ответили, использую локальный fallback")
        response = get_local_fallback_response(user_question)
        model_used = "local_fallback"
        code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', response))
    
    return response, model_used, code_blocks

async def get_parallel_responses(user_question: str) -> Tuple[
    Optional[str], Optional[str], Optional[str], Optional[str], int, int
]:
    """Параллельное получение ответов"""
    main_task = asyncio.create_task(get_ai_response(user_question, "main"))
    deepseek_task = asyncio.create_task(get_ai_response(user_question, "deepseek"))
    
    try:
        main_response, main_model, main_code_blocks = await main_task
        deepseek_response, deepseek_model, deepseek_code_blocks = await deepseek_task
    except Exception as e:
        logger.error(f"Ошибка в параллельных запросах: {e}")
        main_response = deepseek_response = None
        main_model = deepseek_model = None
        main_code_blocks = deepseek_code_blocks = 0
    
    return main_response, deepseek_response, main_model, deepseek_model, main_code_blocks, deepseek_code_blocks

# ==================== ОБРАБОТЧИКИ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    welcome = (
        "👋 Привет! Я Иван Иваныч — бот с продвинутыми ИИ-моделями\n\n"
        "🚀 **Доступные модели:**\n"
        "• **Llama 3.3 70B** — мощная аналитическая модель\n"
        "• **DeepSeek R1** — глубокая аналитика\n"
        "• **Qwen модели** — различные задачи\n\n"
        "🤖 **Возможности:**\n"
        "• Генерация кода с подсветкой\n"
        "• Технический анализ\n"
        "• Ответы на вопросы\n\n"
        "⚡ **Пример кода:**\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_safe_message(message.chat.id, welcome, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Команда /status"""
    status_text = "🔄 Проверяю доступность моделей..."
    status_msg = await send_safe_message(message.chat.id, status_text, message.message_id)
    
    try:
        test_models = [
            MODELS_CONFIG["main"]["primary"],
            MODELS_CONFIG["main"]["backup"],
            MODELS_CONFIG["deepseek"]["primary"],
            MODELS_CONFIG["deepseek"]["backup"],
        ]
        
        status_report = "📊 **Статус моделей:**\n\n"
        
        for model in test_models:
            speed = await test_model_speed(model)
            emoji = "✅" if speed < float('inf') else "❌"
            name_short = model.split('/')[-1]
            time_info = f" ({speed:.1f}с)" if speed < float('inf') else " (недоступна)"
            
            status_report += f"{emoji} `{name_short}`{time_info}\n"
        
        status_report += f"\n⏱️ **Таймауты:** Быстрые: {MODEL_TIMEOUTS['fast']}с, Средние: {MODEL_TIMEOUTS['medium']}с, Глубокие: {MODEL_TIMEOUTS['slow']}с"
        
        if status_msg:
            await status_msg.edit_text(status_report, parse_mode="HTML")
        else:
            await send_safe_message(message.chat.id, status_report, message.message_id)
            
    except Exception as e:
        error_text = f"❌ Ошибка проверки: {str(e)[:100]}"
        if status_msg:
            await status_msg.edit_text(error_text, parse_mode=None)
        else:
            await send_safe_message(message.chat.id, error_text, message.message_id)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Обработка вопросов"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = message.from_user.username or f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    processing_msg = None
    try:
        processing_text = "🤔 Две ИИ-модели анализируют вопрос..."
        processing_msg = await send_safe_message(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            return
        
        start_time = time.time()
        
        logger.info("⚡ Параллельные запросы запущены...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        main_response, deepseek_response, main_model, deepseek_model, main_code_blocks, deepseek_code_blocks = await get_parallel_responses(user_question)
        
        elapsed = time.time() - start_time
        
        # Формируем список использованных моделей
        models_used = []
        if main_model and main_model != "local_fallback":
            models_used.append(main_model.split('/')[-1])
        if deepseek_model and deepseek_model != "local_fallback":
            models_used.append(deepseek_model.split('/')[-1])
        
        if main_response:
            logger.info(f"📤 Отправка основного ответа ({len(main_response)} символов)")
            
            await processing_msg.edit_text(
                "✅ Первый ответ готов! Готовлю анализ...",
                parse_mode=None
            )
            
            await send_long_message(
                chat_id,
                f"🤖 **Основной ответ:**\n\n{main_response}",
                message.message_id
            )
        else:
            logger.warning("⚠️ Основной ответ не получен")
        
        if deepseek_response and len(deepseek_response) > 100:
            logger.info(f"📤 Отправка аналитического ответа ({len(deepseek_response)} символов)")
            
            await send_long_message(
                chat_id,
                f"🔍 **Детальный анализ:**\n\n{deepseek_response}",
                message.message_id
            )
            
            if main_response:
                total_code_blocks = main_code_blocks + deepseek_code_blocks
                completion_text = (
                    f"✅ Анализ завершён!\n"
                    f"⏱️ Общее время: {elapsed:.1f} секунд\n"
                    f"📊 Основной ответ: {len(main_response)} символов\n"
                    f"🔍 Детальный анализ: {len(deepseek_response)} символов"
                )
                
                if total_code_blocks > 0:
                    completion_text += f"\n💻 Код: {total_code_blocks} блок(ов)"
                
                if models_used:
                    completion_text += f"\n🤖 Модели: {', '.join(models_used)}"
                    
            else:
                completion_text = (
                    f"✅ Анализ завершён!\n"
                    f"⏱️ Время: {elapsed:.1f} секунд\n"
                    f"🔍 Ответ: {len(deepseek_response)} символов"
                )
                
                if deepseek_code_blocks > 0:
                    completion_text += f"\n💻 Код: {deepseek_code_blocks} блок(ов)"
                
                if models_used:
                    completion_text += f"\n🤖 Модели: {', '.join(models_used)}"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Успешно! Время: {elapsed:.1f}с, модели: {', '.join(models_used) if models_used else 'local_fallback'}")
            
        elif main_response:
            completion_text = (
                f"✅ Ответ готов!\n"
                f"⏱️ Время: {elapsed:.1f} секунд\n"
                f"📊 Длина: {len(main_response)} символов"
            )
            
            if main_code_blocks > 0:
                completion_text += f"\n💻 Код: {main_code_blocks} блок(ов)"
            
            if models_used:
                completion_text += f"\n🤖 Модели: {', '.join(models_used)}"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Основной ответ за {elapsed:.1f}с, модель: {models_used[0] if models_used else 'local_fallback'}")
            
        else:
            fallback_response = get_local_fallback_response(user_question)
            await processing_msg.edit_text("⚠️ Использую локальную базу знаний...", parse_mode=None)
            
            await send_long_message(
                chat_id, 
                f"📚 **База знаний:**\n\n{fallback_response}", 
                message.message_id
            )
            
            fallback_code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', fallback_response))
            completion_text = f"✅ Локальный ответ за {elapsed:.1f}с"
            
            if fallback_code_blocks > 0:
                completion_text += f"\n💻 Код: {fallback_code_blocks} блок(ов)"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
        
        logger.info(f"✅ Обработка завершена за {elapsed:.1f}с")
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки: {e}")
        
        try:
            fallback = get_local_fallback_response(user_question)
            await processing_msg.edit_text("⚠️ Произошла ошибка, но вот что я могу предложить:", parse_mode=None)
            await send_long_message(chat_id, f"💡 **Предложение:**\n\n{fallback}", message.message_id)
        except Exception as e2:
            logger.error(f"❌ Даже fallback не сработал: {e2}")
            if processing_msg:
                await processing_msg.edit_text("❌ Произошла ошибка. Попробуйте позже.", parse_mode=None)

# ==================== ЗАПУСК ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info("🚀 Бот IvanIvanych запускается...")
    logger.info("🤖 ОСНОВНЫЕ МОДЕЛИ:")
    logger.info(f"  • {MODELS_CONFIG['main']['primary']}")
    logger.info(f"  • {MODELS_CONFIG['main']['backup']}")
    logger.info("🤖 АНАЛИТИЧЕСКИЕ МОДЕЛИ:")
    logger.info(f"  • {MODELS_CONFIG['deepseek']['primary']}")
    logger.info(f"  • {MODELS_CONFIG['deepseek']['backup']}")
    logger.info("=" * 60)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Очищены предыдущие обновления")
        
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