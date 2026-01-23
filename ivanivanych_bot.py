# ==================== НОВЫЙ МОДУЛЬ: ФОРМАТИРОВАНИЕ ФОРМУЛ ====================
import re
from typing import Tuple, List

def convert_latex_to_unicode(latex_formula: str) -> str:
    """
    Конвертирует LaTeX формулы в Unicode-представление для Telegram.
    Это приблизительное представление, но читаемое.
    """
    # Упрощенные замены для основных математических символов
    replacements = [
        (r'\\cdot', '·'),
        (r'\\times', '×'),
        (r'\\div', '÷'),
        (r'\\pm', '±'),
        (r'\\mp', '∓'),
        (r'\\leq', '≤'),
        (r'\\geq', '≥'),
        (r'\\neq', '≠'),
        (r'\\approx', '≈'),
        (r'\\infty', '∞'),
        (r'\\pi', 'π'),
        (r'\\alpha', 'α'),
        (r'\\beta', 'β'),
        (r'\\gamma', 'γ'),
        (r'\\delta', 'δ'),
        (r'\\epsilon', 'ε'),
        (r'\\theta', 'θ'),
        (r'\\lambda', 'λ'),
        (r'\\mu', 'μ'),
        (r'\\sigma', 'σ'),
        (r'\\omega', 'ω'),
        (r'\\sum', 'Σ'),
        (r'\\int', '∫'),
        (r'\\oint', '∮'),
        (r'\\nabla', '∇'),
        (r'\\partial', '∂'),
        (r'\\sqrt', '√'),
        (r'\\frac{(.*?)}{(.*?)}', r'\1/\2'),  # Простые дроби
        (r'\^\{([^}]+)\}', r'^\1'),  # Верхние индексы без скобок
        (r'_\{([^}]+)\}', r'_\1'),  # Нижние индексы без скобок
        (r'\\vec\{([^}]+)\}', r'\1⃗'),  # Векторы
        (r'\\overline\{([^}]+)\}', r'\1̄'),  # Черта сверху
        (r'\\text\{([^}]+)\}', r'\1'),  # Текст
        (r'\\left\(', '('),
        (r'\\right\)', ')'),
        (r'\\left\[', '['),
        (r'\\right\]', ']'),
        (r'\\ ', ' '),
        (r'\\,', ' '),
        (r'\\quad', '  '),
        (r'\\qquad', '    '),
        (r'\\;', ' '),
        (r'\\(?:sin|cos|tan|log|ln|exp)', lambda m: m.group(0)[1:]),  # Функции без обратного слэша
    ]
    
    result = latex_formula
    
    for pattern, replacement in replacements:
        if callable(replacement):
            result = re.sub(pattern, replacement, result)
        else:
            result = result.replace(pattern, replacement)
    
    # Убираем лишние пробелы и упрощаем
    result = re.sub(r'\s+', ' ', result).strip()
    
    return result

def format_formula_for_telegram(latex_formula: str, is_inline: bool = False) -> str:
    """
    Форматирует формулу для Telegram с использованием MarkdownV2
    """
    # Упрощаем LaTeX до Unicode
    unicode_formula = convert_latex_to_unicode(latex_formula)
    
    if is_inline:
        # Для inline формул используем моноширинный шрифт
        return f'`{unicode_formula}`'
    else:
        # Для блочных формул используем блок кода
        return f'```\n{unicode_formula}\n```'

def process_formulas_in_text_new(text: str) -> str:
    """
    Новая обработка формул: преобразование LaTeX в читаемый Unicode + MarkdownV2
    """
    # Защищаем блоки кода от обработки
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    text = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    # Обрабатываем формулы в тегах [f]
    def replace_formula(match):
        latex_content = match.group(1).strip()
        
        # Определяем, inline или блочная формула
        # Если содержит \displaystyle, \sum, \int, \frac - считаем блочной
        is_inline = not any(pattern in latex_content for pattern in [
            '\\displaystyle', '\\sum', '\\int', '\\begin', '\\frac{', '\\lim'
        ])
        
        # Форматируем для Telegram
        formatted = format_formula_for_telegram(latex_content, is_inline)
        
        # Для сложных формул добавляем примечание
        if not is_inline and len(latex_content) > 50:
            formatted += f"\n\n*Для точного отображения скопируйте LaTeX:*\n`{latex_content}`"
        
        return formatted
    
    # Заменяем [f]...[/f] на форматированный текст
    pattern = r'\[f\](.*?)\[/f\]'
    text = re.sub(pattern, replace_formula, text, flags=re.DOTALL)
    
    # Восстанавливаем блоки кода
    for i, code_block in enumerate(code_blocks):
        text = text.replace(f'__CODE_BLOCK_{i}__', code_block)
    
    return text

