
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
# Пытаемся загрузить .env явно из стандартного пути для Render Secret Files
ENV_FILE_PATH = '/etc/secrets/.env'

try:
    if os.path.exists(ENV_FILE_PATH):
        load_dotenv(dotenv_path=ENV_FILE_PATH)
        logger.info(f"✅ Успешно загружен .env файл из {ENV_FILE_PATH}")
    else:
        # Если файл не найден по явному пути, пробуем стандартный поиск load_dotenv()
        logger.warning(f"⚠️ Файл .env не найден по пути {ENV_FILE_PATH}. Попытка загрузить стандартным путем.")
        load_dotenv()
        logger.info("✅ Использован стандартный поиск .env файла.")
except Exception as e:
    logger.error(f"❌ Ошибка при загрузке .env файла: {e}")
    # Продолжаем работу, полагаясь на переменные, установленные напрямую в Render

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

# --- ДОПОЛНИТЕЛЬНЫЙ ДЕБАГГИНГ ---
# Выводим значение переменной USE_PAID_MODELS, чтобы точно понять, что считывается
raw_use_paid_models_value = os.getenv("USE_PAID_MODELS", "false") # Используем "false" как дефолт, если переменная не найдена
logger.info(f"🌟 DEBUG: Сырое значение USE_PAID_MODELS из os.getenv: '{raw_use_paid_models_value}'")
# --- КОНЕЦ ДОПОЛНИТЕЛЬНОГО ДЕБАГГИНГА ---

# Убедитесь, что ваша логика определения USE_PAID_MODELS в коде корректна
# current_use_paid_models_flag = raw_use_paid_models_value.lower() == "true"
# logger.info(f"🌟 DEBUG: Флаг USE_PAID_MODELS установлен в: {current_use_paid_models_flag}")
# USE_PAID_MODELS = current_use_paid_models_flag

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
# Переработанная структура для четкого разделения на бесплатные (включая fallback) и платные модели.
MODELS_CONFIG = {
    # Основные бесплатные модели (пробуются первыми)
    "primary_free_models": [
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen-2.5-vl-7b-instruct:free",
        "microsoft/phi-3.5-mini-instruct:free", # Добавлена для расширения пула быстрых бесплатных моделей
    ],
    
    # Вторичные бесплатные модели (fallback для бесплатных: используются, если основные недоступны)
    "secondary_free_models": [
        "qwen/qwen2.5-32b-instruct:free",
        "mistralai/mistral-7b-instruct:free",
    ],
    
    # Платные модели (пробуются, если USE_PAID_MODELS=true и ВСЕ бесплатные модели не работают)
    "paid_models": [
        "google/gemini-2.5-flash-lite",      # Заменена Llama на Gemini Flash Lite в платном сценарии
        "deepseek/deepseek-v3",
        # Здесь можно добавить другие платные модели, если требуется
        # "openai/gpt-4-turbo", 
        # "anthropic/claude-3-opus",
    ]
}

# Флаг для разрешения использования платных моделей, берется из .env
USE_PAID_MODELS = os.getenv("USE_PAID_MODELS", "false").lower() == "true"

