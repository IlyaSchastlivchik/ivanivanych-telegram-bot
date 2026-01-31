Конечно, вот полностью исправленная версия кода, включающая исправление ошибки `NameError` и предыдущие улучшения для отправки одиночных файлов и ZIP-архивов.

```python
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
import io
import zipfile # Для создания ZIP архивов
import shutil # Для работы с файлами и директориями
import tempfile # Для создания временных директорий

from typing import Optional, List, Tuple, Dict, Any
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.enums import ChatAction
from dotenv import load_dotenv

# ==================== НАСТРОЙКА ====================

# ----- 1. НАСТРОЙКА ЛОГИРОВАНИЯ (ДОЛЖНА БЫТЬ ПЕРВОЙ) -----
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

logger.info("🚀 Инициализация скрипта IvanIvanych Bot...")

# ----- 2. ЗАГРУЗКА .ENV ФАЙЛА -----
ENV_FILE_PATH = '/etc/secrets/.env' 

try:
    if os.path.exists(ENV_FILE_PATH):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        logger.info(f"✅ Успешно загружен .env файл из {ENV_FILE_PATH}")
    else:
        logger.warning(f"⚠️ Файл .env не найден по пути {ENV_FILE_PATH}. Попытка загрузить стандартным путем.")
        if load_dotenv(): 
             logger.info("✅ .env файл успешно загружен стандартным путем.")
        else:
             logger.warning("⚠️ Файл .env не найден ни в '/etc/secrets/' ни стандартным путем. Переменные окружения могут быть не установлены.")
except Exception as e:
    logger.error(f"❌ Критическая ошибка при загрузке .env файла: {e}. Продолжаю работу.", exc_info=True)

# ----- 3. СЧИТЫВАНИЕ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ -----
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# --- ДОПОЛНИТЕЛЬНЫЙ ДЕБАГГИНГ для USE_PAID_MODELS ---
use_paid_models_raw_value = os.getenv("USE_PAID_MODELS", "false") 
logger.info(f"🌟 DEBUG: Значение USE_PAID_MODELS, считанное из окружения (или .env): '{use_paid_models_raw_value}'")
USE_PAID_MODELS = use_paid_models_raw_value.lower() == "true"
logger.info(f"🌟 DEBUG: Флаг USE_PAID_MODELS установлен в: {USE_PAID_MODELS}")
# --- КОНЕЦ ДОПОЛНИТЕЛЬНОГО ДЕБАГГИНГА ---

if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:
    logger.error("❌ Ошибка: Отсутствуют обязательные переменные окружения (TELEGRAM_BOT_TOKEN, OPENROUTER_API_KEY). ")
    exit(1) # Завершаем работу, если критические ключи не установлены

# Конфигурация OpenRouter API
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_API_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_URL = f"{OPENROUTER_BASE_URL}/chat/completions"

# ==================== КОНФИГУРАЦИЯ МОДЕЛЕЙ ====================
MODELS_CONFIG = {
    "primary_free_models": [
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-vl-7b-instruct:free",
        "microsoft/phi-3.5-mini-instruct:free",
    ],
    "secondary_free_models": [
        "qwen/qwen2.5-32b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
    ],
    "paid_models": [
        "google/gemini-2.5-flash-lite",
        "deepseek/deepseek-v3",
    ]
}

MODEL_TIMEOUTS = {
    "fast": 45,      # Быстрые модели
    "medium": 60,    # Средние модели
    "slow": 90,      # Медленные модели
    "paid": 120,     # Платные модели
    "test": 30,      # Таймаут для теста доступности
}

# ==================== КОНФИГУРАЦИЯ ГЕНЕРАЦИИ ====================
GENERATION_CONFIG = {
    "temperature": 0.8,
    "max_tokens": 1500,
    "top_p": 0.9,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
    "stream": False,
}

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
    """Очистка текста от опасных символов."""
    if not text:
        return ""
    
    cleaned = []
    for char in text:
        cat = unicodedata.category(char)
        # Пропускаем управляющие символы, кроме переносов строк и табуляции
        if cat[0] == 'C' and char not in ['\n', '\r', '\t']:
            continue
        cleaned.append(char)
    
    text = ''.join(cleaned)
    
    # Удаляем известные опасные диапазоны символов (некоторые символы нулевой ширины и т.д.)
    dangerous_chars_ranges = [
        '\u0000-\u0008', '\u000b', '\u000c', '\u000e-\u001f',
        '\u200b', '\u200c', '\u200d', '\ufeff'
    ]
    
    for char_range in dangerous_chars_ranges:
        if '-' in char_range:
            start_ord, end_ord = ord(char_range[0]), ord(char_range[-1])
            text = ''.join([c for c in text if not (ord(c) >= start_ord and ord(c) <= end_ord)])
        else:
            text = text.replace(char_range, '')
    
    return text

# ----- HTML подготовка: ТОЛЬКО для кода и экранирования -----
def prepare_html_message(text: str) -> str:
    """Подготовка текста для отправки в формате HTML, корректно обрабатывая блоки кода."""
    text_to_process = clean_text(text)
    
    # --- Плейсхолдеры для блоков кода ---
    code_block_map = {}
    def save_code_block(match):
        key = f"__CODE_BLOCK_{len(code_block_map)}__"
        code_block_map[key] = match.group(0)
        return key
    
    code_section_pattern = r'(```(\w*)\n)([\s\S]*?)(\n```)'
    text_with_placeholders = re.sub(code_section_pattern, save_code_block, text_to_process)
    
    # --- Плейсхолдеры для инлайн-кода ---
    inline_code_map = {}
    def save_inline_code(match):
        key = f"__INLINE_CODE_{len(inline_code_map)}__"
        inline_code_map[key] = match.group(1) # Сохраняем контент
        return key
    
    text_with_placeholders = re.sub(r'`(.*?)`', save_inline_code, text_with_placeholders)
    
    # --- Экранируем остальной текст ---
    escaped_text = html.escape(text_with_placeholders)
    
    # --- Восстанавливаем блоки кода ---
    final_html = escaped_text
    for key, original_block in code_block_map.items():
        match = re.match(code_section_pattern, original_block)
        if match:
            lang = match.group(2)
            content = match.group(3)
            # Отменяем экранирование, сделанное html.escape внутри кода
            restored_content = content.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            restored_content = restored_content.replace('&quot;', '"').replace('&#x27;', "'").replace('&#x2F;', '/')
            
            html_block = f'<pre><code class="language-{lang}">{restored_content}</code></pre>' if lang else f'<pre><code>{restored_content}</code></pre>'
            final_html = final_html.replace(key, html_block)

    # --- Восстанавливаем инлайн-код ---
    for key, inline_content in inline_code_map.items():
        # Отменяем экранирование для контента инлайн-кода
        restored_content = inline_content.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        restored_content = restored_content.replace('&quot;', '"').replace('&#x27;', "'").replace('&#x2F;', '/')
        
        html_inline = f'<code>{restored_content}</code>'
        final_html = final_html.replace(key, html_inline)
        
    return final_html

# ----- MarkdownV2 подготовка: ТОЛЬКО для кода и экранирования -----
def prepare_markdown_message(text: str) -> str:
    """Подготовка текста для отправки в формате MarkdownV2."""
    text = clean_text(text)
    
    # --- Плейсхолдеры для блоков кода ---
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    text_with_placeholders = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    # --- Плейсхолдеры для инлайн-кода ---
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_codes)-1}__"
    
    text_with_placeholders = re.sub(r'`[^`\n]+`', save_inline_code, text_with_placeholders)
    
    # --- Экранируем специальные символы MarkdownV2 ---
    # Символы, которые нужно экранировать, чтобы они отображались как есть.
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text_with_placeholders = text_with_placeholders.replace(char, '\\' + char)
    
    # --- Восстанавливаем оригинальные блоки кода и инлайн-код ---
    # Сначала инлайн-код
    for i, inline_code_segment in enumerate(inline_codes):
        text_with_placeholders = text_with_placeholders.replace(f'__INLINE_CODE_{i}__', inline_code_segment)
    
    # Затем блоки кода
    for i, code_block_segment in enumerate(code_blocks):
        text_with_placeholders = text_with_placeholders.replace(f'__CODE_BLOCK_{i}__', code_block_segment)
    
    return text_with_placeholders

# ----- Умная отправка сообщений (без специфической обработки формул) -----
async def send_message_safe(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Умная отправка сообщений с автоматическим выбором формата (HTML, MarkdownV2, Plain)."""
    if not text:
        return None

    kwargs = {"chat_id": chat_id, "reply_to_message_id": reply_to_message_id}
    
    try:
        html_text = prepare_html_message(text)
        if len(html_text) > 4000: 
            raise ValueError("HTML слишком длинный для отправки.")
        kwargs["text"] = html_text
        kwargs["parse_mode"] = "HTML"
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Сообщение отправлено с HTML (chat_id: {chat_id}), длина: {len(text)} символов (HTML: {len(html_text)}).")
        return result
        
    except Exception as e:
        logger.warning(f"⚠️ HTML не сработал для chat_id {chat_id}: {e}, пробую MarkdownV2...")
        
        try:
            markdown_text = prepare_markdown_message(text)
            if len(markdown_text) > 4000:
                raise ValueError("MarkdownV2 слишком длинный для отправки.")
            kwargs["text"] = markdown_text
            kwargs["parse_mode"] = "MarkdownV2"
            result = await bot.send_message(**kwargs)
            logger.info(f"✅ Сообщение отправлено с MarkdownV2 (chat_id: {chat_id}), длина: {len(text)} символов (MD: {len(markdown_text)}).")
            return result
            
        except Exception as e2:
            logger.warning(f"⚠️ MarkdownV2 не сработал для chat_id {chat_id}: {e2}, пробую без форматирования...")
            
            try:
                cleaned_text = clean_text(text)
                if len(cleaned_text) > 4096:
                     raise ValueError("Простой текст сообщения слишком длинный для отправки.")
                
                kwargs["text"] = cleaned_text
                kwargs["parse_mode"] = None
                result = await bot.send_message(**kwargs)
                logger.info(f"✅ Сообщение отправлено без форматирования (chat_id: {chat_id}), длина: {len(text)} символов (Plain: {len(cleaned_text)}).")
                return result
                
            except Exception as e3:
                logger.error(f"❌ Не удалось отправить сообщение для chat_id {chat_id}: {e3}", exc_info=True)
                return None

def split_message_smart(text: str, max_length: int = 3500) -> List[str]:
    """Умное разбиение длинных сообщений с сохранением блоков кода."""
    if len(text) <= max_length:
        return [text] if text else []
    
    code_blocks = []
    code_pattern = r'```[\s\S]*?```'
    def replace_code_with_placeholder(match):
        code_blocks.append(match.group(0))
        return f'__CODE_BLOCK_{len(code_blocks)-1}__'
    text_with_placeholders = re.sub(code_pattern, replace_code_with_placeholder, text)
    
    parts = []
    current_part = ""
    paragraphs = text_with_placeholders.split('\n\n')
    
    for para in paragraphs:
        if len(current_part) + len(para) + 2 <= max_length:
            current_part += para + "\n\n"
        else:
            if len(para) > max_length: # Если сам параграф слишком длинный
                if current_part: # Сначала сохраняем накопленный текущий кусок
                    parts.append(current_part.strip())
                    current_part = ""
                # Разбиваем длинный параграф на строки
                lines = para.split('\n')
                temp_para_part = ""
                for line in lines:
                    if len(temp_para_part) + len(line) + 1 <= max_length:
                        temp_para_part += line + "\n"
                    else:
                        if temp_para_part:
                            parts.append(temp_para_part.strip())
                        temp_para_part = line + "\n"
                if temp_para_part: # Добавляем последнюю часть разбитого параграфа
                    parts.append(temp_para_part.strip())
            else: # Параграф умещается, но его добавление превысит лимит
                if current_part: # Сохраняем предыдущую часть
                    parts.append(current_part.strip())
                current_part = para + "\n\n" # Начинаем новую часть с текущего параграфа

    if current_part: # Добавляем последнюю накопленную часть
        parts.append(current_part.strip())
    
    # Восстанавливаем блоки кода в каждой части
    final_parts = []
    for part in parts:
        restored_part = part
        for i, code_block in enumerate(code_blocks):
            placeholder = f'__CODE_BLOCK_{i}__'
            restored_part = restored_part.replace(placeholder, code_block)
        final_parts.append(restored_part.strip())
    
    # Фильтруем пустые части, которые могли появиться после стриппинга
    return [p for p in final_parts if p]

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """Отправка длинных сообщений с использованием умного разбиения."""
    if not text:
        return
    logger.info(f"📤 Подготовка сообщения (chat_id: {chat_id}) длиной {len(text)} символов...")
    parts = split_message_smart(text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        await send_message_safe(
            chat_id=chat_id,
            text=part,
            reply_to_message_id=reply_to_message_id if i == 0 else None # Отвечаем только на первое сообщение
        )
        if i < len(parts) - 1: # Небольшая задержка между частями
            await asyncio.sleep(0.5)

# ----- Функция генерации HTML файла с подсветкой кода -----
def generate_html_file_with_code(language: str, filename: str, code_content: str) -> Tuple[str, io.BytesIO]:
    """
    Генерирует HTML файл, который отображает предоставленный код с подсветкой синтаксиса.
    Возвращает имя файла (возможно, с расширением .html) и байты содержимого HTML.
    """
    # Базовое маппинг для класса языка Prism.js
    prism_lang_class = language.lower()
    if prism_lang_class in ["python", "py"]:
        prism_lang_class = "python"
    elif prism_lang_class in ["javascript", "js"]:
        prism_lang_class = "javascript"
    elif prism_lang_class in ["html", "html5"]:
        prism_lang_class = "html"
    elif prism_lang_class == "css":
        prism_lang_class = "css"
    elif prism_lang_class == "json":
        prism_lang_class = "json"
    elif prism_lang_class == "yaml":
        prism_lang_class = "yaml"
    elif prism_lang_class == "bash" or prism_lang_class == "shell":
        prism_lang_class = "bash"
    elif prism_lang_class == "markdown":
        prism_lang_class = "markdown"
    else: # Для неизвестных языков или простого текста
        prism_lang_class = "text"

    # Вставляем сам код. Используем html.escape, чтобы гарантировать, что он будет корректно отображен в HTML.
    # Prism.js затем интерпретирует его как код.
    escaped_code_content = html.escape(code_content)

    # Формируем финальное имя файла. Если язык - HTML, гарантируем .html расширение.
    # Иначе, будем добавлять .html к оригинальному имени файла (например: script.js.html).
    base_filename, ext = os.path.splitext(filename)
    if base_filename == "": # Если filename был только расширением или пустым
        base_filename = "code" # Используем дефолтное имя
        
    if language.lower() == "html":
        output_filename = f"{base_filename}.html"
    else:
        output_filename = f"{base_filename}{ext}.html" if ext else f"{base_filename}.html"

    html_content = f"""
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Code: {html.escape(filename)}</title>
    
    <!-- Prism.js Core CSS -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/themes/prism-okaidia.min.css">
    <!-- Prism.js Core JS -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/prism.min.js"></script>
    <!-- Dynamically load language components -->
    <script src="https://cdnjs.cloudflare.com/ajax/libs/prism/1.29.0/components/prism-{prism_lang_class}.min.js"></script>
    
    <style>
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            margin: 15px; 
            background-color: #282a36; /* Dark background */
            color: #f8f8f2; /* Light text color */
        }}
        pre[class*="language-"] {{ 
            padding: 1em; 
            border-radius: 8px; 
            margin: 0; 
            box-shadow: 0 2px 10px rgba(0,0,0,0.5);
            font-size: 0.9em;
            max-height: 70vh; /* Make code blocks scrollable */
            overflow-y: auto; 
            border-left: 3px solid #50fa7b; /* Accent color */
        }}
        h1 {{ color: #bd93f9; font-size: 1.5em; margin-bottom: 1em; }}
    </style>
</head>
<body>
    <h1>Code Snippet: {html.escape(filename)}</h1>
    <pre><code class="language-{prism_lang_class}">{escaped_code_content}</code></pre>
    
    <script>
        Prism.highlightAll(); 
    </script>
</body>
</html>
"""
    # Prepare for sending as a file
    file_data = io.BytesIO(html_content.encode('utf-8'))
    
    return output_filename, file_data # Return filename and file-like object

# ==================== OPENROUTER ФУНКЦИИ ====================

async def test_model_speed(model: str) -> Tuple[bool, float]:
    """Тестирование скорости и доступности модели."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2", 
        "X-Title": "IvanIvanych Bot",
    }
    data = {
        "model": model,
        "messages": [{"role": "user", "content": "Привет"}],
        "max_tokens": 10,
        "stream": False
    }
    try:
        start = time.time()
        timeout_seconds = MODEL_TIMEOUTS["test"] 
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                elapsed = time.time() - start
                if response.status == 200:
                    return True, elapsed
                else:
                    error_text = await response.text()
                    logger.warning(f"  ⚠️ Тест модели {model.split('/')[-1]}: Статус {response.status}, Ошибка: {error_text[:100]}")
                    return False, float('inf')
    except asyncio.TimeoutError:
        logger.warning(f"  ⏱️ Тест модели {model.split('/')[-1]}: Превышен таймаут ({timeout_seconds}с)")
        return False, float('inf')
    except Exception as e:
        logger.warning(f"  ❌ Тест модели {model.split('/')[-1]}: Неизвестная ошибка ({str(e)[:100]})")
        return False, float('inf')

def get_model_timeout(model: str) -> int:
    """Определение финального таймаута для использования модели."""
    model_lower = model.lower()
    if "phi-3.5" in model_lower or "qwen-2.5-7b" in model_lower or "gemini-2.5-flash-lite" in model_lower:
        return MODEL_TIMEOUTS["fast"]
    elif "qwen2.5-32b" in model_lower or "mistral-7b" in model_lower:
        return MODEL_TIMEOUTS["medium"]
    elif "llama" in model_lower or "70b" in model_lower:
        return MODEL_TIMEOUTS["slow"]
    elif any(paid_model in model_lower for paid_model in ["deepseek-v3", "gpt-4", "claude-3", "gpt-3.5-turbo"]):
        return MODEL_TIMEOUTS["paid"]
    return MODEL_TIMEOUTS["medium"]

async def get_available_models() -> Dict[str, List[Tuple[str, float]]]:
    """Собирает и тестирует все доступные модели."""
    logger.info("🔍 Проверяю доступность AI-моделей...")
    
    models_to_check = {
        'primary_free': MODELS_CONFIG["primary_free_models"],
        'secondary_free': MODELS_CONFIG["secondary_free_models"],
        'paid': MODELS_CONFIG["paid_models"] if USE_PAID_MODELS else []
    }
    available_models_grouped = {'primary_free': [], 'secondary_free': [], 'paid': []}
    
    all_models_for_test = []
    for model_list in models_to_check.values():
        all_models_for_test.extend(model_list)
    
    tasks = [test_model_speed(model) for model in all_models_for_test]
    results = await asyncio.gather(*tasks)

    model_index = 0
    for category, model_list in models_to_check.items():
        for model in model_list:
            is_available, speed = results[model_index]
            if is_available:
                available_models_grouped[category].append((model, speed))
            model_index += 1

    for category in available_models_grouped:
        available_models_grouped[category].sort(key=lambda x: x[1])

    total_available = sum(len(v) for v in available_models_grouped.values())
    logger.info(f"✅ Найдено {total_available} доступных AI-моделей:")
    for category, models in available_models_grouped.items():
        if models:
            model_names = [m[0].split('/')[-1] for m in models]
            logger.info(f"  - {category.replace('_', ' ').title()}: {', '.join(model_names)}")
        else:
            logger.info(f"  - {category.replace('_', ' ').title()}: Нет доступных")

    return available_models_grouped

# --- Константы для парсинга вывода файла от AI ---
FILE_OUTPUT_MARKER_START = "### FILE_OUTPUT_START"
FILE_OUTPUT_MARKER_END = "### FILE_OUTPUT_END"
PACKAGE_OUTPUT_MARKER_START = "### PACKAGE_OUTPUT_START"
PACKAGE_OUTPUT_MARKER_END = "### PACKAGE_OUTPUT_END"
DEFAULT_CODE_FILENAME = "code.txt"
DEFAULT_CODE_LANGUAGE = "text"

async def get_ai_response(user_question: str) -> Tuple[Optional[str], Optional[str], int]:
    """
    Получает ответ от AI, выбирая лучшую модель по приоритету.
    Инструктирует AI использовать Unicode/ASCII для формул, избегая LaTeX.
    
    Возвращает: (текст_ответа, имя_модели, кол-во_блоков_кода)
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2", # Поле для трекинга
        "X-Title": "IvanIvanych Bot", # Название вашего приложения
    }
    
    # --- ИЗМЕНЕННЫЙ СИСТЕМНЫЙ ПРОМПТ ---
    system_prompt = {
        "role": "system",
        "content": (
            "Ты Иван Иваныч — эксперт в технологиях и футуристике. "
            "Отвечай ясно и по делу. Используй Markdown для форматирования. "
            "Для кода используй тройные кавычки с указанием языка (например, ```python). "
            "Для физических и математических формул, используй доступные Unicode символы "
            "и максимально приближенное к математическому написание с помощью стандартных символов клавиатуры (ASCII). "
            "Например: E=mc^2 (вместо E=mc²), a/b (вместо \\frac{a}{b}), Sum(i=0 to n) x_i (вместо ∑_{i=0}^{n} x_i). "
            "Избегай LaTeX синтаксиса. Фокусируйся на читаемости в обычном текстовом формате Telegram. "
            "Если возможно, используй Unicode символы для обозначений (например, α, β, μ, ∑, ∫). "
            "Всегда закрывай блок кода. "
            
            f"**ОСОБАЯ ИНСТРУКЦИЯ ДЛЯ ВЫВОДА КОДА В ФАЙЛ (ОДИНОЧНЫЙ):**\n"
            f"Если пользователь явно просит тебя предоставить код в виде файла или создать HTML-страницу, "
            f"выводи его, заключив в следующий блок:\n"
            f"```html\n{FILE_OUTPUT_MARKER_START}\n"
            f"Language: [язык_программирования]\n"
            f"Filename: [имя_файла.расширение]\n\n"
            f"[САМ КОД]\n"
            f"{FILE_OUTPUT_MARKER_END}\n"
            f"```\n"
            f"   - `[язык_программирования]` должен быть типа `python`, `javascript`, `html`, `css`, `json`, `yaml` и т.д. "
            f"   - `[имя_файла.расширение]` - предлагаемое имя файла (например, `my_script.py`, `index.html`).\n"
            f"   - `[САМ КОД]` - это код, который ты генерируешь.\n"
            f"Примеры:\n"
            f"1. Для Python скрипта:\n"
            f"```html\n{FILE_OUTPUT_MARKER_START}\nLanguage: python\nFilename: hello_world.py\n\nprint('Hello, world!')\n{FILE_OUTPUT_MARKER_END}\n```\n"
            f"2. Для HTML файла:\n"
            f"```html\n{FILE_OUTPUT_MARKER_START}\nLanguage: html\nFilename: my_page.html\n\n<!DOCTYPE html>\n<html>\n<head>\n    <title>My Page</title>\n</head>\n<body>\n    <h1>Hello</h1>\n</body>\n</html>\n{FILE_OUTPUT_MARKER_END}\n```\n"
            f"Если язык не указан, используй `{DEFAULT_CODE_LANGUAGE}`. Если имя файла не указано, используй `{DEFAULT_CODE_FILENAME}`.\n"
            
            f"**ДЛЯ ВЫВОДА НЕСКОЛЬКИХ ФАЙЛОВ (ПРИЛОЖЕНИЯ/ПРОЕКТА) В ОДНОМ АРХИВЕ ZIP:**\n"
            f"Если пользователь просит отправить проект из нескольких файлов или весь каталог, используй следующий формат:\n"
            f"```json\n{PACKAGE_OUTPUT_MARKER_START}\n{{"
            f"\"folder_name\": \"[имя_папки]\",\n"
            f"\"files\": [\n"
            f"    {{\"filename\": \"[путь/имя_файла.расширение]\", \"language\": \"[язык]\", \"content\": \"[содержимое_файла]\"}},\n"
            f"    ...\n"
            f"]\n}}\n{PACKAGE_OUTPUT_MARKER_END}\n```\n"
            f"   - `[имя_папки]` - корневое имя для архива (например, `my_web_app`).\n"
            f"   - `[путь/имя_файла.расширение]` - полный путь внутри папки (например, `src/components/Button.js`, `index.html`).\n"
            f"   - `[язык]` - язык программирования для подсветки (аналогично `Language:` в `FILE_OUTPUT_MARKER`).\n"
            f"   - `[содержимое_файла]` - сам контент файла (должен быть в виде строки, экранированной для JSON).\n"
            f"AI должен отдавать предпочтение формату `PACKAGE_OUTPUT_START` для нескольких файлов.\n"
            
            "\nДержи ответ в 800-1500 символов."
        )
    }
    # --- КОНЕЦ ИЗМЕНЕННОГО ПРОМПТА ---
    
    available_models_data = await get_available_models()
    selected_model_info = None # Будет содержать (имя_модели, тип_модели)

    # Приоритет выбора модели
    if available_models_data.get('primary_free'):
        model_name, speed = available_models_data['primary_free'][0] # Берем самую быструю из доступных
        selected_model_info = (model_name, 'primary_free')
        logger.info(f"🎯 Выбрана основная бесплатная модель: {model_name.split('/')[-1]} (Скорость: {speed:.2f}с)")
    elif available_models_data.get('secondary_free'):
        model_name, speed = available_models_data['secondary_free'][0]
        selected_model_info = (model_name, 'secondary_free')
        logger.info(f"🎯 Выбрана вторичная бесплатная модель: {model_name.split('/')[-1]} (Скорость: {speed:.2f}с)")
    elif USE_PAID_MODELS and available_models_data.get('paid'):
        model_name, speed = available_models_data['paid'][0]
        selected_model_info = (model_name, 'paid')
        logger.info(f"💰 Выбрана платная модель: {model_name.split('/')[-1]} (Скорость: {speed:.2f}с)")
    else:
        logger.warning("⚠️ ВСЕ AI модели недоступны или отключены, перехожу на локальный ответ.")
        response = get_local_fallback_response(user_question)
        return response, "local_fallback", 0 # Возвращаем локальный ответ без кода

    # --- Если модель AI была выбрана, приступаем к запросу ---
    model_to_use, model_type_tag = selected_model_info
    
    # Определяем конфигурацию и тип модели для логгирования
    current_config = PAID_MODEL_CONFIG if model_type_tag == 'paid' else GENERATION_CONFIG
    display_model_type = "💰 Платная" if model_type_tag == 'paid' else "🆓 Бесплатная"
    
    # Определяем таймаут для выбранной модели
    model_timeout = get_model_timeout(model_to_use)
    logger.info(f"▶️ Буду использовать модель: {model_to_use.split('/')[-1]} ({display_model_type}, таймаут: {model_timeout}с)")

    # Формируем данные для запроса
    data = {
        "model": model_to_use,
        "messages": [
            system_prompt,
            {"role": "user", "content": user_question}
        ],
        **current_config # Применяем соответствующую конфигурацию
    }
    
    # Устанавливаем таймаут асинхронной сессии
    timeout = aiohttp.ClientTimeout(total=model_timeout)
    
    response_text = None
    code_blocks_count = 0
    
    # Попытка выполнить запрос к модели (с повторными попытками)
    for attempt in range(2): # Даем 2 попытки на модель
        try:
            logger.info(f"🚀 Запрос к AI ({model_to_use.split('/')[-1]}): попытка {attempt+1}/2...")
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
                            
                            # Проверяем, что ответ содержательный
                            if text and len(text) > 20 and not text.isspace():
                                # --- Попытка исправить распространенные проблемы с кодом ---
                                backtick_count = text.count('`')
                                if backtick_count % 2 != 0:
                                    logger.warning(f"⚠️ Нечётное количество кавычек ({backtick_count}) в ответе от {model_to_use.split('/')[-1]}. Попытка исправить.")
                                    if text.count('```') % 2 != 0: # Если блок ``` не закрыт
                                        text += '\n```'
                                    elif text.endswith('`') and text.rfind('`') == len(text)-1: # Если последний символ - открывающая кавычка
                                        text += '`' # Добавляем закрывающую
                                # --- Конец исправления ---

                                # Считаем количество корректных блоков кода
                                code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text)
                                code_blocks_count = len(code_blocks)
                                
                                logger.info(f"✅ {display_model_type} {model_to_use.split('/')[-1]} ответил за {elapsed:.1f}с, {len(text)} символов, блоков кода: {code_blocks_count}")
                                return text, model_to_use, code_blocks_count
                            else:
                                logger.warning(f"⚠️ {model_to_use.split('/')[-1]} вернул некорректный ответ (слишком короткий/пустой): {len(text)} символов")
                    else:
                        # Ответ с ошибкой от API
                        error_text = await response.text()
                        logger.warning(f"⚠️ {model_to_use.split('/')[-1]} ошибка [{response.status}]: {error_text[:200]}")
                
                # Если первая попытка не удалась, ждем перед второй
                if attempt < 1:
                    wait_time = 2.0
                    logger.info(f"🔄 Повторная попытка через {wait_time} секунд...")
                    await asyncio.sleep(wait_time)
                    
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Таймаут при запросе к {model_to_use.split('/')[-1]} (> {model_timeout}с)")
            if attempt < 1:
                await asyncio.sleep(2.0) # Ждем перед повтором
        except Exception as e:
            logger.error(f"❌ Непредвиденная ошибка при работе с {model_to_use.split('/')[-1]}: {e}", exc_info=True)
            if attempt < 1:
                await asyncio.sleep(2.0) # Ждем перед повтором

    # Если ни одна из попыток не увенчалась успехом для выбранной AI модели
    logger.warning(f"❌ Модель {model_to_use} не сработала после 2 попыток.")
    
    # Переходим на локальный fallback, если AI модель полностью отказала
    logger.warning("🔁 Перехожу на локальный fallback.")
    response = get_local_fallback_response(user_question)
    return response, "local_fallback", 0 # Возвращаем локальный ответ, в нем кода нет

# ==================== ЛОКАЛЬНЫЙ FALLBACK ====================
LOCAL_RESPONSES = {
    "технология": [
        "🤖 **Анализ технологии**\n\n"
        "Для интеграции современных AI-решений от поставщиков вроде OpenRouter в Telegram, "
        "требуется настройка API-ключей и грамотная обработка ответов.\n\n"
        "**Основные шаги:**\n"
        "1. **Получение API ключей** от выбранных AI-провайдеров (OpenRouter, Google AI, OpenAI и т.д.).\n"
        "2. **Конфигурация бота** с использованием библиотеки `aiogram` и асинхронного HTTP-клиента (`aiohttp`).\n"
        "3. **Структурирование запросов** к API, включая системные промпты и параметры генерации.\n"
        "4. **Обработка ответов**: поддержка различных форматов, корректное отображение кода (Markdown/HTML), разбиение длинных сообщений.\n"
        "5. **Реализация логики выбора моделей**: тестирование доступности, приоритезация бесплатных/платных моделей.\n\n"
        "```python\n# Пример базового запроса к AI (упрощенно)\nimport aiohttp\n\nasync def get_ai_response(prompt, api_key):\n    url = \"https://openrouter.ai/api/v1/chat/completions\"\n    headers = {\n        \"Authorization\": f\"Bearer {api_key}\",\n        \"Content-Type\": \"application/json\"\n    }\n    data = {\n        \"model\": \"google/gemini-2.5-flash-lite\", # Пример модели\n        \"messages\": [{\"role\": \"user\", \"content\": prompt}],\n        \"max_tokens\": 500\n    }\n    async with aiohttp.ClientSession() as session:\n        async with session.post(url, headers=headers, json=data) as resp:\n            return await resp.json()\n```\n\n"
        "🚀 **Ключевым является надежный механизм переключения между моделями** в случае их недоступности или низкой производительности."
    ],
    "код": [
        # Этот блок будет заменен, если AI вернет специальный формат для файла
        "💻 **Пример кода для Telegram бота с AI-интеграцией**\n\n"
        "Ниже представлен упрощенный пример обработки пользовательского текста и отправки его AI-модели.\n\n"
        "```python\nimport asyncio\nimport aiohttp\nimport os\n\nTELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')\nOPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')\nOPENROUTER_URL = \"https://openrouter.ai/api/v1/chat/completions\"\n\nasync def fetch_ai_response(user_query):\n    if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:\n        return \"Ошибка конфигурации: API ключи не найдены.\"\n\n    headers = {\n        \"Authorization\": f\"Bearer {OPENROUTER_API_KEY}\",\n        \"Content-Type\": \"application/json\",\n        \"HTTP-Referer\": \"https://t.me/your_bot_user\", # Измените на ваш реферер\n        \"X-Title\": \"MyAiBot\"\n    }\n\n    messages = [\n        {\"role\": \"system\", \"content\": \"Ты полезный ассистент. Отвечай кратко.\"},\n        {\"role\": \"user\", \"content\": user_query}\n    ]\n\n    data = {\n        \"model\": \"google/gemini-2.5-flash-lite\", # Или другая доступная модель\n        \"messages\": messages,\n        \"max_tokens\": 500,\n        \"temperature\": 0.7\n    }\n\n    try:\n        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:\n            async with session.post(OPENROUTER_URL, headers=headers, json=data) as resp:\n                if resp.status == 200:\n                    result = await resp.json()\n                    return result['choices'][0]['message']['content'].strip()\n                else:\n                    return f\"Ошибка API: {resp.status} - {await resp.text()}\"\n    except Exception as e:\n        return f\"Ошибка запроса: {e}\"\n\n# Пример использования (вне aiogram цикла)\n# response = await fetch_ai_response(\"Как работает асинхронность в Python?\")\n# print(response)\n```\n\n"
        "💡 **Важно**: Для продакшена необходимо реализовать обработку ошибок, повторные попытки, выбор моделей и форматирование ответов."
    ],
    "общий": [
        "🧠 **Анализ научно-технического запроса**\n\n"
        "**Пример:** Физика, Работа и Энергия.\n\n"
        "Формула работы при подъеме тела против силы тяжести (приблизительное текстовое представление):\n"
        "$$ A = m \\cdot g \\cdot h $$ \n"
        "где:\n"
        "• '$A$' — работа (Джоули, Дж)\n"
        "• '$m$' — масса тела (килограммы, кг)\n"
        "• '$g$' — ускорение свободного падения (приблизительно 9.81 м/с^2)\n"
        "• '$h$' — высота подъема (метры, м)\n\n"
        "**Пример расчета:**\n"
        "Если нужно поднять груз массой 5 кг на высоту 10 метров:\n"
        "$$ A = 5 \\text{ кг} \\cdot 9.81 \\text{ м/с}^2 \\cdot 10 \\text{ м} \\approx 490.5 \\text{ Дж} $$ \n\n"
        "**Связанные концепции:**\n"
        "- **Потенциальная энергия:** $Ep = m \\cdot g \\cdot h$\n"
        "- **Кинетическая энергия:** $Ek = 1/2 m v^2$\n"
        "- **Закон сохранения энергии:** Полная механическая энергия замкнутой системы остается постоянной.\n\n"
        "Для более сложных формул, где Unicode или ASCII-представления могут быть неинформативны, может потребоваться использование изображений.\n\n"
        "**Электротехника:**\n"
        "- Закон Ома: $I = V / R$ (ток = напряжение / сопротивление).\n"
        "- Мощность: $P = V \\cdot I$ (мощность = напряжение * ток).\n"
    ]
}

def get_local_fallback_response(user_question: str) -> str:
    """Генерация локального ответа, если AI API недоступно."""
    question_lower = user_question.lower()
    
    # Простая эвристика для выбора наиболее релевантного локального ответа
    if any(word in question_lower for word in ['код', 'пример', 'программир', 'python', 'javascript', 'api', 'telegram', 'script', 'файл', 'создать', 'html', 'css', 'json', 'проект', 'папка', 'архив', 'каталог', 'несколько файлов']):
        topic = "код"
    elif any(word in question_lower for word in ['физик', 'формул', 'работа', 'гравитац', 'механик', 'энерги', 'ньютон', 'джоуль', 'электр', 'вольт', 'ампер', 'ом', 'батаре', 'напряжен', 'ток', 'сопротивлен', 'уравнен', ' интеграл', 'сумма']):
        topic = "общий"
    elif any(word in question_lower for word in ['технолог', 'ai', 'модель', 'сервис', 'сервер']):
        topic = "технология"
    else:
        topic = "общий" # По умолчанию

    responses = LOCAL_RESPONSES.get(topic, LOCAL_RESPONSES["общий"])
    return random.choice(responses)

# ==================== ОБРАБОТЧИКИ ТЕЛЕГРАМ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработка команды /start."""
    welcome_text = (
        "👋 Привет! Я Иван Иваныч — ваш умный AI-ассистент.\n\n"
        "🚀 **Мои возможности:**\n"
        "• **Гибкая архитектура:** Автоматический выбор оптимальной AI-модели.\n"
        "• **Стабильная работа:** Увеличенные таймауты и система повторных попыток.\n"
        "• **Продвинутая обработка кода:** Корректная подсветка синтаксиса в Telegram.\n"
        "• **Генерация файлов:** Могу создавать и отправлять HTML-файлы с кодом и подсветкой.\n"
        "• **Отправка ZIP-архивов:** Для проектов из нескольких файлов.\n"
        "• **Научные темы:** Ответы с использованием Unicode/ASCII для формул (в текстовом формате).\n"
        f"• **Платные модели:** {'ВКЛЮЧЕНЫ ✅' if USE_PAID_MODELS else 'отключены'}. Попробуйте задать сложный вопрос!\n\n"
        "⚙️ **Текущая конфигурация:**\n"
        f"• Основные бесплатные: {len(MODELS_CONFIG['primary_free_models'])}\n"
        f"• Вторичные бесплатные: {len(MODELS_CONFIG['secondary_free_models'])}\n"
        f"• Платные: {len(MODELS_CONFIG['paid_models']) if USE_PAID_MODELS else 'отключены'}\n\n"
        "⏱️ **Таймауты:**\n"
        f"• Быстрые: {MODEL_TIMEOUTS['fast']}с, Средние: {MODEL_TIMEOUTS['medium']}с\n"
        f"• Медленные: {MODEL_TIMEOUTS['slow']}с, Платные: {MODEL_TIMEOUTS['paid']}с\n\n"
        "⚡ **Пример кода:**\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "📊 Проверьте доступность AI-моделей: `/status`\n"
        "❓ Просто задайте вопрос с вопросительным знаком '?' в конце."
        "\n💡 Чтобы получить код как файл, запросите: 'Дай мне [язык] код для [задачи] как файл' или 'Создай HTML файл с [описание]'.\n"
        "💡 Для отправки проекта из нескольких файлов: 'Сделай мне проект [название] из [описание] и отправь как ZIP'.\n"
    )
    await send_message_safe(message.chat.id, welcome_text, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Обработка команды /status для проверки доступности AI-моделей."""
    status_text = "🔄 Проверяю доступность AI-моделей..."
    processing_msg = await send_message_safe(message.chat.id, status_text, message.message_id)
    
    if not processing_msg:
        logger.error("Не удалось отправить сообщение о начале проверки статуса.")
        return

    try:
        logger.info("🔍 Запуск проверки моделей для команды /status...")
        available_models_data = await get_available_models()
        
        status_report = "📊 **Сводный статус AI-моделей:**\n"
        
        all_available_models_flat = []
        for category, models in available_models_data.items():
            for model, speed in models:
                model_type = "Платная" if category == 'paid' else "Бесплатная"
                all_available_models_flat.append((model, speed, model_type))
        
        all_available_models_flat.sort(key=lambda x: x[1])

        for model, speed, model_type in all_available_models_flat:
            status_report += f"✅ `{model.split('/')[-1]}` ({model_type}, {speed:.1f}с)\n"
        
        tested_models_set = set([m[0] for m in all_available_models_flat])
        all_config_models = set(
            MODELS_CONFIG["primary_free_models"] +
            MODELS_CONFIG["secondary_free_models"] +
            (MODELS_CONFIG["paid_models"] if USE_PAID_MODELS else [])
        )
        for model in all_config_models:
            if model not in tested_models_set:
                status_report += f"❌ `{model.split('/')[-1]}` (недоступна)\n"

        status_report += "\n"
        status_report += f"⏱️ **Таймауты (сек):** Быстрые={MODEL_TIMEOUTS['fast']}, Средние={MODEL_TIMEOUTS['medium']}, Медленные={MODEL_TIMEOUTS['slow']}, Платные={MODEL_TIMEOUTS['paid']}\n"
        status_report += f"💰 **Платные модели:** {'ВКЛЮЧЕНЫ ✅' if USE_PAID_MODELS else 'отключены'}"
        
        await processing_msg.edit_text(status_report, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке статуса моделей: {e}", exc_info=True)
        error_text = f"❌ Произошла ошибка при проверке статуса: {str(e)[:150]}"
        await processing_msg.edit_text(error_text, parse_mode=None)

@dp.message(lambda msg: msg.text and (
    msg.text.strip().endswith('?') or 
    msg.text.strip().lower().startswith("код") or 
    msg.text.strip().lower().startswith("создай") or
    msg.text.strip().lower().startswith("сделай") or
    msg.text.strip().lower().startswith("дай мне")
))
async def handle_question(message: types.Message):
    """Обработка пользовательских вопросов. Может отправлять одиночные файлы, ZIP-архивы или обычный текст."""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = message.from_user.username or f"user_{message.from_user.id}"
    logger.info(f"🗣️ Вопрос от {username} (chat_id: {chat_id}): {user_question[:100]}...")
    
    processing_msg = None
    try:
        processing_text = "🤔 ИИ обрабатывает запрос..."
        processing_msg = await send_message_safe(chat_id, processing_text, message.message_id)
        
        if not processing_msg: # Если даже первое сообщение не удалось отправить
            logger.warning(f"Не удалось отправить сообщение о начале обработки запроса для {chat_id}")
            return
        
        start_time = time.time()
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        response, model_used, code_blocks_count = await get_ai_response(user_question)
        elapsed = time.time() - start_time
        
        if response:
            # --- ПРОВЕРКА НА СПЕЦИАЛЬНЫЙ ВЫВОД ПАКЕТА ФАЙЛОВ ОТ AI ---
            package_output_match = re.search(rf"{PACKAGE_OUTPUT_MARKER_START}(.*?){PACKAGE_OUTPUT_MARKER_END}", response, re.DOTALL)
            
            if package_output_match:
                # --- ОБРАБОТКА ПАКЕТА ФАЙЛОВ (ZIP) ---
                logger.info("✨ Обнаружен вывод пакета файлов.")
                await processing_msg.edit_text("📂 Собираю и архивирую файлы...", parse_mode=None)

                try:
                    package_content_str = package_output_match.group(1).strip()
                    package_data = json.loads(package_content_str)
                    
                    folder_name = package_data.get("folder_name", "project")
                    files = package_data.get("files", [])

                    if not files:
                        raise ValueError("В пакете файлов не найдено ни одного файла.")

                    with tempfile.TemporaryDirectory() as tmpdir:
                        folder_path = os.path.join(tmpdir, folder_name)
                        os.makedirs(folder_path, exist_ok=True)

                        for file_info in files:
                            filename = file_info.get("filename")
                            language = file_info.get("language", DEFAULT_CODE_LANGUAGE)
                            content = file_info.get("content", "")

                            if not filename:
                                logger.warning("Пропущен файл без имени в пакете.")
                                continue

                            output_html_filename, html_file_data = generate_html_file_with_code(language, filename, content)
                            
                            final_save_path = os.path.join(folder_path, output_html_filename)
                            os.makedirs(os.path.dirname(final_save_path), exist_ok=True)

                            with open(final_save_path, "wb") as f:
                                f.write(html_file_data.getvalue())
                            logger.info(f"Сохранен файл: {os.path.relpath(final_save_path, tmpdir)}")

                        zip_filename_base = folder_name
                        zip_filepath = os.path.join(tmpdir, f"{zip_filename_base}.zip")
                        
                        with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                            for root, dirs, files_in_dir in os.walk(folder_path):
                                for file_ in files_in_dir:
                                    file_path = os.path.join(root, file_)
                                    zipf.write(file_path, os.path.relpath(file_path, folder_path))
                        
                        logger.info(f"ZIP архив '{os.path.basename(zip_filepath)}' создан.")

                        await processing_msg.edit_text("⬆️ Отправляю архив с файлами...", parse_mode=None)
                        
                        caption_text = response.replace(package_output_match.group(0), "").strip()
                        if not caption_text: caption_text = "Ваш архив с файлами готов!"
                        
                        await bot.send_document(
                            chat_id=chat_id,
                            document=types.FSInputFile(zip_filepath),
                            caption=f"Архив с вашими файлами: `{os.path.basename(zip_filepath)}`\n{caption_text}",
                            reply_to_message_id=message.message_id
                        )
                        logger.info(f"ZIP архив '{os.path.basename(zip_filepath)}' отправлен.")
                        
                except json.JSONDecodeError:
                    logger.error("Ошибка декодирования JSON из вывода пакета файлов.")
                    await processing_msg.edit_text("❌ Ошибка: Не удалось разобрать данные для пакета файлов.", parse_mode=None)
                except Exception as e:
                    logger.error(f"❌ Ошибка при обработке пакета файлов: {e}", exc_info=True)
                    await processing_msg.edit_text(f"❌ Произошла ошибка при создании архива: {str(e)[:150]}", parse_mode=None)
            
            else:
                # --- Если это не пакет, проверяем на одиночный файл ---
                file_output_match = re.search(rf"{FILE_OUTPUT_MARKER_START}(.*?){FILE_OUTPUT_MARKER_END}", response, re.DOTALL)
                
                if file_output_match:
                    # --- ОБРАБОТКА ОДИНОЧНОГО ФАЙЛА ---
                    logger.info("✨ Обнаружен вывод одиночного файла.")
                    await processing_msg.edit_text("⬆️ Отправляю файл...", parse_mode=None)

                    file_output_content = file_output_match.group(1).strip()
                    language = DEFAULT_CODE_LANGUAGE
                    filename = DEFAULT_CODE_FILENAME
                    code_content_lines = []
                    
                    parsing_header = True
                    for line in file_output_content.split('\n'):
                        stripped_line = line.strip()
                        if stripped_line.lower().startswith("language:"):
                            language = stripped_line.split(":", 1)[1].strip()
                        elif stripped_line.lower().startswith("filename:"):
                            filename = stripped_line.split(":", 1)[1].strip()
                        elif stripped_line == "":
                            parsing_header = False
                        elif not parsing_header:
                            code_content_lines.append(line)
                    
                    code_content = "\n".join(code_content_lines).strip()

                    output_html_filename, file_data = generate_html_file_with_code(language, filename, code_content)
                    
                    caption_text = response.replace(file_output_match.group(0), "").strip()
                    if not caption_text: caption_text = "Ваш файл с подсвеченным кодом готов!"

                    await bot.send_document(
                        chat_id=chat_id,
                        document=types.BufferedInputFile(file_data.getvalue(), filename=output_html_filename),
                        caption=f"Ваш файл '{output_html_filename}' готов:\n{caption_text}",
                        reply_to_message_id=message.message_id
                    )
                    logger.info(f"Файл '{output_html_filename}' отправлен.")
                
                else:
                    # --- ОБЫЧНАЯ ОТПРАВКА ОТВЕТА (НЕ ФАЙЛ) ---
                    await processing_msg.edit_text("✅ Ответ готов! Отправляю...", parse_mode=None)
                    await send_long_message(
                        chat_id,
                        f"🤖 **Ответ ИИ:**\n\n{response}",
                        message.message_id
                    )
            
            # --- Обновление статус сообщения (общий для всех успешных ответов) ---
            model_name_display = model_used.split('/')[-1] if model_used != "local_fallback" else "Локальная база знаний"
            
            final_status_text = (
                f"✅ Ответ получен!\n"
                f"⏱️ Время генерации: {elapsed:.1f} с\n"
                f"📊 Длина ответа: {len(response)} символов"
            )
            
            if code_blocks_count > 0:
                final_status_text += f"\n💻 Код: {code_blocks_count} блок(ов) обнаружено"
            
            model_type_str = ""
            if model_used != "local_fallback":
                if model_used in MODELS_CONFIG["paid_models"]:
                    model_type_str = " (💰 Платная)"
                elif model_used in MODELS_CONFIG["primary_free_models"] or model_used in MODELS_CONFIG["secondary_free_models"]:
                    model_type_str = " (🆓 Бесплатная)"
                else: # Если модель не найдена в конфигах, но не локальная
                     model_type_str = " (❔ Неизвестный тип)"
            
            final_status_text += f"\n🤖 Используемая модель: `{model_name_display}{model_type_str}`"
            
            await processing_msg.edit_text(final_status_text, parse_mode=None)
            logger.info(f"✅ Успешно обработан вопрос от {username} (chat_id: {chat_id}). Время: {elapsed:.1f}с, модель: {model_name_display}{model_type_str}")
        
        else: # Если ответ был получен от локального fallback
            await processing_msg.edit_text("⚠️ Использую локальный ответ...", parse_mode=None)
            
            await send_long_message(
                chat_id, 
                f"💡 **Предложение из базы знаний:**\n\n{response}", 
                message.message_id
            )
            
            completion_text = f"✅ Локальный ответ готов за {elapsed:.1f} с"
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Локальный fallback успешно обработан для {username} (chat_id: {chat_id}). Время: {elapsed:.1f}с")
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при обработке запроса от {username} (chat_id: {chat_id}): {e}", exc_info=True)
        try:
            # Пытаемся отправить сообщение об ошибке, если возможно
            if not processing_msg: # Если даже первое сообщение не удалось отправить
                await bot.send_message(chat_id=chat_id, text="❌ Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.", reply_to_message_id=message.message_id)
            else:
                await processing_msg.edit_text("❌ Произошла непредвиденная ошибка. Пожалуйста, попробуйте позже.", parse_mode=None)
        except Exception as e2:
            logger.error(f"❌ Не удалось отправить сообщение об ошибке: {e2}", exc_info=True)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска бота."""
    logger.info("=" * 60)
    logger.info("🚀 Бот IvanIvanych запускается...")
    logger.info("🔄 ОПТИМИЗИРОВАННАЯ ВЕРСИЯ с поддержкой ZIP-архивов кода.")
    logger.info(f"💰 Платные модели: {'ВКЛЮЧЕНЫ ✅' if USE_PAID_MODELS else 'отключены'}")
    
    logger.info("--- Конфигурация моделей ---")
    logger.info("  Основные бесплатные:")
    for model in MODELS_CONFIG["primary_free_models"]:
        logger.info(f"    • {model.split('/')[-1]}")
    
    logger.info("  Вторичные бесплатные:")
    for model in MODELS_CONFIG["secondary_free_models"]:
        logger.info(f"    • {model.split('/')[-1]}")
    
    if USE_PAID_MODELS:
        logger.info("  Платные:")
        for model in MODELS_CONFIG["paid_models"]:
            logger.info(f"    • {model.split('/')[-1]}")
    
    logger.info("--- Таймауты (сек) ---")
    logger.info(f"  Быстрые: {MODEL_TIMEOUTS['fast']}, Средние: {MODEL_TIMEOUTS['medium']}, Медленные: {MODEL_TIMEOUTS['slow']}, Платные: {MODEL_TIMEOUTS['paid']}")
    logger.info("=" * 60)
    
    try:
        # Очищаем необработанные обновления перед стартом
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Предыдущие обновления Telegram очищены.")
        
        # Запускаем polling для получения обновлений
        await dp.start_polling(bot, skip_updates=True)
        
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем (KeyboardInterrupt).")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка при запуске бота: {e}", exc_info=True)
    finally:
        # Закрываем сессию бота при завершении работы
        try:
            # Проверяем, существует ли сессия перед закрытием
            if bot and bot.session:
                await bot.session.close()
                logger.info("🔌 Сессия бота закрыта.")
        except Exception as e:
            logger.error(f"Ошибка при закрытии сессии бота: {e}", exc_info=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Завершение работы программы.")
    except Exception as e:
        logger.error(f"💥 Фатальная ошибка при запуске asyncio: {e}", exc_info=True)
```