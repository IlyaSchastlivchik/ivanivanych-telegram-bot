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
OPENROUTER_MODEL_MAIN = "meta-llama/llama-3.3-70b-instruct:free"

# Модель DeepSeek для анализа ответов
OPENROUTER_MODEL_DEEPSEEK = "deepseek/deepseek-r1:free"  # Рабочая модель

# Настройки генерации - оптимизированы для скорости
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

# ==================== УТИЛИТЫ ЭКРАНИРОВАНИЯ ====================
def escape_markdown_v2(text: str) -> str:
    """
    Экранирует текст для MarkdownV2 в Telegram.
    Список символов для экранирования: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return ''.join(['\\' + char if char in escape_chars else char for char in text])

def prepare_markdown_v2_safe(text: str) -> str:
    """
    Подготавливает текст для отправки в режиме MarkdownV2.
    Разделяет на блоки кода и обычный текст, экранируя только обычный текст.
    """
    # Проверяем, есть ли блоки кода в тексте
    if '```' not in text:
        # Если блоков кода нет, экранируем весь текст
        return escape_markdown_v2(text)
    
    # Если есть блоки кода, обрабатываем их отдельно
    pattern = r'(```[\w]*\n[\s\S]*?\n```)'
    parts = re.split(pattern, text)
    result_parts = []
    
    for i, part in enumerate(parts):
        if i % 2 == 1:  # Блоки кода (нечетные индексы)
            result_parts.append(part)
        else:  # Обычный текст (четные индексы)
            if part:  # Если не пустая строка
                result_parts.append(escape_markdown_v2(part))
    
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
    """Отправляет длинное сообщение с правильным экранированием"""
    # Подготавливаем текст для MarkdownV2
    processed_text = prepare_markdown_v2_safe(text)
    
    # Разбиваем на части
    parts = split_message(processed_text)
    
    logger.info(f"📤 Отправка сообщения из {len(parts)} частей...")
    
    for i, part in enumerate(parts):
        try:
            # Проверяем, что часть не пустая
            if not part.strip():
                continue
                
            send_kwargs = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "MarkdownV2"
            }
            
            if i == 0 and reply_to_message_id:
                send_kwargs["reply_to_message_id"] = reply_to_message_id
            
            await bot.send_message(**send_kwargs)
            
            # Небольшая задержка между частями
            if i < len(parts) - 1:
                await asyncio.sleep(0.3)
                
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке части {i+1}/{len(parts)}: {e}")
            # Пробуем отправить без форматирования
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"Часть {i+1} (без форматирования):\n\n{part[:1000]}",
                    parse_mode=None
                )
            except Exception as e2:
                logger.error(f"❌ Не удалось отправить даже без форматирования: {e2}")

async def send_simple_message(chat_id: int, text: str, reply_to_message_id: int = None, 
                              parse_mode: str = "MarkdownV2") -> Optional[types.Message]:
    """Универсальная функция для отправки простых сообщений"""
    try:
        if parse_mode == "MarkdownV2":
            text = escape_markdown_v2(text)
        
        send_kwargs = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode if parse_mode != "MarkdownV2" else "MarkdownV2"
        }
        
        if reply_to_message_id:
            send_kwargs["reply_to_message_id"] = reply_to_message_id
        
        return await bot.send_message(**send_kwargs)
    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения: {e}")
        # Пробуем отправить без форматирования
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None,
                reply_to_message_id=reply_to_message_id if reply_to_message_id else None
            )
        except Exception as e2:
            logger.error(f"❌ Не удалось отправить сообщение вообще: {e2}")
            return None

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
        "👋 *Привет\\! Я Иван Иваныч*\n\n"
        "🤖 *Две модели ИИ:*\n"
        "• **Llama 3\\.1** — быстрый основной ответ\n"
        "• **DeepSeek R1** — глубокий технический анализ\n\n"
        "⚡ *Скорость:* ~15\\-25 секунд на сложный вопрос\n\n"
        "❓ *Как задавать:*\n"
        "Заканчивайте вопрос знаком \\?\n\n"
        "📋 *Команды:*\n"
        "/help — справка\n"
        "/model — модели\n"
        "/status — проверка работы"
    )
    await send_simple_message(message.chat.id, welcome_text, message.message_id)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "📖 *Помощь*\n\n"
        "Бот использует **две модели параллельно**:\n"
        "1️⃣ Llama отвечает первым \\(5\\-10с\\)\n"
        "2️⃣ DeepSeek добавляет анализ \\(10\\-15с\\)\n\n"
        f"🔧 *Модели:*\n"
        f"• Основная: `{escape_markdown_v2(OPENROUTER_MODEL_MAIN)}`\n"
        f"• Аналитик: `{escape_markdown_v2(OPENROUTER_MODEL_DEEPSEEK)}`\n\n"
        "💡 *Совет:* Сложные технические вопросы получают лучший анализ\\!"
    )
    await send_simple_message(message.chat.id, help_text, message.message_id)

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    model_info = (
        f"🤖 *Архитектура бота*\n\n"
        f"**1\\. {escape_markdown_v2(OPENROUTER_MODEL_MAIN.split('/')[-1])}**\n"
        f"• Настройки: {GENERATION_CONFIG_MAIN['temperature']} temp, {GENERATION_CONFIG_MAIN['max_tokens']} токенов\n"
        f"• Задача: Быстрый качественный ответ\n\n"
        f"**2\\. {escape_markdown_v2(OPENROUTER_MODEL_DEEPSEEK.split('/')[-1])}**\n"
        f"• Настройки: {GENERATION_CONFIG_DEEPSEEK['temperature']} temp, {GENERATION_CONFIG_DEEPSEEK['max_tokens']} токенов\n"
        f"• Задача: Глубокий технический анализ"
    )
    await send_simple_message(message.chat.id, model_info, message.message_id)

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    """Проверка работы моделей"""
    status_msg = await send_simple_message(
        message.chat.id, 
        "🔄 Проверка моделей\\.\\.\\.",
        message.message_id
    )
    
    if not status_msg:
        return
    
    # Тестовый запрос к Llama
    test_question = "Какая версия Python лучше для ИИ проектов\\? Ответь кратко\\."
    
    try:
        # Тест Llama
        await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
        llama_test = await ask_openrouter(
            test_question.replace('\\', ''),  # Убираем экранирование для API
            OPENROUTER_MODEL_MAIN,
            SYSTEM_PROMPT_MAIN,
            {"max_tokens": 100, "temperature": 0.7}
        )
        
        llama_status = "✅ Работает" if llama_test else "❌ Ошибка"
        
        # Тест DeepSeek
        deepseek_test = await ask_openrouter(
            "Тестовый запрос\\.",
            OPENROUTER_MODEL_DEEPSEEK,
            SYSTEM_PROMPT_DEEPSEEK,
            {"max_tokens": 50, "temperature": 0.7}
        )
        
        deepseek_status = "✅ Работает" if deepseek_test else "❌ Ошибка"
        
        status_text = (
            f"📊 *Статус моделей*\n\n"
            f"**{escape_markdown_v2(OPENROUTER_MODEL_MAIN.split('/')[-1])}**: {llama_status}\n"
            f"**{escape_markdown_v2(OPENROUTER_MODEL_DEEPSEEK.split('/')[-1])}**: {deepseek_status}\n\n"
            f"⏱️ *Лимиты:*\n"
            f"• Llama: до {GENERATION_CONFIG_MAIN['max_tokens']} токенов\n"
            f"• DeepSeek: до {GENERATION_CONFIG_DEEPSEEK['max_tokens']} токенов"
        )
        
        await status_msg.edit_text(status_text, parse_mode="MarkdownV2")
        
    except Exception as e:
        error_msg = escape_markdown_v2(f"⚠️ Ошибка проверки: {str(e)[:200]}")
        await status_msg.edit_text(error_msg, parse_mode="MarkdownV2")

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
        processing_text = (
            "🤔 *Иван Иваныч думает\\.\\.\\.*\n"
            "Две модели ИИ анализируют ваш вопрос\\. Это займёт ~15\\-25 секунд\\."
        )
        processing_msg = await send_simple_message(
            chat_id, 
            processing_text,
            message.message_id
        )
        
        if not processing_msg:
            await send_simple_message(
                chat_id,
                "Начинаю обработку вопроса\\.\\.\\.",
                message.message_id
            )
            processing_msg = None
        
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
            if processing_msg:
                await processing_msg.edit_text(
                    "❌ Не удалось получить ответ от основной модели\\. Попробуйте позже\\.",
                    parse_mode="MarkdownV2"
                )
            else:
                await send_simple_message(
                    chat_id,
                    "❌ Не удалось получить ответ от основной модели\\. Попробуйте позже\\.",
                    message.message_id
                )
            return
        
        # ШАГ 3: Отправляем ответ Llama сразу
        logger.info(f"📤 Llama готов (за {llama_time:.1f}с), отправка...")
        if processing_msg:
            await processing_msg.edit_text(
                "✅ *Первая часть готова\\!*\nDeepSeek завершает анализ\\.\\.\\.",
                parse_mode="MarkdownV2"
            )
        
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
                text=f"**🔍 Глубокий анализ \\(DeepSeek R1\\):**\n\n{deepseek_response}",
                reply_to_message_id=message.message_id
            )
            
            completion_text = (
                f"✅ *Анализ завершён\\!*\n"
                f"⏱️ Общее время: {total_time:.1f} секунд\n"
                f"📊 Llama: {len(llama_response)} символов\n"
                f"🔍 DeepSeek: {len(deepseek_response)} символов"
            )
            
            if processing_msg:
                await processing_msg.edit_text(completion_text, parse_mode="MarkdownV2")
            else:
                await send_simple_message(chat_id, completion_text)
            
            logger.info(f"✅ Успешно! Llama: {len(llama_response)}с, DeepSeek: {len(deepseek_response)}с, Время: {total_time:.1f}с")
            
        else:
            # Если DeepSeek не сработал
            logger.warning("⚠️ DeepSeek не вернул анализ")
            fallback_text = (
                f"✅ *Ответ готов\\!*\n"
                f"⏱️ Время: {total_time:.1f} секунд\n"
                f"ℹ️ DeepSeek временно недоступен, но основной ответ выше\\."
            )
            
            if processing_msg:
                await processing_msg.edit_text(fallback_text, parse_mode="MarkdownV2")
            else:
                await send_simple_message(chat_id, fallback_text)
        
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки")
        await send_simple_message(
            chat_id,
            "⏱️ Время обработки истекло\\. Вопрос слишком сложный или сервисы перегружены\\.",
            message.message_id
        )
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        error_msg = escape_markdown_v2(f"⚠️ Ошибка обработки: {str(e)[:200]}")
        await send_simple_message(chat_id, error_msg, message.message_id, parse_mode="MarkdownV2")

@dp.message()
async def log_all_messages(message: types.Message):
    """Логирует сообщения без '?'"""
    if message.text:
        logger.debug(f"💬 Сообщение без '?' от {message.from_user.id}: {message.text[:50]}...")

# ==================== ЗАПУСК БОТА ====================
async def close_previous_session():
    """Закрывает предыдущую сессию бота через Telegram API"""
    try:
        logger.info("🔄 Закрытие предыдущей сессии бота...")
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/close"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, timeout=5) as response:
                data = await response.json()
                if data.get('ok'):
                    logger.info("✅ Предыдущая сессия закрыта")
                else:
                    logger.warning(f"⚠️ Не удалось закрыть предыдущую сессию: {data}")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при закрытии предыдущей сессии: {e}")

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
        # Закрываем предыдущую сессию в Telegram
        await close_previous_session()
        await asyncio.sleep(2)  # Ждем 2 секунды
        
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