# УВЕЛИЧЕННЫЕ таймауты (в секундах) для различных категорий моделей
MODEL_TIMEOUTS = {
    "fast": 45,      # Быстрые модели (phi, qwen-7b, gemini-flash)
    "medium": 60,    # Средние модели
    "slow": 90,      # Медленные модели (llama 70b)
    "paid": 120,     # Платные модели (больше времени для качественного ответа)
    "test": 30,      # Таймаут для тестовой проверки доступности модели
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

def prepare_html_message(text: str) -> str:
    """Подготовка текста для отправки в формате HTML, корректно обрабатывая блоки кода."""
    text = clean_text(text)
    
    # Создаем копию текста для экранирования, сохраняя блоки кода
    escaped_text = html.escape(text)
    
    # Восстанавливаем блоки кода, отменяя экранирование внутри них
    def restore_code_block(match):
        language = match.group(1) if match.group(1) else ''
        code_content = match.group(2)
        
        # Отменяем экранирование внутри кода
        code_content = code_content.replace('&lt;', '<').replace('&gt;', '>')
        code_content = code_content.replace('&amp;', '&').replace('&quot;', '"')
        code_content = code_content.replace('&#x27;', "'").replace('&#x2F;', '/')
        
        if language:
            return f'<pre><code class="language-{language}">{code_content}</code></pre>'
        else:
            return f'<pre><code>{code_content}</code></pre>'
    
    # Сначала экранируем весь текст, затем восстанавливаем блоки кода
    # Замена ```(...)``` на временный маркер, чтобы не затронуть внутренний HTML
    
    # Находим блоки кода, экранируем их контент, а затем оборачиваем в <pre><code>
    # Используем регулярное выражение, чтобы найти блоки кода с языком или без
    # Сохраняем оригинальные блоки кода, чтобы потом заменить экранированные версии
    code_blocks_map = {}
    def placeholder_code_block(match):
        key = f"__CODE_BLOCK_{len(code_blocks_map)}__"
        code_blocks_map[key] = match.group(0) # Сохраняем оригинальный блок
        return key
        
    # Сначала заменяем блоки кода на плейсхолдеры
    text_with_placeholders = re.sub(r'(```(\w*)\n)([\s\S]*?)(\n```)', placeholder_code_block, text)
    
    # Далее экранируем остальной текст
    escaped_text_with_placeholders = html.escape(text_with_placeholders)
    
    # Восстанавливаем блоки кода, применяя экранирование только к их содержимому, если оно было
    # Этот подход сложен, проще сначала экранировать всё, а потом восстановить код, отменяя экранирование внутри кода.

    # Повторный подход: экранируем весь текст, затем восстанавливаем код
    # Заменим блоки ```code``` на временные маркеры, затем экранируем, затем восстанавливаем код, отменяя экранирование внутри.
    
    # Работаем с экранированным текстом, но будем восстанавливать код.
    # Найдем блоки кода в *оригинальном* тексте, чтобы понять их структуру
    code_section_pattern = r'(```(\w*)\n)([\s\S]*?)(\n```)'
    
    processed_text = text
    
    # Храним пары: `(начало_блока_экранированного_текста, конец_блока_экранированного_текста, содержимое_кода)`
    code_segments = []
    for match in re.finditer(code_section_pattern, processed_text):
        lang_part = match.group(1) # ```lang\n
        code_content = match.group(3)
        end_part = match.group(4) # \n```
        
        # Экранируем содержимое кода, чтобы потом отменить экранирование
        escaped_code = html.escape(code_content)
        
        # Отменяем экранирование внутри кода
        restored_code = escaped_code.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        restored_code = restored_code.replace('&quot;', '"').replace('&#x27;', "'").replace('&#x2F;', '/')
        
        # Полностью экранируем весь текст, затем заменяем части кода
        
        # Удалим оригинальный блок кода, чтобы избежать двойного экранирования
        processed_text = processed_text.replace(match.group(0), '')

    # Экранируем оставшийся текст
    processed_text = html.escape(processed_text)
    
    # Теперь восстановим блоки кода
    for match in re.finditer(code_section_pattern, text): # Ищем в оригинальном тексте, чтобы получить контент
        lang_part = match.group(1)
        code_content = match.group(3)
        end_part = match.group(4)
        
        # Повторно отменяем экранирование для кода, уже будучи уверенными, что остальной текст экранирован
        restored_code = code_content.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        restored_code = restored_code.replace('&quot;', '"').replace('&#x27;', "'").replace('&#x2F;', '/')
        
        if match.group(2): # Если есть язык
             html_code_block = f'<pre><code class="language-{match.group(2)}">{restored_code}</code></pre>'
        else:
             html_code_block = f'<pre><code>{restored_code}</code></pre>'
        
        # Заменяем плейсхолдеры на реальные блоки кода
        processed_text = processed_text.replace(html.escape(match.group(0)), html_code_block)
    
    # Обработка inline кода
    def restore_inline_code(match):
        code_content = match.group(1)
        # Отменяем экранирование внутри кода
        code_content = code_content.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
        code_content = code_content.replace('&quot;', '"').replace('&#x27;', "'").replace('&#x2F;', '/')
        return f'<code>{code_content}</code>'
    
    # Используем временные маркеры для inline кода, чтобы не перезатирать внутри блоков <pre><code>
    inline_code_map = {}
    def placeholder_inline_code(match):
        key = f"__INLINE_CODE_{len(inline_code_map)}__"
        inline_code_map[key] = match.group(1) # Сохраняем контент
        return key

    # Экранируем текст, но оставляем inline код без экранирования для `html.escape`
    # Это может быть сложно. Проще сначала заменить все блоки `...` на маркеры,
    # затем экранировать остальное, затем восстановить.
    
    # Новый подход для inline кода:
    processed_text = re.sub(r'`(.*?)`', lambda m: f'`{html.escape(m.group(1))}`', processed_text)
    
    # Восстанавливаем inline код
    def restore_escaped_inline_code(match):
        escaped_content = match.group(1)
        if escaped_content:
            # Отменяем экранирование
            restored_content = escaped_content.replace('&lt;', '<').replace('&gt;', '>').replace('&amp;', '&')
            restored_content = restored_content.replace('&quot;', '"').replace('&#x27;', "'").replace('&#x2F;', '/')
            return f'<code>{restored_content}</code>'
        return '<code></code>' # Пустой inline код
        
    processed_text = re.sub(r'`(.*?)`', restore_escaped_inline_code, processed_text)
    
    return processed_text

def prepare_markdown_message(text: str) -> str:
    """Подготовка текста для отправки в формате MarkdownV2."""
    text = clean_text(text)
    
    # Защищаем блоки кода и inline код
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    # Сначала заменяем тройные блоки кода
    text_with_placeholders = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_codes)-1}__"
    
    # Затем заменяем одинарные блоки кода
    text_with_placeholders = re.sub(r'`[^`\n]+`', save_inline_code, text_with_placeholders)
    
    # Экранируем специальные символы MarkdownV2
    # Список символов, которые нужно экранировать в MarkdownV2
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    # Добавляем символы, которые могут быть частью URL или ссылок, но не должны быть интерпретированы
    # (например, если они находятся в тексте, который не является ссылкой)
    # В целом, лучше экранировать все, чтобы избежать неожиданной интерпретации.
    
    for char in chars_to_escape:
        text_with_placeholders = text_with_placeholders.replace(char, '\\' + char)
    
    # Восстанавливаем inline код (маркеры сохранены, оригинальный текст остается)
    for i, inline_code_segment in enumerate(inline_codes):
        # Заменяем маркер на оригинальный блок inline кода
        text_with_placeholders = text_with_placeholders.replace(f'__INLINE_CODE_{i}__', inline_code_segment)
    
    # Восстанавливаем блоки кода
    for i, code_block_segment in enumerate(code_blocks):
        # Заменяем маркер на оригинальный блок кода
        text_with_placeholders = text_with_placeholders.replace(f'__CODE_BLOCK_{i}__', code_block_segment)
    
    return text_with_placeholders