# ==================== ОБНОВЛЕННАЯ prepare_markdown_message ====================
def prepare_markdown_message_with_formulas(text: str) -> str:
    """Подготовка Markdown сообщения с поддержкой формул"""
    text = clean_text(text)
    
    # Обрабатываем формулы
    text = process_formulas_in_text_new(text)
    
    # Сначала защищаем блоки кода (включая те, что получились из формул)
    code_blocks = []
    def save_code_block(match):
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    
    # Захватываем блоки кода с тройными кавычками
    text = re.sub(r'```[\s\S]*?```', save_code_block, text)
    
    # Теперь защищаем inline код (одинарные кавычки)
    inline_codes = []
    def save_inline_code(match):
        inline_codes.append(match.group(0))
        return f"__INLINE_CODE_{len(inline_codes)-1}__"
    
    text = re.sub(r'`[^`\n]+`', save_inline_code, text)
    
    # Экранируем специальные символы MarkdownV2
    chars_to_escape = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    for char in chars_to_escape:
        text = text.replace(char, '\\' + char)
    
    # Восстанавливаем inline код
    for i, inline_code in enumerate(inline_codes):
        text = text.replace(f'__INLINE_CODE_{i}__', inline_code)
    
    # Восстанавливаем блоки кода
    for i, code_block in enumerate(code_blocks):
        text = text.replace(f'__CODE_BLOCK_{i}__', code_block)
    
    return text

# ==================== ОБНОВЛЕННАЯ send_message_safe ====================
async def send_message_safe(chat_id: int, text: str, reply_to_message_id: int = None) -> Optional[types.Message]:
    """Умная отправка сообщений с поддержкой формул"""
    try:
        # Сначала пробуем MarkdownV2 с поддержкой формул
        markdown_text = prepare_markdown_message_with_formulas(text)
        
        kwargs = {
            "chat_id": chat_id,
            "text": markdown_text,
            "parse_mode": "MarkdownV2"
        }
        if reply_to_message_id:
            kwargs["reply_to_message_id"] = reply_to_message_id
        
        result = await bot.send_message(**kwargs)
        logger.info(f"✅ Сообщение отправлено с MarkdownV2+формулами, длина: {len(text)} символов")
        return result
        
    except Exception as e:
        logger.warning(f"⚠️ MarkdownV2 с формулами не сработал: {e}, пробую HTML...")
        
        try:
            # Fallback: HTML без формул
            text_clean = clean_text(text)
            
            # Простая обработка кода для HTML
            def restore_code_simple(match):
                code_content = match.group(2)
                return f'<pre><code>{code_content}</code></pre>'
            
            text_clean = re.sub(r'```(\w*)\n([\s\S]*?)\n```', restore_code_simple, text_clean)
            
            # Убираем теги [f], оставляем только содержимое
            text_clean = re.sub(r'\[f\](.*?)\[/f\]', r'\1', text_clean)
            
            # Экранируем HTML
            text_clean = html.escape(text_clean)
            
            kwargs = {
                "chat_id": chat_id,
                "text": text_clean,
                "parse_mode": "HTML"
            }
            if reply_to_message_id:
                kwargs["reply_to_message_id"] = reply_to_message_id
            
            result = await bot.send_message(**kwargs)
            logger.info(f"✅ Сообщение отправлено с HTML, длина: {len(text)} символов")
            return result
            
        except Exception as e2:
            logger.warning(f"⚠️ HTML не сработал: {e2}, пробую без форматирования...")
            
            try:
                # Отправляем без форматирования, но с обработкой формул
                cleaned_text = clean_text(text)
                
                # Простая замена тегов [f] на читаемый вид
                def replace_formula_simple(match):
                    latex_content = match.group(1)
                    unicode_formula = convert_latex_to_unicode(latex_content)
                    return f" [Формула: {unicode_formula}] "
                
                cleaned_text = re.sub(r'\[f\](.*?)\[/f\]', replace_formula_simple, cleaned_text)
                
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
                
            except Exception as e3:
                logger.error(f"❌ Не удалось отправить сообщение: {e3}")
                return None

