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

# ==================== МОДЕЛИ С УВЕЛИЧЕННЫМИ ТАЙМАУТАМИ ====================
MODELS_CONFIG = {
    "main": {
        "primary": "meta-llama/llama-3.3-70b-instruct:free",      # Мощная, но медленная
        "backup": "qwen/qwen3-next-80b-a3b-instruct:free",        # Очень мощная, медленная
        "fallback": "google/gemma-3-4b-it:free",                  # Быстрая
        "emergency": "microsoft/phi-3.5-mini-instruct:free"       # Очень быстрая
    },
    "deepseek": {
        "primary": "deepseek/deepseek-r1-0528:free",              # Глубокая, медленная
        "backup": "deepseek/deepseek-coder-33b-instruct:free",    # Для кода, средняя
        "fallback": "qwen/qwen2.5-32b-instruct:free",             # Сбалансированная
        "emergency": "google/gemma-3-4b-it:free"                  # Быстрая резервная
    }
}

# ВРЕМЕННО ИСПОЛЬЗУЕМ ТОЛЬКО БЕСПЛАТНЫЕ МОДЕЛИ
logger.info("🔧 Режим: ТОЛЬКО БЕСПЛАТНЫЕ МОДЕЛИ с увеличенными таймаутами")

# Таймауты для разных типов моделей (в секундах)
MODEL_TIMEOUTS = {
    "fast": 45,      # Быстрые модели: gemma, phi-3.5
    "medium": 90,    # Средние: qwen2.5, deepseek-coder
    "slow": 150      # Медленные: llama-70b, qwen3-80b, deepseek-r1
}

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
def clean_text_safe(text: str) -> str:
    """Безопасная очистка текста - только удаляем опасные символы"""
    if not text:
        return ""
    
    # Находим и защищаем блоки кода с ```
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    
    def protect_code_block(match):
        code_content = match.group(0)
        inner_content = match.group(1)
        cleaned_inner = ''.join(char for char in inner_content 
                               if unicodedata.category(char)[0] != 'C' 
                               or char == '\n' or char == '\t' or char == '\r')
        cleaned_inner = cleaned_inner.replace('\u0000', '').replace('\u0001', '').replace('\u0002', '')
        cleaned_inner = cleaned_inner.replace('\u0003', '').replace('\u0004', '').replace('\u0005', '')
        
        language = match.group(0)[3:].split('\n')[0].strip()
        if language and language != '```':
            return f"```{language}\n{cleaned_inner}\n```"
        else:
            return f"```\n{cleaned_inner}\n```"
    
    text = re.sub(code_block_pattern, protect_code_block, text)
    
    # Обрабатываем оставшийся текст
    text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C' 
                  or char == '\n' or char == '\t' or char == '\r' or char == '`')
    
    dangerous_chars = ['\u0000', '\u0001', '\u0002', '\u0003', '\u0004', '\u0005',
                      '\u0006', '\u0007', '\u0008', '\u000b', '\u000c',
                      '\u000e', '\u000f', '\u0010', '\u0011', '\u0012',
                      '\u0013', '\u0014', '\u0015', '\u0016', '\u0017',
                      '\u0018', '\u0019', '\u001a', '\u001b', '\u001c',
                      '\u001d', '\u001e', '\u001f', '\u200b', '\u200c',
                      '\u200d', '\ufeff']
    
    for char in dangerous_chars:
        text = text.replace(char, '')
    
    return text

def escape_markdown_v2_final(text: str) -> str:
    """ФИНАЛЬНАЯ версия экранирования MarkdownV2"""
    text = clean_text_safe(text)
    
    # Защищаем блоки кода
    code_blocks = []
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    
    def replace_code_block(match):
        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    text = re.sub(code_block_pattern, replace_code_block, text)
    
    # Защищаем inline код
    inline_code_blocks = []
    inline_pattern = r'`([^`\n]+)`'
    
    def replace_inline_code(match):
        placeholder = f"__INLINE_CODE_{len(inline_code_blocks)}__"
        inline_code_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    text = re.sub(inline_pattern, replace_inline_code, text)
    
    # Экранируем оставшийся текст
    text = text.replace('\\', '\\\\')
    
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    # Восстанавливаем inline код
    for placeholder, inline_code in inline_code_blocks:
        text = text.replace(placeholder, inline_code)
    
    # Восстанавливаем блоки кода
    for placeholder, code_block in code_blocks:
        text = text.replace(placeholder, code_block)
    
    return text

