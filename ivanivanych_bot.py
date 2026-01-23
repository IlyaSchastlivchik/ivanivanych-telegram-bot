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
    "slow": 180
}

# ==================== КОНФИГУРАЦИЯ ====================
GENERATION_CONFIG = {
    "temperature": 0.8,
    "max_tokens": 1200,
    "top_p": 0.9,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
}

# ТОЧНОЕ ОГРАНИЧЕНИЕ ДЛЯ DEEPSEEK R1 FREE - 750 ТОКЕНОВ (менее 800)
DEEPSEEK_R1_CONFIG = {
    "temperature": 0.7,
    "max_tokens": 750,  # ТОЧНО 750 токенов, чтобы избежать обрезания
    "top_p": 0.85,
    "frequency_penalty": 0.15,
    "presence_penalty": 0.1,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== УТИЛИТЫ ОБРАБОТКИ ТЕКСТА ====================
def clean_text(text: str) -> str:
    """Очистка текста от опасных символов"""
    if not text:
        return ""
    
    # Удаляем управляющие символы
    text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C')
    
    # Удаляем специфичные опасные символы
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

def prepare_html_message(text: str) -> str:
    """Подготовка HTML сообщения с корректной обработкой кода"""
    text = clean_text(text)
    
    # Экранируем HTML символы во всем тексте
    text = html.escape(text)
    
    # Восстанавливаем блоки кода (отменяем экранирование внутри них)
    def restore_code_block(match):
        language = match.group(1) if match.group(1) else ''
        code_content = match.group(2)
        # Отменяем экранирование внутри кода
        code_content = code_content.replace('&lt;', '<').replace('&gt;', '>')
        code_content = code_content.replace('&amp;', '&').replace('&quot;', '"')
        code_content = code_content.replace('&#x27;', "'")
        
        if language:
            return f'<pre><code class="language-{language}">{code_content}</code></pre>'
        else:
            return f'<pre><code>{code_content}</code></pre>'
    
    # Обрабатываем блоки кода с тройными кавычками
    text = re.sub(r'```(\w*)\n([\s\S]*?)\n```', restore_code_block, text, flags=re.DOTALL)
    
    # Обрабатываем inline код
    def restore_inline_code(match):
        code_content = match.group(1)
        # Отменяем экранирование внутри inline кода
        code_content = code_content.replace('&lt;', '<').replace('&gt;', '>')
        code_content = code_content.replace('&amp;', '&').replace('&quot;', '"')
        code_content = code_content.replace('&#x27;', "'")
        return f'<code>{code_content}</code>'
    
    text = re.sub(r'`(.*?)`', restore_inline_code, text)
    
    # Обрабатываем LaTeX формулы (простые, для Telegram)
    # Заменяем \(...\) на инлайн формулы
    def replace_inline_latex(match):
        formula = match.group(1)
        return f'<i>{formula}</i>'
    
    text = re.sub(r'\\\((.*?)\\\)', replace_inline_latex, text)
    
    # Заменяем \[...\] на отдельные строки
    def replace_display_latex(match):
        formula = match.group(1)
        return f'<pre><i>{formula}</i></pre>'
    
    text = re.sub(r'\\\[(.*?)\\\]', replace_display_latex, text)
    
    return text

def prepare_markdown_message(text: str) -> str:
    """Подготовка Markdown сообщения"""
    text = clean_text(text)
    
    # Экранируем символы Markdown
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    return text

def has_code_blocks(text: str) -> bool:
    """Проверяет, содержит ли текст блоки кода"""
    return '```' in text

def has_formulas(text: str) -> bool:
    """Проверяет, содержит ли текст LaTeX формулы"""
    return '\\(' in text or '\\[' in text

async def send_message_safe(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Умная отправка сообщений с автоматическим выбором формата"""
    try:
        # Проверяем наличие блоков кода или формул
        if has_code_blocks(text) or has_formulas(text):
            # Используем HTML для лучшей поддержки кода и формул
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
        else:
            # Используем MarkdownV2
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
            
    except Exception as e:
        logger.warning(f"⚠️ Ошибка форматирования: {e}, пробую без форматирования...")
        
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
            
        except Exception as e2:
            logger.error(f"❌ Не удалось отправить сообщение: {e2}")
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
    
    # Заменяем блоки кода на плейсхолдеры
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
        
        # Проверяем наличие кода или формул в части
        has_code = has_code_blocks(part)
        has_formula = has_formulas(part)
        if has_code:
            logger.info(f"📤 Часть {i+1} содержит код")
        if has_formula:
            logger.info(f"📤 Часть {i+1} содержит формулы")
        
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
    """Попытка использовать модель с приоритетом для Llama"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    # Тестируем модели, но сохраняем порядок приоритета
    available_models = []
    
    for model in model_list:
        speed = await test_model_speed(model)
        if speed < float('inf'):
            available_models.append(model)
            logger.info(f"  • {model.split('/')[-1]}: {speed:.2f}с")
        else:
            logger.warning(f"  • {model.split('/')[-1]}: недоступна")
    
    # Если нет доступных моделей, используем список как есть
    if not available_models:
        logger.warning("⚠️ Ни одна модель не доступна, использую все по порядку")
        available_models = model_list
    
    # Пробуем модели в порядке приоритета (без сортировки по скорости)
    for model in available_models:
        model_timeout = get_model_timeout(model)
        logger.info(f"🎯 Пробую модель: {model.split('/')[-1]} (таймаут: {model_timeout}с)")
        
        for attempt in range(max_retries):
            try:
                # Специальные настройки для DeepSeek R1
                if "deepseek-r1" in model.lower():
                    config = DEEPSEEK_R1_CONFIG
                else:
                    config = GENERATION_CONFIG
                
                data = {
                    "model": model,
                    "messages": [
                        system_prompt,
                        {"role": "user", "content": user_question}
                    ],
                    **config
                }
                
                timeout = aiohttp.ClientTimeout(total=model_timeout)
                
                logger.info(f"🚀 Запрос к {model.split('/')[-1]} (попытка {attempt+1}/{max_retries})...")
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
                                        # Пробуем найти незакрытый блок и закрыть его
                                        if '```' in text:
                                            last_open = text.rfind('```')
                                            # Если после последнего открытия нет закрытия
                                            if text[last_open:].count('```') == 1:
                                                text += '\n```'
                                            else:
                                                text += '`'
                                    
                                    # Для DeepSeek R1 проверяем обрезание
                                    if "deepseek-r1" in model.lower():
                                        # Проверяем признаки обрезания
                                        if (text.endswith(('...', '—', '-', ':', ';', ',')) or 
                                            len(re.findall(r'```[\s\S]*?```', text)) > 0 and '```' in text and text.count('```') % 2 != 0):
                                            logger.warning(f"⚠️ Ответ DeepSeek R1 может быть обрезан")
                                            # Добавляем пояснение, но не портим содержимое
                                            if not any(marker in text for marker in ['[Ответ обрезан]', '[Обрезано]']):
                                                text += '\n\n[Ответ может быть обрезан из-за ограничений бесплатной модели]'
                                    
                                    code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text))
                                    logger.info(f"✅ {model.split('/')[-1]} ответил за {elapsed:.1f}с, {len(text)} символов, блоков кода: {code_blocks}")
                                    return text, model, code_blocks
                                else:
                                    logger.warning(f"⚠️ {model.split('/')[-1]} вернул некорректный ответ: {len(text)} символов")
                        else:
                            error_text = await response.text()
                            logger.warning(f"⚠️ {model.split('/')[-1]} ошибка [{response.status}]: {error_text[:200]}")
                    
                    if attempt < max_retries - 1:
                        wait_time = 1.5 * (attempt + 1)
                        logger.info(f"🔄 Повторная попытка через {wait_time} секунд...")
                        await asyncio.sleep(wait_time)
                        
            except asyncio.TimeoutError:
                logger.warning(f"⏱️ Таймаут {model.split('/')[-1]} (> {model_timeout}с)")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"❌ Ошибка {model.split('/')[-1]}: {e}")
                break
        
        logger.warning(f"❌ Модель {model} не сработала после {max_retries} попыток")
    
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
    
    if any(word in question_lower for word in ['код', 'пример', 'программир', 'python', 'arduino', 'cpp']):
        topic = "код"
    elif any(word in question_lower for word in ['шаг', 'план', 'внедр', 'настрой', 'установ']):
        topic = "технология"
    else:
        topic = "общий"
    
    responses = LOCAL_RESPONSES[topic]
    return random.choice(responses)

