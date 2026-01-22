import asyncio
import logging
import os
import aiohttp
import re
import time
import unicodedata
from typing import Optional, List, Tuple
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
OPENROUTER_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1") + "/chat/completions"

# Основная модель (Llama)
OPENROUTER_MODEL_MAIN = "meta-llama/llama-3.3-70b-instruct:free"

# Модель DeepSeek для анализа ответов
OPENROUTER_MODEL_DEEPSEEK = "deepseek/deepseek-r1-0528:free"

# Настройки генерации
GENERATION_CONFIG_MAIN = {
    "temperature": 0.85,
    "max_tokens": 1200,
    "top_p": 0.92,
    "frequency_penalty": 0.15,
    "presence_penalty": 0.08,
}

GENERATION_CONFIG_DEEPSEEK = {
    "temperature": 0.75,
    "max_tokens": 1600,
    "top_p": 0.88,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== УТИЛИТЫ ЭКРАНИРОВАНИЯ И ОЧИСТКИ ====================
def fix_unbalanced_backticks(text: str) -> str:
    """
    Исправляет нечётное количество обратных кавычек в тексте.
    Это КРИТИЧЕСКАЯ функция для работы с кодом от DeepSeek.
    """
    if not text:
        return text
    
    # Считаем кавычки
    backtick_count = text.count('`')
    
    if backtick_count % 2 == 0:
        return text  # Всё в порядке
    
    logger.warning(f"⚠️ Найдено нечётное количество кавычек: {backtick_count}. Исправляем...")
    
    # Ищем все позиции кавычек
    positions = [m.start() for m in re.finditer('`', text)]
    
    if not positions:
        return text
    
    # Определяем, нужно добавить или удалить кавычку
    if backtick_count % 2 == 1:
        # Нечётное - нужно добавить одну кавычку
        # Добавляем в конец, если это безопасно
        last_backtick_pos = positions[-1]
        last_50_chars = text[last_backtick_pos:min(last_backtick_pos + 50, len(text))]
        
        # Проверяем, не является ли последняя кавычка частью блока кода
        if '```' in last_50_chars:
            # Это блок кода - добавляем закрывающие ```
            text += '```'
            logger.info("✅ Добавлены закрывающие ``` для блока кода")
        else:
            # Одиночная кавычка
            text += '`'
            logger.info("✅ Добавлена закрывающая `")
    
    # Проверяем блоки кода
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)(?:\n```|$)'
    matches = list(re.finditer(code_block_pattern, text))
    
    for match in matches:
        if not match.group(0).endswith('```'):
            # Незакрытый блок кода
            logger.warning(f"⚠️ Найден незакрытый блок кода, добавляем ```")
            start_pos = match.start()
            # Находим позицию для добавления ```
            end_pos = text.rfind('\n', start_pos)
            if end_pos == -1:
                end_pos = len(text)
            text = text[:end_pos] + '\n```' + text[end_pos:]
    
    return text

def clean_text_final(text: str) -> str:
    """
    Финальная очистка текста с исправлением кавычек.
    """
    if not text:
        return ""
    
    # Сначала исправляем кавычки
    text = fix_unbalanced_backticks(text)
    
    # Удаляем опасные управляющие символы (но сохраняем \n, \t, \r, `)
    cleaned = []
    for char in text:
        cat = unicodedata.category(char)
        if cat[0] == 'C':  # Управляющие символы
            if char in ['\n', '\t', '\r', '`']:
                cleaned.append(char)
            else:
                # Удаляем опасные управляющие символы
                pass
        else:
            cleaned.append(char)
    
    text = ''.join(cleaned)
    
    # Удаляем конкретные опасные символы
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

def escape_markdown_v2_with_code(text: str) -> str:
    """
    Экранирование MarkdownV2 с правильной обработкой кода.
    """
    # Очищаем и исправляем кавычки
    text = clean_text_final(text)
    
    # ШАГ 1: Защищаем блоки кода
    code_blocks = []
    
    # Блоки кода с ```
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    
    def replace_code_block(match):
        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    text = re.sub(code_block_pattern, replace_code_block, text)
    
    # ШАГ 2: Защищаем inline код
    inline_blocks = []
    inline_pattern = r'`([^`\n]+)`'
    
    def replace_inline_code(match):
        placeholder = f"__INLINE_CODE_{len(inline_blocks)}__"
        inline_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    text = re.sub(inline_pattern, replace_inline_code, text)
    
    # ШАГ 3: Экранируем оставшийся текст
    # Экранируем обратные слеши
    text = text.replace('\\', '\\\\')
    
    # Экранируем остальные спецсимволы MarkdownV2
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    # ШАГ 4: Восстанавливаем inline код
    for placeholder, inline_code in inline_blocks:
        text = text.replace(placeholder, inline_code)
    
    # ШАГ 5: Восстанавливаем блоки кода
    for placeholder, code_block in code_blocks:
        text = text.replace(placeholder, code_block)
    
    return text

def convert_to_html(text: str) -> str:
    """
    Конвертирует Markdown в HTML для fallback.
    """
    # Блоки кода с ```
    text = re.sub(r'```(?:(\w+)\n)?([\s\S]*?)\n```', 
                 lambda m: f'<pre><code class="language-{m.group(1) or ""}">{m.group(2)}</code></pre>', 
                 text)
    
    # Inline код с `
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    
    # Жирный текст
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    
    # Курсив
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    
    # Подчеркивание
    text = re.sub(r'__([^_]+)__', r'<u>\1</u>', text)
    
    # Заголовки
    text = re.sub(r'^### (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h1>\1</h1>', text, flags=re.MULTILINE)
    
    # Списки
    lines = text.split('\n')
    in_list = False
    result_lines = []
    
    for line in lines:
        if line.strip().startswith('- '):
            if not in_list:
                result_lines.append('<ul>')
                in_list = True
            result_lines.append(f'<li>{line[2:].strip()}</li>')
        elif line.strip().startswith('* '):
            if not in_list:
                result_lines.append('<ul>')
                in_list = True
            result_lines.append(f'<li>{line[2:].strip()}</li>')
        else:
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            result_lines.append(line)
    
    if in_list:
        result_lines.append('</ul>')
    
    return '\n'.join(result_lines)

async def send_message_with_code(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """
    Умная отправка сообщений с кодом.
    Пытается использовать MarkdownV2, потом HTML, потом plain text.
    """
    # Очищаем текст
    cleaned_text = clean_text_final(text)
    
    # Проверяем кавычки
    backtick_count = cleaned_text.count('`')
    logger.info(f"📤 Отправка сообщения, кавычек: {backtick_count}")
    
    # ПРОБУЕМ MARKDOWNV2
    try:
        escaped_text = escape_markdown_v2_with_code(cleaned_text)
        
        # Проверяем, что блоки кода закрыты
        code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', escaped_text)
        if code_blocks:
            logger.info(f"📤 Найдено {len(code_blocks)} блок(ов) кода для MarkdownV2")
        
        kwargs = {
            "chat_id": chat_id,
            "text": escaped_text,
            "parse_mode": "MarkdownV2"
        }
        
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Отправлено с MarkdownV2, длина: {len(escaped_text)} символов")
        return result
        
    except Exception as e:
        error_msg = str(e)
        logger.warning(f"⚠️ MarkdownV2 не сработал: {error_msg}")
        
        # ПРОБУЕМ HTML
        try:
            html_text = convert_to_html(cleaned_text)
            
            kwargs = {
                "chat_id": chat_id,
                "text": html_text,
                "parse_mode": "HTML"
            }
            
            if reply_to_message_id:
                kwargs["reply_to_message_id"] = reply_to_message_id
            
            result = await bot.send_message(**kwargs)
            logger.info(f"✅ Отправлено с HTML, длина: {len(html_text)} символов")
            return result
            
        except Exception as html_e:
            logger.warning(f"⚠️ HTML не сработал: {html_e}")
    
    # ПРОБУЕМ PLAIN TEXT
    try:
        # Упрощаем текст для plain text
        plain_text = cleaned_text
        # Убираем лишние обратные кавычки если они вызывают проблемы
        plain_text = re.sub(r'```(?:[\w]*)\n', '[КОД]\n', plain_text)
        plain_text = plain_text.replace('```', '[/КОД]\n')
        plain_text = plain_text.replace('`', "'")  # Заменяем одиночные кавычки
        
        kwargs = {
            "chat_id": chat_id,
            "text": plain_text,
            "parse_mode": None
        }
        
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Отправлено без форматирования, длина: {len(plain_text)} символов")
        return result
        
    except Exception as e:
        logger.error(f"❌ Не удалось отправить сообщение вообще: {e}")
        return None

async def send_long_message_final(chat_id: int, text: str, reply_to_message_id: int = None):
    """
    Финальная версия отправки длинных сообщений.
    """
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    # Проверяем блоки кода
    code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text)
    logger.info(f"📤 Блоков кода найдено: {len(code_blocks)}")
    
    # Разбиваем на части если нужно (сохраняя блоки кода)
    if original_length <= 4000:
        parts = [text]
    else:
        # Простое разбиение, стараясь не разрывать блоки кода
        parts = []
        current_part = ""
        lines = text.split('\n')
        
        for line in lines:
            if len(current_part) + len(line) + 1 <= 4000:
                current_part += line + "\n"
            else:
                if current_part:
                    parts.append(current_part.strip())
                current_part = line + "\n"
        
        if current_part:
            parts.append(current_part.strip())
    
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        logger.info(f"📤 Отправка части {i+1}/{len(parts)}, длина: {len(part)} символов")
        
        message = await send_message_with_code(
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

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в футуристике и технологиях будущего. "
        "Отвечай ясно, по делу, с технической точностью. "
        "Используй Markdown для форматирования: **жирный** для ключевых терминов. "
        "Если даёшь пример кода, используй ТРОЙНЫЕ обратные кавычки:"
        "```python\nprint('Пример')\n```"
        "ВАЖНО: Всегда проверяй, что количество обратных кавычек ` в твоём ответе ЧЁТНОЕ. "
        "Если нечётное - добавь недостающую кавычку в конец. "
        "Длина ответа не должна превышать 1000 символов."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — технический аналитик. Ответь на вопрос пользователя самостоятельно, "
        "предоставив глубокий анализ, конкретные детали и практические шаги. "
        "ИСПОЛЬЗУЙ Markdown для форматирования: **жирный** для заголовков. "
        "Если даёшь пример кода, ОБЯЗАТЕЛЬНО используй тройные обратные кавычки:"
        "```python\nкод\n```"
        "КРИТИЧЕСКИ ВАЖНО: Убедись, что количество символов ` в твоём ответе ЧЁТНОЕ. "
        "Сосчитай кавычки перед отправкой ответа. Если нечётное - добавь ` в конец. "
        "Пример: 'Вот код: ```python\nprint(1)\n```' - здесь 6 кавычек (чётно). "
        "Длина ответа не должна превышать 1000 символов. "
        "Будь максимально конкретным и техничным."
    )
}

