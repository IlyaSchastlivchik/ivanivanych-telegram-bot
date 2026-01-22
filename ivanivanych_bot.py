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
def clean_text_preserve_code(text: str) -> str:
    """
    Очищает текст от битых символов, но СОХРАНЯЕТ форматирование кода.
    Не удаляет обратные кавычки ``` и символы внутри блоков кода.
    """
    if not text:
        return ""
    
    # Сначала находим и защищаем блоки кода
    code_blocks = []
    
    # Находим все блоки кода с ```
    pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    matches = list(re.finditer(pattern, text))
    
    # Заменяем блоки кода на плейсхолдеры
    for i, match in enumerate(matches):
        code_block = match.group(0)
        placeholder = f"__CODE_BLOCK_{i}__"
        code_blocks.append((placeholder, code_block))
        text = text.replace(code_block, placeholder, 1)
    
    # Теперь находим inline код с `
    inline_pattern = r'`([^`\n]+)`'
    inline_matches = list(re.finditer(inline_pattern, text))
    
    for i, match in enumerate(inline_matches):
        inline_code = match.group(0)
        placeholder = f"__INLINE_CODE_{i}__"
        code_blocks.append((placeholder, inline_code))
        text = text.replace(inline_code, placeholder, 1)
    
    # Теперь очищаем основной текст (без блоков кода)
    # Удаляем нулевые символы и другие опасные управляющие символы
    text = ''.join(char for char in text if unicodedata.category(char)[0] != 'C' or char == '\n' or char == '\t')
    
    # Заменяем проблемные символы (но не удаляем обратные кавычки!)
    replacements = {
        '\u200b': '',  # Zero-width space
        '\u200c': '',  # Zero-width non-joiner
        '\u200d': '',  # Zero-width joiner
        '\ufeff': '',  # Zero-width no-break space (BOM)
        '\u2028': '\n',  # Line separator
        '\u2029': '\n\n',  # Paragraph separator
        '\u0000': '',  # Null character
    }
    
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # Нормализуем юникод
    text = unicodedata.normalize('NFKC', text)
    
    # Восстанавливаем блоки кода
    for placeholder, code_block in code_blocks:
        text = text.replace(placeholder, code_block)
    
    return text

def escape_markdown_v2_preserve_code(text: str) -> str:
    """
    Экранирует текст для MarkdownV2, но НЕ экранирует внутри блоков кода.
    """
    # Очищаем текст, но сохраняем код
    text = clean_text_preserve_code(text)
    
    # Находим и защищаем блоки кода
    code_blocks = []
    
    # Блоки кода с ```
    pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    matches = list(re.finditer(pattern, text))
    
    for i, match in enumerate(matches):
        code_block = match.group(0)
        placeholder = f"__CODE_BLOCK_{i}__"
        code_blocks.append((placeholder, code_block))
        text = text.replace(code_block, placeholder, 1)
    
    # Inline код с `
    inline_pattern = r'`([^`\n]+)`'
    inline_matches = list(re.finditer(inline_pattern, text))
    
    for i, match in enumerate(inline_matches):
        inline_code = match.group(0)
        placeholder = f"__INLINE_CODE_{i}__"
        code_blocks.append((placeholder, inline_code))
        text = text.replace(inline_code, placeholder, 1)
    
    # Экранируем основной текст
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    # Экранируем обратные слеши (но не те, что в начале `)
    text = re.sub(r'(?<!`)``(?!`)|(?<!`)`(?!`)', lambda m: m.group(0), text)  # Защищаем `
    text = text.replace('\\', '\\\\')
    
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    # Восстанавливаем блоки кода (они уже не будут экранированы)
    for placeholder, code_block in code_blocks:
        text = text.replace(placeholder, code_block)
    
    return text