# ==================== ОБНОВЛЕННЫЕ ПРОМТЫ ====================
MAIN_SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "Ты Иван Иваныч — эксперт в технологиях и футуристике. "
        "Отвечай ясно и по делу. Используй Markdown для форматирования.\n\n"
        "**ВАЖНО ДЛЯ ФОРМУЛ:**\n"
        "1. ВСЕ математические формулы оборачивай в теги [f] и [/f]\n"
        "2. Пример: [f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n"
        "3. Для кода используй тройные кавычки с указанием языка.\n"
        "4. Всегда закрывай блок кода и теги [f].\n"
        "5. Пиши формулы в LaTeX, но старайся использовать простой синтаксис.\n"
        "6. Избегай сложных LaTeX конструкций, если можно упростить.\n\n"
        "Держи ответ в 800-1500 символов."
    )
}

DEEPSEEK_SYSTEM_PROMPT = {
    "role": "system",
    "content": (
        "Ты технический аналитик. Дай глубокий анализ с практическими шагами.\n\n"
        "**ВАЖНО ДЛЯ ФОРМУЛ:**\n"
        "1. ВСЕ математические выражения оборачивай в теги [f] и [/f]\n"
        "2. Пример правильного использования:\n"
        "   - Формула работы: [f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n"
        "   - Интеграл: [f]\\int_{a}^{b} f(x) dx[/f]\n"
        "3. Для кода используй тройные кавычки с языком.\n"
        "4. Всегда проверяй баланс тегов.\n"
        "5. Используй простой LaTeX синтаксис для лучшей читаемости.\n\n"
        "Будь конкретным и техничным. Отвечай развернуто (1000-1800 символов)."
    )
}

# ==================== ОБНОВЛЕННАЯ ФУНКЦИЯ get_ai_response ====================
async def get_ai_response(user_question: str, response_type: str = "main") -> Tuple[Optional[str], Optional[str], int]:
    """Получает ответ от AI с поддержкой формул"""
    if response_type == "main":
        models = [
            MODELS_CONFIG["main"]["primary"],
            MODELS_CONFIG["main"]["backup"],
            MODELS_CONFIG["main"]["fallback"],
            MODELS_CONFIG["main"]["emergency"]
        ]
        system_prompt = MAIN_SYSTEM_PROMPT
    else:
        models = [
            MODELS_CONFIG["deepseek"]["primary"],
            MODELS_CONFIG["deepseek"]["backup"],
            MODELS_CONFIG["deepseek"]["fallback"],
            MODELS_CONFIG["deepseek"]["emergency"]
        ]
        system_prompt = DEEPSEEK_SYSTEM_PROMPT
    
    response, model_used, code_blocks = await try_model_with_retry(models, user_question, system_prompt)
    
    if not response:
        logger.warning("⚠️ Все модели не ответили, использую локальный fallback")
        response = get_local_fallback_response(user_question)
        model_used = "local_fallback"
        code_blocks = len(re.findall(r'```(?:[\w]*)\n[\s\S]*?\n```', response))
    
    return response, model_used, code_blocks