# ==================== ФУНКЦИИ ДЛЯ OPENROUTER ====================
async def ask_openrouter_final(user_question: str, model: str, system_prompt: dict, config: dict) -> Optional[str]:
    """Финальная версия запроса с исправлением кавычек"""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    data = {
        "model": model,
        "messages": [
            system_prompt,
            {"role": "user", "content": user_question}
        ],
        **config
    }
    
    model_name = model.split('/')[-1] if '/' in model else model
    logger.info(f"🚀 Запрос к {model_name}...")
    
    timeout_seconds = 150 if "deepseek" in model.lower() else 100
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    
    try:
        start_time = time.time()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                elapsed = time.time() - start_time
                
                if response.status == 200:
                    result = await response.json()
                    if 'choices' in result and len(result['choices']) > 0:
                        response_text = result['choices'][0]['message'].get('content', '').strip()
                        
                        # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: фиксируем кавычки сразу
                        original_backticks = response_text.count('`')
                        response_text = fix_unbalanced_backticks(response_text)
                        fixed_backticks = response_text.count('`')
                        
                        if original_backticks != fixed_backticks:
                            logger.info(f"✅ {model_name}: исправлено кавычек {original_backticks} → {fixed_backticks}")
                        
                        logger.info(f"✅ {model_name} ответил за {elapsed:.1f}с, {len(response_text)} символов, кавычек: {fixed_backticks}")
                        
                        return response_text
                    else:
                        logger.error(f"❌ Неверный формат ответа от {model_name}")
                        return None
                else:
                    error_text = await response.text()
                    logger.error(f"❌ Ошибка {model_name} [{response.status}]: {error_text[:200]}")
                    return None
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Таймаут {model_name} (> {timeout_seconds}с)")
        return None
    except Exception as e:
        logger.error(f"⚠️ Ошибка {model_name}: {e}")
        return None

