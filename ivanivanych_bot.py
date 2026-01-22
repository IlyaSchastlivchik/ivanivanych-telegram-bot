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
def clean_text_safe(text: str) -> str:
    """
    Безопасная очистка текста - только удаляем опасные символы.
    НЕ трогаем обратные кавычки и символы внутри блоков кода.
    """
    if not text:
        return ""
    
    # Находим и защищаем блоки кода с ```
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    
    def protect_code_block(match):
        code_content = match.group(0)  # Весь блок кода
        # Очищаем только опасные символы внутри содержимого кода
        inner_content = match.group(1)
        # Заменяем только нулевые символы и другие опасные управляющие символы
        cleaned_inner = ''.join(char for char in inner_content 
                               if unicodedata.category(char)[0] != 'C' 
                               or char == '\n' or char == '\t' or char == '\r')
        cleaned_inner = cleaned_inner.replace('\u0000', '').replace('\u0001', '').replace('\u0002', '')
        cleaned_inner = cleaned_inner.replace('\u0003', '').replace('\u0004', '').replace('\u0005', '')
        # Восстанавливаем блок кода
        language = match.group(0)[3:].split('\n')[0].strip()
        if language and language != '```':
            return f"```{language}\n{cleaned_inner}\n```"
        else:
            return f"```\n{cleaned_inner}\n```"
    
    # Обрабатываем блоки кода
    text = re.sub(code_block_pattern, protect_code_block, text)
    
    # Теперь обрабатываем оставшийся текст (вне блоков кода)
    # Удаляем опасные управляющие символы
    text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C' 
                  or char == '\n' or char == '\t' or char == '\r' or char == '`')
    
    # Удаляем конкретные опасные символы
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
    """
    ФИНАЛЬНАЯ версия экранирования MarkdownV2.
    Правильно обрабатывает блоки кода.
    """
    # Очищаем опасные символы
    text = clean_text_safe(text)
    
    # ШАГ 1: Находим и защищаем блоки кода
    code_blocks = []
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    
    def replace_code_block(match):
        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    # Заменяем блоки кода на плейсхолдеры
    text = re.sub(code_block_pattern, replace_code_block, text)
    
    # ШАГ 2: Находим и защищаем inline код
    inline_code_blocks = []
    inline_pattern = r'`([^`\n]+)`'
    
    def replace_inline_code(match):
        placeholder = f"__INLINE_CODE_{len(inline_code_blocks)}__"
        inline_code_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    text = re.sub(inline_pattern, replace_inline_code, text)
    
    # ШАГ 3: Экранируем оставшийся текст (НЕ блоки кода)
    # Экранируем обратные слеши
    text = text.replace('\\', '\\\\')
    
    # Экранируем остальные спецсимволы MarkdownV2
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    # Важно: НЕ экранируем обратную кавычку `
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    # ШАГ 4: Восстанавливаем inline код
    for placeholder, inline_code in inline_code_blocks:
        text = text.replace(placeholder, inline_code)
    
    # ШАГ 5: Восстанавливаем блоки кода
    for placeholder, code_block in code_blocks:
        text = text.replace(placeholder, code_block)
    
    return text

