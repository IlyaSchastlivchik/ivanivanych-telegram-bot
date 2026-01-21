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
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"  # Креативная и мощная модель

# Настройки генерации
GENERATION_CONFIG = {
    "temperature": 0.9,       # Высокая креативность для фантазийных ответов
    "max_tokens": 2000,       # Достаточно для развернутых ответов
    "top_p": 0.95,
    "frequency_penalty": 0.2,
    "presence_penalty": 0.1,
}

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# ==================== СИСТЕМНЫЙ ПРОМПТ ====================
SYSTEM_PROMPT = {
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

# ==================== УТИЛИТЫ ====================
def safe_prepare_for_markdown_v2(text: str) -> str:
    """
    Разделяет текст на блоки кода и обычный текст.
    Экранирует только обычный текст для корректной работы parse_mode='MarkdownV2'.
    Это гарантирует подсветку синтаксиса в блоках кода.
    """
    # Шаблон для нахождения блоков кода с указанием языка (```python) или без (```)
    pattern = r'(```[\w]*\n[\s\S]*?\n```)'
    parts = re.split(pattern, text)
    result_parts = []
    
    # Символы, которые нужно экранировать для MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    
    for i, part in enumerate(parts):
        # Нечётные части - это блоки кода (оставляем как есть)
        if i % 2 == 1:
            result_parts.append(part)
        else:
            # Чётные части - обычный текст, экранируем спецсимволы
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
            # Если одно предложение длиннее max_length, разбиваем его
            if len(sentence) > max_length:
                # Особое внимание на незакрытые блоки кода
                if '```' in sentence:
                    # Переносим блок кода целиком в новую часть
                    if current_part.count('```') % 2 != 0:
                        # Если в текущей части нечетное количество ```, закрываем блок
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
    Каждая часть предварительно обрабатывается для MarkdownV2.
    """
    # Сначала обрабатываем весь текст для MarkdownV2
    processed_text = safe_prepare_for_markdown_v2(text)
    
    # Разбиваем на части
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
            
            # Небольшая задержка между частями
            if i < len(parts) - 1:
                await asyncio.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Ошибка при отправке части {i+1}/{len(parts)}: {e}")

# ==================== OPENROUTER ФУНКЦИЯ ====================
async def ask_openrouter(user_question: str) -> Optional[str]:
    """
    Асинхронная функция для запроса к OpenRouter API
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/freenergy2",
        "X-Title": "IvanIvanych Bot",
    }
    
    data = {
        "model": OPENROUTER_MODEL,
        "messages": [
            SYSTEM_PROMPT,
            {"role": "user", "content": user_question}
        ],
        **GENERATION_CONFIG
    }
    
    logger.info(f"Отправка запроса к модели {OPENROUTER_MODEL}")
    
    # Увеличиваем таймаут для сложных запросов
    timeout = aiohttp.ClientTimeout(total=180)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(OPENROUTER_URL, headers=headers, json=data) as response:
                
                if response.status == 200:
                    result = await response.json()
                    if 'choices' in result and len(result['choices']) > 0:
                        response_text = result['choices'][0]['message']['content'].strip()
                        logger.info(f"Получен ответ длиной {len(response_text)} символов")
                        return response_text
                    else:
                        logger.error(f"Неожиданный формат ответа API")
                        return None
                        
                elif response.status == 429:
                    logger.warning("Превышен лимит запросов OpenRouter (429)")
                    return "Сейчас у меня много запросов. Подождите минуту и попробуйте снова."
                    
                elif response.status == 502:
                    logger.warning("Проблема с моделью (502 Bad Gateway)")
                    return "Модель временно недоступна. Попробуйте переформулировать вопрос."
                    
                elif response.status == 504:
                    logger.warning("Таймаут от модели (504 Gateway Timeout)")
                    return "Модель слишком долго думает над ответом. Попробуйте задать более конкретный вопрос."
                    
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка API [{response.status}]: {error_text[:200]}")
                    return f"Ошибка сервиса (код {response.status}). Попробуйте позже."
                    
    except asyncio.TimeoutError:
        logger.error("Таймаут запроса к OpenRouter")
        return "Запрос выполняется слишком долго. Попробуйте задать более конкретный вопрос."
        
    except aiohttp.ClientConnectorError as e:
        logger.error(f"Ошибка подключения: {e}")
        return "Проблемы с интернет-соединением."
        
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        return "Внутренняя ошибка. Попробуйте переформулировать вопрос."

