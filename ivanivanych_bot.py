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
    "slow": 150
}

# ==================== КОНФИГУРАЦИЯ ====================
GENERATION_CONFIG = {
    "temperature": 0.8,
    "max_tokens": 1200,
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

# ==================== УТИЛИТЫ ОБРАБОТКИ ТЕКСТА ====================
def clean_text(text: str) -> str:
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
    
    cleaned = []
    for char in text:
        cat = unicodedata.category(char)
        if cat[0] == 'C' and char not in ['\n', '\r', '\t']:
            continue
        cleaned.append(char)
    
    text = ''.join(cleaned)
    
    for char in dangerous_chars:
        text = text.replace(char, '')
    
    return text

# ==================== ОБРАБОТКА ФОРМУЛ (LaTeX → Unicode) ====================
def convert_latex_to_unicode(latex_formula: str) -> str:
    """
    Конвертирует LaTeX формулы в Unicode-представление для Telegram.
    Это приблизительное представление, но читаемое.
    """
    # Упрощенные замены для основных математических символов
    replacements = [
        (r'\\cdot', '·'),
        (r'\\times', '×'),
        (r'\\div', '÷'),
        (r'\\pm', '±'),
        (r'\\mp', '∓'),
        (r'\\leq', '≤'),
        (r'\\geq', '≥'),
        (r'\\neq', '≠'),
        (r'\\approx', '≈'),
        (r'\\infty', '∞'),
        (r'\\pi', 'π'),
        (r'\\alpha', 'α'),
        (r'\\beta', 'β'),
        (r'\\gamma', 'γ'),
        (r'\\delta', 'δ'),
        (r'\\epsilon', 'ε'),
        (r'\\theta', 'θ'),
        (r'\\lambda', 'λ'),
        (r'\\mu', 'μ'),
        (r'\\sigma', 'σ'),
        (r'\\omega', 'ω'),
        (r'\\sum', 'Σ'),
        (r'\\int', '∫'),
        (r'\\oint', '∮'),
        (r'\\nabla', '∇'),
        (r'\\partial', '∂'),
        (r'\\sqrt', '√'),
        (r'\\frac{(.*?)}{(.*?)}', r'\1/\2'),  # Простые дроби
        (r'\^\{([^}]+)\}', r'^\1'),  # Верхние индексы без скобок
        (r'_\{([^}]+)\}', r'_\1'),  # Нижние индексы без скобок
        (r'\\vec\{([^}]+)\}', r'\1⃗'),  # Векторы
        (r'\\overline\{([^}]+)\}', r'\1̄'),  # Черта сверху
        (r'\\text\{([^}]+)\}', r'\1'),  # Текст
        (r'\\left\(', '('),
        (r'\\right\)', ')'),
        (r'\\left\[', '['),
        (r'\\right\]', ']'),
        (r'\\ ', ' '),
        (r'\\,', ' '),
        (r'\\quad', '  '),
        (r'\\qquad', '    '),
        (r'\\;', ' '),
        (r'\\(?:sin|cos|tan|log|ln|exp)', lambda m: m.group(0)[1:]),  # Функции без обратного слэша
    ]
    
    result = latex_formula
    
    for pattern, replacement in replacements:
        if callable(replacement):
            result = re.sub(pattern, replacement, result)
        else:
            result = result.replace(pattern, replacement)
    
    # Убираем лишние пробелы и упрощаем
    result = re.sub(r'\s+', ' ', result).strip()
    
    return result

def format_formula_for_telegram(latex_formula: str, is_inline: bool = False) -> str:
    """
    Форматирует формулу для Telegram с использованием MarkdownV2
    """
    # Упрощаем LaTeX до Unicode
    unicode_formula = convert_latex_to_unicode(latex_formula)
    
    if is_inline:
        # Для inline формул используем моноширинный шрифт
        return f'`{unicode_formula}`'
    else:
        # Для блочных формул используем блок кода
        return f'```\n{unicode_formula}\n```'

def process_formulas_in_text(text: str) -> str:
    """
    Новая обработка формул: преобразование LaTeX в читаемый Unicode + MarkdownV2
    """
    # Защищаем блоки кода от обработки
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    text = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    # Обрабатываем формулы в тегах [f]
    def replace_formula(match):
        latex_content = match.group(1).strip()
        
        # Определяем, inline или блочная формула
        # Если содержит \displaystyle, \sum, \int, \frac - считаем блочной
        is_inline = not any(pattern in latex_content for pattern in [
            '\\displaystyle', '\\sum', '\\int', '\\begin', '\\frac{', '\\lim'
        ])
        
        # Форматируем для Telegram
        formatted = format_formula_for_telegram(latex_content, is_inline)
        
        # Для сложных формул добавляем примечание
        if not is_inline and len(latex_content) > 50:
            formatted += f"\n\n*Для точного отображения скопируйте LaTeX:*\n`{latex_content}`"
        
        return formatted
    
    # Заменяем [f]...[/f] на форматированный текст
    pattern = r'\[f\](.*?)\[/f\]'
    text = re.sub(pattern, replace_formula, text, flags=re.DOTALL)
    
    # Восстанавливаем блоки кода
    for i, code_block in enumerate(code_blocks):
        text = text.replace(f'__CODE_BLOCK_{i}__', code_block)
    
    return text

def prepare_markdown_message_with_formulas(text: str) -> str:
    """Подготовка Markdown сообщения с поддержкой формул"""
    text = clean_text(text)
    
    # Обрабатываем формулы
    text = process_formulas_in_text(text)
    
    # Сначала защищаем блоки кода (включая те, что получились из формул)
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    # Захватываем блоки кода с тройными кавычками
    text = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    # Теперь защищаем inline код (одинарные кавычки)
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

def prepare_html_message(text: str) -> str:
    """Подготовка HTML сообщения - fallback вариант"""
    text = clean_text(text)
    
    # Простая обработка кода для HTML
    def restore_code_simple(match):
        code_content = match.group(2)
        return f'<pre><code>{code_content}</code></pre>'
    
    text = re.sub(r'```(\w*)\n([\s\S]*?)\n```', restore_code_simple, text)
    
    # Убираем теги [f], оставляем только содержимое
    text = re.sub(r'\[f\](.*?)\[/f\]', r'<i>\1</i>', text)
    
    # Экранируем HTML
    text = html.escape(text)
    
    # Восстанавливаем теги кода после экранирования
    text = text.replace('&lt;pre&gt;&lt;code&gt;', '<pre><code>')
    text = text.replace('&lt;/code&gt;&lt;/pre&gt;', '</code></pre>')
    
    return text

def has_code_blocks(text: str) -> bool:
    """Проверяет, содержит ли текст блоки кода"""
    return '```' in text

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

async def send_message_safe(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Умная отправка сообщений с поддержкой формул"""
    try:
        # Сначала пробуем MarkdownV2 с поддержкой формул
        markdown_text = prepare_markdown_message_with_formulas(text)
        
        kwargs = {
            "chat_id": chat_id,
            "text": markdown_text,
            "parse_mode": "MarkdownV2"
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Сообщение отправлено с MarkdownV2+формулами, длина: {len(text)} символов")
        return result
        
    except Exception as e:
        logger.warning(f"⚠️ MarkdownV2 с формулами не сработал: {e}, пробую HTML...")
        
        try:
            # Fallback: HTML без формул
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
            
        except Exception as e2:
            logger.warning(f"⚠️ HTML не сработал: {e2}, пробую без форматирования...")
            
            try:
                # Отправляем без форматирования, но с обработкой формул
                cleaned_text = clean_text(text)
                
                # Простая замена тегов [f] на читаемый вид
                def replace_formula_simple(match):
                    latex_content = match.group(1)
                    unicode_formula = convert_latex_to_unicode(latex_content)
                    return f" [Формула: {unicode_formula}] "
                
                cleaned_text = re.sub(r'\[f\](.*?)\[/f\]', replace_formula_simple, cleaned_text)
                
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

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """Отправка длинных сообщений с поддержкой формул"""
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    parts = split_message_smart(text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        part_length = len(part)
        logger.info(f"📤 Часть {i+1}/{len(parts)}: {part_length} символов")
        
        # Проверяем наличие кода в части
        has_code = has_code_blocks(part)
        has_formulas = '[f]' in part
        
        if has_code:
            logger.info(f"📤 Часть {i+1} содержит код")
        if has_formulas:
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
    """Попытка использовать модель с поддержкой формул"""
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    # Тестируем модели
    available_models = []
    
    for model in model_list:
        speed = await test_model_speed(model)
        if speed < float('inf'):
            available_models.append(model)
            logger.info(f"  • {model.split('/')[-1]}: {speed:.2f}с")
        else:
            logger.warning(f"  • {model.split('/')[-1]}: недоступна")
    
    if not available_models:
        logger.warning("⚠️ Ни одна модель не доступна, использую все по порядку")
        available_models = model_list
    
    # Пробуем модели в порядке приоритета
    for model in available_models:
        model_timeout = get_model_timeout(model)
        logger.info(f"🎯 Пробую модель: {model.split('/')[-1]} (таймаут: {model_timeout}с)")
        
        for attempt in range(max_retries):
            try:
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
                                        if '```' in text:
                                            last_open = text.rfind('```')
                                            if text[last_open:].count('```') == 1:
                                                text += '\n```'
                                            else:
                                                text += '`'
                                    
                                    # Проверяем и корректируем теги формул
                                    open_tags = text.count('[f]')
                                    close_tags = text.count('[/f]')
                                    if open_tags != close_tags:
                                        logger.warning(f"⚠️ Несбалансированные теги формул: [f]={open_tags}, [/f]={close_tags}")
                                        # Закрываем незакрытые теги
                                        if open_tags > close_tags:
                                            for _ in range(open_tags - close_tags):
                                                text += '[/f]'
                                    
                                    # Для DeepSeek R1 добавляем предупреждение об обрезке
                                    if "deepseek-r1" in model.lower() and len(text) > 700:
                                        if text.endswith(('...', '—', '-', ':', ';', ',')) or \
                                           ('```' in text and text.count('```') % 2 != 0):
                                            if not any(marker in text for marker in ['[ответ обрезан]', '[обрезка]']):
                                                text += '\n\n<i>Примечание: ответ может быть обрезан из-за ограничений бесплатной модели</i>'
                                    
                                    code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text))
                                    formula_blocks = len(re.findall(r'\[f\].*?\[/f\]', text))
                                    
                                    logger.info(f"✅ {model.split('/')[-1]} ответил за {elapsed:.1f}с, {len(text)} символов, код: {code_blocks}, формулы: {formula_blocks}")
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
        "💻 **Пример кода для Telegram бота с SberVision**\n\n"
        "```python\nimport telebot\nimport requests\nimport json\n\n# Настройки\nTOKEN = 'YOUR_BOT_TOKEN'\nSBER_API_KEY = 'YOUR_SBER_VISION_KEY'\n\nbot = telebot.TeleBot(TOKEN)\n\n@bot.message_handler(content_types=['photo'])\ndef handle_photo(message):\n    file_id = message.photo[-1].file_id\n    file_info = bot.get_file(file_id)\n    file_url = f'https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}'\n    \n    response = requests.post(\n        'https://api.sber.dev/vision/v1/ocr',\n        headers={'Authorization': f'Bearer {SBER_API_KEY}'},\n        json={'image_url': file_url}\n    )\n    \n    if response.status_code == 200:\n        result = response.json()\n        text = result.get('text', 'Текст не распознан')\n        bot.reply_to(message, f'📝 Распознанный текст:\\n{text}')\n    else:\n        bot.reply_to(message, '❌ Ошибка распознавания')\n\nbot.polling()\n```\n\n"
        "📁 **Структура проекта**:\n"
        "```\nproject/\n├── bot.py\n├── config.py\n├── sber_vision.py\n├── database.py\n└── requirements.txt\n```"
    ],
    "общий": [
        "🧠 **Анализ запроса**\n\n"
        "Для распознавания текста и формул в Telegram группе:\n\n"
        "[f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n\n"
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
    """Получает ответ от AI с поддержкой формул"""
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
                "Отвечай ясно и по делу. Используй Markdown для форматирования.\n\n"
                "**ВАЖНО ДЛЯ ФОРМУЛ:**\n"
                "1. ВСЕ математические формулы оборачивай в теги [f] и [/f]\n"
                "2. Пример: [f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n"
                "3. Для кода используй тройные кавычки с указанием языка.\n"
                "4. Всегда закрывай блок кода и теги [f].\n"
                "5. Пиши формулы в LaTeX, но старайся использовать простой синтаксис.\n"
                "6. Избегай сложных LaTeX конструкций, если можно упростить.\n\n"
                "Держи ответ в 800-1500 символов."
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
                "Ты технический аналитик. Дай глубокий анализ с практическими шагами.\n\n"
                "**ВАЖНО ДЛЯ ФОРМУЛ:**\n"
                "1. ВСЕ математические выражения оборачивай в теги [f] и [/f]\n"
                "2. Пример правильного использования:\n"
                "   - Формула работы: [f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n"
                "   - Интеграл: [f]\\int_{a}^{b} f(x) dx[/f]\n"
                "3. Для кода используй тройные кавычки с языком.\n"
                "4. Всегда проверяй баланс тегов.\n"
                "5. Используй простой LaTeX синтаксис для лучшей читаемости.\n\n"
                "Будь конкретным и техничным. Отвечай развернуто (1000-1800 символов)."
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
        "🤖 **НОВИНКА: Поддержка формул в Unicode!**\n"
        "• Интеллектуальное преобразование LaTeX → Unicode\n"
        "• Формулы в моноширинном шрифте для читаемости\n"
        "• Автоматическое экранирование MarkdownV2\n\n"
        "⚡ **Примеры:**\n"
        "• Простая формула: [f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n"
        "• Сложная формула: [f]\\int_{a}^{b} f(x) dx[/f]\n"
        "• Код с подсветкой:\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_message_safe(message.chat.id, welcome, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Команда /status - проверка доступности моделей"""
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
        
        # Проверяем сервис формул
        status_report += "\n\n🧮 **Сервис формул:** "
        
        try:
            # Тестируем преобразование формулы
            test_formula = "x^2 + y^2 = z^2"
            unicode_result = convert_latex_to_unicode(test_formula)
            if unicode_result:
                status_report += f"✅ Работает (пример: {unicode_result})"
            else:
                status_report += "⚠️ Проблемы с конвертацией"
        except Exception as e:
            status_report += f"❌ Ошибка: {str(e)[:50]}"
        
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
    """Обработка вопросов с поддержкой формул и кода"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = message.from_user.username or f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    processing_msg = None
    try:
        processing_text = "🤔 Две ИИ-модели анализируют вопрос параллельно..."
        processing_msg = await send_message_safe(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            return
        
        start_time = time.time()
        
        logger.info("⚡ Параллельные запросы запущены...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        main_response, deepseek_response, main_model, deepseek_model, main_code_blocks, deepseek_code_blocks = await get_parallel_responses(user_question)
        
        elapsed = time.time() - start_time
        
        models_used = []
        formula_counts = [0, 0]
        
        if main_response and main_model != "local_fallback":
            models_used.append(main_model.split('/')[-1])
            formula_counts[0] = len(re.findall(r'\[f\].*?\[/f\]', main_response))
        
        if deepseek_response and deepseek_model != "local_fallback":
            models_used.append(deepseek_model.split('/')[-1])
            formula_counts[1] = len(re.findall(r'\[f\].*?\[/f\]', deepseek_response))
        
        if main_response:
            logger.info(f"📤 Отправка основного ответа ({len(main_response)} символов, формул: {formula_counts[0]})")
            
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
            logger.info(f"📤 Отправка аналитического ответа ({len(deepseek_response)} символов, формул: {formula_counts[1]})")
            
            await send_long_message(
                chat_id,
                f"🔍 **Детальный анализ:**\n\n{deepseek_response}",
                message.message_id
            )
            
            if main_response:
                total_code_blocks = main_code_blocks + deepseek_code_blocks
                total_formulas = formula_counts[0] + formula_counts[1]
                
                completion_text = (
                    f"✅ Анализ завершён!\n"
                    f"⏱️ Общее время: {elapsed:.1f} секунд\n"
                    f"📊 Основной ответ: {len(main_response)} символов\n"
                    f"🔍 Детальный анализ: {len(deepseek_response)} символов"
                )
                
                if total_code_blocks > 0:
                    completion_text += f"\n💻 Код: {total_code_blocks} блок(ов)"
                
                if total_formulas > 0:
                    completion_text += f"\n🧮 Формулы: {total_formulas} сложных формул"
                
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
                
                if formula_counts[1] > 0:
                    completion_text += f"\n🧮 Формулы: {formula_counts[1]} сложных формул"
                
                if models_used:
                    completion_text += f"\n🤖 Модели: {', '.join(models_used)}"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Успешно! Время: {elapsed:.1f}с, формулы: {total_formulas if main_response else formula_counts[1]}")
            
        elif main_response:
            completion_text = (
                f"✅ Ответ готов!\n"
                f"⏱️ Время: {elapsed:.1f} секунд\n"
                f"📊 Длина: {len(main_response)} символов"
            )
            
            if main_code_blocks > 0:
                completion_text += f"\n💻 Код: {main_code_blocks} блок(ов)"
            
            if formula_counts[0] > 0:
                completion_text += f"\n🧮 Формулы: {formula_counts[0]} сложных формул"
            
            if models_used:
                completion_text += f"\n🤖 Модели: {', '.join(models_used)}"
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Основной ответ за {elapsed:.1f}с, формулы: {formula_counts[0]}")
            
        else:
            fallback_response = get_local_fallback_response(user_question)
            await processing_msg.edit_text("⚠️ Использую локальную базу знаний...", parse_mode=None)
            
            await send_long_message(
                chat_id, 
                f"📚 **База знаний:**\n\n{fallback_response}", 
                message.message_id
            )
            
            fallback_code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', fallback_response))
            fallback_formulas = len(re.findall(r'\[f\].*?\[/f\]', fallback_response))
            
            completion_text = f"✅ Локальный ответ за {elapsed:.1f}с"
            
            if fallback_code_blocks > 0:
                completion_text += f"\n💻 Код: {fallback_code_blocks} блок(ов)"
            
            if fallback_formulas > 0:
                completion_text += f"\n🧮 Формулы: {fallback_formulas} сложных формул"
            
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
    logger.info("🧮 РЕНДЕРИНГ ФОРМУЛ:")
    logger.info("  • LaTeX → Unicode преобразование")
    logger.info("  • MarkdownV2 как основной режим")
    logger.info("  • Селективная обработка через теги [f]")
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