def split_message_smart(text: str, max_length: int = 3000) -> List[str]:
    """
    Умное разбиение сообщения на части с сохранением блоков кода.
    """
    if len(text) <= max_length:
        return [text]
    
    # Защищаем блоки кода перед разбиением
    code_blocks = []
    pattern = r'```(?:[\w]*)\n([\s\S]*?)\n```'
    
    def replace_code(match):
        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    text_with_placeholders = re.sub(pattern, replace_code, text)
    
    parts = []
    current_part = ""
    
    # Разбиваем по абзацам
    paragraphs = text_with_placeholders.split('\n\n')
    
    for para in paragraphs:
        # Восстанавливаем код в параграфе для проверки длины
        para_with_code = para
        for placeholder, code_block in code_blocks:
            if placeholder in para_with_code:
                # Заменяем плейсхолдер на код для точного подсчёта длины
                temp_para = para_with_code.replace(placeholder, code_block)
                if len(current_part) + len(temp_para) + 2 <= max_length:
                    para_with_code = temp_para
                else:
                    # Код не помещается, оставляем плейсхолдер
                    pass
        
        if len(para_with_code) > max_length:
            # Разбиваем длинный параграф
            lines = para_with_code.split('\n')
            for line in lines:
                if len(line) > max_length:
                    # Очень длинная строка - разбиваем по словам
                    words = line.split(' ')
                    for word in words:
                        if len(current_part) + len(word) + 1 <= max_length:
                            current_part += word + " "
                        else:
                            if current_part:
                                parts.append(current_part.strip())
                            current_part = word + " "
                elif len(current_part) + len(line) + 1 <= max_length:
                    current_part += line + "\n"
                else:
                    if current_part:
                        parts.append(current_part.strip())
                    current_part = line + "\n"
        elif len(current_part) + len(para_with_code) + 2 <= max_length:
            current_part += para_with_code + "\n\n"
        else:
            if current_part:
                parts.append(current_part.strip())
            current_part = para_with_code + "\n\n"
    
    if current_part:
        parts.append(current_part.strip())
    
    # Восстанавливаем блоки кода в каждой части
    final_parts = []
    for part in parts:
        restored_part = part
        for placeholder, code_block in code_blocks:
            if placeholder in restored_part:
                restored_part = restored_part.replace(placeholder, code_block)
        final_parts.append(restored_part)
    
    return final_parts