async def send_message_safe(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Умная отправка сообщений с автоматическим выбором формата (HTML, MarkdownV2, Plain)."""
    if not text:
        return None

    kwargs = {"chat_id": chat_id, "reply_to_message_id": reply_to_message_id}
    
    try:
        # Сначала пытаемся отправить в HTML
        html_text = prepare_html_message(text)
        # Проверка на слишком длинный HTML (Telegram имеет лимит ~4096 символов)
        if len(html_text) > 4000: 
            raise ValueError("HTML слишком длинный")
        kwargs["text"] = html_text
        kwargs["parse_mode"] = "HTML"
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Сообщение отправлено с HTML (chat_id: {chat_id}), длина: {len(text)} символов")
        return result
        
    except Exception as e:
        logger.warning(f"⚠️ HTML не сработал для chat_id {chat_id}: {e}, пробую MarkdownV2...")
        
        try:
            # Пробуем MarkdownV2
            markdown_text = prepare_markdown_message(text)
            # Проверка на слишком длинный Markdown
            if len(markdown_text) > 4000:
                raise ValueError("MarkdownV2 слишком длинный")
            kwargs["text"] = markdown_text
            kwargs["parse_mode"] = "MarkdownV2"
            result = await bot.send_message(**kwargs)
            logger.info(f"✅ Сообщение отправлено с MarkdownV2 (chat_id: {chat_id}), длина: {len(text)} символов")
            return result
            
        except Exception as e2:
            logger.warning(f"⚠️ MarkdownV2 не сработал для chat_id {chat_id}: {e2}, пробую без форматирования...")
            
            try:
                # Отправляем без форматирования, предварительно очистив
                cleaned_text = clean_text(text)
                # Проверка на слишком длинный текст без форматирования
                if len(cleaned_text) > 4096:
                    # Если даже простой текст слишком длинный, его нужно разбивать
                    # Это обработается в send_long_message, здесь мы можем только дать понять, что проблема
                     raise ValueError("Простой текст сообщения слишком длинный")
                
                kwargs["text"] = cleaned_text
                kwargs["parse_mode"] = None
                result = await bot.send_message(**kwargs)
                logger.info(f"✅ Сообщение отправлено без форматирования (chat_id: {chat_id}), длина: {len(text)} символов")
                return result
                
            except Exception as e3:
                logger.error(f"❌ Не удалось отправить сообщение для chat_id {chat_id}: {e3}")
                return None

def split_message_smart(text: str, max_length: int = 3500) -> List[str]:
    """Умное разбиение длинных сообщений с сохранением блоков кода."""
    if len(text) <= max_length:
        return [text] if text else [] # Возвращаем пустой список, если текст пуст
    
    # Сохраняем блоки кода, чтобы они не были потеряны при разбиении
    code_blocks = []
    code_pattern = r'```[\s\S]*?```'
    
    def replace_code_with_placeholder(match):
        code_blocks.append(match.group(0))
        return f'__CODE_BLOCK_{len(code_blocks)-1}__'
    
    text_with_placeholders = re.sub(code_pattern, replace_code_with_placeholder, text)
    
    parts = []
    current_part = ""
    
    # Пытаемся разбить по абзацам (двойные переносы строки)
    paragraphs = text_with_placeholders.split('\n\n')
    
    for para in paragraphs:
        # Если добавление параграфа с двумя переносами строки (para + '\n\n') не превысит лимит
        if len(current_part) + len(para) + 2 <= max_length:
            current_part += para + "\n\n"
        else:
            # Если параграф сам по себе слишком длинный, придётся разбить его по строкам
            if len(para) > max_length:
                # Если текущая часть уже содержит что-то, сохраняем ее
                if current_part:
                    parts.append(current_part.strip())
                    current_part = ""
                
                # Разбиваем сам параграф по строкам
                lines = para.split('\n')
                temp_para_part = ""
                for line in lines:
                    if len(temp_para_part) + len(line) + 1 <= max_length:
                        temp_para_part += line + "\n"
                    else:
                        if temp_para_part:
                            parts.append(temp_para_part.strip())
                        temp_para_part = line + "\n"
                if temp_para_part:
                    parts.append(temp_para_part.strip())
            else:
                # Если параграф не слишком длинный, но его добавление превысит лимит
                if current_part: # Сохраняем предыдущую часть, если она есть
                    parts.append(current_part.strip())
                current_part = para + "\n\n" # Начинаем новую часть с текущего параграфа

    # Добавляем последнюю часть, если она не пустая
    if current_part:
        parts.append(current_part.strip())
    
    # Заполняем финальный список, восстанавливая блоки кода
    final_parts = []
    for part in parts:
        restored_part = part
        for i, code_block in enumerate(code_blocks):
            placeholder = f'__CODE_BLOCK_{i}__'
            restored_part = restored_part.replace(placeholder, code_block)
        
        # Удаляем пустые строки в начале/конце каждой части
        final_parts.append(restored_part.strip())
    
    # Фильтруем пустые части, которые могли появиться после стриппинга
    return [p for p in final_parts if p]

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """Отправка длинных сообщений с использованием умного разбиения."""
    if not text:
        return
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения (chat_id: {chat_id}) длиной {original_length} символов...")
    
    parts = split_message_smart(text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        await send_message_safe(
            chat_id=chat_id,
            text=part,
            reply_to_message_id=reply_to_message_id if i == 0 else None # Отвечаем только на первое сообщение
        )
        
        if i < len(parts) - 1: # Небольшая задержка между частями, чтобы не перегружать API
            await asyncio.sleep(0.5)

# ==================== OPENROUTER ФУНКЦИИ ====================
async def test_model_speed(model: str) -> Tuple[bool, float]:
    """Тестирование скорости и доступности модели с увеличенными таймаутами."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2", # Поле для трекинга
        "X-Title": "IvanIvanych Bot", # Название вашего приложения
    }
    
    # Упрощенный тестовый промпт для быстрого ответа
    data = {
        "model": model,
        "messages": [{"role": "user", "content": "Привет"}], # Короткий запрос
        "max_tokens": 10,  # Минимальное количество токенов для получения ответа
        "stream": False   # Отключаем стриминг для простоты
    }
    
    try:
        start = time.time()
        # Устанавливаем таймаут для теста, отличный от финального таймаута модели
        # Для теста используем фиксированный, но достаточно большой таймаут
        timeout_seconds = MODEL_TIMEOUTS["test"] 
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                elapsed = time.time() - start
                
                if response.status == 200:
                    # Успешный ответ, модель доступна
                    logger.debug(f"  ✅ Тест модели {model.split('/')[-1]}: OK (за {elapsed:.2f}с)")
                    return True, elapsed
                else:
                    # Модель недоступна или вернула ошибку
                    error_text = await response.text()
                    logger.warning(f"  ⚠️ Тест модели {model.split('/')[-1]}: Статус {response.status}, Ошибка: {error_text[:100]}")
                    return False, float('inf') # Возвращаем бесконечную скорость при ошибке
    except asyncio.TimeoutError:
        logger.warning(f"  ⏱️ Тест модели {model.split('/')[-1]}: Превышен таймаут ({timeout_seconds}с)")
        return False, float('inf')
    except Exception as e:
        logger.warning(f"  ❌ Тест модели {model.split('/')[-1]}: Неизвестная ошибка ({str(e)[:100]})")
        return False, float('inf')