# ==================== ОБРАБОТЧИКИ СООБЩЕНИЙ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    welcome_text = (
        "👋 Привет! Я Иван Иваныч — ваш собеседник по футуристике и технологиям.\n\n"
        "*Как задавать вопросы:*\n"
        "• Заканчивайте вопрос знаком вопроса (?)\n"
        "• Будьте конкретны\n"
        "• Сложные вопросы разбивайте на части\n\n"
        "*Доступные команды:*\n"
        "/help - справка\n"
        "/model - текущая модель ИИ\n"
        "/tips - как лучше задавать вопросы"
    )
    # Для простых сообщений без кода также используем MarkdownV2
    await message.answer(welcome_text, parse_mode='MarkdownV2')

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = (
        "🤖 *Помощь по боту*\n\n"
        "Я отвечаю только на вопросы, которые заканчиваются знаком *?*\n\n"
        "⚠️ *Если ответ долго не приходит:*\n"
        "1. Проверьте интернет-соединение\n"
        "2. Задайте более конкретный вопрос\n"
        "3. Разбейте сложный вопрос на части\n\n"
        f"📊 *Текущая модель:* {OPENROUTER_MODEL}"
    )
    await message.answer(help_text, parse_mode='MarkdownV2')

@dp.message(Command("model"))
async def cmd_model(message: types.Message):
    """Показывает текущую модель ИИ"""
    model_info = (
        f"📊 *Текущая модель:* {OPENROUTER_MODEL}\n\n"
        "⚙️ *Настройки генерации:*\n"
        f"• Максимальная длина: {GENERATION_CONFIG['max_tokens']} токенов\n"
        f"• Температура: {GENERATION_CONFIG['temperature']}\n"
        f"• Top-p: {GENERATION_CONFIG['top_p']}"
    )
    await message.answer(model_info, parse_mode='MarkdownV2')

@dp.message(Command("tips"))
async def cmd_tips(message: types.Message):
    """Советы по задаванию вопросов"""
    tips_text = (
        "💡 *Советы для лучших ответов:*\n\n"
        "1. *Будьте конкретны*\n"
        "   ❌ «Расскажи про технологии»\n"
        "   ✅ «Какие технологии изменят транспорт к 2040 году?»\n\n"
        "2. *Разбивайте сложные вопросы*\n"
        "   ❌ «Как создать умный город с нуля?»\n"
        "   ✅ «Какие энергосистемы нужны для умного города?»\n\n"
        "3. *Используйте примеры*\n"
        "   ❌ «Объясни ИИ»\n"
        "   ✅ «Как ИИ поможет в медицинской диагностике?»"
    )
    await message.answer(tips_text, parse_mode='MarkdownV2')

@dp.message(lambda msg: msg.text and msg.text.strip().endswith('?'))
async def handle_question(message: types.Message):
    """Основной обработчик вопросов"""
    user_question = message.text.strip()
    chat_id = message.chat.id
    
    # Показываем статус "печатает"
    await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    
    # Логируем вопрос
    username = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    logger.info(f"Вопрос от {username}: {user_question[:100]}...")
    
    try:
        # Получаем ответ от ИИ
        ai_response = await ask_openrouter(user_question)
        
        if not ai_response:
            await message.reply("Не удалось получить ответ. Попробуйте переформулировать вопрос.")
            return
        
        # Отправляем ответ с поддержкой MarkdownV2 и разбивкой на части
        await send_long_message(
            chat_id=chat_id,
            text=ai_response,
            reply_to_message_id=message.message_id
        )
        
        # Логируем успешный ответ
        logger.info(f"Ответ успешно отправлен пользователю {username}")
        
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
    logger.info(f"Модель: {OPENROUTER_MODEL}")
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