async def get_ai_response(user_question: str, response_type: str = "main") -> Tuple[Optional[str], Optional[str], int]:
    """Получение ответа от AI с приоритетом для Llama"""
    if response_type == "main":
        models = [
            MODELS_CONFIG["main"]["primary"],  # Llama - приоритетная
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
                "Будь конкретным и техничным. "
                "ВАЖНО: Ты используешь бесплатную версию DeepSeek R1 с ограничением 750 токенов. "
                "Поэтому твой ответ должен быть ЛАКОНИЧНЫМ и укладываться в это ограничение. "
                "Если нужно объяснить сложную тему, разбей её на ключевые пункты. "
                "Максимальная длина ответа: 600-700 слов. "
                "ЗАВЕРШАЙ ответ полностью, не обрывай на полуслове."
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

# ==================== ОБРАБОТЧИКИ ТЕЛЕГРАМ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    welcome = (
        "👋 Привет! Я Иван Иваныч — бот с продвинутыми ИИ-моделями\n\n"
        "🚀 **Доступные модели:**\n"
        "• **Llama 3.3 70B** — мощная аналитическая модель (приоритетная)\n"
        "• **DeepSeek R1** — глубокая аналитика с рассуждениями\n"
        "• **Qwen модели** — различные задачи\n\n"
        "🤖 **Особенности:**\n"
        "• Приоритет для Llama 3.3 70B\n"
        "• Корректная подсветка кода и формул в Telegram\n"
        "• DeepSeek R1 имеет ограничение 750 токенов для полных ответов\n"
        "• Параллельная генерация от двух ИИ\n"
        "• Улучшенная обработка блоков кода\n\n"
        "⚡ **Пример формулы:**\n"
        "Работа в механике: \\(W = \\vec{F} \\cdot \\vec{s} = F \\cdot s \\cdot \\cos\\theta\\)\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_message_safe(message.chat.id, welcome, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Команда /status"""
    status_text = "🔄 Проверяю доступность моделей..."
    status_msg = await send_message_safe(message.chat.id, status_text, message.message_id)
    
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
        status_report += f"\n🎯 **Приоритет:** Llama 3.3 70B является приоритетной моделью"
        status_report += f"\n⚠️ **DeepSeek R1:** ограничение 750 токенов (бесплатная версия)"
        status_report += f"\n📄 **Форматирование:** Код и формулы подсвечиваются в Telegram"
        
        if status_msg:
            await status_msg.edit_text(status_report, parse_mode="HTML")
        else:
            await send_message_safe(message.chat.id, status_report, message.message_id)
            
    except Exception as e:
        error_text = f"❌ Ошибка проверки: {str(e)[:100]}"
        if status_msg:
            await status_msg.edit_text(error_text, parse_mode=None)
        else:
            await send_message_safe(message.chat.id, error_text, message.message_id)

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
        processing_msg = await send_message_safe(chat_id, processing_text, message.message_id)
        
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

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info("🚀 Бот IvanIvanych запускается...")
    logger.info("🎯 ПРИОРИТЕТ: Llama 3.3 70B")
    logger.info("⚠️ DEEPSEEK R1: Ограничение 750 токенов (бесплатная версия)")
    logger.info("📊 ГЕНЕРАЦИЯ: Код и формулы подсвечиваются в Telegram")
    logger.info("🤖 ОСНОВНЫЕ МОДЕЛИ:")
    logger.info(f"  1. {MODELS_CONFIG['main']['primary']}")
    logger.info(f"  2. {MODELS_CONFIG['main']['backup']}")
    logger.info("🤖 АНАЛИТИЧЕСКИЕ МОДЕЛИ:")
    logger.info(f"  1. {MODELS_CONFIG['deepseek']['primary']}")
    logger.info(f"  2. {MODELS_CONFIG['deepseek']['backup']}")
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