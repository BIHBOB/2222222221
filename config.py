import os
from pathlib import Path
import logging
import sys
import pytz
from datetime import datetime, timedelta

# Base directory
BASE_DIR = Path(__file__).resolve().parent

# Database settings
DB_PATH = BASE_DIR / 'vk_parser.db'
# Приоритет у переменной окружения DATABASE_URL для production
DB_URI = os.environ.get('DATABASE_URL', f'sqlite:///{DB_PATH}')

# Directory settings - используем абсолютные пути для production
UPLOAD_DIR = BASE_DIR / 'uploads'
RESULTS_DIR = BASE_DIR / 'results'
LOG_DIR = BASE_DIR / 'logs'

# Create necessary directories
UPLOAD_DIR.mkdir(exist_ok=True)
RESULTS_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# App environment
ENV = os.environ.get('FLASK_ENV', 'development')
DEBUG = ENV == 'development'

# VK API settings
VK_TOKEN = os.environ.get('VK_TOKEN', '')

# Timezone setting - Moscow time (UTC+3)
MOSCOW_TZ = pytz.timezone('Europe/Moscow')
UTC_TZ = pytz.UTC

# Вспомогательные функции для работы с часовыми поясами
def get_now_moscow():
    """Возвращает текущее время в московском часовом поясе"""
    return datetime.now(MOSCOW_TZ)

def to_moscow_time(dt):
    """Возвращает время в московском часовом поясе.
    Если входное время без часового пояса, считаем его временем в Москве.
    Если входное время с часовым поясом, конвертируем его в московское.
    """
    if dt is None:
        return None
    
    # Если время без часового пояса, считаем его сразу московским
    if dt.tzinfo is None:
        return dt.replace(tzinfo=MOSCOW_TZ)
    
    # Если время с другим часовым поясом, конвертируем его в московское
    return dt.astimezone(MOSCOW_TZ)

def utc_to_moscow(dt):
    """Конвертирует время UTC (без tzinfo) в московское время (тоже без tzinfo)
    для хранения в БД"""
    if dt is None:
        return None
    
    # 1. Если у времени нет tzinfo, считаем его UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC_TZ)
    
    # 2. Применяем смещение +3 часа для Moscow
    moscow_dt = dt.astimezone(MOSCOW_TZ)
    
    # 3. Убираем tzinfo для хранения в БД как наивное время
    return moscow_dt.replace(tzinfo=None)

# Parse time options
PARSE_OPTION_STANDARD = "standard"  # Стандартный парсинг (23:50 после публикации)
PARSE_OPTION_NOW = "now"            # Немедленный парсинг
PARSE_OPTION_5MIN = "5min"          # За 5 минут до истечения 24 часов
PARSE_OPTION_30MIN = "30min"        # За 30 минут до истечения 24 часов
PARSE_OPTION_1HOUR = "1hour"        # За 1 час до истечения 24 часов

# Default settings
DEFAULT_SETTINGS = {
    'vk_token': VK_TOKEN,
    'parse_time': '23:50',  # Время парсинга по умолчанию
    'export_format': 'txt',  # Формат результатов по умолчанию
    'parse_interval': 23.83,  # Часы между публикацией и парсингом
    'default_parse_option': 'standard'
}

# Logging configuration
log_level = logging.DEBUG if DEBUG else logging.INFO
log_file = os.path.join(LOG_DIR, 'vk_parser.log')

# Настройка логирования
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)

# Добавляем информацию о запуске
logger.info(f"Запуск приложения в режиме {ENV}")
logger.info(f"База данных: {DB_URI}")
logger.debug(f"Директория загрузок: {UPLOAD_DIR}")
logger.debug(f"Директория результатов: {RESULTS_DIR}")
logger.debug(f"Директория логов: {LOG_DIR}")
