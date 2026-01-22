import asyncio
import logging
import os
import aiohttp
import re
import time
import unicodedata
import json
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

# Основная модель (Llama)
OPENROUTER_MODEL_MAIN = "meta-llama/llama-3.3-70b-instruct:free"
# Модель DeepSeek для анализа ответов
OPENROUTER_MODEL_DEEPSEEK = "deepseek/deepseek-r1-0528:free"

# Настройки генерации
GENERATION_CONFIG_MAIN = {
    "temperature": 0.85,
    "max_tokens": 1000,
    "top_p": 0.92,
    "frequency_penalty": 0.15,
    "presence_penalty": 0.08,
}

GENERATION_CONFIG_DEEPSEEK = {
    "temperature": 0.75,
    "max_tokens": 1200,
    "top_p": 0.88,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== УТИЛИТЫ ОБРАБОТКИ ТЕКСТА ====================
def fix_unbalanced_backticks(text: str) -> str:
    """
    Исправляет нечётное количество обратных кавычек в тексте.
    Возвращает текст с чётным количеством кавычек.
    """
    if not text:
        return text
    
    # Считаем общее количество кавычек
    total_backticks = text.count('`')
    
    if total_backticks == 0:
        return text
    
    # Если количество чётное - возвращаем как есть
    if total_backticks % 2 == 0:
        return text
    
    logger.warning(f"⚠️ Найдено нечётное количество кавычек: {total_backticks}. Исправляем...")
    
    # Ищем незакрытые блоки кода
    code_block_pattern = r'```(?:[a-zA-Z0-9]*\n)?(.*?)(?:\n```|$)'
    matches = list(re.finditer(code_block_pattern, text, re.DOTALL))
    
    # Если есть незакрытые блоки кода
    if matches:
        for match in matches:
            if not text[match.end()-3:match.end()] == '```':
                # Это незакрытый блок кода
                end_pos = text.rfind('\n', match.start(), len(text))
                if end_pos == -1:
                    text = text + '\n```'
                else:
                    text = text[:end_pos] + '\n```' + text[end_pos:]
                logger.info("✅ Добавлены закрывающие ``` для блока кода")
                return text
    
    # Если это просто нечётное количество одиночных кавычек
    # Добавляем одну кавычку в конец (самый безопасный вариант)
    text += '`'
    logger.info(f"✅ Добавлена закрывающая кавычка. Теперь кавычек: {total_backticks + 1}")
    
    return text

def clean_text_safe(text: str) -> str:
    """
    Безопасная очистка текста от опасных символов.
    Сохраняет форматирование и блоки кода.
    """
    if not text:
        return ""
    
    # Сначала фиксируем кавычки
    text = fix_unbalanced_backticks(text)
    
    # Удаляем опасные управляющие символы
    cleaned = []
    for char in text:
        cat = unicodedata.category(char)
        if cat[0] == 'C':  # Управляющие символы
            # Разрешаем только безопасные символы
            if char in ['\n', '\t', '\r', '`']:
                cleaned.append(char)
            # Иначе удаляем
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

def escape_markdown_v2_smart(text: str) -> str:
    """
    Умное экранирование MarkdownV2.
    Сохраняет блоки кода и правильно экранирует остальной текст.
    """
    # Очищаем текст
    text = clean_text_safe(text)
    
    # ШАГ 1: Защищаем блоки кода ```
    code_blocks = []
    def protect_code_block(match):
        placeholder = f"__CODE_BLOCK_{len(code_blocks)}__"
        code_blocks.append((placeholder, match.group(0)))
        return placeholder
    
    # Регулярка для блоков кода с языком и без
    text = re.sub(r'```[a-zA-Z0-9]*\n[\s\S]*?\n```', protect_code_block, text)
    
    # ШАГ 2: Защищаем inline код `
    inline_codes = []
    def protect_inline_code(match):
        placeholder = f"__INLINE_CODE_{len(inline_codes)}__"
        inline_codes.append((placeholder, match.group(0)))
        return placeholder
    
    text = re.sub(r'`[^`\n]+`', protect_inline_code, text)
    
    # ШАГ 3: Экранируем оставшийся текст
    # Сначала экранируем обратные слеши
    text = text.replace('\\', '\\\\')
    
    # Экранируем специальные символы MarkdownV2
    special_chars = '_*[]()~>#+-=|{}.!'
    for char in special_chars:
        text = text.replace(char, f'\\{char}')
    
    # ШАГ 4: Восстанавливаем inline код
    for placeholder, inline_code in inline_codes:
        text = text.replace(placeholder, inline_code)
    
    # ШАГ 5: Восстанавливаем блоки кода
    for placeholder, code_block in code_blocks:
        text = text.replace(placeholder, code_block)
    
    return text

def text_to_html_safe(text: str) -> str:
    """
    Конвертирует текст с Markdown в безопасный HTML.
    """
    text = clean_text_safe(text)
    
    # Блоки кода с языком
    def code_block_to_html(match):
        lang_match = re.match(r'```([a-zA-Z0-9]+)\n', match.group(0))
        if lang_match:
            lang = lang_match.group(1)
            code = match.group(0)[len(lang)+4:-3]
            return f'<pre><code class="language-{lang}">{code}</code></pre>'
        else:
            # Блок кода без языка
            code = match.group(0)[3:-3]
            return f'<pre><code>{code}</code></pre>'
    
    text = re.sub(r'```[a-zA-Z0-9]*\n[\s\S]*?\n```', code_block_to_html, text)
    
    # Inline код
    text = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', text)
    
    # Жирный текст
    text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
    
    # Курсив
    text = re.sub(r'\*([^*]+)\*', r'<i>\1</i>', text)
    text = re.sub(r'_([^_]+)_', r'<i>\1</i>', text)
    
    # Подчеркивание
    text = re.sub(r'~~([^~]+)~~', r'<u>\1</u>', text)
    
    # Заголовки (только для отдельных строк)
    lines = text.split('\n')
    result_lines = []
    for line in lines:
        if line.startswith('### '):
            result_lines.append(f'<h3>{line[4:]}</h3>')
        elif line.startswith('## '):
            result_lines.append(f'<h2>{line[3:]}</h2>')
        elif line.startswith('# '):
            result_lines.append(f'<h1>{line[2:]}</h1>')
        else:
            result_lines.append(line)
    
    text = '\n'.join(result_lines)
    
    # Списки
    text = re.sub(r'^[*-] (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    lines = text.split('\n')
    result_lines = []
    in_list = False
    
    for line in lines:
        if line.startswith('<li>'):
            if not in_list:
                result_lines.append('<ul>')
                in_list = True
            result_lines.append(line)
        else:
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            result_lines.append(line)
    
    if in_list:
        result_lines.append('</ul>')
    
    text = '\n'.join(result_lines)
    
    return text

# ==================== ФУНКЦИИ ОТПРАВКИ ====================
async def send_message_safe(
    chat_id: int, 
    text: str, 
    reply_to_message_id: Optional[int] = None,
    max_retries: int = 3
) -> Optional[types.Message]:
    """
    Умная отправка сообщений с автовыбором метода.
    Пробует MarkdownV2 → HTML → Plain text.
    """
    if not text or len(text.strip()) == 0:
        logger.error("❌ Пустой текст для отправки")
        return None
    
    # Очищаем текст
    cleaned_text = clean_text_safe(text)
    original_length = len(cleaned_text)
    logger.info(f"📤 Подготовка сообщения длиной {original_length} символов...")
    
    # Проверяем кавычки
    backtick_count = cleaned_text.count('`')
    logger.info(f"📤 Кавычек в тексте: {backtick_count}")
    
    # Проверяем блоки кода
    code_blocks = re.findall(r'```[a-zA-Z0-9]*\n[\s\S]*?\n```', cleaned_text)
    inline_codes = re.findall(r'`[^`\n]+`', cleaned_text)
    logger.info(f"📤 Блоков кода: {len(code_blocks)}, inline кода: {len(inline_codes)}")
    
    methods = [
        ("MarkdownV2", "escape_markdown_v2_smart"),
        ("HTML", "text_to_html_safe"),
        ("Plain", None)
    ]
    
    for method_name, transform_func in methods:
        for attempt in range(max_retries):
            try:
                if transform_func == "escape_markdown_v2_smart":
                    transformed_text = escape_markdown_v2_smart(cleaned_text)
                    parse_mode = "MarkdownV2"
                elif transform_func == "text_to_html_safe":
                    transformed_text = text_to_html_safe(cleaned_text)
                    parse_mode = "HTML"
                else:
                    transformed_text = cleaned_text
                    # Для plain text удаляем лишние кавычки
                    transformed_text = re.sub(r'```[a-zA-Z0-9]*\n', '[КОД]\n', transformed_text)
                    transformed_text = transformed_text.replace('```', '[/КОД]\n')
                    transformed_text = transformed_text.replace('`', "'")
                    parse_mode = None
                
                kwargs = {
                    "chat_id": chat_id,
                    "text": transformed_text,
                }
                
                if parse_mode:
                    kwargs["parse_mode"] = parse_mode
                
                if reply_to_message_id:
                    kwargs["reply_to_message_id"] = reply_to_message_id
                
                # Проверяем длину
                if len(transformed_text) > 4096:
                    logger.warning(f"⚠️ Текст слишком длинный ({len(transformed_text)} символов), разбиваем...")
                    return await send_long_message(chat_id, cleaned_text, reply_to_message_id)
                
                message = await bot.send_message(**kwargs)
                logger.info(f"✅ Отправлено с {method_name} (попытка {attempt+1}/{max_retries})")
                return message
                
            except Exception as e:
                error_msg = str(e)
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ {method_name} не сработал (попытка {attempt+1}): {error_msg[:100]}")
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.error(f"❌ {method_name} полностью не сработал: {error_msg[:100]}")
    
    logger.error("❌ Все методы отправки не сработали")
    return None

async def send_long_message(
    chat_id: int, 
    text: str, 
    reply_to_message_id: Optional[int] = None,
    max_length: int = 3500
) -> None:
    """
    Отправляет длинные сообщения, разбивая на части.
    """
    if len(text) <= max_length:
        await send_message_safe(chat_id, text, reply_to_message_id)
        return
    
    logger.info(f"📤 Разбиваем сообщение длиной {len(text)} символов...")
    
    # Простое разбиение по абзацам
    parts = []
    current_part = ""
    paragraphs = text.split('\n\n')
    
    for para in paragraphs:
        if len(current_part) + len(para) + 2 <= max_length:
            current_part += para + "\n\n"
        else:
            if current_part:
                parts.append(current_part.strip())
            current_part = para + "\n\n"
    
    if current_part:
        parts.append(current_part.strip())
    
    logger.info(f"📤 Разбито на {len(parts)} частей")
    
    for i, part in enumerate(parts):
        logger.info(f"📤 Отправка части {i+1}/{len(parts)} ({len(part)} символов)...")
        
        await send_message_safe(
            chat_id=chat_id,
            text=part,
            reply_to_message_id=reply_to_message_id if i == 0 else None
        )
        
        if i < len(parts) - 1:
            await asyncio.sleep(0.3)

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в футуристике и технологиях будущего. "
        "Отвечай ясно, по делу, с технической точностью. "
        "Используй Markdown для форматирования: **жирный** для ключевых терминов, `inline код` для фрагментов кода. "
        "Для блоков кода используй тройные обратные кавычки с указанием языка:"
        "```python\nprint('Пример')\n```"
        "ВАЖНО: Всегда проверяй, что блоки кода закрыты тремя кавычками ```. "
        "Не используй LaTeX или другие специальные символы. "
        "Длина ответа должна быть 500-1000 символов."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — технический аналитик. Ответь на вопрос пользователя самостоятельно, "
        "предоставив глубокий анализ, конкретные детали и практические шаги. "
        "Используй Markdown для форматирования: **заголовки**, `inline код`, списки. "
        "Для блоков кода всегда используй:"
        "```язык\nкод\n```"
        "И ЗАКРЫВАЙ блок кода тремя кавычками! "
        "Проверь ответ перед отправкой: все блоки кода должны быть закрыты. "
        "Не используй LaTeX. Будь конкретным и техничным. "
        "Длина ответа: 800-1200 символов."
    )
}