# ==================== ОБНОВЛЕННЫЙ LOCAL_RESPONSES ====================
LOCAL_RESPONSES = {
    "технология": [
        "🤖 **ИИ-анализ технологии**\n\n"
        "Для внедрения ИИ от SberVision в Telegram группу:\n\n"
        "1. **Получите API ключ SberVision** на developer.sber.ru\n"
        "2. **Создайте Telegram бота** через @BotFather\n"
        "3. **Настройте вебхук** для обработки сообщений\n"
        "4. **Интегрируйте SberVision API** для распознавания\n"
        "5. **Добавьте логику** обработки изображений и формул\n\n"
        "```python\n# Пример обработки изображения\nimport requests\n\ndef recognize_image(image_url):\n    api_key = 'YOUR_API_KEY'\n    response = requests.post(\n        'https://api.sber.dev/vision/v1/recognize',\n        json={'image': image_url},\n        headers={'Authorization': f'Bearer {api_key}'}\n    )\n    return response.json()\n```\n\n"
        "🔧 **Следующие шаги**: Настройка базы данных для хранения результатов и добавление панели администратора."
    ],
    "код": [
        "💻 **Пример кода для Telegram бота с SberVision**\n\n"
        "```python\nimport telebot\nimport requests\nimport json\n\n# Настройки\nTOKEN = 'YOUR_BOT_TOKEN'\nSBER_API_KEY = 'YOUR_SBER_VISION_KEY'\n\nbot = telebot.TeleBot(TOKEN)\n\n@bot.message_handler(content_types=['photo'])\ndef handle_photo(message):\n    file_id = message.photo[-1].file_id\n    file_info = bot.get_file(file_id)\n    file_url = f'https://api.telegram.org/file/bot{TOKEN}/{file_info.file_path}'\n    \n    response = requests.post(\n        'https://api.sber.dev/vision/v1/ocr',\n        headers={'Authorization': f'Bearer {SBER_API_KEY}'},\n        json={'image_url': file_url}\n    )\n    \n    if response.status_code == 200:\n        result = response.json()\n        text = result.get('text', 'Текст не распознан')\n        bot.reply_to(message, f'📝 Распознанный текст:\\n{text}')\n    else:\n        bot.reply_to(message, '❌ Ошибка распознавания')\n\nbot.polling()\n```\n\n"
        "📁 **Структура проекта**:\n"
        "```\nproject/\n├── bot.py\n├── config.py\n├── sber_vision.py\n├── database.py\n└── requirements.txt\n```"
    ],
    "общий": [
        "🧠 **Анализ запроса**\n\n"
        "Для распознавания текста и формул в Telegram группе:\n\n"
        "[f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n\n"
        "**Этапы внедрения:**\n"
        "1. **Подготовка инфраструктуры**\n"
        "   - Сервер/VPS с Python 3.8+\n"
        "   - База данных (PostgreSQL/Redis)\n"
        "   - SSL сертификат для вебхуков\n\n"
        "2. **Интеграция API**\n"
        "   - SberVision для OCR и распознавания формул\n"
        "   - Telegram Bot API\n"
        "   - Дополнительные сервисы (если нужно)\n\n"
        "3. **Разработка логики**\n"
        "   - Обработка изображений\n"
        "   - Парсинг математических выражений\n"
        "   - Хранение и поиск результатов\n\n"
        "4. **Тестирование и деплой**\n"
        "   - Юнит-тесты\n"
        "   - Нагрузочное тестирование\n"
        "   - Мониторинг и логирование\n\n"
        "⏱️ **Примерные сроки**: 2-3 недели для MVP"
    ]
}

# ==================== ОБНОВЛЕННАЯ КОМАНДА /start ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Команда /start"""
    welcome = (
        "👋 Привет! Я Иван Иваныч — бот с продвинутыми ИИ-моделями\n\n"
        "🚀 **Новые мощные модели:**\n"
        "• **Qwen3 Next 80B** — самая мощная бесплатная модель\n"
        "• **Gemma 3 4B** — быстрая и эффективная\n"
        "• **DeepSeek R1** — глубокая аналитика\n\n"
        "🤖 **НОВИНКА: Поддержка формул в Unicode!**\n"
        "• Интеллектуальное преобразование LaTeX → Unicode\n"
        "• Формулы в моноширинном шрифте для читаемости\n"
        "• Автоматическое экранирование MarkdownV2\n\n"
        "⚡ **Примеры:**\n"
        "• Простая формула: [f]A = F \\cdot s \\cdot \\cos(\\alpha)[/f]\n"
        "• Сложная формула: [f]\\int_{a}^{b} f(x) dx[/f]\n"
        "• Код с подсветкой:\n"
        "```python\nprint('Привет, мир!')\n```\n\n"
        "❓ Просто задайте вопрос с '?' в конце"
    )
    await send_message_safe(message.chat.id, welcome, message.message_id)