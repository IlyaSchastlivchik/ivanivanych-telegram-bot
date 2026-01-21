import asyncio
import logging
import os
import aiohttp
import re
from typing import Optional, List
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

import asyncio
import logging
import os
import aiohttp
import re
import time
from typing import Optional, List
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

# Основная модель (Llama) - ускоренная версия
OPENROUTER_MODEL_MAIN = "meta-llama/llama-3.1-8b-instruct:free"  # Быстрее 70B

# Модель DeepSeek для анализа ответов
OPENROUTER_MODEL_DEEPSEEK = "deepseek/deepseek-r1:free"  # Рабочая модель

# Настройки генерации - оптимизированы для скорости
GENERATION_CONFIG_MAIN = {
    "temperature": 0.85,
    "max_tokens": 1200,  # Уменьшено для скорости
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

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в футуристике и технологиях будущего. "
        "Отвечай ясно, по делу, с технической точностью. Используй Markdown для форматирования: "
        "**жирный** для ключевых терминов, ```код``` для примеров."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — технический аналитик. Получив вопрос и ответ, предоставь:\n"
        "1. **Глубокий анализ** недостатков/пробелов\n"
        "2. **Конкретные детали** (цифры, технологии, даты)\n"
        "3. **Практические шаги реализации**\n"
        "4. **Риски и альтернативы**\n"
        "Будь максимально конкретным и техничным."
    )
}

# ==================== УТИЛИТЫ ====================
def safe_prepare_for_markdown_v2(text: str) -> str:
    """Экранирует текст для MarkdownV2, сохраняя блоки кода"""
    pattern = r'(```[\w]*\n[\s\S]*?\n```)'
    parts = re.split(pattern, text)
    result_parts = []
    
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    
    for i, part in enumerate(parts):
        if i % 2 == 1:  # Блоки кода
            result_parts.append(part)
        else:  # Обычный текст
            escaped_text = ''.join(['\\' + char if char in escape_chars else char for char in part])
            result_parts.append(escaped_text)
    
    return ''.join(result_parts)

