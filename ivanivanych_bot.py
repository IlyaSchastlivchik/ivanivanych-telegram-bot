import asyncio
import logging
import os
import aiohttp
import re
import time
import unicodedata
import json
import random
import html
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

# ==================== КОНФИГУРАЦИЯ МОДЕЛЕЙ ====================
MODELS_CONFIG = {
    # Основной стек моделей (пробуются по порядку)
    "primary_models": [
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-vl-7b-instruct:free",
    ],
    
    # Платные модели (только если указан USE_PAID_MODELS=true в .env)
    "paid_models": [
        "deepseek/deepseek-v3.2",
        "meta-llama/llama-3.3-70b-instruct",
    ],
    
    # Экстренный fallback
    "fallback_models": [
        "microsoft/phi-3.5-mini-instruct:free",
        "qwen/qwen2.5-32b-instruct:free",
    ]
}

# Флаг для разрешения платных моделей
USE_PAID_MODELS = os.getenv("USE_PAID_MODELS", "false").lower() == "true"

# УВЕЛИЧЕННЫЕ таймауты (в секундах)
MODEL_TIMEOUTS = {
    "fast": 45,      # Быстрые модели (phi, qwen-7b)
    "medium": 60,    # Средние модели
    "slow": 90,      # Медленные модели (llama 70b)
    "paid": 120,     # Платные модели (больше времени для качественного ответа)
    "test": 30,      # Таймаут для тестовой проверки
}

# ==================== КОНФИГУРАЦИЯ ГЕНЕРАЦИИ ====================
GENERATION_CONFIG = {
    "temperature": 0.8,
    "max_tokens": 1500,
    "top_p": 0.9,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
    "stream": False,  # Отключаем стриминг для упрощения
}

# Специальные настройки для платных моделей
PAID_MODEL_CONFIG = {
    "temperature": 0.7,
    "max_tokens": 2000,
    "top_p": 0.85,
    "frequency_penalty": 0.15,
    "presence_penalty": 0.1,
    "stream": False,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== УТИЛИТЫ ОБРАБОТКИ ТЕКСТА ====================
def clean_text(text: str) -> str:
    """Очистка текста от опасных символов"""
    if not text:
        return ""
    
    cleaned = []
    for char in text:
        cat = unicodedata.category(char)
        if cat[0] == 'C' and char not in ['\n', '\r', '\t']:
            continue
        cleaned.append(char)
    
    text = ''.join(cleaned)
    
    # Удаляем опасные символы
    dangerous_chars = [
        '\u0000-\u0008', '\u000b', '\u000c', '\u000e-\u001f',
        '\u200b', '\u200c', '\u200d', '\ufeff'
    ]
    
    for char_range in dangerous_chars:
        if '-' in char_range:
            start, end = ord(char_range[0]), ord(char_range[-1])
            text = ''.join([c for c in text if ord(c) < start or ord(c) > end])
        else:
            text = text.replace(char_range, '')
    
    return text

def prepare_html_message(text: str) -> str:
    """Подготовка HTML сообщения с корректной обработкой кода"""
    text = clean_text(text)
    
    # Экранируем HTML символы во всем тексте
    text = html.escape(text)
    
    # Восстанавливаем блоки кода
    def restore_code_block(match):
        language = match.group(1) if match.group(1) else ''
        code_content = match.group(2)
        # Отменяем экранирование внутри кода
        code_content = code_content.replace('&lt;', '<').replace('&gt;', '>')
        code_content = code_content.replace('&amp;', '&').replace('&quot;', '"')
        code_content = code_content.replace('&#x27;', "'")
        code_content = code_content.replace('&#x2F;', '/')
        
        if language:
            return f'<pre><code class="language-{language}">{code_content}</code></pre>'
        else:
            return f'<pre><code>{code_content}</code></pre>'
    
    # Обрабатываем блоки кода с тройными кавычками
    text = re.sub(r'```(\w*)\n([\s\S]*?)\n```', restore_code_block, text)
    
    # Обрабатываем inline код
    def restore_inline_code(match):
        code_content = match.group(1)
        code_content = code_content.replace('&lt;', '<').replace('&gt;', '>')
        code_content = code_content.replace('&amp;', '&').replace('&quot;', '"')
        code_content = code_content.replace('&#x27;', "'")
        code_content = code_content.replace('&#x2F;', '/')
        return f'<code>{code_content}</code>'
    
    text = re.sub(r'`(.*?)`', restore_inline_code, text)
    
    return text

def prepare_markdown_message(text: str) -> str:
    """Подготовка Markdown сообщения"""
    text = clean_text(text)
    
    # Защищаем блоки кода
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    text = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    # Защищаем inline код
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_codes)-1}__"
    
    text = re.sub(r'`[^`\n]+`', save_inline_code, text)
    
    # Экранируем специальные символы MarkdownV2
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    # Восстанавливаем inline код
    for i, inline_code in enumerate(inline_codes):
        text = text.replace(f'__INLINE_CODE_{i}__', inline_code)
    
    # Восстанавливаем блоки кода
    for i, code_block in enumerate(code_blocks):
        text = text.replace(f'__CODE_BLOCK_{i}__', code_block)
    
    return text