def split_message_smart_final(text: str, max_length: int = 3500) -> List[str]:
    """
    ФИНАЛЬНАЯ версия разбиения сообщений.
    Не разбивает блоки кода.
    """
    if len(text) <= max_length:
        return [text]
    
    # Находим блоки кода
    code_block_pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    code_matches = list(re.finditer(code_block_pattern, text))
    
    if not code_matches:
        # Нет блоков кода - простое разбиение
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
    
    # Есть блоки кода - разбиваем аккуратно
    parts = []
    current_pos = 0
    
    for match in code_matches:
        code_start = match.start()
        code_end = match.end()
        code_block = match.group(0)
        
        # Текст до блока кода
        text_before = text[current_pos:code_start]
        if text_before:
            # Разбиваем текст до блока кода
            text_parts = split_message_smart_final(text_before, max_length)
            if text_parts:
                if parts:
                    parts[-1] += text_parts[0]
                    parts.extend(text_parts[1:])
                else:
                    parts.extend(text_parts)
        
        # Добавляем блок кода
        if parts and len(parts[-1]) + len(code_block) <= max_length:
            parts[-1] += code_block
        else:
            parts.append(code_block)
        
        current_pos = code_end
    
    # Текст после последнего блока кода
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
    """
    ФИНАЛЬНАЯ версия отправки сообщений.
    """
    # Попытка 1: С MarkdownV2 и правильным экранированием
    try:
        escaped_text = escape_markdown_v2_final(text)
        
        # Проверяем наличие незакрытых блоков кода
        backtick_count = escaped_text.count('`')
        if backtick_count % 2 != 0:
            logger.warning(f"⚠️ Нечётное количество обратных кавычек: {backtick_count}")
            # Добавляем недостающую кавычку в конец
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
        
        # Анализируем ошибку
        if "PreCode" in error_msg or "can't parse" in error_msg:
            logger.warning("⚠️ Проблема с блоками кода, пробуем альтернативный метод...")
            # Попытка 1.5: Отправляем с HTML разметкой для кода
            try:
                # Преобразуем блоки кода в HTML
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
    
    # Попытка 2: Без форматирования
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
    """
    ФИНАЛЬНАЯ версия отправки длинных сообщений.
    """
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    # Проверяем наличие блоков кода
    code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', text)
    inline_codes = re.findall(r'`[^`\n]+`', text)
    logger.info(f"📤 Блоков кода найдено: {len(code_blocks)}, inline кода: {len(inline_codes)}")
    
    if code_blocks:
        for i, block in enumerate(code_blocks[:2]):
            logger.info(f"📤 Блок кода {i+1}: {block[:50]}...")
    
    # Разбиваем сообщение
    parts = split_message_smart_final(text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        part_length = len(part)
        logger.info(f"📤 Часть {i+1}/{len(parts)}: {part_length} символов")
        
        # Проверяем блоки кода в этой части
        part_code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', part)
        if part_code_blocks:
            logger.info(f"📤 Часть {i+1} содержит {len(part_code_blocks)} блок(ов) кода")
        
        # Отправляем с правильным методом
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

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в футуристике и технологиях будущего. "
        "Отвечай ясно, по делу, с технической точностью. "
        "Используй Markdown для форматирования: **жирный** для ключевых терминов. "
        "Если даёшь пример кода, используй ТРОЙНЫЕ обратные кавычки с указанием языка:"
        "```python\nprint('Пример кода')\n```"
        "Важно: всегда закрывай блок кода тремя обратными кавычками ```"
        "НЕ используй LaTeX (\\( \\)) или специальные символы в ответах. "
        "Длина ответа не должна превышать 1200 символов."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — технический аналитик. Ответь на вопрос пользователя самостоятельно, "
        "предоставив глубокий анализ, конкретные детали и практические шаги. "
        "ИСПОЛЬЗУЙ Markdown для форматирования: **жирный** для заголовков. "
        "Если даёшь пример кода, ОБЯЗАТЕЛЬНО используй тройные обратные кавычки:"
        "```python\nimport requests\n```"
        "ВСЕГДА закрывай блок кода тремя обратными кавычками ```"
        "Проверяй, что количество обратных кавычек ` в твоём ответе чётное. "
        "НЕ используй LaTeX (\\( \\)) или специальные символы в ответах. "
        "Длина ответа не должна превышать 1200 символов. "
        "Будь максимально конкретным и техничным."
    )
}