def split_message(text: str, max_length: int = 3800) -> List[str]:
    """Умное разбиение сообщений с сохранением форматирования"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_part = ""
    
    # Разбиваем по логическим блокам
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
    
    return parts

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """Отправляет длинное сообщение с прогрессом"""
    processed_text = safe_prepare_for_markdown_v2(text)
    parts = split_message(processed_text)
    
    logger.info(f"📤 Отправка сообщения из {len(parts)} частей...")
    
    for i, part in enumerate(parts):
        try:
            if i == 0 and reply_to_message_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode='MarkdownV2'
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    parse_mode='MarkdownV2'
                )
            
            # Небольшая задержка между частями
            if i < len(parts) - 1:
                await asyncio.sleep(0.3)
                
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке части {i+1}/{len(parts)}: {e}")

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
    
    # Динамический таймаут в зависимости от модели
    timeout_seconds = 120 if "deepseek" in model.lower() else 90
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    
    try:
        start_time = time.time()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                
                elapsed = time.time() - start_time
                
                if response.status == 200:
                    result = await response.json()
                    if 'choices' in result and len(result['choices']) > 0:
                        response_text = result['choices'][0]['message']['content'].strip()
                        logger.info(f"✅ {model_name} ответил за {elapsed:.1f}с, {len(response_text)} символов")
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

async def get_main_response(user_question: str) -> Optional[str]:
    """Получает ответ от основной модели"""
    return await ask_openrouter(
        user_question=user_question,
        model=OPENROUTER_MODEL_MAIN,
        system_prompt=SYSTEM_PROMPT_MAIN,
        config=GENERATION_CONFIG_MAIN
    )

async def get_deepseek_analysis(user_question: str, llama_response: str) -> Optional[str]:
    """Получает анализ от DeepSeek"""
    if not llama_response:
        return None
    
    analysis_prompt = f"""
    ВОПРОС: {user_question}
    
    ОТВЕТ: {llama_response}
    
    Задача: Проанализировать ответ и дать глубокое дополнение с:
    1. Техническими деталями
    2. Конкретными примерами
    3. Практическими шагами
    4. Альтернативными подходами
    """
    
    return await ask_openrouter(
        user_question=analysis_prompt,
        model=OPENROUTER_MODEL_DEEPSEEK,
        system_prompt=SYSTEM_PROMPT_DEEPSEEK,
        config=GENERATION_CONFIG_DEEPSEEK
    )

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 *Привет! Я Иван Иваныч*\n\n"
        "🤖 *Две модели ИИ:*\n"
        "• **Llama 3.1** — быстрый основной ответ\n"
        "• **DeepSeek R1** — глубокий технический анализ\n\n"
        "⚡ *Скорость:* ~15-25 секунд на сложный вопрос\n\n"
        "❓ *Как задавать:*\n"
        "Заканчивайте вопрос знаком ?\n\n"
        "📋 *Команды:*\n"
        "/help — справка\n"
        "/model — модели\n"
        "/status — проверка работы"
    )
    await message.answer(welcome_text, parse_mode='MarkdownV2')

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 *Помощь*\n\n"
        "Бот использует **две модели параллельно**:\n"
        "1️⃣ Llama отвечает первым (5-10с)\n"
        "2️⃣ DeepSeek добавляет анализ (10-15с)\n\n"
        f"🔧 *Модели:*\n"
        f"• Основная: `{OPENROUTER_MODEL_MAIN}`\n"
        f"• Аналитик: `{OPENROUTER_MODEL_DEEPSEEK}`\n\n"
        "💡 *Совет:* Сложные технические вопросы получают лучший анализ!"
    )
    await message.answer(help_text, parse_mode='MarkdownV2')

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    model_info = (
        f"🤖 *Архитектура бота*\n\n"
        f"**1. {OPENROUTER_MODEL_MAIN}**\n"
        f"• Настройки: {GENERATION_CONFIG_MAIN['temperature']} temp, {GENERATION_CONFIG_MAIN['max_tokens']} токенов\n"
        f"• Задача: Быстрый качественный ответ\n\n"
        f"**2. {OPENROUTER_MODEL_DEEPSEEK}**\n"
        f"• Настройки: {GENERATION_CONFIG_DEEPSEEK['temperature']} temp, {GENERATION_CONFIG_DEEPSEEK['max_tokens']} токенов\n"
        f"• Задача: Глубокий технический анализ"
    )
    await message.answer(model_info, parse_mode='MarkdownV2')

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Проверка работы моделей"""
    status_msg = await message.reply("🔄 Проверка моделей...")
    
    # Тестовый запрос к Llama
    test_question = "Какая версия Python лучше для ИИ проектов? Ответь кратко."
    
    try:
        # Тест Llama
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        llama_test = await ask_openrouter(
            test_question,
            OPENROUTER_MODEL_MAIN,
            SYSTEM_PROMPT_MAIN,
            {"max_tokens": 100, "temperature": 0.7}
        )
        
        llama_status = "✅ Работает" if llama_test else "❌ Ошибка"
        
        # Тест DeepSeek
        deepseek_test = await ask_openrouter(
            "Тестовый запрос.",
            OPENROUTER_MODEL_DEEPSEEK,
            SYSTEM_PROMPT_DEEPSEEK,
            {"max_tokens": 50, "temperature": 0.7}
        )
        
        deepseek_status = "✅ Работает" if deepseek_test else "❌ Ошибка"
        
        status_text = (
            f"📊 *Статус моделей*\n\n"
            f"**{OPENROUTER_MODEL_MAIN.split('/')[-1]}**: {llama_status}\n"
            f"**{OPENROUTER_MODEL_DEEPSEEK.split('/')[-1]}**: {deepseek_status}\n\n"
            f"⏱️ *Лимиты:*\n"
            f"• Llama: до {GENERATION_CONFIG_MAIN['max_tokens']} токенов\n"
            f"• DeepSeek: до {GENERATION_CONFIG_DEEPSEEK['max_tokens']} токенов"
        )
        
        await status_msg.edit_text(status_text, parse_mode='MarkdownV2')
        
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Ошибка проверки: {str(e)[:200]}")

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Улучшенный обработчик с параллельной обработкой"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    # Идентификатор пользователя для логирования
    username = f"@{message.from_user.username}" if message.from_user.username else f"user_{message.from_user.id}"
    logger.info(f"🧠 Вопрос от {username}: {user_question[:80]}...")
    
    try:
        # ШАГ 1: Уведомление о начале обработки
        processing_msg = await message.reply(
            "🤔 *Иван Иваныч думает...*\n"
            "Две модели ИИ анализируют ваш вопрос. Это займёт ~15-25 секунд.",
            parse_mode='MarkdownV2'
        )
        
        start_total_time = time.time()
        
        # ШАГ 2: ПАРАЛЛЕЛЬНЫЕ ЗАПРОСЫ
        logger.info(f"⚡ Параллельные запросы к Llama и DeepSeek...")
        
        # Запускаем оба запроса одновременно
        llama_task = asyncio.create_task(get_main_response(user_question))
        deepseek_task = asyncio.create_task(get_deepseek_analysis(user_question, ""))
        
        # Ждём сначала Llama (основной ответ)
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        llama_response = await llama_task
        llama_time = time.time() - start_total_time
        
        if not llama_response:
            await processing_msg.edit_text("❌ Не удалось получить ответ от основной модели. Попробуйте позже.")
            return
        
        # ШАГ 3: Отправляем ответ Llama сразу
        logger.info(f"📤 Llama готов (за {llama_time:.1f}с), отправка...")
        await processing_msg.edit_text("✅ *Первая часть готова!*\nDeepSeek завершает анализ...")
        
        await send_long_message(
            chat_id=chat_id,
            text=f"**🤖 Ответ IvanIvanych:**\n\n{llama_response}",
            reply_to_message_id=message.message_id
        )
        
        # ШАГ 4: Ждём DeepSeek (уже в фоне)
        logger.info("⏳ Ожидание DeepSeek...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        deepseek_response = await deepseek_task
        total_time = time.time() - start_total_time
        
        # ШАГ 5: Отправляем анализ DeepSeek (если есть)
        if deepseek_response and len(deepseek_response) > 50:
            logger.info(f"📤 DeepSeek готов (общее время {total_time:.1f}с), отправка...")
            
            # Обновляем промпт для DeepSeek с реальным ответом Llama
            if "ОТВЕТ:" in deepseek_response:
                # Улучшаем анализ на основе реального ответа
                better_analysis = await get_deepseek_analysis(user_question, llama_response)
                if better_analysis:
                    deepseek_response = better_analysis
            
            await send_long_message(
                chat_id=chat_id,
                text=f"**🔍 Глубокий анализ (DeepSeek R1):**\n\n{deepseek_response}",
                reply_to_message_id=message.message_id
            )
            
            await processing_msg.edit_text(
                f"✅ *Анализ завершён!*\n"
                f"⏱️ Общее время: {total_time:.1f} секунд\n"
                f"📊 Llama: {len(llama_response)} символов\n"
                f"🔍 DeepSeek: {len(deepseek_response)} символов",
                parse_mode='MarkdownV2'
            )
            
            logger.info(f"✅ Успешно! Llama: {len(llama_response)}с, DeepSeek: {len(deepseek_response)}с, Время: {total_time:.1f}с")
            
        else:
            # Если DeepSeek не сработал
            logger.warning("⚠️ DeepSeek не вернул анализ")
            await processing_msg.edit_text(
                f"✅ *Ответ готов!*\n"
                f"⏱️ Время: {total_time:.1f} секунд\n"
                f"ℹ️ DeepSeek временно недоступен, но основной ответ выше.",
                parse_mode='MarkdownV2'
            )
        
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки")
        await message.reply("⏱️ Время обработки истекло. Вопрос слишком сложный или сервисы перегружены.")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        await message.reply(f"⚠️ Ошибка обработки: {str(e)[:200]}")

@dp.message()
async def log_all_messages(message: types.Message):
    """Логирует сообщения без '?'"""
    if message.text:
        logger.debug(f"💬 Сообщение без '?' от {message.from_user.id}: {message.text[:50]}...")

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info(f"🚀 Бот IvanIvanych запускается...")
    logger.info(f"🤖 Основная модель: {OPENROUTER_MODEL_MAIN}")
    logger.info(f"🔍 Модель анализа: {OPENROUTER_MODEL_DEEPSEEK}")
    logger.info(f"⚡ Архитектура: Параллельная обработка")
    logger.info(f"📝 Логирование: Детальное")
    logger.info("=" * 60)
    
    try:
        # Очищаем предыдущие обновления
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Очищены предыдущие обновления")
        
        # Запускаем polling
        await dp.start_polling(bot, skip_updates=True, handle_signals=True)
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logger.info("🔌 Сессия бота закрыта")

if __name__ == "__main__":
    # Настройка обработки исключений
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        logger.info("👋 Завершение работы...")
    finally:
        loop.close()

# Настройки генерации
GENERATION_CONFIG_MAIN = {
    "temperature": 0.9,
    "max_tokens": 1500,  # Уменьшено для Llama, чтобы оставить место для DeepSeek
    "top_p": 0.95,
    "frequency_penalty": 0.2,
    "presence_penalty": 0.1,
}

GENERATION_CONFIG_DEEPSEEK = {
    "temperature": 0.8,
    "max_tokens": 2000,  # DeepSeek может давать более развёрнутые ответы
    "top_p": 0.9,
    "frequency_penalty": 0.1,
    "presence_penalty": 0.05,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — душа компании в телеграм-канале о футуристике, "
        "технологиях будущего и свободной энергии. Твой тон — дружелюбный, "
        "увлеченный и вдохновляющий. Ты с энтузиазмом поддерживаешь любые "
        "смелые гипотезы, поощряешь творческое обсуждение и готов фантазировать "
        "о будущем. Отвечай подробно, с интересом и без излишнего скептицизма.\n\n"
        "**ВАЖНО ДЛЯ ФОРМАТИРОВАНИЯ:**\n"
        "1. Когда приводишь пример кода, ОБЯЗАТЕЛЬНО обрамляй его тройными обратными кавычками и указывай язык.\n"
        "   Пример: ```python\\nprint('Привет')\\n```\n"
        "2. Для выделения текста используй Markdown: *курсив*, **жирный**.\n"
        "3. Весь остальной текст пиши обычным образом."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — эксперт-аналитик в области футуристики и технологий. "
        "Твоя задача — проанализировать вопрос пользователя и ответ ИИ, "
        "затем предоставить максимально РАЗВЁРНУТОЕ, ГЛУБОКОЕ и ДЕТАЛЬНОЕ дополнение.\n\n"
        "**ТВОИ ОБЯЗАННОСТИ:**\n"
        "1. ВНИМАТЕЛЬНО изучи исходный вопрос и ответ\n"
        "2. Выяви недостатки, пробелы или упрощения в ответе\n"
        "3. Добавь конкретные технические детали, цифры, даты, примеры\n"
        "4. Рассмотри альтернативные сценарии и точки зрения\n"
        "5. Предложи практические шаги для реализации идей\n"
        "6. Укажи на потенциальные риски и вызовы\n"
        "7. Приведи ссылки на исследования, компании или технологии (если знаешь)\n\n"
        "**ФОРМАТ ОТВЕТА:**\n"
        "🎯 **ГЛУБОКИЙ АНАЛИЗ:** [подробный разбор вопроса]\n"
        "🔍 **ДЕТАЛИЗАЦИЯ:** [конкретные технические/научные детали]\n"
        "💡 **ДОПОЛНЕНИЯ:** [что упущено в исходном ответе]\n"
        "🚀 **ПРАКТИЧЕСКАЯ РЕАЛИЗАЦИЯ:** [как можно воплотить идеи]\n"
        "⚠️ **РИСКИ И ВЫЗОВЫ:** [потенциальные проблемы]\n"
        "📚 **ДЛЯ ДАЛЬНЕЙШЕГО ИЗУЧЕНИЯ:** [что почитать/посмотреть]\n\n"
        "Будь максимально конкретным, технически грамотным и подробным. "
        "Не повторяй уже сказанное в исходном ответе — ДОПОЛНЯЙ и РАСШИРЯЙ!"
    )
}