async def get_responses_parallel_final(user_question: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Финальная версия параллельных запросов.
    """
    llama_task = asyncio.create_task(
        ask_openrouter_final(
            user_question=user_question,
            model=OPENROUTER_MODEL_MAIN,
            system_prompt=SYSTEM_PROMPT_MAIN,
            config=GENERATION_CONFIG_MAIN
        )
    )
    
    deepseek_task = asyncio.create_task(
        ask_openrouter_final(
            user_question=user_question,
            model=OPENROUTER_MODEL_DEEPSEEK,
            system_prompt=SYSTEM_PROMPT_DEEPSEEK,
            config=GENERATION_CONFIG_DEEPSEEK
        )
    )
    
    llama_response, deepseek_response = await asyncio.gather(
        llama_task, 
        deepseek_task,
        return_exceptions=True
    )
    
    if isinstance(llama_response, Exception):
        logger.error(f"❌ Исключение в Llama: {llama_response}")
        llama_response = None
    if isinstance(deepseek_response, Exception):
        logger.error(f"❌ Исключение в DeepSeek: {deepseek_response}")
        deepseek_response = None
    
    return llama_response, deepseek_response

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 Привет! Я Иван Иваныч\n\n"
        "🤖 Две модели ИИ работают параллельно:\n"
        "• Llama 3.3 — быстрый основной ответ\n"
        "• DeepSeek R1 — глубокий технический анализ\n\n"
        "⚡ Оба ответа генерируются одновременно!\n\n"
        "💻 *Работающая подсветка кода!*\n"
        "Пример кода:\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_message_with_code(message.chat.id, welcome_text, message.message_id)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question_final(message: types.Message):
    """Финальный обработчик вопросов"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = f"@{message.from_user.username}" if message.from_user.username else f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    processing_msg = None
    try:
        # ШАГ 1: Уведомление
        processing_text = "🤔 Две модели ИИ анализируют вопрос параллельно..."
        processing_msg = await send_message_with_code(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            logger.error("❌ Не удалось отправить уведомление")
            return
        
        start_total_time = time.time()
        
        # ШАГ 2: ПАРАЛЛЕЛЬНЫЕ ЗАПРОСЫ
        logger.info("⚡ Параллельные запросы запущены...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        llama_response, deepseek_response = await get_responses_parallel_final(user_question)
        
        # ШАГ 3: LLAMA
        if llama_response:
            llama_time = time.time() - start_total_time
            logger.info(f"📤 Отправка ответа Llama (за {llama_time:.1f}с)...")
            
            await processing_msg.edit_text("✅ Llama ответил! Готовим анализ DeepSeek...", parse_mode=None)
            
            await send_long_message_final(
                chat_id=chat_id,
                text=f"🤖 Ответ Llama 3.3:\n\n{llama_response}",
                reply_to_message_id=message.message_id
            )
        else:
            logger.error("❌ Llama не ответил")
            await processing_msg.edit_text("❌ Основная модель не ответила. Попробуйте позже.", parse_mode=None)
            return
        
        # ШАГ 4: DEEPSEEK
        if deepseek_response and len(deepseek_response) > 50:
            logger.info("📤 Отправка ответа DeepSeek...")
            
            await send_long_message_final(
                chat_id=chat_id,
                text=f"🔍 Глубокий анализ DeepSeek R1:\n\n{deepseek_response}",
                reply_to_message_id=message.message_id
            )
            
            total_time = time.time() - start_total_time
            completion_text = (
                f"✅ Анализ завершён!\n"
                f"⏱️ Общее время: {total_time:.1f} секунд\n"
                f"📊 Llama: {len(llama_response)} символов\n"
                f"🔍 DeepSeek: {len(deepseek_response)} символов"
            )
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Успешно! Время: {total_time:.1f}с")
            
        else:
            logger.warning("⚠️ DeepSeek не вернул ответ")
            total_time = time.time() - start_total_time
            fallback_text = (
                f"✅ Основной ответ готов!\n"
                f"⏱️ Время: {total_time:.1f} секунд\n"
                f"ℹ️ DeepSeek временно недоступен"
            )
            
            await processing_msg.edit_text(fallback_text, parse_mode=None)
        
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки")
        if processing_msg:
            await processing_msg.edit_text("⏱️ Время обработки истекло. Попробуйте позже.", parse_mode=None)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        error_text = f"⚠️ Ошибка обработки: {str(e)[:200]}"
        if processing_msg:
            await send_message_with_code(chat_id, error_text)

# ==================== ЗАПУСК БОТА ====================
async def main_final():
    """Финальная версия запуска"""
    logger.info("=" * 60)
    logger.info(f"🚀 Бот IvanIvanych запускается...")
    logger.info(f"🤖 Модели: {OPENROUTER_MODEL_MAIN} + {OPENROUTER_MODEL_DEEPSEEK}")
    logger.info(f"⚡ Архитектура: Параллельная генерация")
    logger.info(f"💻 Функция: Автоисправление кавычек DeepSeek")
    logger.info("=" * 60)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Очищены предыдущие обновления")
        
        await dp.start_polling(bot, skip_updates=True)
        
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
        raise
    finally:
        try:
            await bot.session.close()
            logger.info("🔌 Сессия бота закрыта")
        except:
            pass

if __name__ == "__main__":
    asyncio.run(main_final())