def split_message_smart_final(text: str, max_length: int = 3500) -> List[str]:
    """ФИНАЛЬНАЯ версия разбиения сообщений"""
    if len(text) <= max_length:
        return [text]
    
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    code_matches = list(re.finditer(code_block_pattern, text))
    
    if not code_matches:
        parts = []
        current = ""
        paragraphs = text.split('\n\n')
        
        for para in paragraphs:
            if len(current) + len(para) + 2 <= max_length:
                current += para + "\n\n"
            else:
                if current:
                    parts.append(current.strip())
                current = para + "\n\n"
        
        if current:
            parts.append(current.strip())
        
        return parts
    
    parts = []
    current_pos = 0
    
    for match in code_matches:
        code_start = match.start()
        code_end = match.end()
        code_block = match.group(0)
        
        text_before = text[current_pos:code_start]
        if text_before:
            text_parts = split_message_smart_final(text_before, max_length)
            if text_parts:
                if parts:
                    parts[-1] += text_parts[0]
                    parts.extend(text_parts[1:])
                else:
                    parts.extend(text_parts)
        
        if parts and len(parts[-1]) + len(code_block) <= max_length:
            parts[-1] += code_block
        else:
            parts.append(code_block)
        
        current_pos = code_end
    
    text_after = text[current_pos:]
    if text_after:
        text_parts = split_message_smart_final(text_after, max_length)
        if text_parts:
            if parts and len(parts[-1]) + len(text_parts[0]) <= max_length:
                parts[-1] += text_parts[0]
                parts.extend(text_parts[1:])
            else:
                parts.extend(text_parts)
    
    return parts

async def send_safe_message_final(chat_id: int, text: str, reply_to_message_id: int = None, 
                                 parse_mode: str = "MarkdownV2") -> Optional[types.Message]:
    """ФИНАЛЬНАЯ версия отправки сообщений"""
    try:
        escaped_text = escape_markdown_v2_final(text)
        
        backtick_count = escaped_text.count('`')
        if backtick_count % 2 != 0:
            logger.warning(f"⚠️ Нечётное количество кавычек: {backtick_count}")
            escaped_text += '`'
        
        kwargs = {
            "chat_id": chat_id,
            "text": escaped_text,
            "parse_mode": parse_mode
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Сообщение отправлено с MarkdownV2, длина: {len(escaped_text)} символов")
        return result
        
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"⚠️ MarkdownV2 не сработал: {error_msg}")
        
        if "PreCode" in error_msg or "can't parse" in error_msg:
            logger.warning("⚠️ Проблема с блоками кода, пробуем альтернативный метод...")
            try:
                html_text = text
                html_text = re.sub(r'```(?:[\w]*)\n([\s\S]*?)\n```', 
                                 r'<pre><code>\1</code></pre>', html_text)
                html_text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', html_text)
                
                kwargs = {
                    "chat_id": chat_id,
                    "text": html_text,
                    "parse_mode": "HTML"
                }
                if reply_to_message_id:
                    kwargs["reply_to_message_id"] = reply_to_message_id
                
                result = await bot.send_message(**kwargs)
                logger.info("✅ Сообщение отправлено с HTML форматированием")
                return result
            except Exception as html_e:
                logger.warning(f"⚠️ HTML тоже не сработал: {html_e}")
    
    try:
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
        
    except Exception as e:
        logger.error(f"❌ Не удалось отправить сообщение вообще: {e}")
        return None