# ==================== OPENROUTER ФУНКЦИИ ====================
async def ask_openrouter(
    user_question: str, 
    model: str, 
    system_prompt: Dict[str, str], 
    config: Dict[str, Any],
    timeout: int = 120
) -> Optional[str]:
    """
    Улучшенная функция запроса к OpenRouter с обработкой ошибок.
    """
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
    
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    
    try:
        start_time = time.time()
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                elapsed = time.time() - start_time
                
                if response.status == 200:
                    result = await response.json()
                    
                    # Детальный лог для отладки
                    logger.debug(f"📊 Ответ от {model_name}: {json.dumps(result, ensure_ascii=False)[:500]}")
                    
                    if 'choices' in result and len(result['choices']) > 0:
                        response_text = result['choices'][0]['message'].get('content', '').strip()
                        
                        if not response_text:
                            logger.warning(f"⚠️ {model_name} вернул пустой ответ")
                            return None
                        
                        # Исправляем кавычки
                        original_backticks = response_text.count('`')
                        fixed_text = fix_unbalanced_backticks(response_text)
                        fixed_backticks = fixed_text.count('`')
                        
                        if original_backticks != fixed_backticks:
                            logger.info(f"✅ {model_name}: исправлено кавычек {original_backticks} → {fixed_backticks}")
                        
                        logger.info(f"✅ {model_name} ответил за {elapsed:.1f}с, {len(fixed_text)} символов")
                        return fixed_text
                    else:
                        # Пробуем получить сообщение об ошибке
                        error_detail = result.get('error', {}).get('message', 'Неизвестная ошибка')
                        logger.error(f"❌ Ошибка {model_name}: {error_detail}")
                        return None
                        
                elif response.status == 429:
                    logger.error(f"⏱️ {model_name}: Rate limit exceeded")
                    return None
                elif response.status == 502 or response.status == 503:
                    logger.error(f"🔧 {model_name}: Service temporarily unavailable")
                    return None
                else:
                    error_text = await response.text()
                    logger.error(f"❌ {model_name} ошибка [{response.status}]: {error_text[:200]}")
                    return None
                    
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Таймаут {model_name} (> {timeout}с)")
        return None
    except aiohttp.ClientError as e:
        logger.error(f"🌐 Сетевая ошибка {model_name}: {e}")
        return None
    except Exception as e:
        logger.error(f"⚠️ Неизвестная ошибка {model_name}: {e}")
        return None

