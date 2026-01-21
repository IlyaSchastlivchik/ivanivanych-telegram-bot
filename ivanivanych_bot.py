import asyncio
import logging
import os
import aiohttp
import re
import time
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

# ✅ ИСПРАВЛЕНИЕ 1: Рабочая модель DeepSeek
OPENROUTER_MODEL_DEEPSEEK = "deepseek/deepseek-r1-0528:free"  # Было: "deepseek/deepseek-r1:free"

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

# ==================== УТИЛИТЫ ЭКРАНИРОВАНИЯ ====================
def escape_markdown_v2(text: str) -> str:
    """
    Экранирует текст для MarkdownV2 в Telegram.
    """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """Отправляет длинное сообщение с правильным экранированием"""
    # ✅ ИСПРАВЛЕНИЕ 2: Двойное экранирование для заголовков
    processed_text = escape_markdown_v2(text)
    
    # Разбиваем на части если слишком длинное
    if len(processed_text) > 3800:
        parts = [processed_text[i:i+3800] for i in range(0, len(processed_text), 3800)]
    else:
        parts = [processed_text]
    
    for i, part in enumerate(parts):
        try:
            send_kwargs = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "MarkdownV2"
            }
            
            if i == 0 and reply_to_message_id:
                send_kwargs["reply_to_message_id"] = reply_to_message_id
            
            await bot.send_message(**send_kwargs)
            if i < len(parts) - 1:
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке части: {e}")
            # ✅ ИСПРАВЛЕНИЕ 3: Отправка без форматирования при ошибке
            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"Часть {i+1}:\n\n{escape_markdown_v2(part)[:1000]}",
                    parse_mode=None
                )
            except Exception as e2:
                logger.error(f"❌ Не удалось отправить даже без форматирования: {e2}")

async def send_simple_message(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Универсальная функция для отправки простых сообщений"""
    try:
        escaped_text = escape_markdown_v2(text)
        return await bot.send_message(
            chat_id=chat_id,
            text=escaped_text,
            parse_mode="MarkdownV2",
            reply_to_message_id=reply_to_message_id
        )
    except Exception as e:
        logger.error(f"❌ Ошибка отправки сообщения: {e}")
        # Отправка без форматирования при ошибке
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=None,
                reply_to_message_id=reply_to_message_id
            )
        except Exception as e2:
            logger.error(f"❌ Не удалось отправить сообщение вообще: {e2}")
            return None

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в футуристике и технологиях будущего. "
        "Отвечай ясно, по делу, с технической точностью."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — технический аналитик. Ответь на вопрос пользователя самостоятельно, "
        "предоставив глубокий анализ, конкретные детали и практические шаги. "
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
    
    # Увеличенные таймауты для стабильности
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
    
    # Ждём оба ответа параллельно
    llama_response, deepseek_response = await asyncio.gather(
        llama_task, 
        deepseek_task,
        return_exceptions=True
    )
    
    # Обработка исключений
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
        "👋 Привет\\! Я Иван Иваныч\n\n"
        "🤖 Две модели ИИ работают параллельно:\n"
        "• Llama 3\\.3 — быстрый основной ответ\n"
        "• DeepSeek R1 — глубокий технический анализ\n\n"
        "⚡ Оба ответа генерируются одновременно\\!\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_simple_message(message.chat.id, welcome_text, message.message_id)

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
        processing_text = "🤔 Две модели ИИ анализируют вопрос параллельно\\.\\.\\."
        processing_msg = await send_simple_message(chat_id, processing_text, message.message_id)
        
        start_total_time = time.time()
        
        # ШАГ 2: ПАРАЛЛЕЛЬНЫЙ ЗАПРОС К ОБЕИМ МОДЕЛЯМ
        logger.info("⚡ Параллельные запросы запущены...")
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        
        llama_response, deepseek_response = await get_responses_parallel(user_question)
        
        # ШАГ 3: СНАЧАЛА ОТПРАВЛЯЕМ ОТВЕТ LLAMA
        if llama_response:
            llama_time = time.time() - start_total_time
            logger.info(f"📤 Отправка ответа Llama (за {llama_time:.1f}с)...")
            
            if processing_msg:
                await processing_msg.edit_text(
                    "✅ Llama ответил\\! Готовим анализ DeepSeek\\.\\.\\.",
                    parse_mode="MarkdownV2"
                )
            
            # Экранируем заголовок отдельно
            header = "**🤖 Ответ Llama 3\\.3:**"
            await send_long_message(
                chat_id=chat_id,
                text=f"{header}\n\n{llama_response}",
                reply_to_message_id=message.message_id
            )
        else:
            logger.error("❌ Llama не ответил")
            if processing_msg:
                await processing_msg.edit_text(
                    "❌ Основная модель не ответила\\. Попробуйте позже\\.",
                    parse_mode="MarkdownV2"
                )
            return
        
        # ШАГ 4: ПОТОМ ОТПРАВЛЯЕМ ОТВЕТ DEEPSEEK (ЕСЛИ ЕСТЬ)
        if deepseek_response and len(deepseek_response) > 50:
            logger.info("📤 Отправка ответа DeepSeek...")
            header = "**🔍 Глубокий анализ DeepSeek R1:**"
            await send_long_message(
                chat_id=chat_id,
                text=f"{header}\n\n{deepseek_response}",
                reply_to_message_id=message.message_id
            )
            
            total_time = time.time() - start_total_time
            completion_text = (
                f"✅ Анализ завершён\\!\n"
                f"⏱️ Общее время: {total_time:.1f} секунд\n"
                f"📊 Llama: {len(llama_response)} символов\n"
                f"🔍 DeepSeek: {len(deepseek_response)} символов"
            )
            
            if processing_msg:
                await processing_msg.edit_text(completion_text, parse_mode="MarkdownV2")
            else:
                await send_simple_message(chat_id, completion_text)
            
            logger.info(f"✅ Успешно! Время: {total_time:.1f}с")
        else:
            # Если DeepSeek не ответил
            logger.warning("⚠️ DeepSeek не вернул ответ")
            total_time = time.time() - start_total_time
            fallback_text = (
                f"✅ Основной ответ готов\\!\n"
                f"⏱️ Время: {total_time:.1f} секунд\n"
                f"ℹ️ DeepSeek временно недоступен"
            )
            
            if processing_msg:
                await processing_msg.edit_text(fallback_text, parse_mode="MarkdownV2")
        
    except asyncio.TimeoutError:
        logger.error("⏱️ Общий таймаут обработки")
        timeout_text = "⏱️ Время обработки истекло\\. Попробуйте позже\\."
        if processing_msg:
            await processing_msg.edit_text(timeout_text, parse_mode="MarkdownV2")
        else:
            await send_simple_message(chat_id, timeout_text, message.message_id)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        error_text = escape_markdown_v2(f"⚠️ Ошибка обработки: {str(e)[:200]}")
        if processing_msg:
            await processing_msg.edit_text(error_text, parse_mode="MarkdownV2")
        else:
            await send_simple_message(chat_id, error_text, message.message_id)

# ==================== ЗАПУСК БОТА ====================
async def main():
    """Основная функция запуска"""
    logger.info("=" * 60)
    logger.info(f"🚀 Бот IvanIvanych запускается...")
    logger.info(f"🤖 Модели: {OPENROUTER_MODEL_MAIN} + {OPENROUTER_MODEL_DEEPSEEK}")
    logger.info(f"⚡ Архитектура: Параллельная генерация")
    logger.info("=" * 60)
    
    try:
        # Очищаем предыдущие обновления
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("🔄 Очищены предыдущие обновления")
        
        # Запускаем polling
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