async def send_long_message_final(chat_id: int, text: str, reply_to_message_id: int = None):
    """ФИНАЛЬНАЯ версия отправки длинных сообщений"""
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text)
    inline_codes = re.findall(r'`[^`\n]+`', text)
    
    parts = split_message_smart_final(text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        part_length = len(part)
        logger.info(f"📤 Часть {i+1}/{len(parts)}: {part_length} символов")
        
        part_code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', part)
        if part_code_blocks:
            logger.info(f"📤 Часть {i+1} содержит {len(part_code_blocks)} блок(ов) кода")
        
        message = await send_safe_message_final(
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

# ==================== OPENROUTER ФУНКЦИИ С УВЕЛИЧЕННЫМИ ТАЙМАУТАМИ ====================
async def test_model_speed(model: str) -> float:
    """Тестирует скорость ответа модели с увеличенным таймаутом"""
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
        # Увеличиваем таймаут для тестирования
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
    """Определяет таймаут для модели на основе её типа"""
    model_lower = model.lower()
    
    # Быстрые модели
    if "gemma" in model_lower or "phi" in model_lower:
        return MODEL_TIMEOUTS["fast"]
    
    # Средние модели
    elif "qwen2.5" in model_lower or "coder" in model_lower:
        return MODEL_TIMEOUTS["medium"]
    
    # Медленные/глубокие модели
    elif "llama" in model_lower or "qwen3" in model_lower or "deepseek" in model_lower:
        return MODEL_TIMEOUTS["slow"]
    
    # По умолчанию - средний таймаут
    return MODEL_TIMEOUTS["medium"]

async def try_model_with_retry(
    model_list: List[str],
    user_question: str,
    system_prompt: Dict[str, str],
    max_retries: int = 2
) -> Tuple[Optional[str], Optional[str], int]:
    """Пробует несколько моделей с увеличенными таймаутами"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    # Тестируем все модели, а не только первые 3
    speeds = {}
    
    for model in model_list:
        speed = await test_model_speed(model)
        speeds[model] = speed
        if speed < float('inf'):
            logger.info(f"  • {model.split('/')[-1]}: {speed:.2f}с")
    
    # Сортируем модели по скорости (быстрые первыми)
    sorted_models = sorted(
        [m for m, s in speeds.items() if s < float('inf')],
        key=lambda x: speeds[x]
    )
    
    # Если ни одна модель не ответила, используем первую из списка
    if not sorted_models:
        logger.warning("⚠️ Ни одна модель не ответила при тестировании")
        sorted_models = model_list
    
    # Пробуем каждую модель по порядку
    for best_model in sorted_models:
        model_timeout = get_model_timeout(best_model)
        logger.info(f"🎯 Выбрана модель: {best_model.split('/')[-1]} (таймаут: {model_timeout}с)")
        
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
                                if text and len(text) > 10:  # Минимальная длина ответа
                                    # Проверяем блоки кода
                                    backtick_count = text.count('`')
                                    if backtick_count % 2 != 0:
                                        logger.warning(f"⚠️ Нечётное количество кавычек: {backtick_count}")
                                        text += '`'
                                    
                                    code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text))
                                    logger.info(f"✅ {best_model.split('/')[-1]} ответил за {elapsed:.1f}с, {len(text)} символов, блоков кода: {code_blocks}")
                                    return text, best_model, code_blocks
                                else:
                                    logger.warning(f"⚠️ {best_model.split('/')[-1]} вернул слишком короткий ответ: {len(text)} символов")
                        else:
                            error_text = await response.text()
                            logger.warning(f"⚠️ {best_model.split('/')[-1]} ошибка [{response.status}]: {error_text[:200]}")
                    
                    if attempt < max_retries - 1:
                        logger.info(f"🔄 Повторная попытка через 1 секунду...")
                        await asyncio.sleep(1)
                        
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ Таймаут {best_model.split('/')[-1]} (> {model_timeout}с)")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
            except aiohttp.ClientError as e:
                logger.warning(f"🌐 Сетевая ошибка {best_model.split('/')[-1]}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"❌ Неизвестная ошибка {best_model.split('/')[-1]}: {e}")
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
    ]
}

def get_local_fallback_response(user_question: str) -> str:
    """Генерация локального ответа если API недоступно"""
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
    """Получает ответ от AI"""
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
    """Параллельное получение ответов от обеих систем"""
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

# ==================== ОБРАБОТЧИКИ ТЕЛЕГРАМ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    welcome = (
        "👋 Привет! Я Иван Иваныч — бот с продвинутыми ИИ-моделями\n\n"
        "🚀 **Новые мощные модели:**\n"
        "• **Qwen3 Next 80B** — самая мощная бесплатная модель\n"
        "• **Gemma 3 4B** — быстрая и эффективная\n"
        "• **DeepSeek R1** — глубокая аналитика\n\n"
        "🤖 **Особенности:**\n"
        "• Работающая подсветка кода в Telegram\n"
        "• Увеличенные таймауты для глубоких моделей\n"
        "• Параллельная генерация от двух ИИ\n"
        "• Статистика ответов и времени\n\n"
        "⚡ **Пример кода с подсветкой:**\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_safe_message_final(message.chat.id, welcome, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Команда /status - проверка доступности моделей"""
    status_text = "🔄 Проверяю доступность моделей..."
    status_msg = await send_safe_message_final(message.chat.id, status_text, message.message_id)
    
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
            await status_msg.edit_text(status_report, parse_mode="MarkdownV2")
        else:
            await send_safe_message_final(message.chat.id, status_report, message.message_id)
            
    except Exception as e:
        error_text = f"❌ Ошибка проверки: {str(e)[:100]}"
        if status_msg:
            await status_msg.edit_text(error_text, parse_mode=None)
        else:
            await send_safe_message_final(message.chat.id, error_text, message.message_id)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Обработка вопросов с подсветкой кода"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = message.from_user.username or f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    processing_msg = None
    try:
        processing_text = "🤔 Две ИИ-модели анализируют вопрос параллельно..."
        processing_msg = await send_safe_message_final(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            return
        
        start_time = time.time()
        
        logger.info("⚡ Параллельные запросы запущены...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        main_response, deepseek_response, main_model, deepseek_model, main_code_blocks, deepseek_code_blocks = await get_parallel_responses(user_question)
        
        elapsed = time.time() - start_time
        
        if main_response:
            logger.info(f"📤 Отправка основного ответа ({len(main_response)} символов)")
            
            await processing_msg.edit_text(
                "✅ Первый ответ готов! Готовлю анализ...",
                parse_mode=None
            )
            
            await send_long_message_final(
                chat_id,
                f"🤖 **Основной ответ:**\n\n{main_response}",
                message.message_id
            )
        else:
            logger.warning("⚠️ Основной ответ не получен")
        
        if deepseek_response and len(deepseek_response) > 100:
            logger.info(f"📤 Отправка аналитического ответа ({len(deepseek_response)} символов)")
            
            await send_long_message_final(
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
                
                if main_model and main_model != "local_fallback":
                    model_name = main_model.split('/')[-1]
                    completion_text += f"\n🤖 Модели: {model_name}"
                    
            else:
                completion_text = (
                    f"✅ Анализ завершён!\n"
                    f"⏱️ Время: {elapsed:.1f} секунд\n"
                    f"🔍 Ответ: {len(deepseek_response)} символов"
                )
                
                if deepseek_code_blocks > 0:
                    completion_text += f"\n💻 Код: {deepseek_code_blocks} блок(ов)"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Успешно! Время: {elapsed:.1f}с")
            
        elif main_response:
            completion_text = (
                f"✅ Ответ готов!\n"
                f"⏱️ Время: {elapsed:.1f} секунд\n"
                f"📊 Длина: {len(main_response)} символов"
            )
            
            if main_code_blocks > 0:
                completion_text += f"\n💻 Код: {main_code_blocks} блок(ов)"
            
            if main_model and main_model != "local_fallback":
                model_name = main_model.split('/')[-1]
                completion_text += f"\n🤖 Модель: {model_name}"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Основной ответ за {elapsed:.1f}с")
            
        else:
            fallback_response = get_local_fallback_response(user_question)
            await processing_msg.edit_text("⚠️ Использую локальную базу знаний...", parse_mode=None)
            
            await send_long_message_final(
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
            await send_long_message_final(chat_id, f"💡 **Предложение:**\n\n{fallback}", message.message_id)
        except Exception as e2:
            logger.error(f"❌ Даже fallback не сработал: {e2}")
            if processing_msg:
                await processing_msg.edit_text("❌ Произошла ошибка. Попробуйте позже.", parse_mode=None)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info("🚀 Бот IvanIvanych запускается с УВЕЛИЧЕННЫМИ ТАЙМАУТАМИ...")
    logger.info("🤖 ОСНОВНЫЕ МОДЕЛИ:")
    logger.info(f"  • {MODELS_CONFIG['main']['primary']}")
    logger.info(f"  • {MODELS_CONFIG['main']['backup']}")
    logger.info("🤖 АНАЛИТИЧЕСКИЕ МОДЕЛИ:")
    logger.info(f"  • {MODELS_CONFIG['deepseek']['primary']}")
    logger.info(f"  • {MODELS_CONFIG['deepseek']['backup']}")
    logger.info("⏱️ ТАЙМАУТЫ:")
    logger.info(f"  • Быстрые модели: {MODEL_TIMEOUTS['fast']}с")
    logger.info(f"  • Средние модели: {MODEL_TIMEOUTS['medium']}с")
    logger.info(f"  • Глубокие модели: {MODEL_TIMEOUTS['slow']}с")
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