async def get_responses_parallel(user_question: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Параллельные запросы к обеим моделям.
    """
    llama_timeout = 100  # Llama может отвечать медленнее
    deepseek_timeout = 150  # DeepSeek R1 думает дольше
    
    llama_task = asyncio.create_task(
        ask_openrouter(
            user_question=user_question,
            model=OPENROUTER_MODEL_MAIN,
            system_prompt=SYSTEM_PROMPT_MAIN,
            config=GENERATION_CONFIG_MAIN,
            timeout=llama_timeout
        )
    )
    
    deepseek_task = asyncio.create_task(
        ask_openrouter(
            user_question=user_question,
            model=OPENROUTER_MODEL_DEEPSEEK,
            system_prompt=SYSTEM_PROMPT_DEEPSEEK,
            config=GENERATION_CONFIG_DEEPSEEK,
            timeout=deepseek_timeout
        )
    )
    
    try:
        llama_response, deepseek_response = await asyncio.gather(
            llama_task, 
            deepseek_task,
            return_exceptions=True
        )
    except Exception as e:
        logger.error(f"💥 Ошибка в parallel gather: {e}")
        llama_response = deepseek_response = None
    
    # Обрабатываем исключения
    if isinstance(llama_response, Exception):
        logger.error(f"❌ Исключение в Llama: {llama_response}")
        llama_response = None
    if isinstance(deepseek_response, Exception):
        logger.error(f"❌ Исключение в DeepSeek: {deepseek_response}")
        deepseek_response = None
    
    return llama_response, deepseek_response

# ==================== ОБРАБОТЧИКИ ТЕЛЕГРАМ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    welcome_text = (
        "👋 Привет! Я Иван Иваныч\n\n"
        "🤖 Две модели ИИ работают параллельно:\n"
        "• **Llama 3.3** — быстрый основной ответ\n"
        "• **DeepSeek R1** — глубокий технический анализ\n\n"
        "⚡ Оба ответа генерируются одновременно!\n\n"
        "💻 *Полная поддержка кода:*\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце\n\n"
        "🔧 Бот автоматически исправляет форматирование!"
    )
    
    await send_message_safe(message.chat.id, welcome_text, message.message_id)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = (
        "📖 **Помощь по боту:**\n\n"
        "• Задавайте вопросы с '?' в конце\n"
        "• Бот использует две модели параллельно\n"
        "• Код форматируется автоматически\n"
        "• Если что-то не работает — попробуйте переформулировать вопрос\n\n"
        "🔄 **Статус моделей:**\n"
        f"• Llama 3.3: {'✅' if OPENROUTER_MODEL_MAIN else '❌'}\n"
        f"• DeepSeek R1: {'✅' if OPENROUTER_MODEL_DEEPSEEK else '❌'}\n\n"
        "💡 **Совет:** Для технических вопросов DeepSeek даёт более детальные ответы"
    )
    
    await send_message_safe(message.chat.id, help_text, message.message_id)

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Основной обработчик вопросов"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    username = f"@{message.from_user.username}" if message.from_user.username else f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:100]}...")
    
    processing_msg = None
    try:
        # Уведомление о начале обработки
        processing_text = "🤔 Две модели ИИ анализируют вопрос параллельно..."
        processing_msg = await send_message_safe(chat_id, processing_text, message.message_id)
        
        if not processing_msg:
            logger.error("❌ Не удалось отправить уведомление")
            return
        
        start_time = time.time()
        
        # Параллельные запросы
        logger.info("⚡ Запуск параллельных запросов...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        llama_response, deepseek_response = await get_responses_parallel(user_question)
        
        # Обработка ответов
        elapsed_time = time.time() - start_time
        
        # Сначала Llama
        if llama_response:
            logger.info(f"📤 Отправка ответа Llama ({len(llama_response)} символов)...")
            
            # Обновляем статус
            await processing_msg.edit_text(
                "✅ Llama ответил! Готовим анализ DeepSeek...",
                parse_mode=None
            )
            
            # Отправляем ответ Llama
            await send_long_message(
                chat_id=chat_id,
                text=f"🤖 **Ответ Llama 3.3:**\n\n{llama_response}",
                reply_to_message_id=message.message_id
            )
        else:
            logger.warning("⚠️ Llama не ответил")
            # Не прерываем, может быть DeepSeek ответит
        
        # Затем DeepSeek
        if deepseek_response and len(deepseek_response) > 100:
            logger.info(f"📤 Отправка ответа DeepSeek ({len(deepseek_response)} символов)...")
            
            await send_long_message(
                chat_id=chat_id,
                text=f"🔍 **Глубокий анализ DeepSeek R1:**\n\n{deepseek_response}",
                reply_to_message_id=message.message_id
            )
            
            # Финальное сообщение
            if llama_response:
                completion_text = (
                    f"✅ Анализ завершён!\n"
                    f"⏱️ Время: {elapsed_time:.1f} секунд\n"
                    f"📊 Llama: {len(llama_response)} символов\n"
                    f"🔍 DeepSeek: {len(deepseek_response)} символов"
                )
            else:
                completion_text = (
                    f"✅ Анализ завершён (только DeepSeek)!\n"
                    f"⏱️ Время: {elapsed_time:.1f} секунд\n"
                    f"🔍 DeepSeek: {len(deepseek_response)} символов"
                )
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Успешно! Общее время: {elapsed_time:.1f}с")
            
        elif llama_response:
            # Только Llama ответил
            completion_text = (
                f"✅ Ответ готов!\n"
                f"⏱️ Время: {elapsed_time:.1f} секунд\n"
                f"📊 Llama: {len(llama_response)} символов\n"
                f"ℹ️ DeepSeek временно недоступен"
            )
            
            await processing_msg.edit_text(completion_text, parse_mode=None)
            logger.info(f"✅ Только Llama ответил за {elapsed_time:.1f}с")
            
        else:
            # Никто не ответил
            error_text = "❌ Обе модели не ответили. Пожалуйста, попробуйте позже."
            await processing_msg.edit_text(error_text, parse_mode=None)
            logger.error("❌ Ни одна модель не ответила")
        
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки")
        if processing_msg:
            await processing_msg.edit_text("⏱️ Время обработки истекло. Попробуйте позже.", parse_mode=None)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в обработчике: {e}", exc_info=True)
        if processing_msg:
            error_msg = f"⚠️ Ошибка обработки: {str(e)[:150]}"
            await send_message_safe(chat_id, error_msg)

@dp.message()
async def handle_other_messages(message: types.Message):
    """Обработчик всех остальных сообщений"""
    if message.text and len(message.text.strip()) > 3:
        response = "🤔 Задайте вопрос с '?' в конце, чтобы получить развёрнутый ответ от обеих моделей ИИ."
        await send_message_safe(message.chat.id, response, message.message_id)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска бота"""
    logger.info("=" * 60)
    logger.info(f"🚀 Бот IvanIvanych запускается...")
    logger.info(f"🤖 Модели: {OPENROUTER_MODEL_MAIN} + {OPENROUTER_MODEL_DEEPSEEK}")
    logger.info(f"⚡ Архитектура: Параллельная генерация")
    logger.info(f"💻 Функция: Автоисправление кавычек и кода")
    logger.info("=" * 60)
    
    try:
        # Очищаем предыдущие обновления
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Очищены предыдущие обновления")
        
        # Запускаем поллинг
        await dp.start_polling(bot, skip_updates=True)
        
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка при запуске: {e}", exc_info=True)
        raise
    finally:
        try:
            await bot.session.close()
            logger.info("🔌 Сессия бота закрыта")
        except:
            pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот завершил работу")
    except Exception as e:
        logger.error(f"💥 Фатальная ошибка: {e}", exc_info=True)