def get_model_timeout(model: str) -> int:
    """Определение финального таймаута для использования модели."""
    model_lower = model.lower()
    
    # Быстрые модели (обычно с меньшим количеством параметров или оптимизированные)
    if "phi-3.5" in model_lower or "qwen-2.5-7b" in model_lower or "gemini-2.5-flash-lite" in model_lower:
        return MODEL_TIMEOUTS["fast"]
    # Средние модели
    elif "qwen2.5-32b" in model_lower or "coder" in model_lower:
        return MODEL_TIMEOUTS["medium"]
    # Медленные модели (большие, ресурсоемкие)
    elif "llama" in model_lower or "70b" in model_lower:
        return MODEL_TIMEOUTS["slow"]
    # Платные/Премиум модели, которым дается больше времени
    elif any(paid_model in model_lower for paid_model in ["deepseek-v3", "gpt-4", "claude-3", "gpt-3.5-turbo"]):
        return MODEL_TIMEOUTS["paid"]
    
    # По умолчанию для всех остальных моделей - средний таймаут
    return MODEL_TIMEOUTS["medium"]

async def get_available_models() -> Dict[str, List[Tuple[str, float]]]:
    """
    Собирает и тестирует все доступные модели, категоризируя их по типу.
    Возвращает словарь: {'primary_free': [...], 'secondary_free': [...], 'paid': [...]}
    """
    logger.info("🔍 Проверяю доступность моделей...")
    
    models_to_check = {
        'primary_free': MODELS_CONFIG["primary_free_models"],
        'secondary_free': MODELS_CONFIG["secondary_free_models"],
        'paid': MODELS_CONFIG["paid_models"] if USE_PAID_MODELS else []
    }

    available_models_grouped = {
        'primary_free': [],
        'secondary_free': [],
        'paid': []
    }

    # Сначала тестируем все типы моделей, чтобы знать, какие доступны
    for category, model_list in models_to_check.items():
        for model in model_list:
            is_available, speed = await test_model_speed(model)
            if is_available:
                available_models_grouped[category].append((model, speed))

    # Сортируем каждую категорию по скорости (от самой быстрой к самой медленной)
    for category in available_models_grouped:
        available_models_grouped[category].sort(key=lambda x: x[1])

    # Логирование результатов проверки
    total_available = sum(len(v) for v in available_models_grouped.values())
    logger.info(f"✅ Найдено {total_available} доступных моделей:")
    for category, models in available_models_grouped.items():
        if models:
            model_names = [m[0].split('/')[-1] for m in models]
            logger.info(f"  - {category.replace('_', ' ').title()}: {', '.join(model_names)}")
        else:
            logger.info(f"  - {category.replace('_', ' ').title()}: Нет доступных")

    return available_models_grouped