def has_code_blocks(text: str) -> bool:
    """Проверяет, содержит ли текст блоки кода"""
    return '```' in text

async def send_message_safe(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Умная отправка сообщений с автоматическим выбором формата"""
    try:
        # Сначала пробуем HTML
        html_text = prepare_html_message(text)
        
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
        logger.warning(f"⚠️ HTML не сработал: {e}, пробую MarkdownV2...")
        
        try:
            # Пробуем MarkdownV2
            markdown_text = prepare_markdown_message(text)
            
            kwargs = {
                "chat_id": chat_id,
                "text": markdown_text,
                "parse_mode": "MarkdownV2"
            }
            if reply_to_message_id:
                kwargs["reply_to_message_id"] = reply_to_message_id
            
            result = await bot.send_message(**kwargs)
            logger.info(f"✅ Сообщение отправлено с MarkdownV2, длина: {len(text)} символов")
            return result
            
        except Exception as e2:
            logger.warning(f"⚠️ MarkdownV2 не сработал: {e2}, пробую без форматирования...")
            
            try:
                # Отправляем без форматирования
                cleaned_text = clean_text(text)
                
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
    """Умное разбиение сообщений с сохранением блоков кода"""
    if len(text) <= max_length:
        return [text]
    
    # Сохраняем блоки кода
    code_blocks = []
    code_pattern = r'```[\s\S]*?```'
    
    def replace_code(match):
        code_blocks.append(match.group(0))
        return f'__CODE_BLOCK_{len(code_blocks)-1}__'
    
    text_with_placeholders = re.sub(code_pattern, replace_code, text)
    
    # Разбиваем по абзацам
    parts = []
    paragraphs = text_with_placeholders.split('\n\n')
    
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
    
    # Если части все еще слишком длинные, разбиваем по строкам
    final_parts = []
    for part in parts:
        if len(part) <= max_length:
            final_parts.append(part)
        else:
            lines = part.split('\n')
            current = ""
            for line in lines:
                if len(current) + len(line) + 1 <= max_length:
                    current += line + "\n"
                else:
                    if current:
                        final_parts.append(current.strip())
                    current = line + "\n"
            if current:
                final_parts.append(current.strip())
    
    # Восстанавливаем блоки кода
    restored_parts = []
    for part in final_parts:
        restored_part = part
        for i, code_block in enumerate(code_blocks):
            placeholder = f'__CODE_BLOCK_{i}__'
            if placeholder in restored_part:
                restored_part = restored_part.replace(placeholder, code_block)
        restored_parts.append(restored_part)
    
    return restored_parts

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """Отправка длинных сообщений"""
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    parts = split_message_smart(text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        part_length = len(part)
        logger.info(f"📤 Часть {i+1}/{len(parts)}: {part_length} символов")
        
        has_code = has_code_blocks(part)
        if has_code:
            logger.info(f"📤 Часть {i+1} содержит код")
        
        message = await send_message_safe(
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
async def test_model_speed(model: str) -> Tuple[bool, float]:
    """ИСПРАВЛЕННАЯ: Тестирование скорости и доступности модели с увеличенными таймаутами"""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    # УПРОЩЕННЫЙ тестовый промпт для быстрого ответа
    data = {
        "model": model,
        "messages": [{"role": "user", "content": "Ответь одним словом: Привет"}],
        "max_tokens": 1,  # Запрашиваем МИНИМУМ токенов
        "stream": False   # Отключаем стриминг для простоты
    }
    
    try:
        start = time.time()
        # ЗНАЧИТЕЛЬНО УВЕЛИЧЕННЫЕ таймауты для теста
        timeout_seconds = 40 if "70b" in model or "deepseek" in model else 30
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                elapsed = time.time() - start
                
                # Логируем результат для диагностики
                if response.status == 200:
                    logger.info(f"  ✅ Тест модели {model.split('/')[-1]} пройден за {elapsed:.2f}с")
                    return True, elapsed
                else:
                    error_text = await response.text()
                    logger.warning(f"  ⚠️ Модель {model.split('/')[-1]} вернула статус {response.status}: {error_text[:100]}")
                    return False, float('inf')
    except asyncio.TimeoutError:
        logger.warning(f"  ⏱️ Тест модели {model.split('/')[-1]} превысил таймаут ({timeout_seconds}с)")
        return False, float('inf')
    except Exception as e:
        logger.warning(f"  ❌ Ошибка теста модели {model.split('/')[-1]}: {str(e)[:100]}")
        return False, float('inf')

def get_model_timeout(model: str) -> int:
    """Определение таймаута для модели"""
    model_lower = model.lower()
    
    if "phi-3.5" in model_lower or "qwen-2.5-7b" in model_lower:
        return MODEL_TIMEOUTS["fast"]
    elif "qwen2.5-32b" in model_lower or "coder" in model_lower:
        return MODEL_TIMEOUTS["medium"]
    elif "llama" in model_lower or "70b" in model_lower:
        return MODEL_TIMEOUTS["slow"]
    elif any(paid_model in model_lower for paid_model in ["deepseek-v3", "gpt-4", "claude-3"]):
        return MODEL_TIMEOUTS["paid"]
    
    return MODEL_TIMEOUTS["medium"]

async def get_available_models() -> List[Tuple[str, float]]:
    """ИСПРАВЛЕННАЯ: Получить список доступных моделей с их скоростью"""
    logger.info("🔍 Проверяю доступность моделей...")
    
    # Собираем все модели, которые нужно проверить
    all_models = []
    
    # Добавляем основные модели
    all_models.extend(MODELS_CONFIG["primary_models"])
    
    # Добавляем платные модели если разрешено
    if USE_PAID_MODELS:
        logger.info("💰 Платные модели включены в проверку")
        all_models.extend(MODELS_CONFIG["paid_models"])
    else:
        logger.info("💰 Платные модели отключены")
    
    # Добавляем fallback модели
    all_models.extend(MODELS_CONFIG["fallback_models"])
    
    # Убираем возможные дубликаты
    all_models = list(dict.fromkeys(all_models))
    
    # Тестируем модели
    available_models = []
    
    for model in all_models:
        is_available, speed = await test_model_speed(model)
        if is_available:
            available_models.append((model, speed))
        else:
            logger.warning(f"  ❌ {model.split('/')[-1]}: недоступна")
    
    # Сортируем по скорости (быстрее - первее)
    available_models.sort(key=lambda x: x[1])
    
    if available_models:
        logger.info(f"✅ Найдено {len(available_models)} доступных моделей")
        return [model for model, speed in available_models]
    else:
        logger.warning("⚠️ Ни одна модель не доступна")
        return []

async def get_ai_response(user_question: str) -> Tuple[Optional[str], Optional[str], int]:
    """ИСПРАВЛЕННАЯ: Получает ответ от AI с оптимизированной логикой"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    # Промт для ИИ
    system_prompt = {
        "role": "system",
        "content": (
            "Ты Иван Иваныч — эксперт в технологиях и футуристике. "
            "Отвечай ясно и по делу. Используй Markdown для форматирования. "
            "Для кода используй тройные кавычки с указанием языка. "
            "Всегда закрывай блок кода. "
            "Держи ответ в 800-1500 символов."
        )
    }
    
    # Получаем доступные модели
    available_models = await get_available_models()
    
    if not available_models:
        logger.warning("⚠️ Ни одна модель не доступна, использую локальный fallback")
        response = get_local_fallback_response(user_question)
        model_used = "local_fallback"
        code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', response))
        return response, model_used, code_blocks
    
    logger.info(f"🎯 Доступные модели ({len(available_models)}): {', '.join([m.split('/')[-1] for m in available_models])}")
    
    # Пробуем модели по порядку (уже отсортированы по скорости)
    for model in available_models:
        model_timeout = get_model_timeout(model)
        logger.info(f"🎯 Пробую модель: {model.split('/')[-1]} (таймаут: {model_timeout}с)")
        
        for attempt in range(2):  # 2 попытки
            try:
                # Определяем конфигурацию (платная или обычная)
                model_lower = model.lower()
                if any(paid_model in model_lower for paid_model in ["deepseek-v3", "gpt-4", "claude-3"]):
                    config = PAID_MODEL_CONFIG
                    model_type = "💰 Платная"
                else:
                    config = GENERATION_CONFIG
                    model_type = "🆓 Бесплатная"
                
                data = {
                    "model": model,
                    "messages": [
                        system_prompt,
                        {"role": "user", "content": user_question}
                    ],
                    **config
                }
                
                timeout = aiohttp.ClientTimeout(total=model_timeout)
                
                logger.info(f"🚀 Запрос к {model_type} модели {model.split('/')[-1]} (попытка {attempt+1}/2)...")
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
                                        logger.warning(f"⚠️ Нечётное количество кавычек: {backtick_count}")
                                        if '```' in text:
                                            last_open = text.rfind('```')
                                            if text[last_open:].count('```') == 1:
                                                text += '\n```'
                                            else:
                                                text += '`'
                                    
                                    code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text))
                                    logger.info(f"✅ {model_type} {model.split('/')[-1]} ответил за {elapsed:.1f}с, {len(text)} символов, блоков кода: {code_blocks}")
                                    return text, model, code_blocks
                                else:
                                    logger.warning(f"⚠️ {model.split('/')[-1]} вернул некорректный ответ: {len(text)} символов")
                        else:
                            error_text = await response.text()
                            logger.warning(f"⚠️ {model.split('/')[-1]} ошибка [{response.status}]: {error_text[:200]}")
                    
                    if attempt < 1:  # Только одна повторная попытка
                        wait_time = 2.0  # Увеличено с 1.5
                        logger.info(f"🔄 Повторная попытка через {wait_time} секунд...")
                        await asyncio.sleep(wait_time)
                        
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ Таймаут {model.split('/')[-1]} (> {model_timeout}с)")
                if attempt < 1:
                    await asyncio.sleep(2.0)
            except Exception as e:
                logger.error(f"❌ Ошибка {model.split('/')[-1]}: {e}")
                break
        
        logger.warning(f"❌ Модель {model} не сработала после 2 попыток")
    
    logger.warning("❌ Все модели не сработали, использую локальный fallback")
    response = get_local_fallback_response(user_question)
    model_used = "local_fallback"
    code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', response))
    return response, model_used, code_blocks

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
        "```python\nimport telebot\nimport requests\nimport json\n\n# Настройки\nTOKEN = 'YOUR_BOT_TOKEN'\nSBER_API_KEY = 'YOUR_SBER_VISION_KEY'\n\nbot = telebot.TeleBot(TOKEN)\n\n@bot.message_handler(content_types=['photo'])\ndef handle_photo(message):\n    file_id = message.photo[-1].file_id\n    file_info = bot.get_file(file_id)\n    file_url = f'https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}'\n    \n    response = requests.post(\n        'https://api.sber.dev/vision/v1/ocr',\n        headers={'Authorization': f'Bearer {SBER_API_KEY}'},\n        json={'image_url': file_url}\n    )\n    \n    if response.status_code == 200:\n        result = response.json()\n        text = result.get('text', 'Текст не распознан')\n        bot.reply_to(message, f'📝 Распознанный текст:\\n{text}')\n    else:\n        bot.reply_to(message, '❌ Ошибка распознавания')\n\nbot.polling()\n```\n\n"
        "📁 **Структура проекта**:\n"
        "```\nproject/\n├── bot.py\n├── config.py\n├── sber_vision.py\n├── database.py\n└── requirements.txt\n```"
    ],
    "общий": [
        "🧠 **Анализ запроса**\n\n"
        "**Работа по преодолению гравитации**\n\n"
        "Формула работы против силы тяжести:\n"
        "```\nA = m * g * h\n```\n"
        "где:\n"
        "• A - работа (Дж)\n"
        "• m - масса тела (кг)\n"
        "• g - ускорение свободного падения (~9.8 м/с²)\n"
        "• h - высота подъема (м)\n\n"
        "**Пример:**\n"
        "Подъем груза массой 10 кг на высоту 5 м:\n"
        "```\nA = 10 * 9.8 * 5 = 490 Дж\n```\n\n"
        "**Курс физики:**\n"
        "• Механика: работа, энергия, мощность\n"
        "• Термодинамика: законы сохранения\n"
        "• Электродинамика: работа электрического поля"
    ]
}