async def send_safe_message(chat_id: int, text: str, reply_to_message_id: int = None, 
                           parse_mode: str = "MarkdownV2") -> Optional[types.Message]:
    """
    БЕЗОПАСНАЯ отправка сообщений с автоматическим fallback.
    """
    # Попытка 1: С MarkdownV2 с сохранением кода
    try:
        escaped_text = escape_markdown_v2_preserve_code(text)
        kwargs = {
            "chat_id": chat_id,
            "text": escaped_text,
            "parse_mode": parse_mode
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        return await bot.send_message(**kwargs)
    except Exception as e:
        logger.warning(f"⚠️ MarkdownV2 не сработал, пробуем без форматирования: {e}")
    
    # Попытка 2: Без форматирования
    try:
        # Очищаем, но сохраняем читаемость кода
        cleaned_text = clean_text_preserve_code(text)
        kwargs = {
            "chat_id": chat_id,
            "text": cleaned_text,
            "parse_mode": None
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        return await bot.send_message(**kwargs)
    except Exception as e:
        logger.error(f"❌ Не удалось отправить сообщение вообще: {e}")
        return None

async def edit_safe_message(message: types.Message, text: str, parse_mode: str = "MarkdownV2") -> bool:
    """
    БЕЗОПАСНОЕ редактирование сообщений.
    """
    # Попытка 1: С MarkdownV2
    try:
        escaped_text = escape_markdown_v2_preserve_code(text)
        await message.edit_text(escaped_text, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.warning(f"⚠️ Не удалось отредактировать с MarkdownV2: {e}")
    
    # Попытка 2: Без форматирования
    try:
        cleaned_text = clean_text_preserve_code(text)
        await message.edit_text(cleaned_text, parse_mode=None)
        return True
    except Exception as e:
        logger.error(f"❌ Не удалось отредактировать сообщение вообще: {e}")
        return False

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """
    Отправка длинных сообщений с правильным разбиением на части и сохранением кода.
    """
    original_length = len(text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    # Экранируем с сохранением кода
    escaped_text = escape_markdown_v2_preserve_code(text)
    escaped_length = len(escaped_text)
    logger.info(f"📤 После экранирования: {escaped_length} символов (увеличилось на {escaped_length - original_length} символов)")
    
    # Проверяем наличие блоков кода
    code_block_count = len(re.findall(r'```[\w]*\n[\s\S]*?\n```', escaped_text))
    inline_code_count = len(re.findall(r'`[^`\n]+`', escaped_text))
    logger.info(f"📤 Найдено блоков кода: {code_block_count}, inline кода: {inline_code_count}")
    
    # Разбиваем на части с сохранением кода
    parts = split_message_smart(escaped_text, max_length=3500)
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        part_length = len(part)
        logger.info(f"📤 Часть {i+1}/{len(parts)}: {part_length} символов")
        
        # Проверяем наличие кода в этой части
        has_code_block = '```' in part
        if has_code_block:
            logger.info(f"📤 Часть {i+1} содержит блок кода")
        
        max_attempts = 2
        
        for attempt in range(max_attempts):
            try:
                kwargs = {
                    "chat_id": chat_id,
                    "text": part,
                    "parse_mode": "MarkdownV2"
                }
                
                if i == 0 and reply_to_message_id:
                    kwargs["reply_to_message_id"] = reply_to_message_id
                
                await bot.send_message(**kwargs)
                logger.info(f"✅ Часть {i+1}/{len(parts)} отправлена успешно")
                break  # Успешно отправили
                
            except Exception as e:
                error_msg = str(e)
                logger.error(f"❌ Попытка {attempt+1}/{max_attempts}: Ошибка при отправке части {i+1}: {error_msg}")
                
                # Если ошибка связана с парсингом Markdown, пробуем отправить без форматирования
                if "can't parse" in error_msg.lower() or "bad request" in error_msg.lower() or "400" in error_msg:
                    logger.warning(f"⚠️ Проблема с Markdown, отправляем часть {i+1} без форматирования")
                    try:
                        # Для plain text просто очищаем
                        plain_text = clean_text_preserve_code(part)
                        
                        plain_kwargs = {
                            "chat_id": chat_id,
                            "text": f"Часть {i+1}/{len(parts)}:\n\n{plain_text}",
                            "parse_mode": None
                        }
                        
                        if i == 0 and reply_to_message_id:
                            plain_kwargs["reply_to_message_id"] = reply_to_message_id
                        
                        await bot.send_message(**plain_kwargs)
                        logger.info(f"✅ Часть {i+1}/{len(parts)} отправлена без форматирования")
                        break
                    except Exception as e2:
                        logger.error(f"❌ Не удалось отправить даже без форматирования: {e2}")
                
                elif "message is too long" in error_msg.lower():
                    logger.warning(f"⚠️ Часть {i+1} слишком длинная, разбиваем ещё")
                    # Рекурсивно разбиваем эту часть
                    sub_parts = split_message_smart(part, max_length=3000)
                    for j, sub_part in enumerate(sub_parts):
                        try:
                            sub_kwargs = {
                                "chat_id": chat_id,
                                "text": sub_part,
                                "parse_mode": "MarkdownV2"
                            }
                            
                            if i == 0 and j == 0 and reply_to_message_id:
                                sub_kwargs["reply_to_message_id"] = reply_to_message_id
                            
                            await bot.send_message(**sub_kwargs)
                            logger.info(f"✅ Подчасть {j+1}/{len(sub_parts)} части {i+1} отправлена")
                        except Exception as sub_e:
                            logger.error(f"❌ Ошибка отправки подчасти: {sub_e}")
                    break
                
                if attempt == max_attempts - 1:  # Последняя попытка
                    logger.error(f"❌ Не удалось отправить часть {i+1} после всех попыток")
                
                await asyncio.sleep(0.5)
        
        if i < len(parts) - 1:
            await asyncio.sleep(0.3)

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в футуристике и технологиях будущего. "
        "Отвечай ясно, по делу, с технической точностью. "
        "Используй Markdown для форматирования: **жирный** для ключевых терминов, ```код``` для примеров кода. "
        "Пример кода должен быть в тройных обратных кавычках с указанием языка:"
        "```python\nкод на питоне\n```"
        "НЕ используй LaTeX (\\( \\)) или специальные символы в ответах. "
        "Избегай использования нестандартных юникод-символов. "
        "Длина ответа не должна превышать 1200 символов."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — технический аналитик. Ответь на вопрос пользователя самостоятельно, "
        "предоставив глубокий анализ, конкретные детали и практические шаги. "
        "ИСПОЛЬЗУЙ Markdown для форматирования: **жирный** для заголовков, ```код``` для примеров кода. "
        "Когда даёшь пример кода, всегда используй тройные обратные кавычки с указанием языка:"
        "```python\nimport requests\nresponse = requests.get(url)\n```"
        "НЕ используй LaTeX (\\( \\)) или специальные символы в ответах. "
        "Избегай использования нестандартных юникод-символов. "
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
                        # Очищаем, но сохраняем код
                        response_text = clean_text_preserve_code(response_text)
                        logger.info(f"✅ {model_name} ответил за {elapsed:.1f}с, {len(response_text)} символов")
                        
                        # Проверяем наличие кода в ответе
                        if '```' in response_text:
                            logger.info(f"📝 {model_name} вернул ответ с кодом")
                        
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
        "💻 *Теперь с подсветкой кода!*\n"
        "Модели могут возвращать код с форматированием:\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_safe_message(message.chat.id, welcome_text, message.message_id)

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
        processing_msg = await send_safe_message(chat_id, processing_text, message.message_id)
        
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
            logger.info(f"📊 Длина ответа Llama: {len(llama_response)} символов")
            
            # Проверяем наличие кода
            if '```' in llama_response:
                logger.info("📝 Ответ Llama содержит код")
            
            status_text = "✅ Llama ответил! Готовим анализ DeepSeek..."
            await edit_safe_message(processing_msg, status_text)
            
            await send_long_message(
                chat_id=chat_id,
                text=f"🤖 Ответ Llama 3.3:\n\n{llama_response}",
                reply_to_message_id=message.message_id
            )
        else:
            logger.error("❌ Llama не ответил")
            await edit_safe_message(processing_msg, "❌ Основная модель не ответила. Попробуйте позже.")
            return
        
        # ШАГ 4: ПОТОМ ОТПРАВЛЯЕМ ОТВЕТ DEEPSEEK (ЕСЛИ ЕСТЬ)
        if deepseek_response and len(deepseek_response) > 50:
            logger.info("📤 Отправка ответа DeepSeek...")
            logger.info(f"📊 Длина ответа DeepSeek: {len(deepseek_response)} символов")
            
            # Проверяем наличие кода
            code_blocks = re.findall(r'```[\w]*\n[\s\S]*?\n```', deepseek_response)
            if code_blocks:
                logger.info(f"📝 Ответ DeepSeek содержит {len(code_blocks)} блок(ов) кода")
                for i, block in enumerate(code_blocks[:2]):  # Показываем первые 2 блока
                    logger.info(f"📝 Блок кода {i+1}: {block[:100]}...")
            
            await send_long_message(
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
            
            await edit_safe_message(processing_msg, completion_text)
            logger.info(f"✅ Успешно! Время: {total_time:.1f}с")
            
        else:
            logger.warning("⚠️ DeepSeek не вернул ответ")
            total_time = time.time() - start_total_time
            fallback_text = (
                f"✅ Основной ответ готов!\n"
                f"⏱️ Время: {total_time:.1f} секунд\n"
                f"ℹ️ DeepSeek временно недоступен"
            )
            
            await edit_safe_message(processing_msg, fallback_text)
        
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки")
        if processing_msg:
            await edit_safe_message(processing_msg, "⏱️ Время обработки истекло. Попробуйте позже.")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        error_text = f"⚠️ Ошибка обработки: {str(e)[:200]}"
        if processing_msg:
            await send_safe_message(chat_id, error_text)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info(f"🚀 Бот IvanIvanych запускается...")
    logger.info(f"🤖 Модели: {OPENROUTER_MODEL_MAIN} + {OPENROUTER_MODEL_DEEPSEEK}")
    logger.info(f"⚡ Архитектура: Параллельная генерация")
    logger.info(f"💻 Функция: Подсветка кода сохранена")
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