# ==================== ФУНКЦИИ ДЛЯ OPENROUTER ====================
async def ask_openrouter(user_question: str, model: str, system_prompt: dict, config: dict) -> Optional[str]:
    """Улучшенная функция запроса с таймаутами"""
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
                        # Проверяем блоки кода
                        backtick_count = response_text.count('`')
                        if backtick_count % 2 != 0:
                            logger.warning(f"⚠️ {model_name} вернул нечётное количество кавычек: {backtick_count}")
                            # Добавляем недостающую кавычку
                            response_text += '`'
                        
                        logger.info(f"✅ {model_name} ответил за {elapsed:.1f}с, {len(response_text)} символов, кавычек: {backtick_count}")
                        
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

async def get_responses_parallel(user_question: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Получает ответы от обеих моделей ПАРАЛЛЕЛЬНО.
    Возвращает: (ответ_llama, ответ_deepseek)
    """
    llama_task = asyncio.create_task(
        ask_openrouter(
            user_question=user_question,
            model=OPENROUTER_MODEL_MAIN,
            system_prompt=SYSTEM_PROMPT_MAIN,
            config=GENERATION_CONFIG_MAIN
        )
    )
    
    deepseek_task = asyncio.create_task(
        ask_openrouter(
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
        "💻 *Теперь с работающей подсветкой кода!*\n"
        "Пример кода будет отображаться с форматированием:\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_safe_message_final(message.chat.id, welcome_text, message.message_id)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Обработчик вопросов с параллельной генерацией"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = f"@{message.from_user.username}" if message.from_user.username else f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    processing_msg = None
    try:
        # ШАГ 1: Уведомление о начале обработки
        processing_text = "🤔 Две модели ИИ анализируют вопрос параллельно..."
        processing_msg = await send_safe_message_final(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            logger.error("❌ Не удалось отправить уведомление о начале обработки")
            return
        
        start_total_time = time.time()
        
        # ШАГ 2: ПАРАЛЛЕЛЬНЫЙ ЗАПРОС К ОБЕИМ МОДЕЛЯМ
        logger.info("⚡ Параллельные запросы запущены...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        llama_response, deepseek_response = await get_responses_parallel(user_question)
        
        # ШАГ 3: СНАЧАЛА ОТПРАВЛЯЕМ ОТВЕТ LLAMA
        if llama_response:
            llama_time = time.time() - start_total_time
            logger.info(f"📤 Отправка ответа Llama (за {llama_time:.1f}с)...")
            
            status_text = "✅ Llama ответил! Готовим анализ DeepSeek..."
            await processing_msg.edit_text(status_text, parse_mode=None)
            
            await send_long_message_final(
                chat_id=chat_id,
                text=f"🤖 Ответ Llama 3.3:\n\n{llama_response}",
                reply_to_message_id=message.message_id
            )
        else:
            logger.error("❌ Llama не ответил")
            await processing_msg.edit_text("❌ Основная модель не ответила. Попробуйте позже.", parse_mode=None)
            return
        
        # ШАГ 4: ПОТОМ ОТПРАВЛЯЕМ ОТВЕТ DEEPSEEK (ЕСЛИ ЕСТЬ)
        if deepseek_response and len(deepseek_response) > 50:
            logger.info("📤 Отправка ответа DeepSeek...")
            
            # Проверяем блоки кода
            code_blocks = re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', deepseek_response)
            if code_blocks:
                logger.info(f"📝 Ответ DeepSeek содержит {len(code_blocks)} блок(ов) кода")
            
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
            
            if code_blocks:
                completion_text += f"\n💻 Код: {len(code_blocks)} блок(ов)"
            
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
            await send_safe_message_final(chat_id, error_text)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info(f"🚀 Бот IvanIvanych запускается...")
    logger.info(f"🤖 Модели: {OPENROUTER_MODEL_MAIN} + {OPENROUTER_MODEL_DEEPSEEK}")
    logger.info(f"⚡ Архитектура: Параллельная генерация")
    logger.info(f"💻 Функция: Работающая подсветка кода")
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
    asyncio.run(main())