# ==================== УТИЛИТЫ ====================
def safe_prepare_for_markdown_v2(text: str) -> str:
    """
    Разделяет текст на блоки кода и обычный текст.
    Экранирует только обычный текст для корректной работы parse_mode='MarkdownV2'.
    """
    pattern = r'(```[\w]*\n[\s\S]*?\n```)'
    parts = re.split(pattern, text)
    result_parts = []
    
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    
    for i, part in enumerate(parts):
        if i % 2 == 1:  # Блоки кода
            result_parts.append(part)
        else:  # Обычный текст
            escaped_text = ''.join(['\\' + char if char in escape_chars else char for char in part])
            result_parts.append(escaped_text)
    
    return ''.join(result_parts)

def split_message(text: str, max_length: int = 4000) -> List[str]:
    """
    Разбивает длинное сообщение на части, стараясь не разрывать предложения.
    Учитывает особенности форматирования MarkdownV2.
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    current_part = ""
    
    # Разбиваем по предложениям
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    for sentence in sentences:
        if len(current_part) + len(sentence) + 1 <= max_length:
            current_part += (sentence + " ")
        else:
            if current_part:
                parts.append(current_part.strip())
            if len(sentence) > max_length:
                if '```' in sentence:
                    if current_part.count('```') % 2 != 0:
                        current_part += '```'
                        parts.append(current_part.strip())
                        current_part = '```' + sentence.split('```', 1)[1] + " "
                    else:
                        current_part = sentence + " "
                else:
                    words = sentence.split()
                    current_part = ""
                    for word in words:
                        if len(current_part) + len(word) + 1 <= max_length:
                            current_part += (word + " ")
                        else:
                            if current_part:
                                parts.append(current_part.strip())
                            current_part = word + " "
            else:
                current_part = sentence + " "
    
    if current_part:
        parts.append(current_part.strip())
    
    return parts

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """
    Отправляет длинное сообщение частями с задержкой между частями.
    """
    processed_text = safe_prepare_for_markdown_v2(text)
    parts = split_message(processed_text)
    
    for i, part in enumerate(parts):
        try:
            if i == 0 and reply_to_message_id:
                await bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    reply_to_message_id=reply_to_message_id,
                    parse_mode='MarkdownV2'
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=part,
                    parse_mode='MarkdownV2'
                )
            
            if i < len(parts) - 1:
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Ошибка при отправке части {i+1}/{len(parts)}: {e}")

# ==================== ФУНКЦИИ ДЛЯ OPENROUTER ====================
async def ask_openrouter(user_question: str, model: str, system_prompt: dict, config: dict) -> Optional[str]:
    """
    Общая функция для запроса к OpenRouter API
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
    
    logger.info(f"Отправка запроса к модели {model}")
    
    timeout = aiohttp.ClientTimeout(total=180)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                
                if response.status == 200:
                    result = await response.json()
                    if 'choices' in result and len(result['choices']) > 0:
                        response_text = result['choices'][0]['message']['content'].strip()
                        logger.info(f"Получен ответ от {model} длиной {len(response_text)} символов")
                        return response_text
                    else:
                        logger.error(f"Неожиданный формат ответа API для модели {model}")
                        return None
                        
                elif response.status == 429:
                    logger.warning(f"Превышен лимит запросов для {model} (429)")
                    return None
                    
                elif response.status == 502:
                    logger.warning(f"Проблема с моделью {model} (502 Bad Gateway)")
                    return None
                    
                elif response.status == 504:
                    logger.warning(f"Таймаут от модели {model} (504 Gateway Timeout)")
                    return None
                    
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка API [{response.status}] для {model}: {error_text[:200]}")
                    return None
                    
    except asyncio.TimeoutError:
        logger.error(f"Таймаут запроса к {model}")
        return None
        
    except aiohttp.ClientConnectorError as e:
        logger.error(f"Ошибка подключения к {model}: {e}")
        return None
        
    except Exception as e:
        logger.error(f"Неожиданная ошибка при запросе к {model}: {e}")
        return None