def get_local_fallback_response(user_question: str) -> str:
    """Генерация локального ответа если API недоступно"""
    question_lower = user_question.lower()
    
    if any(word in question_lower for word in ['код', 'пример', 'программир', 'python', 'javascript']):
        topic = "код"
    elif any(word in question_lower for word in ['физик', 'формул', 'работа', 'гравитац', 'механик']):
        topic = "общий"
    else:
        topic = "общий"
    
    responses = LOCAL_RESPONSES[topic]
    return random.choice(responses)

# ==================== ОБРАБОТЧИКИ ТЕЛЕГРАМ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    welcome = (
        "👋 Привет! Я Иван Иваныч — бот с умной системой ИИ\n\n"
        "🚀 **ОПТИМИЗИРОВАННАЯ АРХИТЕКТУРА:**\n"
        "• **Увеличенные таймауты** для стабильной работы\n"
        "• **Умный выбор модели** по скорости и доступности\n"
        f"• **Платные модели:** {'ВКЛЮЧЕНЫ ✅' if USE_PAID_MODELS else 'отключены'}\n"
        "• **Работающая подсветка кода** в Telegram\n\n"
        "⚙️ **Текущая конфигурация:**\n"
        f"• Основные модели: {len(MODELS_CONFIG['primary_models'])}\n"
        f"• Платные модели: {len(MODELS_CONFIG['paid_models']) if USE_PAID_MODELS else 'отключены'}\n"
        f"• Fallback модели: {len(MODELS_CONFIG['fallback_models'])}\n\n"
        "⏱️ **Таймауты:**\n"
        f"• Быстрые: {MODEL_TIMEOUTS['fast']}с, Средние: {MODEL_TIMEOUTS['medium']}с\n"
        f"• Глубокие: {MODEL_TIMEOUTS['slow']}с, Платные: {MODEL_TIMEOUTS['paid']}с\n\n"
        "⚡ **Пример кода с подсветкой:**\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "📊 Проверьте доступность моделей: /status\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_message_safe(message.chat.id, welcome, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Команда /status - проверка доступности моделей"""
    status_text = "🔄 Проверяю доступность моделей..."
    status_msg = await send_message_safe(message.chat.id, status_text, message.message_id)
    
    try:
        logger.info("🔍 Запуск проверки моделей для /status...")
        
        status_report = "📊 **Статус моделей:**\n\n"
        
        # Проверяем все категории моделей
        categories = [
            ("Основные модели", MODELS_CONFIG["primary_models"]),
            ("Fallback модели", MODELS_CONFIG["fallback_models"]),
        ]
        
        if USE_PAID_MODELS:
            categories.append(("Платные модели", MODELS_CONFIG["paid_models"]))
        
        for category_name, models in categories:
            status_report += f"**{category_name}:**\n"
            
            for model in models:
                is_available, speed = await test_model_speed(model)
                emoji = "✅" if is_available else "❌"
                name_short = model.split('/')[-1]
                
                if is_available:
                    status_report += f"{emoji} `{name_short}` ({speed:.1f}с)\n"
                else:
                    status_report += f"{emoji} `{name_short}` (недоступна)\n"
            
            status_report += "\n"
        
        status_report += f"⏱️ **Таймауты:** Быстрые: {MODEL_TIMEOUTS['fast']}с, Средние: {MODEL_TIMEOUTS['medium']}с, Глубокие: {MODEL_TIMEOUTS['slow']}с"
        
        if USE_PAID_MODELS:
            status_report += f", Платные: {MODEL_TIMEOUTS['paid']}с"
        
        status_report += f"\n💰 **Платные модели:** {'ВКЛЮЧЕНЫ ✅' if USE_PAID_MODELS else 'отключены'}"
        
        if status_msg:
            await status_msg.edit_text(status_report, parse_mode="HTML")
        else:
            await send_message_safe(message.chat.id, status_report, message.message_id)
            
    except Exception as e:
        logger.error(f"❌ Ошибка проверки: {e}")
        error_text = f"❌ Ошибка проверки: {str(e)[:100]}"
        if status_msg:
            await status_msg.edit_text(error_text, parse_mode=None)
        else:
            await send_message_safe(message.chat.id, error_text, message.message_id)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Обработка вопросов с умной системой моделей"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = message.from_user.username or f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    processing_msg = None
    try:
        processing_text = "🤔 ИИ анализирует вопрос..."
        processing_msg = await send_message_safe(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            return
        
        start_time = time.time()
        
        logger.info("⚡ Запрос к ИИ...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        response, model_used, code_blocks = await get_ai_response(user_question)
        
        elapsed = time.time() - start_time
        
        if response:
            logger.info(f"📤 Отправка ответа ({len(response)} символов)")
            
            await processing_msg.edit_text(
                "✅ Ответ готов! Отправляю...",
                parse_mode=None
            )
            
            await send_long_message(
                chat_id,
                f"🤖 **Ответ ИИ:**\n\n{response}",
                message.message_id
            )
            
            # Формируем статистику
            model_name = model_used.split('/')[-1] if model_used != "local_fallback" else "локальная база знаний"
            model_type = ""
            
            if model_used != "local_fallback":
                model_lower = model_used.lower()
                if any(paid_model in model_lower for paid_model in ["deepseek-v3", "gpt-4", "claude-3"]):
                    model_type = "💰 (платная)"
                else:
                    model_type = "🆓 (бесплатная)"
            
            completion_text = (
                f"✅ Ответ готов!\n"
                f"⏱️ Время: {elapsed:.1f} секунд\n"
                f"📊 Длина: {len(response)} символов"
            )
            
            if code_blocks > 0:
                completion_text += f"\n💻 Код: {code_blocks} блок(ов)"
            
            completion_text += f"\n🤖 Модель: {model_name} {model_type}"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Успешно! Время: {elapsed:.1f}с, модель: {model_name}")
            
        else:
            fallback_response = get_local_fallback_response(user_question)
            await processing_msg.edit_text("⚠️ Использую локальную базу знаний...", parse_mode=None)
            
            await send_long_message(
                chat_id, 
                f"📚 **База знаний:**\n\n{fallback_response}", 
                message.message_id
            )
            
            completion_text = f"✅ Локальный ответ за {elapsed:.1f}с"
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

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info("🚀 Бот IvanIvanych запускается...")
    logger.info("🔄 ОПТИМИЗИРОВАННАЯ ВЕРСИЯ с увеличенными таймаутами")
    logger.info(f"💰 Платные модели: {'ВКЛЮЧЕНЫ ✅' if USE_PAID_MODELS else 'отключены'}")
    
    logger.info("🆓 Основные бесплатные модели:")
    for model in MODELS_CONFIG["primary_models"]:
        logger.info(f"  • {model.split('/')[-1]}")
    
    if USE_PAID_MODELS:
        logger.info("💰 Платные модели:")
        for model in MODELS_CONFIG["paid_models"]:
            logger.info(f"  • {model.split('/')[-1]}")
    
    logger.info("🛡️ Fallback модели:")
    for model in MODELS_CONFIG["fallback_models"]:
        logger.info(f"  • {model.split('/')[-1]}")
    
    logger.info("⏱️ УВЕЛИЧЕННЫЕ ТАЙМАУТЫ:")
    logger.info(f"  • Быстрые модели: {MODEL_TIMEOUTS['fast']}с")
    logger.info(f"  • Средние модели: {MODEL_TIMEOUTS['medium']}с")
    logger.info(f"  • Глубокие модели: {MODEL_TIMEOUTS['slow']}с")
    if USE_PAID_MODELS:
        logger.info(f"  • Платные модели: {MODEL_TIMEOUTS['paid']}с")
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