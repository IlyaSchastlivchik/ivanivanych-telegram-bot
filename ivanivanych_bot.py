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

# ==================== УТИЛИТЫ ЭКРАНИРОВАНИЯ ====================
def escape_markdown_v2(text: str) -> str:
    """
    НАДЁЖНОЕ экранирование ВСЕХ спецсимволов для MarkdownV2.
    """
    # Сначала экранируем обратные слеши
    text = text.replace('\\', '\\\\')
    
    # Затем экранируем все остальные спецсимволы MarkdownV2
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    return text

def split_message_smart(text: str, max_length: int = 3500) -> List[str]:
    """
    Умное разбиение сообщения на части с сохранением структуры.
    3500 вместо 4096, так как при экранировании текст становится длиннее.
    """
    if len(text) <= max_length:
        return [text]
    
    parts = []
    
    # Пытаемся разбить по абзацам
    paragraphs = text.split('\n\n')
    current_part = ""
    
    for para in paragraphs:
        # Проверяем, не превысит ли добавление параграфа лимит
        if len(current_part) + len(para) + 2 <= max_length:
            current_part += para + "\n\n"
        else:
            if current_part:
                parts.append(current_part.strip())
            # Если один параграф слишком длинный, разбиваем его по строкам
            if len(para) > max_length:
                lines = para.split('\n')
                current_lines = ""
                for line in lines:
                    if len(current_lines) + len(line) + 1 <= max_length:
                        current_lines += line + "\n"
                    else:
                        if current_lines:
                            parts.append(current_lines.strip())
                        current_lines = line + "\n"
                if current_lines:
                    current_part = current_lines.strip() + "\n\n"
            else:
                current_part = para + "\n\n"
    
    if current_part:
        parts.append(current_part.strip())
    
    return parts

async def send_safe_message(chat_id: int, text: str, reply_to_message_id: int = None, 
                           parse_mode: str = "MarkdownV2") -> Optional[types.Message]:
    """
    БЕЗОПАСНАЯ отправка сообщений с автоматическим fallback.
    """
    # Попытка 1: С MarkdownV2
    try:
        escaped_text = escape_markdown_v2(text)
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
        kwargs = {
            "chat_id": chat_id,
            "text": text,
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
        escaped_text = escape_markdown_v2(text)
        await message.edit_text(escaped_text, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.warning(f"⚠️ Не удалось отредактировать с MarkdownV2: {e}")
    
    # Попытка 2: Без форматирования
    try:
        await message.edit_text(text, parse_mode=None)
        return True
    except Exception as e:
        logger.error(f"❌ Не удалось отредактировать сообщение вообще: {e}")
        return False

async def send_long_message(chat_id: int, text: str, reply_to_message_id: int = None):
    """
    Отправка длинных сообщений с правильным разбиением на части.
    """
    # Экранируем текст
    escaped_text = escape_markdown_v2(text)
    
    # Умное разбиение на части
    parts = split_message_smart(escaped_text, max_length=3500)
    
    logger.info(f"📤 Отправка сообщения из {len(parts)} частей...")
    
    for i, part in enumerate(parts):
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
                break  # Успешно отправили
                
            except Exception as e:
                logger.error(f"❌ Попытка {attempt+1}/{max_attempts}: Ошибка при отправке части {i+1}: {e}")
                
                if attempt == max_attempts - 1:  # Последняя попытка
                    # Фоллбэк без форматирования
                    try:
                        # Убираем экранирование для plain text
                        plain_text = part.replace('\\\\', '\\')
                        plain_text = re.sub(r'\\([_*\[\]()~`>#+\-=|{}.!])', r'\1', plain_text)
                        
                        plain_kwargs = {
                            "chat_id": chat_id,
                            "text": f"Часть {i+1}/{len(parts)}:\n\n{plain_text[:3000]}",
                            "parse_mode": None
                        }
                        
                        if i == 0 and reply_to_message_id:
                            plain_kwargs["reply_to_message_id"] = reply_to_message_id
                        
                        await bot.send_message(**plain_kwargs)
                    except Exception as e2:
                        logger.error(f"❌ Не удалось отправить даже без форматирования: {e2}")
                
                await asyncio.sleep(0.5)
        
        if i < len(parts) - 1:
            await asyncio.sleep(0.3)  # Небольшая задержка между частями

# ==================== СИСТЕМНЫЕ ПРОМПТЫ ====================
SYSTEM_PROMPT_MAIN = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в футуристике и технологиях будущего. "
        "Отвечай ясно, по делу, с технической точностью. "
        "НЕ используй Markdown разметку, LaTeX (\\( \\)) или специальные символы в ответах. "
        "Используй только обычный текст. "
        "Длина ответа не должна превышать 1500 символов."
    )
}

SYSTEM_PROMPT_DEEPSEEK = {
    "role": "system",
    "content": (
        "Ты — технический аналитик. Ответь на вопрос пользователя самостоятельно, "
        "предоставив глубокий анализ, конкретные детали и практические шаги. "
        "НЕ используй Markdown разметку, LaTeX (\\( \\)) или специальные символы в ответах. "
        "Используй только обычный текст. "
        "Длина ответа не должна превышать 1500 символов. "
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
            
            # БЕЗОПАСНОЕ редактирование статусного сообщения
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
            
            await edit_safe_message(processing_msg, completion_text)
            logger.info(f"✅ Успешно! Время: {total_time:.1f}с")
            
        else:
            # Если DeepSeek не ответил
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
            # Важно: при ошибке используем send_safe_message, а не edit_safe_message
            await send_safe_message(chat_id, error_text)

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