async def get_main_response(user_question: str) -> Optional[str]:
    """Получает ответ от основной модели (Llama)"""
    return await ask_openrouter(
        user_question=user_question,
        model=OPENROUTER_MODEL_MAIN,
        system_prompt=SYSTEM_PROMPT_MAIN,
        config=GENERATION_CONFIG_MAIN
    )

async def get_deepseek_analysis(user_question: str, llama_response: str) -> Optional[str]:
    """Получает развёрнутый анализ от DeepSeek"""
    if not llama_response:
        return None
    
    analysis_prompt = f"""
    ИСХОДНЫЙ ВОПРОС ПОЛЬЗОВАТЕЛЯ:
    {user_question}
    
    ОТВЕТ ОСНОВНОЙ МОДЕЛИ (Llama):
    {llama_response}
    
    ---
    
    Пожалуйста, предоставь максимально РАЗВЁРНУТЫЙ, ГЛУБОКИЙ и ДЕТАЛЬНЫЙ анализ.
    Добавь технические детали, конкретные примеры, цифры, даты, альтернативные сценарии.
    Не повторяй уже сказанное — ДОПОЛНЯЙ и РАСШИРЯЙ!
    """
    
    return await ask_openrouter(
        user_question=analysis_prompt,
        model=OPENROUTER_MODEL_DEEPSEEK,
        system_prompt=SYSTEM_PROMPT_DEEPSEEK,
        config=GENERATION_CONFIG_DEEPSEEK
    )

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    welcome_text = (
        "👋 Привет! Я Иван Иваныч — ваш собеседник по футуристике и технологиям.\n\n"
        "*Новая функция:* Теперь каждый ответ анализируется DeepSeek, который даёт "
        "развёрнутые дополнения с техническими деталями!\n\n"
        "*Как задавать вопросы:*\n"
        "• Заканчивайте вопрос знаком вопроса (?)\n"
        "• Будьте конкретны\n"
        "• Сложные вопросы разбивайте на части\n\n"
        "*Доступные команды:*\n"
        "/help - справка\n"
        "/model - текущие модели ИИ\n"
        "/tips - как лучше задавать вопросы"
    )
    await message.answer(welcome_text, parse_mode='MarkdownV2')

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "🤖 *Помощь по боту*\n\n"
        "Я отвечаю только на вопросы, которые заканчиваются знаком *?*\n\n"
        "🔄 *Как работает бот:*\n"
        "1. Llama 3.3 даёт основной ответ\n"
        "2. DeepSeek анализирует вопрос и ответ\n"
        "3. DeepSeek предоставляет развёрнутое дополнение\n\n"
        f"📊 *Основная модель:* {OPENROUTER_MODEL_MAIN}\n"
        f"🔍 *Аналитик:* {OPENROUTER_MODEL_DEEPSEEK}"
    )
    await message.answer(help_text, parse_mode='MarkdownV2')

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    model_info = (
        f"🤖 *Используемые модели:*\n\n"
        f"*Основная:* {OPENROUTER_MODEL_MAIN}\n"
        f"• Максимальная длина: {GENERATION_CONFIG_MAIN['max_tokens']} токенов\n"
        f"• Температура: {GENERATION_CONFIG_MAIN['temperature']}\n\n"
        f"*Аналитик:* {OPENROUTER_MODEL_DEEPSEEK}\n"
        f"• Максимальная длина: {GENERATION_CONFIG_DEEPSEEK['max_tokens']} токенов\n"
        f"• Температура: {GENERATION_CONFIG_DEEPSEEK['temperature']}\n\n"
        f"DeepSeek предоставляет развёрнутые дополнения с техническими деталями."
    )
    await message.answer(model_info, parse_mode='MarkdownV2')