async def get_ai_response(user_question: str) -> Tuple[Optional[str], Optional[str], int]:
    """
    Получает ответ от AI, выбирая лучшую модель по приоритету:
    1. Основные бесплатные
    2. Вторичные бесплатные (fallback для бесплатных)
    3. Платные (если USE_PAID_MODELS=true)
    4. Локальный fallback (если все AI модели недоступны)
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2", # Поле для трекинга
        "X-Title": "IvanIvanych Bot", # Название вашего приложения
    }
    
    # Системный промпт для ИИ
    system_prompt = {
        "role": "system",
        "content": (
            "Ты Иван Иваныч — эксперт в технологиях и футуристике. "
            "Отвечай ясно и по делу. Используй Markdown для форматирования. "
            "Для кода используй тройные кавычки с указанием языка (например, ```python). "
            "Всегда закрывай блок кода. "
            "Держи ответ в 800-1500 символов."
        )
    }
    
    # Получаем информацию о доступных моделях
    available_models_data = await get_available_models()

    selected_model_info = None # Будет содержать (имя_модели, тип_модели)

    # 1. Приоритет: Основные бесплатные модели
    if available_models_data.get('primary_free'):
        model_name, speed = available_models_data['primary_free'][0] # Берем самую быструю из доступных
        selected_model_info = (model_name, 'primary_free')
        logger.info(f"🎯 Выбрана основная бесплатная модель: {model_name.split('/')[-1]} (Скорость: {speed:.2f}с)")
    
    # 2. Приоритет: Вторичные бесплатные модели (fallback для бесплатных)
    elif available_models_data.get('secondary_free'):
        model_name, speed = available_models_data['secondary_free'][0]
        selected_model_info = (model_name, 'secondary_free')
        logger.info(f"🎯 Выбрана вторичная бесплатная модель: {model_name.split('/')[-1]} (Скорость: {speed:.2f}с)")
    
    # 3. Приоритет: Платные модели (если разрешено и бесплатные не сработали)
    elif USE_PAID_MODELS and available_models_data.get('paid'):
        model_name, speed = available_models_data['paid'][0]
        selected_model_info = (model_name, 'paid')
        logger.info(f"💰 Выбрана платная модель: {model_name.split('/')[-1]} (Скорость: {speed:.2f}с)")
    
    # 4. Если все AI модели недоступны или отключены, используем локальный fallback
    else:
        logger.warning("⚠️ ВСЕ AI модели недоступны или отключены, перехожу на локальный ответ.")
        response = get_local_fallback_response(user_question)
        return response, "local_fallback", 0 # Возвращаем локальный ответ без кода

    # --- Если модель AI была выбрана, приступаем к запросу ---
    model_to_use, model_type_tag = selected_model_info
    
    # Определяем конфигурацию и тип модели для логгирования
    current_config = GENERATION_CONFIG
    display_model_type = ""

    if model_type_tag == 'paid':
        current_config = PAID_MODEL_CONFIG
        display_model_type = "💰 Платная"
    elif model_type_tag == 'primary_free' or model_type_tag == 'secondary_free':
        display_model_type = "🆓 Бесплатная"
    
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
                                # --- Коррекция блоков кода ---
                                cleaned_text = text
                                # Проверяем на нечетное количество кавычек (частая проблема с Markdown)
                                backtick_count = cleaned_text.count('`')
                                if backtick_count % 2 != 0:
                                    logger.warning(f"⚠️ Нечётное количество кавычек ({backtick_count}) в ответе от {model_to_use.split('/')[-1]}. Попытка исправить.")
                                    # Попытка добавить закрывающие кавычки, если они явно отсутствуют
                                    if cleaned_text.count('```') % 2 != 0: # Если блок ``` не закрыт
                                        cleaned_text += '\n```'
                                    elif cleaned_text.endswith('`'): # Если последний символ - открывающая кавычка
                                        cleaned_text += '`' # Добавляем закрывающую
                                # --- Конец коррекции ---

                                # Считаем количество корректных блоков кода
                                code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', cleaned_text)
                                code_blocks_count = len(code_blocks)
                                
                                logger.info(f"✅ {display_model_type} {model_to_use.split('/')[-1]} ответил за {elapsed:.1f}с, {len(cleaned_text)} символов, блоков кода: {code_blocks_count}")
                                return cleaned_text, model_to_use, code_blocks_count
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
            logger.error(f"❌ Непредвиденная ошибка при работе с {model_to_use.split('/')[-1]}: {e}")
            if attempt < 1:
                await asyncio.sleep(2.0) # Ждем перед повтором

    # Если ни одна из попыток не увенчалась успехом для выбранной AI модели
    logger.warning(f"❌ Модель {model_to_use} не сработала после 2 попыток.")
    
    # Переходим на локальный fallback, если AI модель полностью отказала
    logger.warning("🔁 Перехожу на локальный fallback.")
    response = get_local_fallback_response(user_question)
    return response, "local_fallback", 0 # Возвращаем локальный ответ, в нем кода нет

# ==================== ЛОКАЛЬНЫЙ FALLBACK ====================
# Заранее подготовленные ответы для случаев, когда API недоступно или возвращает ошибки
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
        "💻 **Пример кода для Telegram бота с AI-интеграцией**\n\n"
        "Ниже представлен упрощенный пример обработки пользовательского текста и отправки его AI-модели.\n\n"
        "```python\nimport asyncio\nimport aiohttp\nimport os\n\nTELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')\nOPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')\nOPENROUTER_URL = \"https://openrouter.ai/api/v1/chat/completions\"\n\nasync def fetch_ai_response(user_query):\n    if not TELEGRAM_BOT_TOKEN or not OPENROUTER_API_KEY:\n        return \"Ошибка конфигурации: API ключи не найдены.\"\n\n    headers = {\n        \"Authorization\": f\"Bearer {OPENROUTER_API_KEY}\",\n        \"Content-Type\": \"application/json\",\n        \"HTTP-Referer\": \"https://t.me/your_bot_user\", # Измените на ваш реферер\n        \"X-Title\": \"MyAiBot\"\n    }\n\n    messages = [\n        {\"role\": \"system\", \"content\": \"Ты полезный ассистент. Отвечай кратко.\"},\n        {\"role\": \"user\", \"content\": user_query}\n    ]\n\n    data = {\n        \"model\": \"google/gemini-2.5-flash-lite\", # Или другая доступная модель\n        \"messages\": messages,\n        \"max_tokens\": 500,\n        \"temperature\": 0.7\n    }\n\n    try:\n        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:\n            async with session.post(OPENROUTER_URL, headers=headers, json=data) as resp:\n                if resp.status == 200:\n                    result = await resp.json()\n                    return result['choices'][0]['message']['content'].strip()\n                else:\n                    return f\"Ошибка API: {resp.status} - {await resp.text()}\"\n    except Exception as e:\n        return f\"Ошибка запроса: {e}\"\n\n# Пример использования (вне aiogram цикла)\n# response = await fetch_ai_response(\"Как работает асинхронность в Python?\")\n# print(response)\n```\n\n"
        "💡 **Важно**: Для продакшена необходимо реализовать обработку ошибок, повторные попытки, выбор моделей и форматирование ответов."
    ],
    "общий": [
        "🧠 **Общий анализ запроса**\n\n"
        "**Тема:** Физика, Работа и Энергия\n\n"
        "Формула работы при подъеме тела против силы тяжести:\n"
        "$$ A = m \\cdot g \\cdot h $$ \n"
        "где:\n"
        "• '$A$' — работа (Джоули, Дж)\n"
        "• '$m$' — масса тела (килограммы, кг)\n"
        "• '$g$' — ускорение свободного падения (приблизительно 9.81 м/с²)\n"
        "• '$h$' — высота подъема (метры, м)\n\n"
        "**Пример расчета:**\n"
        "Предположим, нужно поднять груз массой 5 кг на высоту 10 метров.\n"
        "$$ A = 5 \\text{ кг} \\cdot 9.81 \\text{ м/с}^2 \\cdot 10 \\text{ м} \\approx 490.5 \\text{ Дж} $$ \n\n"
        "**Другие важные концепции:**\n"
        "- **Потенциальная энергия:** $E_p = m \\cdot g \\cdot h$\n"
        "- **Кинетическая энергия:** $E_k = \\frac{1}{2} m v^2$\n"
        "- **Закон сохранения энергии:** Полная механическая энергия замкнутой системы остается постоянной.\n\n"
        "Эти концепции являются фундаментальными в классической механике."
    ]
}

def get_local_fallback_response(user_question: str) -> str:
    """Генерация локального ответа, если AI API недоступно."""
    question_lower = user_question.lower()
    
    # Простая эвристика для выбора наиболее релевантного локального ответа
    if any(word in question_lower for word in ['код', 'пример', 'программир', 'python', 'javascript', 'api', 'telegram', 'script']):
        topic = "код"
    elif any(word in question_lower for word in ['физик', 'формул', 'работа', 'гравитац', 'механик', 'энерги', 'ньютон', 'джоуль']):
        topic = "общий"
    elif any(word in question_lower for word in ['технолог', 'ai', 'модель', 'сервис', 'сервер']):
        topic = "технология"
    else:
        topic = "общий" # По умолчанию

    # Выбираем случайный ответ из выбранной категории
    responses = LOCAL_RESPONSES.get(topic, LOCAL_RESPONSES["общий"]) # Если категория не найдена, берем общий
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
    )
    await send_message_safe(message.chat.id, welcome_text, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Обработка команды /status для проверки доступности AI-моделей."""
    status_text = "🔄 Проверяю доступность AI-моделей..."
    
    # Сначала отправляем сообщение о начале проверки
    processing_msg = await send_message_safe(message.chat.id, status_text, message.message_id)
    
    if not processing_msg:
        logger.error("Не удалось отправить сообщение о начале проверки статуса.")
        return

    try:
        logger.info("🔍 Запуск проверки моделей для команды /status...")
        
        # Структурируем отчет по категориям моделей
        status_report = "📊 **Статус AI-моделей:**\n\n"
        
        # Извлекаем категории и модели из конфига
        model_categories = [
            ("Основные бесплатные", MODELS_CONFIG["primary_free_models"]),
            ("Вторичные бесплатные", MODELS_CONFIG["secondary_free_models"]),
        ]
        
        if USE_PAID_MODELS:
            model_categories.append(("Платные", MODELS_CONFIG["paid_models"]))
        
        # Тестируем каждую модель и добавляем результат в отчет
        all_tested_models = []
        for category_name, models in model_categories:
            status_report += f"**{category_name}:**\n"
            if not models:
                status_report += "  - Нет моделей для проверки.\n\n"
                continue
            
            # Тестируем модели в данной категории
            for model in models:
                is_available, speed = await test_model_speed(model)
                emoji = "✅" if is_available else "❌"
                name_short = model.split('/')[-1] # Сокращенное имя модели
                
                all_tested_models.append((model, is_available, speed)) # Собираем для финального списка
                
                status_report += f"{emoji} `{name_short}` ({speed:.1f}с)" if is_available else f"{emoji} `{name_short}` (недоступна)"
                status_report += "\n"
            status_report += "\n" # Пустая строка между категориями

        # Тестируем все модели для отображения общего списка и сортировки
        # Получаем все доступные модели и их скорость
        available_models_data = await get_available_models() # Это уже делает тестирование
        
        # Формируем сводный отчет
        status_report_summary = "📊 **Сводный статус AI-моделей:**\n\n"
        
        try:
            # Сначала основные бесплатные
            for model, speed in available_models_data.get('primary_free', []):
                status_report_summary += f"✅ `{model.split('/')[-1]}` (Бесплатная, {speed:.1f}с)\n"
            # Затем вторичные бесплатные
            for model, speed in available_models_data.get('secondary_free', []):
                status_report_summary += f"✅ `{model.split('/')[-1]}` (Бесплатная, {speed:.1f}с)\n"
            # Затем платные
            if USE_PAID_MODELS:
                for model, speed in available_models_data.get('paid', []):
                    status_report_summary += f"✅ `{model.split('/')[-1]}` (Платная, {speed:.1f}с)\n"
            
            # Отмечаем те, что не удалось протестировать (не попали в доступные)
            tested_models_set = set([m[0] for cat_models in available_models_data.values() for m in cat_models])
            all_config_models = set(
                MODELS_CONFIG["primary_free_models"] +
                MODELS_CONFIG["secondary_free_models"] +
                (MODELS_CONFIG["paid_models"] if USE_PAID_MODELS else [])
            )
            for model in all_config_models:
                if model not in tested_models_set:
                    status_report_summary += f"❌ `{model.split('/')[-1]}` (недоступна)\n"

        except Exception as error_in_report:
            logger.error(f"Ошибка при формировании сводного отчета: {error_in_report}")
            status_report_summary += "❌ Ошибка при формировании детального статуса.\n"
        
        status_report_summary += "\n"
        
        # Добавляем информацию о таймаутах
        status_report_summary += f"⏱️ **Таймауты (сек):** Быстрые={MODEL_TIMEOUTS['fast']}, Средние={MODEL_TIMEOUTS['medium']}, Медленные={MODEL_TIMEOUTS['slow']}, Платные={MODEL_TIMEOUTS['paid']}\n"
        status_report_summary += f"💰 **Платные модели:** {'ВКЛЮЧЕНЫ ✅' if USE_PAID_MODELS else 'отключены'}"
        
        # Редактируем изначальное сообщение для отображения полного отчета
        await processing_msg.edit_text(status_report_summary, parse_mode="HTML")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке статуса моделей: {e}")
        error_text = f"❌ Произошла ошибка при проверке статуса: {str(e)[:150]}"
        await processing_msg.edit_text(error_text, parse_mode=None)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Обработка пользовательских вопросов с использованием AI-моделей."""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = message.from_user.username or f"user_{message.from_user.id}"
    logger.info(f"🗣️ Вопрос от {username} (chat_id: {chat_id}): {user_question[:100]}...")
    
    processing_msg = None
    try:
        # Сообщение о начале обработки
        processing_text = "🤔 ИИ анализирует запрос..."
        processing_msg = await send_message_safe(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            logger.warning(f"Не удалось отправить сообщение о начале обработки запроса для {chat_id}")
            return # Если даже это не удалось, выходим
        
        start_time = time.time()
        
        # Отправляем действие "печатает" для лучшего UX
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        # Получаем ответ от AI
        response, model_used, code_blocks_count = await get_ai_response(user_question)
        
        elapsed = time.time() - start_time
        
        if response and model_used != "local_fallback":
            # Если получен ответ от AI модели
            logger.info(f"📤 Отправка ответа ({len(response)} символов) от {model_used.split('/')[-1]}...")
            
            # Обновляем сообщение "Идет обработка" на "Ответ готов"
            await processing_msg.edit_text("✅ Ответ готов! Отправляю...", parse_mode=None)
            
            # Отправляем ответ пользователя, используя умное разбиение для длинных сообщений
            await send_long_message(
                chat_id,
                f"🤖 **Ответ ИИ:**\n\n{response}",
                message.message_id
            )
            
            # Формируем финальное сообщение о завершении
            model_name_display = model_used.split('/')[-1] if model_used != "local_fallback" else "Локальная база знаний"
            
            final_status_text = (
                f"✅ Ответ получен!\n"
                f"⏱️ Время генерации: {elapsed:.1f} с\n"
                f"📊 Длина ответа: {len(response)} символов"
            )
            
            if code_blocks_count > 0:
                final_status_text += f"\n💻 Код: {code_blocks_count} блок(ов) обнаружено"
            
            # Определяем тип модели для отображения
            model_type_str = ""
            if model_used != "local_fallback":
                model_lower = model_used.lower()
                if any(paid_model in model_lower for paid_model in ["deepseek-v3", "gpt-4", "claude-3", "gpt-3.5-turbo"]):
                    model_type_str = " (💰 Платная)"
                elif any(free_model in model_used for free_model in MODELS_CONFIG["primary_free_models"] + MODELS_CONFIG["secondary_free_models"]):
                     model_type_str = " (🆓 Бесплатная)"
                else: # Если модель не найдена в конфигах, но не локальная
                     model_type_str = " (❔ Неизвестный тип)"
            
            final_status_text += f"\n🤖 Используемая модель: `{model_name_display}{model_type_str}`"
            
            await processing_msg.edit_text(final_status_text, parse_mode=None)
            logger.info(f"✅ Успешно обработан вопрос от {username} (chat_id: {chat_id}). Время: {elapsed:.1f}с, модель: {model_name_display}{model_type_str}")
        
        else:
            # Если ответ был получен от локального fallback
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
            logger.error(f"❌ Не удалось отправить сообщение об ошибке: {e2}")

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска бота."""
    logger.info("=" * 60)
    logger.info("🚀 Бот IvanIvanych запускается...")
    logger.info("🔄 ОПТИМИЗИРОВАННАЯ ВЕРСИЯ с улучшенной логикой выбора моделей.")
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
            logger.error(f"Ошибка при закрытии сессии бота: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Завершение работы программы.")
    except Exception as e:
        logger.error(f"💥 Фатальная ошибка при запуске asyncio: {e}", exc_info=True)