@dp.message(Command("tips"))
async def cmd_tips(message: types.Message):
    tips_text = (
        "💡 *Советы для лучших ответов:*\n\n"
        "1. *Будьте конкретны*\n"
        "   ❌ «Расскажи про технологии»\n"
        "   ✅ «Какие технологии изменят транспорт к 2040 году?»\n\n"
        "2. *Задавайте сложные вопросы*\n"
        "   DeepSeek лучше всего работает с технически сложными темами, "
        "где нужны детальные разъяснения\n\n"
        "3. *Используйте технические термины*\n"
        "   Это поможет моделям дать более точные и специализированные ответы"
    )
    await message.answer(tips_text, parse_mode='MarkdownV2')

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Основной обработчик вопросов с двухэтапным ответом"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    # Показываем статус "печатает"
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    
    # Логируем вопрос
    username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    logger.info(f"Вопрос от {username}: {user_question[:100]}...")
    
    try:
        # ЭТАП 1: Получаем ответ от основной модели
        logger.info("🔹 Этап 1: Запрос к Llama...")
        llama_response = await get_main_response(user_question)
        
        if not llama_response:
            await message.reply("Не удалось получить ответ от основной модели. Попробуйте позже.")
            return
        
        # Отправляем ответ Llama сразу
        logger.info("📤 Отправка ответа Llama...")
        await send_long_message(
            chat_id=chat_id,
            text=f"**🤖 Ответ IvanIvanych (Llama 3.3):**\n\n{llama_response}",
            reply_to_message_id=message.message_id
        )
        
        # ЭТАП 2: Получаем развёрнутый анализ от DeepSeek
        logger.info("🔹 Этап 2: Анализ DeepSeek...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        deepseek_response = await get_deepseek_analysis(user_question, llama_response)
        
        if deepseek_response:
            logger.info("📤 Отправка анализа DeepSeek...")
            # Отправляем анализ DeepSeek как отдельное сообщение
            await send_long_message(
                chat_id=chat_id,
                text=f"**🔍 Глубокий анализ (DeepSeek R1):**\n\n{deepseek_response}",
                reply_to_message_id=message.message_id
            )
            
            # Логируем успешный ответ
            logger.info(f"✅ Ответ успешно отправлен пользователю {username}")
            logger.info(f"📊 Статистика: Llama - {len(llama_response)} символов, DeepSeek - {len(deepseek_response)} символов")
            
        else:
            logger.warning("DeepSeek не предоставил анализ")
            await bot.send_message(
                chat_id=chat_id,
                text="⚠️ *Примечание:* Не удалось получить дополнительный анализ от DeepSeek. "
                     "Основной ответ выше полный и информативный.",
                parse_mode='MarkdownV2'
            )
        
    except Exception as e:
        logger.error(f"Ошибка в обработчике вопроса: {e}")
        await message.reply("Произошла ошибка при обработке вопроса. Попробуйте позже.")

@dp.message()
async def log_all_messages(message: types.Message):
    """Логирует все сообщения без '?'"""
    if message.text:
        logger.debug(f"Сообщение без '?' от {message.from_user.id}: {message.text[:50]}...")

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска бота"""
    logger.info("=" * 50)
    logger.info(f"Бот IvanIvanych запускается...")
    logger.info(f"Основная модель: {OPENROUTER_MODEL_MAIN}")
    logger.info(f"Модель анализа: {OPENROUTER_MODEL_DEEPSEEK}")
    logger.info(f"Поддержка MarkdownV2: АКТИВНА")
    logger.info("=" * 50)
    
    try:
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())