import os
import re
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton, 
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
import schedule
import time
from datetime import datetime, timedelta
import asyncio
import logging
import pdfplumber
import json
import aiosqlite
from pathlib import Path
import sys
from typing import Optional, Dict, Set, List, Any
import threading
import functools
import math
import PyPDF2
import traceback
import csv
import aiohttp

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Классы для обработки ошибок
class VKAPIError(Exception):
    """Ошибка API ВКонтакте"""
    def __init__(self, message, error_code=None):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)

class ParsingError(Exception):
    """Ошибка парсинга данных"""
    pass

class FileProcessingError(Exception):
    """Ошибка обработки файла"""
    pass

# Функция для логирования и обработки ошибок
def log_and_handle_error(e, error_type, context=""):
    """
    Логирует ошибку с подробным контекстом и возвращает сообщение для пользователя
    """
    error_trace = traceback.format_exc()
    
    if isinstance(e, VKAPIError):
        logger.error(f"VKAPI ошибка [{e.error_code}] в {context}: {e.message}\n{error_trace}")
        user_message = f"Ошибка API ВКонтакте: {e.message}"
    elif isinstance(e, ParsingError):
        logger.error(f"Ошибка парсинга в {context}: {str(e)}\n{error_trace}")
        user_message = f"Ошибка при обработке данных: {str(e)}"
    elif isinstance(e, FileProcessingError):
        logger.error(f"Ошибка обработки файла в {context}: {str(e)}\n{error_trace}")
        user_message = f"Ошибка при обработке файла: {str(e)}"
    else:
        logger.error(f"Непредвиденная ошибка в {context}: {str(e)}\n{error_trace}")
        user_message = f"Произошла непредвиденная ошибка: {str(e)}"
    
    return user_message

# Кеширование результатов API запросов
class VKAPICache:
    """Класс для кеширования результатов запросов к API ВКонтакте"""
    def __init__(self, cache_ttl=3600):  # TTL по умолчанию 1 час
        self.cache = {}
        self.cache_ttl = cache_ttl
    
    def get(self, cache_key):
        """Получить значение из кеша по ключу"""
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            # Проверяем актуальность кеша
            if datetime.now().timestamp() - entry['timestamp'] < self.cache_ttl:
                logger.debug(f"Получены данные из кеша для ключа {cache_key}")
                return entry['data']
            else:
                # Удаляем устаревшую запись
                del self.cache[cache_key]
                logger.debug(f"Удалена устаревшая запись кеша для ключа {cache_key}")
        return None
    
    def set(self, cache_key, data):
        """Сохранить значение в кеш по ключу"""
        self.cache[cache_key] = {
            'data': data,
            'timestamp': datetime.now().timestamp()
        }
        logger.debug(f"Данные сохранены в кеш для ключа {cache_key}")
    
    def clear(self):
        """Очистить кеш"""
        self.cache = {}
        logger.info("Кеш очищен")
    
    def clear_expired(self):
        """Очистить устаревшие записи в кеше"""
        current_time = datetime.now().timestamp()
        expired_keys = [
            key for key, entry in self.cache.items() 
            if current_time - entry['timestamp'] >= self.cache_ttl
        ]
        for key in expired_keys:
            del self.cache[key]
        if expired_keys:
            logger.info(f"Очищено {len(expired_keys)} устаревших записей в кеше")

# Создаем экземпляр кеша
vk_api_cache = VKAPICache()

# Глобальные переменные
PARSED_FILES: List[str] = []  # Список загруженных файлов
POSTS_TO_PARSE: List[Dict[str, Any]] = []  # Список постов для парсинга (ссылка, время парсинга)
ARCHIVE_FILES: Dict[str, Dict[str, Any]] = {}  # Архив файлов: {file_id: {file_path, status, uploaded_at, parse_time}}
# ID администратора для уведомлений
ADMIN_CHAT_ID = None  # Установите ваш чат ID для получения уведомлений

# Настройки времени парсинга
PARSE_OPTION_STANDARD = "standard"  # Стандартный парсинг (23:50 после публикации)
PARSE_OPTION_2MIN = "2min"          # Через 2 минуты после публикации
PARSE_OPTION_5MIN = "5min"          # За 5 минут до истечения 24ч
PARSE_OPTION_30MIN = "30min"        # За 30 минут до истечения 24ч
PARSE_OPTION_1HOUR = "1hour"        # За 1 час до истечения 24ч

# Глобальные переменные
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'bot.db'
SETTINGS_FILE = BASE_DIR / 'settings.json'
UPLOAD_DIR = BASE_DIR / 'uploads'
RESULTS_DIR = BASE_DIR / 'results'

# Создаём необходимые директории
for directory in [UPLOAD_DIR, RESULTS_DIR]:
    try:
        directory.mkdir(exist_ok=True)
        logger.info(f"Директория {directory} создана или уже существует")
    except Exception as e:
        logger.error(f"Ошибка при создании директории {directory}: {e}")
        raise

# Инициализация бота
TELEGRAM_TOKEN = '7386406020:AAHnJMgc0QUdQIhUMSjbjtzakFFhCecCRpU'
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Структура настроек по умолчанию
DEFAULT_SETTINGS = {
    'vk_token': None,
    'parse_time': '23:50',  # Время парсинга по умолчанию
    'result_format': 'txt',  # Формат результатов по умолчанию
    'last_parsed': None,
    'total_parsed': 0,
    'parse_interval': 23.83,  # Часы между публикацией и парсингом
    'default_parse_option': 'standard'
}

# Функции для работы с настройками
async def load_settings_from_db() -> Dict[str, Any]:
    """Загружает настройки из базы данных"""
    try:
        settings = DEFAULT_SETTINGS.copy()
        
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT key, value FROM settings')
            rows = await cursor.fetchall()
            
            for key, value in rows:
                if key in settings:
                    # Преобразуем строковые значения в нужный тип
                    if key in ['parse_interval'] and value:
                        try:
                            settings[key] = float(value)
                        except:
                            pass
                    elif key in ['total_parsed'] and value:
                        try:
                            settings[key] = int(value)
                        except:
                            pass
                    elif value.startswith('{') or value.startswith('['):
                        try:
                            settings[key] = json.loads(value)
                        except:
                            settings[key] = value
                    else:
                        settings[key] = value
        
        return settings
    except Exception as e:
        logger.error(f"Ошибка при загрузке настроек из БД: {e}")
        return DEFAULT_SETTINGS.copy()

async def save_settings_to_db(settings: Dict[str, Any]) -> bool:
    """Сохраняет настройки в базу данных"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            for key, value in settings.items():
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                elif value is None:
                    value = ''
                else:
                    value = str(value)
                
                await db.execute(
                    'INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)', 
                    (key, value)
                )
            
            await db.commit()
        
        return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек в БД: {e}")
        return False

# Для совместимости со старым кодом
def load_settings() -> Dict[str, Any]:
    """Синхронная обертка для загрузки настроек"""
    try:
        # Проверяем, есть ли работающий цикл событий
        try:
            loop = asyncio.get_running_loop()
            logger.info("Обнаружен работающий цикл событий, возвращаем DEFAULT_SETTINGS")
            # Если мы в асинхронном контексте, возвращаем значения по умолчанию
            # так как не можем запустить новый цикл
            # Правильное решение - вызывающий код должен использовать load_settings_from_db
            return DEFAULT_SETTINGS.copy()
        except RuntimeError:
            # Нет работающего цикла событий, можем создать свой
            loop = asyncio.new_event_loop()
            try:
                settings = loop.run_until_complete(load_settings_from_db())
                return settings
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"Ошибка при синхронной загрузке настроек: {e}")
        return DEFAULT_SETTINGS.copy()

def save_settings(settings: Dict[str, Any]) -> None:
    """Синхронная обертка для сохранения настроек"""
    try:
        # Проверяем, есть ли работающий цикл событий
        try:
            loop = asyncio.get_running_loop()
            logger.warning("Обнаружен работающий цикл событий, невозможно синхронно сохранить настройки")
            # Если мы в асинхронном контексте, нельзя создать новый цикл
            # Правильное решение - вызывающий код должен использовать save_settings_to_db
            return
        except RuntimeError:
            # Нет работающего цикла событий, можем создать свой
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(save_settings_to_db(settings))
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"Ошибка при синхронном сохранении настроек: {e}")

# Инициализация базы данных
async def init_db() -> None:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link TEXT NOT NULL,
                    publish_time TEXT NOT NULL,
                    parse_time TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    file_id TEXT,
                    parse_data TEXT
                )
            ''')
            await db.execute('''
                CREATE TABLE IF NOT EXISTS results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER,
                    user_id INTEGER,
                    activity_type TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (post_id) REFERENCES posts (id)
                )
            ''')
            
            # Таблица для архива файлов
            await db.execute('''
                CREATE TABLE IF NOT EXISTS archive_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT UNIQUE NOT NULL,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    scheduled_parse_time TEXT,
                    last_parsed_at TEXT,
                    result_file_path TEXT,
                    scheduled_parse_data TEXT,
                    earliest_parse_time TEXT
                )
            ''')
            
            # Добавляем таблицу настроек для хранения конфигурации
            await db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
            ''')
            
            # Проверяем наличие ключевых настроек и создаем их если нужно
            await init_settings(db)
            
            await db.commit()
            logger.info("База данных успешно инициализирована")
    except Exception as e:
        logger.error(f"Ошибка при инициализации БД: {e}")
        raise

# Функция для инициализации настроек
async def init_settings(db):
    """Инициализирует настройки в базе данных"""
    try:
        # Импортируем данные из settings.json, если он существует
        settings_from_file = {}
        if SETTINGS_FILE.exists():
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    settings_from_file = json.load(f)
            except:
                logger.warning("Не удалось прочитать settings.json")
        
        # Создаем настройки по умолчанию
        merged_settings = {**DEFAULT_SETTINGS, **settings_from_file}
        
        # Проверяем и создаем каждую настройку
        for key, value in merged_settings.items():
            cursor = await db.execute('SELECT value FROM settings WHERE key = ?', (key,))
            exists = await cursor.fetchone()
            
            if not exists:
                if isinstance(value, (dict, list)):
                    value = json.dumps(value)
                elif value is None:
                    value = ''
                else:
                    value = str(value)
                
                await db.execute('INSERT INTO settings (key, value) VALUES (?, ?)', (key, value))
                logger.info(f"Создана настройка: {key} = {value}")
        
        # Удаляем settings.json, если он существует, так как теперь все в БД
        if SETTINGS_FILE.exists():
            try:
                os.remove(SETTINGS_FILE)
                logger.info("Файл settings.json удален после импорта данных")
            except:
                logger.warning("Не удалось удалить settings.json")
        
        await db.commit()
    except Exception as e:
        logger.error(f"Ошибка при инициализации настроек: {e}")
        raise

# Функции для работы с базой данных
async def add_post(link: str, publish_time: datetime, parse_time: datetime) -> Optional[int]:
    try:
        # Преобразуем datetime в строку, если они не строки
        publish_time_str = publish_time.isoformat() if isinstance(publish_time, datetime) else publish_time
        parse_time_str = parse_time.isoformat() if isinstance(parse_time, datetime) else parse_time
        
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                'INSERT INTO posts (link, publish_time, parse_time) VALUES (?, ?, ?)',
                (link, publish_time_str, parse_time_str)
            )
            await db.commit()
            return cursor.lastrowid
    except Exception as e:
        logger.error(f"Ошибка при добавлении поста: {e}")
        return None

async def update_post_status(post_id: int, status: str) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                'UPDATE posts SET status = ? WHERE id = ?',
                (status, post_id)
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса поста: {e}")
        return False

async def save_results(post_id: int, users: Set[int], activity_type: str) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            for user_id in users:
                await db.execute(
                    'INSERT INTO results (post_id, user_id, activity_type) VALUES (?, ?, ?)',
                    (post_id, user_id, activity_type)
                )
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при сохранении результатов: {e}")
        return False

async def get_stats() -> Dict[str, int]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT COUNT(*) FROM posts')
            total_posts = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM posts WHERE status = "completed"')
            completed_posts = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM results')
            total_results = (await cursor.fetchone())[0]
            
            cursor = await db.execute('SELECT COUNT(*) FROM archive_files')
            total_files = (await cursor.fetchone())[0]
            
            return {
                'total_posts': total_posts,
                'completed_posts': completed_posts,
                'total_results': total_results,
                'total_files': total_files
            }
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")
        return {'total_posts': 0, 'completed_posts': 0, 'total_results': 0, 'total_files': 0}

# Функция для проверки и сохранения токена VK API
async def validate_and_save_token(token: str) -> tuple:
    """
    Проверяет валидность токена VK API и сохраняет его в настройках
    
    Args:
        token: Токен VK API для проверки
        
    Returns:
        tuple: (success, message)
    """
    try:
        url = f'https://api.vk.com/method/users.get'
        params = {
            'access_token': token,
            'v': '5.131'
        }
        
        # Используем нашу функцию для асинхронного запроса
        response = await vk_api_request(url, params)
        
        if 'response' in response:
            # Сохраняем токен в настройках
            settings = await load_settings_from_db()
            settings['vk_token'] = token
            await save_settings_to_db(settings)
            
            logger.info("Токен VK API успешно сохранен")
            return True, "✅ Токен VK API успешно сохранен!"
        else:
            # Получаем информацию об ошибке
            error = response.get('error', {})
            error_msg = error.get('error_msg', 'Неизвестная ошибка')
            error_code = error.get('error_code', 0)
            
            logger.error(f"Ошибка при проверке токена: {error_msg} (код: {error_code})")
            
            if error_code == 5:
                return False, "❌ Ошибка авторизации: неверный токен"
            elif error_code == 17:
                return False, "❌ Требуется валидация через redirect_uri"
            else:
                return False, f"❌ Ошибка при проверке токена: {error_msg}"
    except Exception as e:
        logger.error(f"Ошибка при проверке токена: {e}")
        return False, f"❌ Ошибка при проверке токена: {str(e)}"

# Функция для сохранения токена VK API
def save_vk_token(token: str) -> None:
    try:
        with open('vk_token.txt', 'w') as f:
            f.write(token)
        logger.info("Токен VK API сохранен в файл")
    except Exception as e:
        logger.error(f"Ошибка при сохранении токена: {e}")
        raise

# Функция для загрузки токена VK API
async def load_vk_token_async() -> Optional[str]:
    """Асинхронно загружает токен VK API из настроек"""
    try:
        settings = await load_settings_from_db()
        return settings.get('vk_token')
    except Exception as e:
        logger.error(f"Ошибка при загрузке токена: {e}")
        return None

# Для совместимости со старым кодом
def load_vk_token() -> Optional[str]:
    """Синхронная обертка для загрузки токена VK API"""
    try:
        # Проверяем, есть ли работающий цикл событий
        try:
            loop = asyncio.get_running_loop()
            logger.info("Обнаружен работающий цикл событий при загрузке токена, возвращаем None")
            # Если мы в асинхронном контексте, возвращаем None
            # Правильное решение - вызывающий код должен использовать load_vk_token_async
            return None
        except RuntimeError:
            # Нет работающего цикла событий, можем создать свой
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(load_vk_token_async())
            finally:
                loop.close()
    except Exception as e:
        logger.error(f"Ошибка при синхронной загрузке токена: {e}")
        return None

# Функция для извлечения ссылок и времени публикации из файла
def extract_links_and_time(file_path):
    """Извлекает из файла ссылки на посты ВКонтакте и время их публикации"""
    try:
        logger.info(f"Извлечение данных из файла: {file_path}")
        
        # Проверяем существование файла
        if not os.path.exists(file_path):
            logger.error(f"Файл не найден: {file_path}")
            return []
            
        # Определяем тип файла по расширению
        file_extension = os.path.splitext(file_path)[1].lower()
        
        # Выбираем соответствующий метод обработки в зависимости от типа файла
        if file_extension == '.txt':
            return extract_from_txt(file_path)
        elif file_extension == '.html':
            return extract_from_html(file_path)
        elif file_extension == '.pdf':
            return extract_from_pdf(file_path)
        else:
            # Попытка определить тип файла по содержимому
            try:
                with open(file_path, 'rb') as f:
                    header = f.read(1024)
                    
                # Проверяем сигнатуру PDF
                if header.startswith(b'%PDF'):
                    logger.info(f"Файл определен как PDF по содержимому, хотя имеет расширение {file_extension}")
                    return extract_from_pdf(file_path)
                    
                # Проверяем HTML-сигнатуру
                if b'<!DOCTYPE html>' in header or b'<html' in header:
                    logger.info(f"Файл определен как HTML по содержимому, хотя имеет расширение {file_extension}")
                    return extract_from_html(file_path)
                    
                # Пробуем обработать как текст
                return extract_from_txt(file_path)
                
            except Exception as e:
                logger.error(f"Не удалось определить тип файла по содержимому: {e}")
                logger.warning(f"Неподдерживаемый тип файла: {file_extension}")
                return []
        
    except Exception as e:
        logger.error(f"Ошибка при извлечении данных из файла: {e}")
        return []

def extract_from_pdf(file_path):
    posts = []
    try:
        logger.info(f"Извлечение данных из PDF файла: {file_path}")
        
        # Пытаемся различными методами извлечь содержимое PDF
        pdf_text = ""
        tables_data = []
        
        # Метод 1: pdfplumber для таблиц и текста
        with pdfplumber.open(file_path) as pdf:
            # Сначала пытаемся извлечь все таблицы
            for page in pdf.pages:
                # Извлекаем таблицы
                try:
                    tables = page.extract_tables()
                    if tables:
                        tables_data.extend(tables)
                except Exception as e:
                    logger.warning(f"Ошибка при извлечении таблиц со страницы PDF: {e}")
                
                # Извлекаем текст
                try:
                    page_text = page.extract_text() or ""
                    pdf_text += page_text + "\n"
                except Exception as e:
                    logger.warning(f"Ошибка при извлечении текста со страницы PDF: {e}")
        
        # Метод 2: PyPDF2 для текста (резервный вариант)
        if not pdf_text.strip():
            try:
                pdf_reader = PyPDF2.PdfReader(file_path)
                pdf_text = ' '.join([page.extract_text() or "" for page in pdf_reader.pages])
            except Exception as e:
                logger.warning(f"Ошибка при извлечении текста из PDF с помощью PyPDF2: {e}")
        
        # Обработка найденных таблиц
        if tables_data:
            logger.info(f"Найдено {len(tables_data)} таблиц в PDF файле")
            
            for table in tables_data:
                # Пропускаем пустые таблицы и заголовок
                if not table or len(table) <= 1:
                    continue
                
                # Предполагаем, что первая строка может быть заголовком
                for row in table[1:]:
                    if not row or all(cell is None or cell == "" for cell in row):
                        continue
                    
                    try:
                        link = None
                        publish_time = None
                        
                        # Ищем в каждой ячейке ссылки и время
                        for cell in row:
                            if not cell or not isinstance(cell, str):
                                continue
                            
                            # Ищем ссылки ВКонтакте
                            vk_links = re.findall(r'https?://(?:www\.)?vk\.com/\S+', cell)
                            if vk_links:
                                link = vk_links[0]
                            
                            # Ищем дату и время в различных форматах
                            # Формат 1: "27 мар. 2024 г., 08:53"
                            time_match = re.search(r'(\d{1,2}\s+\w+\.\s+\d{4}\s+г\.,\s+\d{1,2}:\d{2})', cell)
                            if time_match:
                                try:
                                    time_str = time_match.group(1)
                                    # Заменяем сокращения месяцев на полные названия
                                    month_map = {
                                        'янв.': 'января', 'фев.': 'февраля', 'мар.': 'марта', 'апр.': 'апреля',
                                        'мая.': 'мая', 'июн.': 'июня', 'июл.': 'июля', 'авг.': 'августа',
                                        'сен.': 'сентября', 'окт.': 'октября', 'ноя.': 'ноября', 'дек.': 'декабря'
                                    }
                                    for abbr, full in month_map.items():
                                        if abbr in time_str:
                                            time_str = time_str.replace(abbr, full)
                                    
                                    # Пытаемся разные форматы
                                    try:
                                        publish_time = datetime.strptime(time_str, "%d %B %Y г., %H:%M")
                                    except ValueError:
                                        try:
                                            publish_time = parse_time_string(time_str)
                                        except:
                                            continue
                                except Exception as e:
                                    logger.warning(f"Ошибка при разборе даты: {e}")
                                    continue
                            
                            # Формат 2: "27.03.2024, 08:53"
                            if not publish_time:
                                date_match = re.search(r'(\d{1,2}\.\d{1,2}\.\d{4}[,\s]+\d{1,2}:\d{2})', cell)
                                if date_match:
                                    try:
                                        time_str = date_match.group(1).replace(',', ' ')
                                        publish_time = datetime.strptime(time_str, "%d.%m.%Y %H:%M")
                                    except Exception as e:
                                        logger.warning(f"Ошибка при разборе даты в формате ДД.ММ.ГГГГ: {e}")
                        
                        # Если нашли ссылку, но не нашли время публикации, используем текущее время
                        if link and not publish_time:
                            publish_time = datetime.now()
                            logger.warning(f"Не найдено время публикации для {link}, используется текущее время")
                        
                        if link and publish_time:
                            # Время парсинга рассчитываем на основе настроек
                            parse_time = calculate_standard_parse_time(publish_time)
                            posts.append({
                                'link': link,
                                'publish_time': publish_time,
                                'parse_time': parse_time
                            })
                            logger.info(f"Найден пост в таблице: {link} с временем публикации {publish_time}")
                    except Exception as e:
                        logger.error(f"Ошибка при обработке строки таблицы: {e}")
                        continue
        
        # Обработка текста (если таблицы не дали результатов или нужно дополнить)
        if pdf_text:
            # Ищем все ссылки на посты ВК в тексте
            vk_links = re.findall(r'https?://(?:www\.)?vk\.com/\S+', pdf_text)
            
            if vk_links:
                logger.info(f"Найдено {len(vk_links)} ссылок ВК в тексте PDF")
                
                # Для каждой ссылки пытаемся найти время публикации рядом
                for link in vk_links:
                    # Проверяем, есть ли уже этот пост в результатах
                    if any(post['link'] == link for post in posts):
                        continue
                    
                    # Ищем время публикации в тексте вокруг ссылки
                    link_pos = pdf_text.find(link)
                    if link_pos != -1:
                        # Проверяем текст вокруг ссылки (50 символов до и после)
                        start_pos = max(0, link_pos - 50)
                        end_pos = min(len(pdf_text), link_pos + len(link) + 50)
                        context = pdf_text[start_pos:end_pos]
                        
                        # Ищем различные форматы даты и времени
                        publish_time = None
                        
                        # Проверяем разные форматы дат
                        date_formats = [
                            r'(\d{1,2}\s+\w+\.\s+\d{4}\s+г\.,\s+\d{1,2}:\d{2})',  # 27 мар. 2024 г., 08:53
                            r'(\d{1,2}\.\d{1,2}\.\d{4}[,\s]+\d{1,2}:\d{2})',      # 27.03.2024, 08:53
                            r'(\d{1,2}\s+\w+\s+\d{4}[,\s]+\d{1,2}:\d{2})'         # 27 марта 2024, 08:53
                        ]
                        
                        for pattern in date_formats:
                            date_match = re.search(pattern, context)
                            if date_match:
                                try:
                                    publish_time = parse_time_string(date_match.group(1))
                                    break
                                except:
                                    continue
                        
                        # Если не нашли время публикации, используем текущее время
                        if not publish_time:
                            publish_time = datetime.now()
                            logger.warning(f"Не найдено время публикации для {link}, используется текущее время")
                        
                        # Рассчитываем время парсинга
                        parse_time = calculate_standard_parse_time(publish_time)
                        posts.append({
                            'link': link,
                            'publish_time': publish_time,
                            'parse_time': parse_time
                        })
                        logger.info(f"Найден пост в тексте: {link} с временем публикации {publish_time}")
    
    except Exception as e:
        logger.error(f"Ошибка при обработке PDF файла {file_path}: {e}")
    
    return posts

def extract_from_txt(file_path):
    """
    Извлекает ссылки и время публикации из текстового файла.
    
    Args:
        file_path: Путь к файлу
        
    Returns:
        list: Список постов с информацией о ссылке, времени публикации и парсинга
    """
    posts = []
    processed_links = set()  # Для отслеживания уже обработанных ссылок
    content = ""
    
    try:
        # Пробуем различные кодировки при чтении файла
        encodings = ['utf-8', 'cp1251', 'latin-1']
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding, errors='ignore') as file:
                    content = file.read()
                logger.info(f"Успешно прочитан файл {file_path} с кодировкой {encoding}")
                break
            except Exception as e:
                logger.warning(f"Не удалось прочитать файл с кодировкой {encoding}: {e}")
        
        if not content:
            logger.error(f"Не удалось прочитать содержимое файла {file_path} ни с одной из кодировок")
            return []
            
        # Ищем все возможные форматы ссылок на постов ВКонтакте
        patterns = [
            r'https?://(?:www\.)?vk\.com/wall-\d+_\d+',  # Полные ссылки
            r'wall-\d+_\d+',  # Ссылки в формате wall-ID_POST-ID
            r'href=3D"https?://(?:www\.)?vk\.com/wall-\d+_\d+"',  # Ссылки в HTML-кодировке
            r'href=3D"wall-\d+_\d+"',  # Ссылки в формате wall-ID_POST-ID в HTML-кодировке
            r'class=3D"exchange_ad_post_link"\s+href=3D"wall-\d+_\d+"',  # Ссылки в формате VK Adblogger
            r'class=3D"exchange_ad_post_link"\s+href=3D"https?://(?:www\.)?vk\.com/wall-\d+_\d+"'  # Полные ссылки в формате VK Adblogger
        ]
        
        # Находим все ссылки в файле
        all_links = []
        for pattern in patterns:
            matches = re.finditer(pattern, content)
            for match in matches:
                link_text = match.group(0)
                # Обрабатываем различные форматы ссылок
                if 'href=3D"' in link_text:
                    link_text = re.search(r'href=3D"(.*?)"', link_text).group(1)
                
                # Нормализуем ссылку
                if not link_text.startswith('http'):
                    if link_text.startswith('wall-'):
                        link_text = f"https://vk.com/{link_text}"
                    else:
                        link_text = f"https://vk.com/wall{link_text}"
                
                # Добавляем только если это действительно ссылка на пост VK
                if 'vk.com/wall' in link_text and link_text not in processed_links:
                    processed_links.add(link_text)
                    all_links.append(link_text)
        
        logger.info(f"Найдено {len(all_links)} уникальных ссылок на посты в файле {file_path}")
        
        # В данной функции мы просто добавляем ссылки с текущим временем
        # Реальное время публикации будет получено позже асинхронным методом get_post_publish_time
        # при формировании мини-статистики или других операциях
        for link in all_links:
            now = datetime.now()
            parse_time = calculate_standard_parse_time(now)
            
            posts.append({
                'link': link,
                'publish_time': now,  # Временное значение, будет обновлено при асинхронном запросе
                'parse_time': parse_time
            })
            logger.info(f"Добавлен пост {link} в список для дальнейшей обработки")
                
    except Exception as e:
        logger.error(f"Ошибка при обработке текстового файла {file_path}: {e}")
    
    return posts

def find_nearest_time_element(element, max_distance=5):
    """Ищет ближайший элемент с временем публикации"""
    current = element
    distance = 0
    while current and distance < max_distance:
        if isinstance(current, str):
            if any(marker in current for marker in ['сегодня в', 'вчера в', 'Опубликована:']):
                return current
        elif hasattr(current, 'text'):
            if any(marker in current.text for marker in ['сегодня в', 'вчера в', 'Опубликована:']):
                return current
        current = current.find_next()
        distance += 1
    
    # Если не нашли впереди, поищем позади
    current = element
    distance = 0
    while current and distance < max_distance:
        if isinstance(current, str):
            if any(marker in current for marker in ['сегодня в', 'вчера в', 'Опубликована:']):
                return current
        elif hasattr(current, 'text'):
            if any(marker in current.text for marker in ['сегодня в', 'вчера в', 'Опубликована:']):
                return current
        current = current.find_previous()
        distance += 1
    
    return None

def parse_time_string(time_str):
    """
    Пытается распознать различные форматы времени и даты
    
    Args:
        time_str: Строка с датой/временем
        
    Returns:
        datetime объект или None, если не удалось распознать
    """
    try:
        # Словарь с сокращениями месяцев и их полными наименованиями
        month_map = {
            'янв': 'января', 'фев': 'февраля', 'мар': 'марта', 'апр': 'апреля',
            'май': 'мая', 'июн': 'июня', 'июл': 'июля', 'авг': 'августа',
            'сен': 'сентября', 'окт': 'октября', 'ноя': 'ноября', 'дек': 'декабря'
        }
        
        # Преобразуем строку к нижнему регистру для поиска месяцев
        time_str_lower = time_str.lower()
        
        # Заменяем сокращения месяцев на полные названия
        for abbr, full in month_map.items():
            if abbr in time_str_lower:
                time_str_lower = time_str_lower.replace(abbr, full)
        
        # Словарь форматов даты и соответствующих им паттернов
        formats = [
            # Стандартные форматы
            '%d %B %Y г., %H:%M',    # 27 марта 2024 г., 08:53
            '%d %B %Y, %H:%M',       # 27 марта 2024, 08:53
            '%d %B %Y, %H:%M',       # 30 мар 2025, 7:15 (без ведущих нулей)
            '%d %B %Y в %H:%M',      # 27 марта 2024 в 08:53
            '%d.%m.%Y, %H:%M',       # 27.03.2024, 08:53
            '%d.%m.%Y в %H:%M',      # 27.03.2024 в 08:53
            '%d.%m.%Y %H:%M',        # 27.03.2024 08:53
            '%Y-%m-%d %H:%M:%S',     # 2024-03-27 08:53:00
            '%Y-%m-%dT%H:%M:%S',     # 2024-03-27T08:53:00
            '%H:%M %d.%m.%Y',        # 08:53 27.03.2024
            '%H:%M:%S %d.%m.%Y',     # 08:53:00 27.03.2024
            
            # Дополнительные форматы
            '%d %m %Y %H:%M',        # 27 03 2024 08:53
            '%d-%m-%Y %H:%M',        # 27-03-2024 08:53
            '%d/%m/%Y %H:%M',        # 27/03/2024 08:53
            '%d %B, %H:%M',          # 27 марта, 08:53 (текущий год)
            '%d.%m, %H:%M'           # 27.03, 08:53 (текущий год)
        ]
        
        # Проверяем специальные случаи
        if 'сегодня' in time_str_lower:
            time_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                return datetime.combine(datetime.now().date(), datetime.min.time().replace(hour=hour, minute=minute))
                
        if 'вчера' in time_str_lower:
            time_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                yesterday = datetime.now().date() - timedelta(days=1)
                return datetime.combine(yesterday, datetime.min.time().replace(hour=hour, minute=minute))
        
        # Пробуем распознать по различным форматам
        for fmt in formats:
            try:
                # Возвращаем локализованные названия месяцев для тестового формата
                if '%B' in fmt:
                    return datetime.strptime(time_str_lower, fmt)
                else:
                    return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        
        # Если стандартные форматы не подходят, пробуем использовать регулярные выражения
        
        # Формат с точки зрения на фото: "DD мар YYYY, H:MM" (без ведущих нулей в часах)
        match = re.search(r'(\d{1,2})\s+(\w{3})\s+(\d{4}),\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            day = int(match.group(1))
            month_abbr = match.group(2).lower()
            year = int(match.group(3))
            hour = int(match.group(4))
            minute = int(match.group(5))
            
            # Словарь сокращений месяцев для преобразования в номер
            month_abbr_to_num = {
                'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4,
                'май': 5, 'июн': 6, 'июл': 7, 'авг': 8,
                'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12
            }
            
            if month_abbr in month_abbr_to_num:
                month = month_abbr_to_num[month_abbr]
                return datetime(year, month, day, hour, minute)
        
        # Формат: HH:MM DD.MM.YYYY
        match = re.search(r'(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})', time_str)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            day = int(match.group(3))
            month = int(match.group(4))
            year = int(match.group(5))
            return datetime(year, month, day, hour, minute)
        
        # Формат: DD.MM.YYYY HH:MM
        match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})', time_str)
        if match:
            day = int(match.group(1))
            month = int(match.group(2))
            year = int(match.group(3))
            hour = int(match.group(4))
            minute = int(match.group(5))
            return datetime(year, month, day, hour, minute)
        
        # Формат: DD месяц YYYY, HH:MM
        match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4}),\s+(\d{1,2}):(\d{2})', time_str_lower)
        if match:
            day = int(match.group(1))
            month_name = match.group(2)
            year = int(match.group(3))
            hour = int(match.group(4))
            minute = int(match.group(5))
            
            # Словарь месяцев для преобразования названия в номер
            month_to_num = {
                'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4,
                'мая': 5, 'июня': 6, 'июля': 7, 'августа': 8,
                'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
            }
            
            if month_name in month_to_num:
                month = month_to_num[month_name]
                return datetime(year, month, day, hour, minute)
        
        # Если даже с регулярками не удалось, ищем просто время, если оно есть, используем текущий день
        time_only_match = re.search(r'(\d{1,2}):(\d{2})', time_str)
        if time_only_match:
            hour = int(time_only_match.group(1))
            minute = int(time_only_match.group(2))
            return datetime.combine(datetime.now().date(), datetime.min.time().replace(hour=hour, minute=minute))
            
        logger.warning(f"Не удалось распознать формат времени: {time_str}")
        return None
        
    except Exception as e:
        logger.error(f"Ошибка при разборе строки времени '{time_str}': {e}")
        return None

def extract_from_html(file_path):
    """
    Извлекает ссылки из HTML файла.
    Время публикации будет получено позже с помощью асинхронного API-запроса.
    
    Args:
        file_path: Путь к HTML файлу
        
    Returns:
        list: Список постов с ссылками и временными метками
    """
    posts = []
    processed_links = set()
    
    try:
        # Пытаемся разными способами прочитать файл
        content = None
        encodings = ['utf-8', 'cp1251', 'latin-1']
        
        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding, errors='ignore') as file:
                    content = file.read()
                logger.info(f"Успешно прочитан HTML файл с кодировкой {encoding}: {file_path}")
                break
            except Exception as e:
                logger.warning(f"Не удалось прочитать HTML с кодировкой {encoding}: {e}")
        
        if not content:
            logger.error(f"Не удалось прочитать HTML файл: {file_path}")
            return []
        
        # Создаем объект BeautifulSoup для парсинга
        soup = BeautifulSoup(content, 'html.parser')
        
        # 1. Ищем все ссылки в HTML
        links = soup.find_all('a', href=True)
        
        # Фильтруем ссылки, оставляя только посты ВКонтакте
        for link in links:
            href = link['href']
            
            # Проверяем, что это ссылка на пост ВКонтакте
            if 'vk.com' in href and (
                'wall' in href or 
                'photo' in href or 
                'video' in href or 
                'club' in href or 
                'public' in href or 
                'feed?w=wall' in href
            ):
                # Нормализуем ссылку
                normalized_link = href
                
                # Извлекаем идентификатор поста, если есть
                wall_match = re.search(r'wall(-?\d+_\d+)', href)
                if wall_match:
                    wall_id = wall_match.group(1)
                    # Формируем стандартную ссылку на пост
                    normalized_link = f"https://vk.com/wall{wall_id}"
                
                # Избегаем дубликатов
                if normalized_link not in processed_links:
                    processed_links.add(normalized_link)
                    
                    # Используем текущее время как временное значение
                    # Реальное время публикации будет получено позже через API
                    now = datetime.now()
                    parse_time = calculate_standard_parse_time(now)
                    
                    # Добавляем пост в результаты
                    posts.append({
                        'link': normalized_link,
                        'publish_time': now,  # Будет обновлено при запросе API
                        'parse_time': parse_time
                    })
                    logger.info(f"Найден пост в HTML: {normalized_link}")
        
        # 2. Дополнительно ищем ссылки в тексте HTML, если мало результатов
        if len(posts) < 3:
            text_content = soup.get_text()
            additional_links = re.findall(r'https?://(?:www\.)?vk\.com/\S+', text_content)
            
            for link in additional_links:
                # Очищаем ссылку от лишних символов
                clean_link = link.strip('\'".,;:() \n\t\r')
                
                # Проверяем, что это потенциально пост ВКонтакте
                if 'vk.com' in clean_link and (
                    'wall' in clean_link or 
                    'photo' in clean_link or 
                    'video' in clean_link or 
                    'club' in clean_link or 
                    'public' in clean_link or 
                    'feed?w=wall' in clean_link
                ) and clean_link not in processed_links:
                    processed_links.add(clean_link)
                    
                    # Используем текущее время как временное значение
                    now = datetime.now()
                    parse_time = calculate_standard_parse_time(now)
                    
                    posts.append({
                        'link': clean_link,
                        'publish_time': now,  # Будет обновлено при запросе API
                        'parse_time': parse_time
                    })
                    logger.info(f"Найден дополнительный пост в HTML: {clean_link}")
    
    except Exception as e:
        logger.error(f"Ошибка при обработке HTML файла {file_path}: {e}", exc_info=True)
    
    logger.info(f"Всего найдено {len(posts)} постов в HTML файле {file_path}")
    return posts

# Функция для выполнения API запроса с использованием кеша
async def vk_api_request_with_cache(url, params=None, max_retries=3, use_cache=True, cache_ttl=None):
    """
    Выполняет запрос к API ВКонтакте с кешированием результатов
    """
    # Создаем ключ кеша
    cache_key = f"{url}_{str(params)}" if params else url
    
    # Если используем кеш, пытаемся получить данные из него
    if use_cache:
        cached_data = vk_api_cache.get(cache_key)
        if cached_data:
            return cached_data
    
    # Если данных в кеше нет или кеш не используется, делаем запрос к API
    response = await vk_api_request(url, params, max_retries)
    
    # Если запрос успешен и используем кеш, сохраняем результат
    if use_cache and 'response' in response:
        # Если указано свое время жизни кеша, используем его
        if cache_ttl is not None:
            old_ttl = vk_api_cache.cache_ttl
            vk_api_cache.cache_ttl = cache_ttl
            vk_api_cache.set(cache_key, response)
            vk_api_cache.cache_ttl = old_ttl
        else:
            vk_api_cache.set(cache_key, response)
    
    return response

async def get_users_who_liked(post_id, access_token, use_cache=True):
    try:
        url = "https://api.vk.com/method/likes.getList"
        params = {
            "type": "post",
            "item_id": post_id.split('_')[1],
            "owner_id": post_id.split('_')[0],
            "count": 1000,
            "access_token": access_token,
            "v": "5.131"
        }
        
        response = await vk_api_request_with_cache(url, params, use_cache=use_cache)
        
        if 'response' in response:
            return [user_id for user_id in response['response']['items']]
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении лайков: {e}")
        raise

async def get_users_who_commented(post_id, access_token, use_cache=True):
    try:
        url = "https://api.vk.com/method/wall.getComments"
        params = {
            "owner_id": post_id.split('_')[0],
            "post_id": post_id.split('_')[1],
            "count": 100,
            "access_token": access_token,
            "v": "5.131"
        }
        
        response = await vk_api_request_with_cache(url, params, use_cache=use_cache)
        
        if 'response' in response:
            return [comment['from_id'] for comment in response['response']['items'] if 'from_id' in comment]
        return []
    except Exception as e:
        logger.error(f"Ошибка при получении комментариев: {e}")
        raise

async def get_users_who_reposted(post_id, access_token, use_cache=True):
    try:
        user_ids = set()
        owner_id, post_id_num = post_id.split('_')
        
        # Метод 1: Получаем репосты через wall.getReposts
        url = "https://api.vk.com/method/wall.getReposts"
        params = {
            "owner_id": owner_id,
            "post_id": post_id_num,
            "count": 1000,
            "access_token": access_token,
            "v": "5.131"
        }
        
        response = await vk_api_request_with_cache(url, params, use_cache=use_cache)
        
        if 'response' in response:
            # Обрабатываем основные репосты
            for item in response['response']['items']:
                if 'from_id' in item:
                    user_ids.add(item['from_id'])
                
                # Обрабатываем репосты в комментариях и другие вложенные данные
                if 'copy_history' in item:
                    for copy in item['copy_history']:
                        if 'from_id' in copy:
                            user_ids.add(copy['from_id'])
        
        # Метод 2: Поиск репостов через wall.search (с пагинацией)
        offset = 0
        max_count = 100
        total_found = max_count  # Начальное значение для входа в цикл
        
        while offset < total_found and offset < 1000:  # Ограничиваем количество запросов
            search_url = "https://api.vk.com/method/wall.search"
            search_params = {
                "owner_id": owner_id,
                "query": f"wall{owner_id}_{post_id_num}",
                "count": max_count,
                "offset": offset,
                "access_token": access_token,
                "v": "5.131"
            }
            
            search_response = await vk_api_request(search_url, search_params)
            
            if 'response' in search_response:
                items = search_response['response'].get('items', [])
                total_found = search_response['response'].get('count', 0)
                
                for item in items:
                    # Проверяем наличие репоста в тексте
                    if 'copy_history' in item:
                        for copy in item['copy_history']:
                            if str(copy.get('id')) == post_id_num and str(copy.get('owner_id')) == owner_id:
                                if 'from_id' in item:
                                    user_ids.add(item['from_id'])
                
                offset += len(items)
                if len(items) < max_count:
                    break  # Больше нет результатов
            else:
                break  # Ошибка или нет данных
        
        return list(user_ids)
            
    except Exception as e:
        logger.error(f"Ошибка при получении репостов: {e}")
        raise

async def parse_post_activities(post_id, access_token):
    try:
        users = {
            'likes': set(),
            'comments': set(),
            'reposts': set()
        }
        
        # Лайки
        users['likes'] = set(await get_users_who_liked(post_id, access_token))
        
        # Комментарии
        users['comments'] = set(await get_users_who_commented(post_id, access_token))
        
        # Репосты
        users['reposts'] = set(await get_users_who_reposted(post_id, access_token))
        
        return users
    except Exception as e:
        logger.error(f"Ошибка при парсинге активностей: {e}")
        raise

# Функция для парсинга поста и сохранения результатов
async def parse_and_save(post, access_token, chat_id, all_results):
    try:
        post_id = None
        if isinstance(post, tuple):  # Если пост из базы данных
            post_id = post[1].split('wall')[-1] if 'wall' in post[1] else None
            post_link = post[1]
        else:  # Если пост напрямую
            post_id = post['link'].split('wall')[-1] if 'wall' in post['link'] else None
            post_link = post['link']

        if not post_id and 'vk.com' in post_link:
            # Извлекаем ID сообщества для постов без wall
            match = re.search(r'vk\.com/(?:club|public)(\d+)', post_link)
            if match:
                community_id = '-' + match.group(1)
                # Получаем последний пост сообщества
                url = f'https://api.vk.com/method/wall.get'
                params = {
                    'owner_id': community_id,
                    'count': 1,
                    'access_token': access_token,
                    'v': '5.131'
                }
                response = requests.get(url, params=params).json()
                
                if 'response' in response and response['response']['items']:
                    post_data = response['response']['items'][0]
                    post_id = f"{community_id}_{post_data['id']}"

        if post_id:
            # Получаем активности
            users = {
                'likes': set(),
                'comments': set(),
                'reposts': set()
            }

            try:
                users['likes'] = set(await get_users_who_liked(post_id, access_token))
            except Exception as e:
                logger.error(f"Ошибка при получении лайков: {e}")

            try:
                users['comments'] = set(await get_users_who_commented(post_id, access_token))
            except Exception as e:
                logger.error(f"Ошибка при получении комментариев: {e}")

            try:
                users['reposts'] = set(await get_users_who_reposted(post_id, access_token))
            except Exception as e:
                logger.error(f"Ошибка при получении репостов: {e}")

            # Обновляем общую статистику
            all_results['total_likes'] += len(users['likes'])
            all_results['total_comments'] += len(users['comments'])
            all_results['total_reposts'] += len(users['reposts'])
            all_results['all_users'].update(users['likes'] | users['comments'] | users['reposts'])
            
            # Добавляем данные поста
            all_results['posts_data'].append({
                'post_id': post_id,
                'link': post_link,
                'likes': users['likes'],
                'comments': users['comments'],
                'reposts': users['reposts']
            })

            # Обновляем статус поста в базе данных
            if isinstance(post, tuple):
                await update_post_status(post[0], 'completed')

            logger.info(f"Пост {post_id} успешно спарсен")
            return True

        else:
            error_msg = f"❌ Не удалось определить ID поста из ссылки: {post_link}"
            await bot.send_message(chat_id, error_msg)
            logger.error(error_msg)
            return False

    except Exception as e:
        error_msg = f"❌ Ошибка при парсинге поста: {str(e)}"
        await bot.send_message(chat_id, error_msg)
        logger.error(f"Ошибка при парсинге поста {post_link if 'post_link' in locals() else 'Unknown'}: {e}")
        return False

# Функция для планирования парсинга
async def schedule_parsing(message: types.Message):
    """Обработчик для команды планирования парсинга из архива"""
    try:
        # Проверяем токен
        vk_token = await load_vk_token_async()
        if not vk_token:
            await message.reply("⚠️ Не настроен токен VK API. Пожалуйста, настройте токен в разделе настроек.")
            return

        if not POSTS_TO_PARSE:
            await message.reply(
                "⚠️ Нет постов для парсинга.\n\n"
                "Загрузите файлы с данными через кнопку 'Загрузить файл'."
            )
            return

        await message.reply("🔄 Планирую парсинг...")
        
        # Получаем посты из базы данных
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT * FROM posts WHERE status = "pending"')
            posts = await cursor.fetchall()
        
        if not posts:
            await message.reply("⚠️ Нет постов для парсинга в базе данных.")
            return

        scheduled_count = 0
        for post in posts:
            post_id, link, publish_time, parse_time, status, created_at = post
            parse_time = datetime.fromisoformat(parse_time)
            now = datetime.now()
            
            if parse_time > now:
                delay = (parse_time - now).total_seconds()
                schedule.every(delay).seconds.do(
                    lambda p=post, t=vk_token, c=message.chat.id: asyncio.run_coroutine_threadsafe(
                        parse_and_save(p, t, c, {}), asyncio.get_event_loop()
                    )
                ).tag(link)
                scheduled_count += 1
                await message.reply(
                    f"✅ Парсинг поста {link} запланирован на {parse_time.strftime('%d.%m.%Y %H:%M')}"
                )
            else:
                await message.reply(
                    f"⚠️ Время парсинга для {link} уже прошло.\n"
                    f"Парсинг будет выполнен немедленно."
                )
                await parse_and_save(post, vk_token, message.chat.id, {})
        
        await message.reply(
            f"✅ Планирование завершено:\n"
            f"📝 Всего постов: {len(posts)}\n"
            f"⏰ Запланировано: {scheduled_count}\n"
            f"🔄 Выполнено: {len(posts) - scheduled_count}"
        )
        
    except Exception as e:
        error_msg = f"❌ Ошибка при планировании парсинга: {e}"
        await message.reply(error_msg)
        logger.error(error_msg)

# Функция для немедленного парсинга
async def parse_now(message: types.Message, file_id: str = None):
    """Парсит файл немедленно"""
    try:
        # Проверяем наличие токена VK API
        vk_token = await load_vk_token_async()
        if not vk_token:
            await message.reply(
                "⚠️ Не настроен токен VK API. Пожалуйста, настройте токен в разделе настроек.",
                reply_markup=get_settings_menu()
            )
            return
        
        # Создаем и отправляем сообщение о начале парсинга
        status_message = await message.reply(
            "🔄 Подготовка к парсингу...\n"
            "⏳ Пожалуйста, подождите..."
        )
        
        # Получаем посты для парсинга
        posts_to_parse = []
        file_data = None
        
        if file_id:
            # Если указан ID файла, парсим только посты из этого файла
            file_data = await get_file_by_id(file_id)
            
            # Логируем информацию о файле для отладки
            logger.info(f"Получен файл с ID {file_id}: {file_data}")
            
            if not file_data:
                await status_message.edit_text(f"❌ Файл с ID {file_id} не найден в архиве.")
                return
            
            # Получаем файл с диска, если он существует
            file_path = file_data.get('file_path')
            if not file_path or not os.path.exists(file_path):
                await status_message.edit_text(f"❌ Файл не найден на диске: {file_path}")
                return
            
            # Извлекаем посты напрямую из файла
            posts = extract_links_and_time(file_path)
            
            if not posts:
                # Если не удалось извлечь посты из файла, пробуем найти их в базе данных
                await status_message.edit_text(
                    f"🔍 Пробуем найти посты в базе данных для файла {file_data['file_name']}..."
                )
                
                # Получаем посты из базы данных
                async with aiosqlite.connect(DB_PATH) as db:
                    db.row_factory = aiosqlite.Row
                    # Пытаемся получить недавно добавленные посты
                    cursor = await db.execute('SELECT * FROM posts WHERE status = "pending" ORDER BY id DESC LIMIT 50')
                    db_posts = await cursor.fetchall()
                    
                    if db_posts:
                        posts = [dict(post) for post in db_posts]
                        await status_message.edit_text(
                            f"✅ Найдено {len(posts)} постов в базе данных.\n"
                            f"🔄 Подготовка к парсингу..."
                        )
                    else:
                        await status_message.edit_text("⚠️ Не удалось найти посты ни в файле, ни в базе данных.")
                        return
            else:
                await status_message.edit_text(
                    f"✅ Найдено {len(posts)} постов в файле {file_data['file_name']}.\n"
                    f"🔄 Подготовка к парсингу..."
                )
        else:
            # Если ID файла не указан, берем все непарсенные посты из базы данных
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute('SELECT * FROM posts WHERE status = "pending"')
                db_posts = await cursor.fetchall()
                
                if db_posts:
                    posts = [dict(post) for post in db_posts]
                else:
                    await status_message.edit_text("⚠️ Нет постов для парсинга в базе данных.")
                    return
        
        # Проверяем наличие постов
        if not posts:
            await status_message.edit_text("⚠️ Нет постов для парсинга.")
            return

        # Словарь для хранения всех результатов
        all_results = {
            'total_likes': 0,
            'total_comments': 0,
            'total_reposts': 0,
            'all_users': set(),
            'posts_data': []
        }
        
        # Обновляем статус файла, если он указан
        if file_data:
            await update_file_status(file_id, "parsing")
        
        success_count = 0
        error_count = 0
        total_count = len(posts)
        
        # Создаем общие файлы для результатов
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        results_dir = os.path.join(BASE_DIR, 'results')
        os.makedirs(results_dir, exist_ok=True)
        
        # Названия файлов результатов
        file_prefix = f"file_{file_id}_" if file_id else "all_"
        txt_file = os.path.join(results_dir, f'{file_prefix}parsed_{timestamp}.txt')
        csv_file = os.path.join(results_dir, f'{file_prefix}parsed_{timestamp}.csv')
        
        # Функция для обновления сообщения о прогрессе
        async def update_progress(current, processed_success, processed_error):
            progress_percent = int((current / total_count) * 100)
            progress_bar = "▓" * (progress_percent // 5) + "░" * ((100 - progress_percent) // 5)
            
            await status_message.edit_text(
                f"🔄 Парсинг в процессе... {progress_percent}%\n"
                f"{progress_bar}\n\n"
                f"📊 Прогресс: {current}/{total_count}\n"
                f"✅ Успешно: {processed_success}\n"
                f"❌ Ошибок: {processed_error}\n\n"
                f"ℹ️ Обрабатываются посты ВКонтакте..."
            )
        
        # Начинаем парсинг
        await update_progress(0, 0, 0)  # Показываем начальный прогресс
        
        try:
            for i, post in enumerate(posts, 1):
                try:
                    # Обновляем статус каждые 2 поста или 10%
                    if i % max(2, total_count // 10) == 0 or i == 1 or i == total_count:
                        await update_progress(i, success_count, error_count)

                    # Получаем данные поста
                    post_link = post['link'] if isinstance(post, dict) else post[1]
                    post_id = None
                    
                    # Определяем ID поста из URL
                    match = re.search(r'wall(-?\d+)_(\d+)', post_link)
                    if match:
                        owner_id = match.group(1)
                        post_item_id = match.group(2)
                        post_id = f"{owner_id}_{post_item_id}"
                    else:
                        logger.error(f"Не удалось определить ID поста из ссылки: {post_link}")
                        error_count += 1
                        continue
                    
                    # Парсим активности поста
                    logger.info(f"Парсинг поста: {post_id}")
                    activities = await parse_post_activities(post_id, vk_token)
                    
                    # Сохраняем результаты
                    all_results['total_likes'] += len(activities['likes'])
                    all_results['total_comments'] += len(activities['comments'])
                    all_results['total_reposts'] += len(activities['reposts'])
                    all_results['all_users'].update(activities['likes'])
                    all_results['all_users'].update(activities['comments'])
                    all_results['all_users'].update(activities['reposts'])
                    
                    all_results['posts_data'].append({
                        'link': post_link,
                        'post_id': post_id,
                        'likes': activities['likes'],
                        'comments': activities['comments'],
                        'reposts': activities['reposts']
                    })
                    
                    # Обновляем счетчик успешных парсингов
                    success_count += 1
                    
                    # Небольшая задержка между запросами
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    error_count += 1
                    logger.error(f"Ошибка при парсинге поста {post_link if 'post_link' in locals() else 'Unknown'}: {e}")
                    continue
        except Exception as e:
            logger.error(f"Общая ошибка при обработке постов: {e}")
            await status_message.edit_text(f"❌ Ошибка при обработке постов: {e}")
            return
        
        # Формируем отчет о результатах парсинга
        report = f"📊 Общая статистика парсинга\n"
        report += f"Время парсинга: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        report += "=" * 50 + "\n"
        
        # Определяем режим парсинга
        parse_option_name = "Стандарт"
        if file_data and 'scheduled_parse_data' in file_data:
            try:
                parse_data = json.loads(file_data.get('scheduled_parse_data', '{}'))
                if isinstance(parse_data, dict) and 'parse_option' in parse_data:
                    parse_option_name = get_parse_option_name(parse_data['parse_option'])
            except:
                pass
                
        report += f"⚙️ Режим парсинга: {parse_option_name}\n"
        report += f"📝 Всего постов обработано: {total_count}\n"
        report += f"✅ Успешно: {success_count}\n"
        report += f"❌ Ошибок: {error_count}\n"
        report += f"❤️ Всего лайков: {all_results['total_likes']}\n"
        report += f"💬 Всего комментариев: {all_results['total_comments']}\n"
        report += f"🔄 Всего репостов: {all_results['total_reposts']}\n"
        report += f"🔢 Всего активностей: {all_results['total_likes'] + all_results['total_comments'] + all_results['total_reposts']}\n"
        report += f"👥 Всего уникальных пользователей: {len(all_results['all_users'])}\n"
        report += "=" * 50 + "\n\n"

        # Добавляем детальную информацию по каждому посту
        for post_data in all_results['posts_data']:
            report += f"Пост: {post_data['link']}\n"
            report += f"ID поста: {post_data['post_id']}\n"
            report += f"❤️ Лайки: {len(post_data['likes'])}\n"
            report += f"💬 Комментарии: {len(post_data['comments'])}\n"
            report += f"🔄 Репосты: {len(post_data['reposts'])}\n"
            report += "-" * 30 + "\n"
            
            # Информация о пользователях (сокращенная для экономии места)
            report += f"Пользователи (лайки): {', '.join(str(uid) for uid in list(post_data['likes'])[:5])}...\n"
            report += f"Пользователи (комментарии): {', '.join(str(uid) for uid in list(post_data['comments'])[:5])}...\n"
            report += f"Пользователи (репосты): {', '.join(str(uid) for uid in list(post_data['reposts'])[:5])}...\n"
            report += "=" * 50 + "\n\n"

        # Сохраняем отчет в TXT файл
        with open(txt_file, 'w', encoding='utf-8') as f:
            f.write(report)

        # Сохраняем данные в CSV файл
        with open(csv_file, 'w', encoding='utf-8', newline='') as f:
            f.write("user_id,post_id,activity_type\n")
            for post_data in all_results['posts_data']:
                post_id = post_data['post_id']
                for user_id in post_data['likes']:
                    f.write(f"{user_id},{post_id},like\n")
                for user_id in post_data['comments']:
                    f.write(f"{user_id},{post_id},comment\n")
                for user_id in post_data['reposts']:
                    f.write(f"{user_id},{post_id},repost\n")

        # Обновляем статус файла в архиве, если он указан
        if file_data:
            await update_file_status(file_id, "completed", txt_file)
        
        # Отправляем итоговые файлы
        try:
            # Обновляем статус
            await status_message.edit_text(
                f"✅ Парсинг завершен!\n\n"
                f"📊 Результаты:\n"
                f"📝 Всего постов: {total_count}\n"
                f"✅ Успешно: {success_count}\n"
                f"❌ Ошибок: {error_count}\n"
                f"👥 Всего уникальных пользователей: {len(all_results['all_users'])}\n\n"
                f"⏳ Подготовка файлов с результатами..."
            )
            
            # Отправляем мини-статистику для каждого найденного поста
            mini_stats_message = "📋 <b>Мини-статистика постов:</b>\n\n"
            
            # Показываем только первые 10 постов
            posts_to_show = min(10, len(all_results['posts_data']))
            for i, post_data in enumerate(all_results['posts_data'][:posts_to_show], 1):
                link = post_data['link']
                
                # Получаем актуальное время публикации непосредственно из API
                try:
                    logger.info(f"Запрос актуального времени публикации для мини-статистики, пост: {link}")
                    publish_time, _, error_reason = await get_post_publish_time(link)
                    if publish_time:
                        time_str = publish_time.strftime('%d.%m.%Y %H:%M')
                    else:
                        time_str = f"Время неизвестно: {error_reason if error_reason else 'причина не указана'}"
                except Exception as e:
                    logger.error(f"Ошибка при получении времени публикации для мини-статистики: {e}")
                    time_str = "Ошибка определения времени"
                
                likes_count = len(post_data['likes'])
                comments_count = len(post_data['comments'])
                reposts_count = len(post_data['reposts'])
                
                mini_stats_message += f"<b>Пост {i}:</b>\n"
                mini_stats_message += f"🔗 <a href='{link}'>Ссылка на пост</a>\n"
                mini_stats_message += f"🕒 Время публикации: {time_str}\n"
                mini_stats_message += f"❤️ Лайки: {likes_count}\n"
                mini_stats_message += f"💬 Комментарии: {comments_count}\n"
                mini_stats_message += f"🔄 Репосты: {reposts_count}\n"
                mini_stats_message += f"👥 Активности: {likes_count + comments_count + reposts_count}\n"
                mini_stats_message += "-" * 30 + "\n"
            
            if len(all_results['posts_data']) > posts_to_show:
                mini_stats_message += f"\n... и еще {len(all_results['posts_data']) - posts_to_show} постов\n"
            
            # Отправляем сообщение с мини-статистикой
            await bot.send_message(
                message.chat.id,
                mini_stats_message,
                parse_mode="HTML"
            )
            
            # Отправляем TXT файл с результатами
            if os.path.exists(txt_file) and os.path.getsize(txt_file) > 0:
                await bot.send_document(
                    message.chat.id,
                    document=types.FSInputFile(txt_file),
                    caption=f"📊 Общие результаты парсинга:\n"
                           f"📝 Всего постов: {total_count}\n"
                           f"✅ Успешно: {success_count}\n"
                           f"❌ Ошибок: {error_count}\n"
                           f"👥 Всего пользователей: {len(all_results['all_users'])}"
                )

            # Отправляем CSV файл с ID пользователей
            if os.path.exists(csv_file) and os.path.getsize(csv_file) > 0:
                await bot.send_document(
                    message.chat.id,
                    document=types.FSInputFile(csv_file),
                    caption="📋 CSV файл со всеми ID пользователей"
                )
            
            # Финальное сообщение
            # Определяем время парсинга (опцию)
            parse_option_name = "Стандарт"
            if file_data and 'scheduled_parse_data' in file_data:
                try:
                    parse_data = json.loads(file_data.get('scheduled_parse_data', '{}'))
                    if isinstance(parse_data, dict) and 'parse_option' in parse_data:
                        parse_option_name = get_parse_option_name(parse_data['parse_option'])
                except:
                    pass
            
            # Вычисляем общее количество активностей
            total_activities = all_results['total_likes'] + all_results['total_comments'] + all_results['total_reposts']
            
            # Создаем клавиатуру для просмотра результатов
            keyboard = InlineKeyboardBuilder()
            
            if file_id:
                keyboard.add(InlineKeyboardButton(text="📊 Результаты", callback_data=f"results:{file_id}"))
                keyboard.add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
            
            keyboard.add(InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main"))
            keyboard.adjust(1)
                
            await status_message.edit_text(
                f"✅ Парсинг успешно завершен!\n\n"
                f"📊 <b>Результаты:</b>\n"
                f"⚙️ Режим парсинга: {parse_option_name}\n"
                f"📝 Всего постов: {total_count}\n"
                f"✅ Успешно: {success_count}\n"
                f"❌ Ошибок: {error_count}\n\n"
                f"👥 <b>Активности:</b>\n"
                f"❤️ Лайки: {all_results['total_likes']}\n"
                f"💬 Комментарии: {all_results['total_comments']}\n"
                f"🔄 Репосты: {all_results['total_reposts']}\n"
                f"🔢 Всего активностей: {total_activities}\n"
                f"👤 Уникальных пользователей: {len(all_results['all_users'])}\n\n"
                f"📁 Результаты сохранены и отправлены.",
                reply_markup=keyboard.as_markup(),
                parse_mode="HTML"
            )
            
        except Exception as e:
            logger.error(f"Ошибка при отправке файлов: {e}")
            await status_message.edit_text(
                f"✅ Парсинг завершен, но произошла ошибка при отправке файлов: {e}\n\n"
                f"📊 Результаты:\n"
                f"📝 Всего постов: {total_count}\n"
                f"✅ Успешно: {success_count}\n"
                f"❌ Ошибок: {error_count}"
            )
        
    except Exception as e:
        error_msg = f"❌ Ошибка при выполнении парсинга: {e}"
        await message.reply(error_msg)
        logger.error(error_msg)

# Функция для запуска планировщика
def run_scheduler():
    """Запускает планировщик задач"""
    logger.info("Запуск планировщика задач")
    
    # Добавляем механизм повторных попыток
    max_retries = 5
    retry_delay = 60  # секунды
    current_retry = 0
    
    while current_retry < max_retries:
        try:
            # Запускаем отдельный поток для планировщика
            scheduler_thread = threading.Thread(target=scheduler_job)
            scheduler_thread.daemon = True
            scheduler_thread.start()
            logger.info("Планировщик успешно запущен")
            return
        except Exception as e:
            current_retry += 1
            logger.error(f"Ошибка при запуске планировщика (попытка {current_retry}/{max_retries}): {e}")
            if current_retry < max_retries:
                logger.info(f"Повторная попытка через {retry_delay} секунд...")
                time.sleep(retry_delay)
            else:
                logger.error("Превышено максимальное количество попыток запуска планировщика")
                
# Функция для выполнения планировщика в отдельном потоке
def scheduler_job():
    """Выполняет работу планировщика в отдельном потоке"""
    logger.info("Запущен поток планировщика")
    
    # Устанавливаем проверку запланированных парсингов каждые 30 секунд
    schedule.every(30).seconds.do(check_scheduled_parsing)
    
    # Запускаем первую проверку сразу
    try:
        check_scheduled_parsing()
    except Exception as e:
        logger.error(f"Ошибка при первой проверке запланированных задач: {e}")
    
    # Бесконечный цикл для выполнения запланированных задач
    while True:
        try:
            schedule.run_pending()
            time.sleep(5)  # Проверяем каждые 5 секунд
        except Exception as e:
            logger.error(f"Ошибка в цикле планировщика: {e}")
            # Продолжаем работу даже после ошибки
            time.sleep(10)  # Делаем паузу перед следующей попыткой

def check_scheduled_parsing():
    """Выполняет проверку запланированных задач парсинга"""
    try:
        # Получаем глобальный цикл событий asyncio
        loop = asyncio.get_event_loop()
        # Создаем асинхронное событие для запуска проверки и ожидаем его завершения
        asyncio.run_coroutine_threadsafe(_check_and_run_scheduled(), loop).result(timeout=30)
        return True
    except Exception as e:
        logger.error(f"Ошибка при проверке запланированных парсингов: {e}")
        return False

async def _check_and_run_scheduled():
    """Проверяет и запускает запланированные задачи парсинга"""
    try:
        logger.info("Проверка запланированных задач парсинга")
        vk_token = await load_vk_token_async()
        if not vk_token:
            logger.warning("Не настроен токен VK API, пропуск проверки запланированных задач")
            return
        
        # Получаем все запланированные файлы
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                '''SELECT * FROM archive_files 
                WHERE status = 'scheduled' AND scheduled_parse_time IS NOT NULL 
                ORDER BY scheduled_parse_time ASC'''
            )
            files = await cursor.fetchall()
        
        scheduled_count = 0
        
        # Проверяем каждый запланированный файл
        now = datetime.now()
        
        for file in files:
            try:
                # Преобразуем sqlite3.Row в dict для безопасного использования .get()
                file_dict = dict(file)
                
                # Получаем запланированное время парсинга
                scheduled_time_str = file_dict.get('scheduled_parse_time')
                if not scheduled_time_str:
                    logger.warning(f"Файл {file_dict.get('file_id', 'Unknown')} не имеет запланированного времени парсинга")
                    continue
                
                # Парсим строку с датой
                try:
                    scheduled_time = parse_iso_datetime(scheduled_time_str)
                except Exception as e:
                    logger.error(f"Ошибка при парсинге времени '{scheduled_time_str}': {e}")
                    continue
                
                # Если время еще не пришло, пропускаем
                if scheduled_time > datetime.now():
                    logger.debug(f"Время парсинга для {file_dict.get('file_id', 'Unknown')} еще не наступило: {scheduled_time}")
                    continue
                
                # Если время уже прошло, запускаем парсинг
                logger.info(f"Запускаем запланированный парсинг для файла {file_dict.get('file_name', 'Unknown')} (ID: {file_dict.get('file_id', 'Unknown')})")
                
                # Получаем ID файла для парсинга
                numeric_id = file_dict.get('id')
                if not numeric_id:
                    logger.warning(f"Файл {file_dict.get('file_id', 'Unknown')} не имеет числового ID")
                    continue
                
                # Получаем ID чата из сохраненных данных
                chat_id = None
                scheduled_parse_data = file_dict.get('scheduled_parse_data')
                
                if scheduled_parse_data:
                    try:
                        parse_data = json.loads(scheduled_parse_data)
                        # Проверяем наличие chat_id в данных
                        if isinstance(parse_data, dict) and 'chat_id' in parse_data:
                            chat_id = parse_data['chat_id']
                    except Exception as e:
                        file_id_str = file_dict.get('file_id', 'Unknown')
                        logger.warning(f"Ошибка при извлечении данных для файла {file_id_str}: {e}")
                    
                    if not chat_id:
                        file_id_str = file_dict.get('file_id', 'Unknown')
                        logger.warning(f"Невозможно запустить запланированный парсинг для {file_id_str}: не указан ID чата")
                        continue
                    
                # Запускаем парсинг, используя числовой ID вместо текстового
                logger.info(f"Запуск запланированного парсинга для файла с ID={numeric_id}")
                asyncio.create_task(parse_scheduled_file(str(numeric_id), vk_token, chat_id))
                scheduled_count += 1
            except Exception as e:
                logger.error(f"Ошибка при обработке запланированного файла {file.get('file_id', 'Unknown')}: {e}")
                continue
                    
        if scheduled_count > 0:
            logger.info(f"Запущено {scheduled_count} запланированных задач для выполнения")
        else:
            logger.info("Нет запланированных задач для запуска в данный момент")
            
    except Exception as e:
        logger.error(f"Ошибка при проверке запланированных задач: {e}")

# Создаем основное меню
def get_main_menu():
    """Создает основное меню бота"""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📤 Загрузить файл")],
            [KeyboardButton(text="📋 Архив файлов"), KeyboardButton(text="🔍 Посмотреть запланированные")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="📊 Статистика")],
            [KeyboardButton(text="❓ FAQ/Помощь")]
        ],
        resize_keyboard=True
    )
    return keyboard

# Создаем меню настроек
def get_settings_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔑 Указать токен VK API")],
            [KeyboardButton(text="⏱ Настройка времени парсинга"), KeyboardButton(text="📤 Формат результатов")],
            [KeyboardButton(text="🔙 Главное меню")]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите настройку"
    )
    return keyboard

# Создаем меню выбора времени парсинга по умолчанию
def get_default_parse_time_menu():
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Стандарт (23:50 после публикации)")],
            [KeyboardButton(text="⚡ Через 2 минуты после нажатия кнопки")],
            [KeyboardButton(text="⏱ За 5 минут до истечения 24ч")],
            [KeyboardButton(text="⏰ За 30 минут до истечения 24ч")],
            [KeyboardButton(text="🕰 За 1 час до истечения 24ч")],
            [KeyboardButton(text="⚙️ Настройки")]
        ],
        resize_keyboard=True
    )
    return keyboard

# Команда /start
@dp.message(CommandStart())
async def start(message: types.Message):
    welcome_text = """👋 Привет! Я бот для парсинга активностей постов ВКонтакте.

🔍 Что я умею:
• Парсить лайки, комментарии и репосты
• Работать с постами из Маркет-платформы и VK AdBlogger
• Автоматически парсить посты за 10 минут до удаления
• Сохранять результаты в удобном формате

📝 Поддерживаемые форматы:
• HTML файлы (сохраненные страницы) 
• PDF файлы
• TXT файлы

⚙️ Для начала работы:
1. Укажите токен VK API в настройках
2. Загрузите файл с данными
3. Выберите режим парсинга

Используйте кнопки меню для навигации:"""
    
    await message.reply(welcome_text, reply_markup=get_main_menu())

# Обработка загруженных файлов
@dp.message(lambda message: message.document is not None)
async def handle_file(message: types.Message):
    """Обработчик загрузки файла"""
    try:
        logger.info(f"Получен файл: {message.document.file_name}")
        
        # Проверка расширения файла
        file_name = message.document.file_name
        if not (file_name.endswith('.txt') or file_name.endswith('.html') or file_name.endswith('.pdf')):
            await message.reply("❌ Поддерживаются только файлы .txt, .html и .pdf")
            return
            
        # Отправляем сообщение о начале обработки
        status_message = await message.reply("⏳ Загрузка файла...")
        
        # Загружаем файл
        file_id = message.document.file_id
        file_info = await bot.get_file(file_id)
        file_path = os.path.join(UPLOAD_DIR, file_name)
        
        await bot.download_file(file_info.file_path, file_path)
        logger.info(f"Файл загружен: {file_path}")
        
        # Сохраняем в архив
        db_file_id = await add_file_to_archive(file_id, file_name, file_path, None)
        
        if not db_file_id:
            await status_message.edit_text("❌ Не удалось сохранить файл в архиве")
            return
        
        # Извлекаем ссылки и время публикации
        await status_message.edit_text("🔍 Извлечение ссылок и анализ файла...")
        
        posts = extract_links_and_time(file_path)
        
        if not posts or len(posts) == 0:
            await status_message.edit_text("❌ Не удалось извлечь ссылки из файла")
            return
        
        # Отправляем статус о найденных постах
        await status_message.edit_text(
            f"✅ Файл '{file_name}' успешно загружен!\n\n"
            f"🔍 Найдено {len(posts)} ссылок на посты"
        )
        
        # Создаем мини-статистику постов
        mini_stats_message = "📋 <b>Предварительная статистика постов:</b>\n\n"
        
        # Обрабатываем первые 10 постов
        posts_to_show = min(10, len(posts))
        for i in range(posts_to_show):
            link = posts[i]['link']
            
            # Запрашиваем свежее время публикации через API
            try:
                publish_time, _, error_reason = await get_post_publish_time(link)
                if publish_time:
                    time_str = publish_time.strftime('%d.%m.%Y %H:%M')
                else:
                    time_str = f"Время неизвестно: {error_reason if error_reason else 'причина не указана'}"
            except Exception as e:
                logger.error(f"Ошибка при получении времени публикации для {link}: {e}")
                time_str = "Не удалось определить"
            
            mini_stats_message += f"<b>Пост {i+1}:</b>\n"
            mini_stats_message += f"🔗 <a href='{link}'>Ссылка на пост</a>\n"
            mini_stats_message += f"🕒 Время публикации: {time_str}\n"
            mini_stats_message += "-" * 30 + "\n"
        
        if len(posts) > 10:
            mini_stats_message += f"\n... и еще {len(posts) - 10} постов\n"
        
        # Отправляем мини-статистику
        await bot.send_message(
            message.chat.id,
            mini_stats_message,
            parse_mode="HTML"
        )
        
        # Создаем меню выбора времени парсинга
        keyboard = get_parse_time_options_menu(db_file_id)
        
        # Отправляем сообщение с меню
        await bot.send_message(
            message.chat.id,
            f"Выберите время парсинга для постов:", 
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке файла: {e}")
        await message.reply(f"❌ Ошибка: {str(e)[:100]}")

# Обработка текстовых команд меню
@dp.message()
async def handle_menu(message: types.Message):
    """Обработчик текстовых сообщений"""
    # Пропускаем обработку, если сообщение содержит документ
    if message.document is not None:
        # Документы обрабатываются в другом обработчике
        logger.info("Сообщение содержит документ, передаем в специализированный обработчик")
        return
    
    # Проверяем, есть ли текст в сообщении
    if not message.text:
        logger.warning("Получено сообщение без текста, игнорируем")
        return
    
    if message.text == "📤 Загрузить файл":
        settings = await load_settings_from_db()
        if not settings.get('vk_token'):
            await message.reply(
                "⚠️ Токен VK API не настроен.\n"
                "Пожалуйста, настройте токен VK API в меню настроек перед загрузкой файла."
            )
            return
        
        await message.reply(
            "📤 Отправьте файл (.txt, .html, .pdf) с ссылками на посты ВКонтакте.\n"
            "После загрузки вы сможете выбрать время парсинга для постов."
        )
        return
    
    elif message.text == "📋 Архив файлов":
        await show_archive_page(message, 1)
        return
    
    elif message.text == "⚙️ Настройки":
        # Показываем меню настроек
        keyboard = get_settings_menu()
        await message.reply("⚙️ Меню настроек:", reply_markup=keyboard)
        return
    
    elif message.text == "📊 Статистика":
        # Получаем статистику
        stats = await get_stats()
        
        # Формируем сообщение со статистикой
        text = (
            "📊 <b>Статистика парсера:</b>\n\n"
            f"📑 Всего файлов: {stats['total_files']}\n"
            f"📝 Всего постов: {stats['total_posts']}\n"
            f"✅ Обработано постов: {stats['completed_posts']}\n"
            f"👥 Собрано активностей: {stats['total_results']}\n"
        )
        
        await message.reply(text, parse_mode="HTML")
        return
    
    elif message.text == "🔍 Посмотреть запланированные":
        await show_scheduled_parsing(message)
        return
    
    elif message.text == "❓ FAQ/Помощь":
        await show_faq(message)
        return
    
    elif message.text == "🔙 Главное меню":
        await message.reply(
            "Вы вернулись в главное меню. Выберите действие:",
            reply_markup=get_main_menu()
        )
        return
        
    elif message.text.startswith("/token"):
        # Обрабатываем команду установки токена
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply(
                "Пожалуйста, укажите токен VK API после команды /token.\n"
                "Пример: /token vk1.a.AbCdEfG..."
            )
            return
        
        token = parts[1].strip()
        success, msg = await validate_and_save_token(token)
        
        if success:
            await message.reply(msg, reply_markup=get_main_menu())
        else:
            await message.reply(msg)
        return
    
    # Обработка кнопок из меню настроек
    elif message.text == "🔑 Указать токен VK API":
        await message.reply(
            "Для установки токена VK API, пожалуйста, отправьте команду в формате:\n"
            "/token ВАШ_ТОКЕН\n\n"
            "Как получить токен VK API:\n"
            "1. Перейдите на сайт https://vkhost.github.io/\n"
            "2. Выберите Kate Mobile\n"
            "3. Разрешите доступ\n"
            "4. Скопируйте часть URL после access_token= и до &expires_in"
        )
        return
    
    elif message.text == "⏱ Настройка времени парсинга":
        # Показываем меню выбора времени парсинга по умолчанию
        keyboard = get_default_parse_time_menu()
        
        # Получаем текущие настройки
        settings = await load_settings_from_db()
        current_parse_option = settings.get('default_parse_option', PARSE_OPTION_STANDARD)
        parse_option_name = get_parse_option_name(current_parse_option)
        
        await message.reply(
            f"⏱ Выберите время парсинга по умолчанию:\n\n"
            f"Текущая настройка: <b>{parse_option_name}</b>\n\n"
            f"Это время будет использоваться при парсинге, если вы не выберете другой вариант после загрузки файла.",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
    
    # Обрабатываем выбор времени парсинга по умолчанию (из меню настроек)
    elif message.text in [
        "📊 Стандарт (23:50 после публикации)",
        "⚡ Через 2 минуты после нажатия кнопки",
        "⏱ За 5 минут до истечения 24ч",
        "⏰ За 30 минут до истечения 24ч",
        "🕰 За 1 час до истечения 24ч"
    ]:
        # Определяем выбранную опцию парсинга
        option_map = {
            "📊 Стандарт (23:50 после публикации)": PARSE_OPTION_STANDARD,
            "⚡ Через 2 минуты после нажатия кнопки": PARSE_OPTION_2MIN,
            "⏱ За 5 минут до истечения 24ч": PARSE_OPTION_5MIN,
            "⏰ За 30 минут до истечения 24ч": PARSE_OPTION_30MIN,
            "🕰 За 1 час до истечения 24ч": PARSE_OPTION_1HOUR
        }
        selected_option = option_map.get(message.text, PARSE_OPTION_STANDARD)
        
        # Сохраняем выбранную опцию в настройках
        settings = await load_settings_from_db()
        settings['default_parse_option'] = selected_option
        await save_settings_to_db(settings)
        
        # Возвращаемся в меню настроек
        keyboard = get_settings_menu()
        await message.reply(
            f"✅ Время парсинга по умолчанию успешно установлено: <b>{get_parse_option_name(selected_option)}</b>",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        return
        
    else:
        # Обрабатываем неизвестные команды
        await message.reply(
            "Пожалуйста, выберите действие из меню.",
            reply_markup=get_main_menu()
        )

# Функции для работы с архивом файлов
async def add_file_to_archive(file_id, file_name, file_path, scheduled_parse_time):
    """Добавляет файл в архив"""
    try:
        current_time = datetime.now().isoformat()
        
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем структуру таблицы
            cursor = await db.execute("PRAGMA table_info(archive_files)")
            columns = await cursor.fetchall()
            column_names = [column[1] for column in columns]
            
            # Проверяем, нужен ли file_id и есть ли поле uploaded_at
            file_id_required = False
            for column in columns:
                if column[1] == 'file_id' and column[3] == 1:  # column[3] == 1 значит NOT NULL
                    file_id_required = True
            
            # Если file_id требуется, но не передан, создаем его
            if file_id_required and file_id is None:
                file_id = str(hash(file_name + file_path + current_time) % 1000000)  # Генерируем уникальный ID
            
            # Строим SQL запрос в зависимости от структуры таблицы
            if 'uploaded_at' in column_names:
                upload_field = 'uploaded_at'
            else:
                upload_field = 'upload_date'
                
            if 'status' in column_names:
                status_field = 'status'
            else:
                status_field = 'parse_status'
            
            # Строим SQL запрос БЕЗ RETURNING
            query = f'INSERT INTO archive_files (file_id, file_name, file_path, {upload_field}, {status_field}, scheduled_parse_time) VALUES (?, ?, ?, ?, ?, ?)'
            
            await db.execute(
                query,
                (file_id, file_name, file_path, current_time, "pending", scheduled_parse_time)
            )
            await db.commit()
            
            # Получаем ID последней вставленной записи отдельным запросом
            cursor = await db.execute("SELECT last_insert_rowid()")
            result = await cursor.fetchone()
            
            if result:
                logger.info(f"Файл {file_name} добавлен в архив с ID {result[0]}")
                return result[0]  # Возвращаем ID, присвоенный базой данных
            else:
                logger.error("Не удалось получить ID файла после добавления")
                return None
    except Exception as e:
        logger.error(f"Ошибка при добавлении файла в архив: {e}")
        return None

async def update_file_status(file_id, status, result_file_path=None, scheduled_parse_time=None, scheduled_parse_data=None, earliest_parse_time=None, latest_parse_time=None):
    """
    Обновляет статус файла в архиве и дополнительные данные
    
    Args:
        file_id: ID файла
        status: Новый статус файла (pending, scheduled, parsing, completed, error)
        result_file_path: Путь к файлу с результатами (опционально)
        scheduled_parse_time: Запланированное время парсинга (опционально)
        scheduled_parse_data: Данные для запланированного парсинга в формате JSON (опционально)
        earliest_parse_time: Самое раннее время парсинга для группы постов (опционально)
        latest_parse_time: Самое позднее время парсинга для группы постов (опционально)
    
    Returns:
        bool: True если статус успешно обновлен, иначе False
    """
    try:
        logger.info(f"Обновление статуса файла {file_id}: {status}, время парсинга: {scheduled_parse_time}")
        
        update_data = {"status": status}
        
        if result_file_path:
            update_data["result_file_path"] = result_file_path
            
        if scheduled_parse_time:
            if isinstance(scheduled_parse_time, datetime):
                scheduled_parse_time = scheduled_parse_time.isoformat()
            update_data["scheduled_parse_time"] = scheduled_parse_time
        
        # Если указаны данные парсинга, добавляем их
        if scheduled_parse_data:
            update_data["scheduled_parse_data"] = scheduled_parse_data
            
        # Добавляем earliest_parse_time если указано
        if earliest_parse_time:
            if isinstance(earliest_parse_time, datetime):
                earliest_parse_time = earliest_parse_time.isoformat()
            update_data["earliest_parse_time"] = earliest_parse_time
            
        # Добавляем latest_parse_time если указано
        if latest_parse_time:
            if isinstance(latest_parse_time, datetime):
                latest_parse_time = latest_parse_time.isoformat()
            update_data["latest_parse_time"] = latest_parse_time
            
        # Обновляем данные файла в БД
        result = await update_file_data(file_id, **update_data)
            
            logger.info(f"Обновлен статус файла {file_id} на '{status}'")
        return result
    except Exception as e:
        logger.error(f"Ошибка при обновлении статуса файла {file_id}: {e}", exc_info=True)
        return False

async def get_archive_files() -> List[Dict[str, Any]]:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT * FROM archive_files ORDER BY uploaded_at DESC')
            rows = await cursor.fetchall()
            
            columns = [description[0] for description in cursor.description]
            files = []
            
            for row in rows:
                file_data = {}
                for i, column in enumerate(columns):
                    file_data[column] = row[i]
                files.append(file_data)
            
            return files
    except Exception as e:
        logger.error(f"Ошибка при получении списка файлов из архива: {e}")
        return []

async def get_file_by_id(file_id: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о файле по его ID
    
    Args:
        file_id: ID файла в архиве
        
    Returns:
        dict: Словарь с информацией о файле или None, если файл не найден
    """
    try:
        logger.info(f"Получение информации о файле с ID: {file_id}")
        
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
                    cursor = await db.execute('SELECT * FROM archive_files WHERE id = ?', (file_id,))
                    file = await cursor.fetchone()
            
            if not file:
                logger.warning(f"Файл с ID {file_id} не найден в архиве")
                return None
                
            # Преобразуем Row в dict для удобства работы
                return dict(file)
            
    except Exception as e:
        logger.error(f"Ошибка при получении информации о файле {file_id}: {e}")
        return None

# Настройки стандартного парсинга
DEFAULT_PARSE_BEFORE_DELETION_MINUTES = 10  # За сколько минут до удаления (24ч) парсить пост
DEFAULT_MIN_PARSE_DELAY_MINUTES = 10  # Минимальная задержка перед парсингом для новых постов

# Задаем время стандартного парсинга относительно времени публикации
def calculate_standard_parse_time(publish_time: datetime) -> datetime:
    """
    Рассчитывает стандартное время парсинга: за 10 минут до удаления (24 часа от публикации)
    
    Args:
        publish_time: Время публикации поста
        
    Returns:
        datetime: Время парсинга
    """
    # Добавляем 23 часа 50 минут к времени публикации
    return publish_time + timedelta(hours=23, minutes=50)

# Функция для расчета времени парсинга с учетом различных вариантов
def calculate_parse_time(publish_time: datetime, parse_option: str = PARSE_OPTION_STANDARD) -> datetime:
    """
    Рассчитывает время парсинга в зависимости от выбранного варианта
    
    Args:
        publish_time: Время публикации поста
        parse_option: Вариант парсинга:
            - PARSE_OPTION_STANDARD: Стандартный (23:50 после публикации)
            - PARSE_OPTION_2MIN: Через 2 минуты после публикации
            - PARSE_OPTION_5MIN: За 5 минут до истечения 24ч
            - PARSE_OPTION_30MIN: За 30 минут до истечения 24ч
            - PARSE_OPTION_1HOUR: За 1 час до истечения 24ч
        
    Returns:
        datetime: Время парсинга
    """
    now = datetime.now()
    post_age = now - publish_time
    deletion_time = publish_time + timedelta(hours=24)  # Время удаления поста (24ч после публикации)
    
    # Проверяем вариант парсинга
    if parse_option == PARSE_OPTION_2MIN:
        # Через 2 минуты после НАЖАТИЯ КНОПКИ (не публикации)
        parse_time = now + timedelta(minutes=2)
        
    elif parse_option == PARSE_OPTION_5MIN:
        # За 5 минут до удаления
        parse_time = deletion_time - timedelta(minutes=5)
        
    elif parse_option == PARSE_OPTION_30MIN:
        # За 30 минут до удаления
        parse_time = deletion_time - timedelta(minutes=30)
        
    elif parse_option == PARSE_OPTION_1HOUR:
        # За 1 час до удаления
        parse_time = deletion_time - timedelta(hours=1)
        
    else:  # PARSE_OPTION_STANDARD или любой другой вариант по умолчанию
        # Стандартный вариант: 23:50 после публикации
        parse_time = publish_time + timedelta(hours=23, minutes=50)
    
    # Если пост старый и время парсинга уже прошло, парсим через минимальную задержку
    if parse_time <= now:
        return now + timedelta(minutes=DEFAULT_MIN_PARSE_DELAY_MINUTES)
    
    return parse_time

# Добавляем функцию для выполнения парсинга по расписанию
async def parse_scheduled_file(file_id, vk_token, chat_id):
    """Выполняет парсинг файла по расписанию"""
    try:
        # Получаем информацию о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            logger.error(f"Файл с ID {file_id} не найден при выполнении планового парсинга")
            return
        
        # Убедимся что file_data - это словарь
        if not isinstance(file_data, dict):
            file_data = dict(file_data)
        
        logger.info(f"Начинаем парсинг запланированного файла: ID={file_id}, имя={file_data.get('file_name')}")
        
        # Создаем фиктивное сообщение для передачи в функцию parse_now
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat = type('obj', (object,), {'id': chat_id})
                
            async def reply(self, text, reply_markup=None):
                return await bot.send_message(self.chat.id, text, reply_markup=reply_markup)
                
            async def edit_text(self, text, reply_markup=None):
                return await bot.send_message(self.chat.id, text, reply_markup=reply_markup)
        
        fake_message = FakeMessage(chat_id)
        
        # Отправляем уведомление о начале парсинга
        notification_message = await bot.send_message(
            chat_id,
            f"🔄 Начинается запланированный парсинг файла '{file_data.get('file_name')}'\n"
            f"⏳ Пожалуйста, подождите..."
        )
        
        # Выполняем парсинг
        logger.info(f"Запущен запланированный парсинг файла с ID {file_id}")
        await parse_now(fake_message, file_id)
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении запланированного парсинга: {e}")
        try:
            await bot.send_message(
                chat_id,
                f"❌ Ошибка при выполнении запланированного парсинга: {e}\n"
                f"Попробуйте выполнить парсинг вручную."
            )
        except Exception as notify_error:
            logger.error(f"Также не удалось отправить сообщение об ошибке в чат {chat_id}: {notify_error}")

# Функция для извлечения ссылок и времени с проверкой данных в БД
async def extract_links_and_time_with_db(file_path, file_id, parse_option=PARSE_OPTION_STANDARD, chat_id=None):
    """Извлекает ссылки и время публикации из файла с учетом данных из БД"""
        logger.info(f"Извлечение ссылок из файла {file_path} (без кеша)")
        
    try:
        # Сначала используем стандартную функцию для извлечения ссылок
            posts = extract_links_and_time(file_path)
        
        # Для каждой ссылки пытаемся получить время публикации через API
        if not posts:
            logger.warning(f"В файле {file_path} не найдены посты для планирования")
            return False
        
        # Находим самый ранний пост
        earliest_publish_time = None
        valid_posts = []
        
        # Валидация и нормализация данных постов
        for post in posts:
            try:
                # Проверяем наличие обязательных полей
                if 'link' not in post:
                    logger.warning(f"Пропущен пост без ссылки")
                    continue
                
                # Проверяем и нормализуем publish_time
                if 'publish_time' not in post or post['publish_time'] is None:
                    post['publish_time'] = datetime.now()
                elif isinstance(post['publish_time'], str):
                    try:
                        post['publish_time'] = datetime.fromisoformat(post['publish_time'])
                    except ValueError:
                        logger.warning(f"Не удалось преобразовать строку времени: {post['publish_time']}")
                        post['publish_time'] = datetime.now()
                
                # Проверяем валидность ссылки
                if not re.match(r'https?://(?:www\.)?vk\.com/', post['link']):
                    logger.warning(f"Невалидная ссылка: {post['link']}")
                    continue
                
                valid_posts.append(post)
                
                # Обновляем самое раннее время публикации
                if earliest_publish_time is None or post['publish_time'] < earliest_publish_time:
                    earliest_publish_time = post['publish_time']
            except Exception as e:
                logger.warning(f"Ошибка при обработке поста: {e}")
                continue
        
        if not valid_posts:
            logger.warning(f"Нет валидных постов для планирования парсинга")
            return False
        
        # Если не удалось определить время публикации, используем текущее
        if not earliest_publish_time:
            earliest_publish_time = datetime.now()
        
        # Рассчитываем оптимальное время парсинга для каждого поста в зависимости от выбранной опции
        now = datetime.now()
        earliest_parse_time = None
        latest_parse_time = None  # Добавляем отслеживание самого позднего времени парсинга
        
        logger.info(f"Начинаем расчёт времени парсинга для {len(valid_posts)} постов в режиме {parse_option}")
        
        for post in valid_posts:
            try:
                logger.info(f"Расчёт времени парсинга для поста {post.get('link')} с временем публикации {post['publish_time']}")
                # Используем функцию расчета времени парсинга с учетом выбранной опции
                custom_parse_time = calculate_parse_time(post['publish_time'], parse_option)
                post['parse_time'] = custom_parse_time
                logger.info(f"Рассчитанное время парсинга для поста {post.get('link')}: {custom_parse_time}")
                
                # Отслеживаем самое раннее время парсинга для всего файла
                if earliest_parse_time is None or custom_parse_time < earliest_parse_time:
                    earliest_parse_time = custom_parse_time
                
                # Отслеживаем самое позднее время парсинга
                if latest_parse_time is None or custom_parse_time > latest_parse_time:
                    latest_parse_time = custom_parse_time
            except Exception as e:
                logger.warning(f"Ошибка при расчете времени парсинга для поста {post.get('link')}: {e}")
                # В случае ошибки используем стандартное время парсинга
                post['parse_time'] = calculate_standard_parse_time(post['publish_time'])
                
                if earliest_parse_time is None or post['parse_time'] < earliest_parse_time:
                    earliest_parse_time = post['parse_time']
                
                if latest_parse_time is None or post['parse_time'] > latest_parse_time:
                    latest_parse_time = post['parse_time']
        
        # Если не удалось определить время парсинга, используем время через 10 минут
        if not earliest_parse_time:
            earliest_parse_time = now + timedelta(minutes=DEFAULT_MIN_PARSE_DELAY_MINUTES)
            latest_parse_time = earliest_parse_time
            logger.warning(f"Не удалось определить время парсинга, установлено через {DEFAULT_MIN_PARSE_DELAY_MINUTES} минут")
        
        # Если самое раннее время парсинга в прошлом, устанавливаем на ближайшее будущее
        if earliest_parse_time <= now:
            time_shift = now + timedelta(minutes=DEFAULT_MIN_PARSE_DELAY_MINUTES) - earliest_parse_time
            earliest_parse_time = now + timedelta(minutes=DEFAULT_MIN_PARSE_DELAY_MINUTES)
            # Сдвигаем все времена парсинга вперед на такое же время
            if latest_parse_time:
                latest_parse_time = latest_parse_time + time_shift
            # Обновляем времена парсинга для всех постов
            for post in valid_posts:
                if 'parse_time' in post and post['parse_time'] <= now:
                    post['parse_time'] = post['parse_time'] + time_shift
            logger.info(f"Расчетное время парсинга в прошлом, установлено через {DEFAULT_MIN_PARSE_DELAY_MINUTES} минут")
        
        # Журналируем рассчитанное время парсинга
        logger.info(f"Рассчитано время парсинга от {earliest_parse_time} до {latest_parse_time}")
        
        try:
            # Для каждого поста сохраняем информацию в БД
            for post in valid_posts:
                try:
                    post_id = await check_post_exists(post['link'])
                    
                    if post_id:
                        # Обновляем время парсинга
                        await update_post_parse_time(post_id, post['parse_time'])
                    else:
                        # Добавляем новый пост с заданным временем парсинга
                        await add_post(post['link'], post['publish_time'], post['parse_time'])
                except Exception as e:
                    logger.warning(f"Ошибка при сохранении информации о посте {post.get('link')}: {e}")
                    continue
            
            # Подготавливаем данные для JSON (обеспечивая сериализуемость)
            serialized_posts = []
            for post in valid_posts:
                try:
                    serialized_post = {
                        "link": post['link'],
                        "publish_time": post['publish_time'].isoformat() if isinstance(post['publish_time'], datetime) else str(post['publish_time']),
                        "parse_time": post['parse_time'].isoformat() if isinstance(post['parse_time'], datetime) else str(post['parse_time'])
                    }
                    serialized_posts.append(serialized_post)
                except Exception as e:
                    logger.warning(f"Ошибка при сериализации поста {post.get('link')}: {e}")
                    continue
            
            # Добавляем информацию для планировщика, включая выбранную опцию парсинга и ID чата
            scheduled_parse_data = json.dumps({
                "parse_option": parse_option,
                "posts": serialized_posts,
                "chat_id": chat_id  # Сохраняем ID чата для автоматического запуска
            }, ensure_ascii=False)
            
            # Обновляем статус файла в БД
            update_result = await update_file_status(
                file_id=file_id,
                status="scheduled",
                scheduled_parse_time=earliest_parse_time,
                scheduled_parse_data=scheduled_parse_data,
                earliest_parse_time=earliest_parse_time,
                latest_parse_time=latest_parse_time
            )
            
            if not update_result:
                logger.error(f"Не удалось обновить статус файла в БД для ID {file_id}")
                return False
            
            # Если время парсинга очень близко (менее 1 минуты), запускаем парсинг сразу
            if (earliest_parse_time - now).total_seconds() < 60:
                logger.info(f"Время парсинга близко, запускаем парсинг немедленно для файла {file_id}")
                asyncio.create_task(parse_scheduled_file(file_id, load_vk_token(), chat_id))
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении информации в БД: {e}", exc_info=True)
            return False
        
        logger.info(f"Парсинг для файла {file_id} успешно запланирован на {earliest_parse_time}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при планировании парсинга: {e}", exc_info=True)
        return False

# Основная функция для запуска бота
async def main():
    """Основная функция для запуска бота"""
    # Инициализируем базу данных
    await init_db()
    
    # Запускаем планировщик в отдельном потоке
    scheduler_thread = threading.Thread(target=scheduler_job)
    scheduler_thread.start()
    
    # Запускаем бота
    try:
        # Очищаем вебхуки на всякий случай
        await bot.delete_webhook(drop_pending_updates=True)
        
        # Запускаем поллинг
        await dp.start_polling(bot)
    finally:
        # Закрываем бота при выходе
        await bot.session.close()

async def check_post_exists(link: str) -> Optional[int]:
    """Проверяет существование поста по ссылке и возвращает его ID"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT id FROM posts WHERE link = ?', (link,))
            result = await cursor.fetchone()
            return result[0] if result else None
    except Exception as e:
        logger.error(f"Ошибка при проверке существования поста {link}: {e}")
        return None

async def update_post_parse_time(post_id: int, parse_time: datetime) -> bool:
    """Обновляет время парсинга поста"""
    try:
        if isinstance(parse_time, datetime):
            parse_time = parse_time.isoformat()
            
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                'UPDATE posts SET parse_time = ? WHERE id = ?',
                (parse_time, post_id)
            )
            await db.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при обновлении времени парсинга поста: {e}")
        return False

# Обработчик для кнопки "В главное меню"
@dp.callback_query(lambda c: c.data == "back_to_main" or c.data == "main_menu")
async def callback_to_main_menu(callback_query: types.CallbackQuery):
    """Обработчик для возврата в главное меню"""
    try:
        # Отвечаем на callback query
        await callback_query.answer("🏠 Переход в главное меню...")
        
        # Удаляем сообщение с инлайн-клавиатурой, если оно существует
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
        
        # Отправляем сообщение с основным меню
        await callback_query.message.answer(
            "🏠 Выберите действие:",
            reply_markup=get_main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка при возврате в главное меню: {e}")
        await callback_query.answer(f"Ошибка: {str(e)[:50]}")

# Обработчик для кнопки "Архив файлов"
@dp.callback_query(lambda c: c.data == "archive_list")
async def callback_to_archive(callback_query: types.CallbackQuery):
    """Обработчик для перехода к архиву файлов"""
    try:
        # Отвечаем на callback query
        await callback_query.answer("📋 Загрузка архива файлов...")
        
        # Удаляем текущее сообщение
        try:
            await callback_query.message.delete()
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
        
        # Показываем страницу архива
        await show_archive_page(callback_query.message, 1)
    except Exception as e:
        logger.error(f"Ошибка при переходе к архиву: {e}")
        await callback_query.answer(f"Ошибка: {str(e)[:50]}")

# Обработчик для кнопки "Результаты"
@dp.callback_query(lambda c: c.data.startswith("results:"))
async def callback_view_results(callback_query: types.CallbackQuery):
    """Обработчик для просмотра результатов парсинга"""
    try:
        # Отвечаем на callback query
        await callback_query.answer("📊 Загрузка результатов...")
        
        file_id = callback_query.data.split(':')[1]
        file_data = await get_file_by_id(file_id)
        
        if not file_data:
            await callback_query.message.reply("❌ Файл не найден в архиве.")
            return
        
        # Получаем результаты парсинга
        results = await get_parse_results(file_id)
        
        if not results:
            # Создаем клавиатуру для действий
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
            keyboard.add(InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main"))
            keyboard.adjust(1)
            
            await callback_query.message.reply(
                "❌ Результаты парсинга не найдены для данного файла.",
                reply_markup=keyboard.as_markup()
            )
            return
        
        # Формируем сообщение с результатами
        message_text = f"📊 <b>Результаты парсинга файла:</b>\n"
        message_text += f"📄 {file_data.get('file_name', 'Неизвестный файл')}\n\n"
        
        # Добавляем статистику
        message_text += f"📝 Всего постов: {results.get('total_posts', 0)}\n"
        message_text += f"👍 Лайки: {results.get('total_likes', 0)}\n"
        message_text += f"💬 Комментарии: {results.get('total_comments', 0)}\n"
        message_text += f"🔄 Репосты: {results.get('total_reposts', 0)}\n\n"
        
        # Общее количество активностей
        total_activities = results.get('total_likes', 0) + results.get('total_comments', 0) + results.get('total_reposts', 0)
        message_text += f"👥 Общее количество активностей: {total_activities}\n"
        
        # Создаем клавиатуру для действий
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="👁 Просмотр постов", callback_data=f"view_posts:{file_id}"))
        keyboard.add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
        keyboard.add(InlineKeyboardButton(text="🏠 В главное меню", callback_data="back_to_main"))
        keyboard.adjust(1)
        
        # Отправляем сообщение с результатами
        await callback_query.message.reply(
            message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Ошибка при просмотре результатов: {e}")
        await callback_query.answer(f"Ошибка: {str(e)[:50]}")

# Функция для получения результатов парсинга по file_id
async def get_parse_results(file_id):
    """Получает результаты парсинга для файла по его ID"""
    try:
        # Проверяем, есть ли файл в базе данных
        file_data = await get_file_by_id(file_id)
        if not file_data:
            return None
        
        # Ищем результаты парсинга в директории результатов
        results_dir = os.path.join(BASE_DIR, 'results')
        file_prefix = f"file_{file_id}_"
        
        # Данные для результатов
        results = {
            'total_posts': 0,
            'total_likes': 0,
            'total_comments': 0,
            'total_reposts': 0,
            'all_users': set(),
            'posts_data': []
        }
        
        # Ищем самый свежий файл результатов для этого файла
        result_files = [f for f in os.listdir(results_dir) if f.startswith(file_prefix) and f.endswith('.txt')]
        
        if not result_files:
            # Если нет файлов результатов, пробуем получить данные из базы
            async with aiosqlite.connect(DB_PATH) as db:
                db.row_factory = aiosqlite.Row
                # Получаем посты из файла
                cursor = await db.execute('SELECT * FROM posts WHERE file_id = ?', (file_id,))
                posts = await cursor.fetchall()
                
                # Считаем статистику
                results['total_posts'] = len(posts)
                
                for post in posts:
                    if post['status'] == 'completed':
                        # Десериализуем данные парсинга, если они есть
                        if post['parse_data']:
                            try:
                                parse_data = json.loads(post['parse_data'])
                                results['total_likes'] += len(parse_data.get('likes', []))
                                results['total_comments'] += len(parse_data.get('comments', []))
                                results['total_reposts'] += len(parse_data.get('reposts', []))
                                
                                # Добавляем пользователей в общий список
                                for user_id in parse_data.get('likes', []):
                                    results['all_users'].add(user_id)
                                for user_id in parse_data.get('comments', []):
                                    results['all_users'].add(user_id)
                                for user_id in parse_data.get('reposts', []):
                                    results['all_users'].add(user_id)
                            except:
                                pass
        else:
            # Сортируем файлы по времени создания (самый новый первый)
            result_files.sort(reverse=True)
            latest_file = os.path.join(results_dir, result_files[0])
            
            # Парсим результаты из файла
            with open(latest_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
                # Извлекаем общую статистику
                total_posts_match = re.search(r'Всего постов обработано: (\d+)', content)
                if total_posts_match:
                    results['total_posts'] = int(total_posts_match.group(1))
                
                total_likes_match = re.search(r'Всего лайков: (\d+)', content)
                if total_likes_match:
                    results['total_likes'] = int(total_likes_match.group(1))
                
                total_comments_match = re.search(r'Всего комментариев: (\d+)', content)
                if total_comments_match:
                    results['total_comments'] = int(total_comments_match.group(1))
                
                total_reposts_match = re.search(r'Всего репостов: (\d+)', content)
                if total_reposts_match:
                    results['total_reposts'] = int(total_reposts_match.group(1))
                
        return results
        
    except Exception as e:
        logger.error(f"Ошибка при получении результатов парсинга: {e}")
        return None

# Функция для удаления файла из архива по ID
async def delete_file_from_archive(file_id):
    """Удаляет файл из архива по его ID"""
    try:
        # Проверяем, существует ли файл
        file_data = await get_file_by_id(file_id)
        if not file_data:
            return False, "Файл не найден в архиве"
        
        # Проверяем, существует ли физический файл и удаляем его
        file_path = file_data.get('file_path')
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Физический файл {file_path} успешно удален")
            except Exception as e:
                logger.error(f"Ошибка при удалении физического файла {file_path}: {e}")
                # Продолжаем удалять запись из БД, даже если файл не удалось удалить
        
        # Удаляем запись из БД
        async with aiosqlite.connect(DB_PATH) as db:
            # Удаляем связанные посты
            await db.execute('DELETE FROM posts WHERE file_id = ?', (file_id,))
            
            # Если file_id - число, то ищем по id, иначе по file_id
            if str(file_id).isdigit():
                await db.execute('DELETE FROM archive_files WHERE id = ?', (file_id,))
            else:
                await db.execute('DELETE FROM archive_files WHERE file_id = ?', (file_id,))
            
            await db.commit()
            
        return True, "Файл успешно удален из архива"
    except Exception as e:
        logger.error(f"Ошибка при удалении файла из архива: {e}")
        return False, f"Ошибка при удалении файла: {str(e)}"

# Функция для очистки всего архива
async def clear_archive():
    """Очищает весь архив файлов"""
    try:
        # Получаем все файлы из архива
        archive_files = await get_archive_files()
        
        # Удаляем физические файлы
        for file in archive_files:
            file_path = file.get('file_path')
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"Физический файл {file_path} успешно удален")
                except Exception as e:
                    logger.error(f"Ошибка при удалении физического файла {file_path}: {e}")
        
        # Удаляем записи из БД
        async with aiosqlite.connect(DB_PATH) as db:
            # Удаляем все посты, связанные с файлами
            await db.execute('DELETE FROM posts')
            
            # Удаляем все записи из таблицы archive_files
            await db.execute('DELETE FROM archive_files')
            
            await db.commit()
            
        return True, f"Архив очищен. Удалено {len(archive_files)} файлов."
    except Exception as e:
        logger.error(f"Ошибка при очистке архива: {e}")
        return False, f"Ошибка при очистке архива: {str(e)}"

# Обработчик для кнопки "Удалить файл"
@dp.callback_query(lambda c: c.data == "delete_file_prompt")
async def callback_delete_file_prompt(callback_query: types.CallbackQuery):
    """Обработчик для запроса на удаление файла"""
    try:
        await callback_query.answer("🗑️ Введите ID файла для удаления...")
        
        # Отправляем сообщение с инструкцией
        await callback_query.message.reply(
            "🗑️ <b>Удаление файла</b>\n\n"
            "Введите ID файла для удаления.\n"
            "Можно указать несколько ID через запятую.\n\n"
            "Пример: <code>1</code> или <code>1,2,3</code>",
            parse_mode="HTML"
        )
        
        # Регистрируем следующий шаг
        dp.register_message_handler(process_delete_file_ids)
    except Exception as e:
        logger.error(f"Ошибка при запросе на удаление файла: {e}")
        await callback_query.answer(f"Ошибка: {str(e)[:50]}")

# Обработчик для ввода ID файлов на удаление
async def process_delete_file_ids(message: types.Message):
    """Обрабатывает ввод ID файлов для удаления"""
    try:
        # Удаляем регистрацию обработчика
        dp.message_handlers.unregister(process_delete_file_ids)
        
        # Проверяем, что сообщение содержит текст
        if not message.text:
            await message.reply("❌ Необходимо ввести ID файла.")
            return
        
        # Разбиваем текст на ID файлов
        file_ids = [id.strip() for id in message.text.split(',')]
        
        if not file_ids:
            await message.reply("❌ Необходимо ввести хотя бы один ID файла.")
            return
        
        # Удаляем каждый файл
        results = []
        for file_id in file_ids:
            success, msg = await delete_file_from_archive(file_id)
            results.append(f"ID {file_id}: {'✅' if success else '❌'} {msg}")
        
        # Отправляем результат
        await message.reply(
            "🗑️ <b>Результаты удаления:</b>\n\n" + "\n".join(results),
            parse_mode="HTML"
        )
        
        # Показываем обновленный архив
        await show_archive_page(message, 1)
        
    except Exception as e:
        logger.error(f"Ошибка при обработке ID файлов для удаления: {e}")
        await message.reply(f"❌ Ошибка: {str(e)}")

# Обработчик для кнопки "Очистить весь архив"
@dp.callback_query(lambda c: c.data == "clear_archive_prompt")
async def callback_clear_archive_prompt(callback_query: types.CallbackQuery):
    """Обработчик для запроса на очистку архива"""
    try:
        await callback_query.answer("🗑️ Подтвердите очистку архива...")
        
        # Создаем клавиатуру для подтверждения
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="✅ Да, очистить", callback_data="clear_archive_confirm"))
        keyboard.add(InlineKeyboardButton(text="❌ Отмена", callback_data="archive_list"))
        keyboard.adjust(1)
        
        # Отправляем сообщение с подтверждением
        await callback_query.message.reply(
            "🗑️ <b>Очистка архива</b>\n\n"
            "Вы уверены, что хотите очистить весь архив?\n"
            "Это приведет к удалению всех файлов и их данных.\n\n"
            "<b>Это действие нельзя отменить!</b>",
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ошибка при запросе на очистку архива: {e}")
        await callback_query.answer(f"Ошибка: {str(e)[:50]}")

# Обработчик для подтверждения очистки архива
@dp.callback_query(lambda c: c.data == "clear_archive_confirm")
async def callback_clear_archive_confirm(callback_query: types.CallbackQuery):
    """Обработчик для подтверждения очистки архива"""
    try:
        # Отвечаем на callback
        await callback_query.answer("🗑️ Очистка архива...")
        
        # Очищаем архив
        success, msg = await clear_archive()
        
        # Отправляем результат
        await callback_query.message.reply(
            f"{'✅' if success else '❌'} {msg}",
            parse_mode="HTML"
        )
        
        # Возвращаемся в главное меню
        await callback_query.message.reply(
            "🏠 Выберите действие:",
            reply_markup=get_main_menu()
        )
        
    except Exception as e:
        logger.error(f"Ошибка при очистке архива: {e}")
        await callback_query.answer(f"Ошибка: {str(e)[:50]}")

# Функция для парсинга ISO строк времени
def parse_iso_datetime(datetime_str):
    """
    Парсит строку в формате ISO в datetime объект
    
    Args:
        datetime_str: Строка с датой и временем в формате ISO
        
    Returns:
        datetime: Объект datetime
    """
    try:
        # Если строка уже включает миллисекунды, используем полный формат
        if '.' in datetime_str:
    try:
        return datetime.fromisoformat(datetime_str)
            except ValueError:
                # Некоторые версии Python не поддерживают миллисекунды в fromisoformat
                formats = [
                    '%Y-%m-%dT%H:%M:%S.%f',  # С миллисекундами
                    '%Y-%m-%dT%H:%M:%S.%fZ',  # С миллисекундами и Z в конце
                ]
                
                for fmt in formats:
                    try:
                        return datetime.strptime(datetime_str, fmt)
                    except ValueError:
                        continue
                        
                # Если ни один формат не подошел, пробуем обрезать миллисекунды
                return datetime.fromisoformat(datetime_str.split('.')[0])
            else:
            # Базовый ISO формат
            try:
                return datetime.fromisoformat(datetime_str)
            except ValueError:
                # Формат с Z в конце
                if datetime_str.endswith('Z'):
                    datetime_str = datetime_str[:-1]  # Убираем Z
                    return datetime.fromisoformat(datetime_str)
                else:
                    raise
    except Exception as e:
        logger.error(f"Ошибка при парсинге ISO даты '{datetime_str}': {e}")
        raise

@dp.callback_query(lambda c: c.data.startswith("parse_now:"))
async def callback_parse_now(callback_query: types.CallbackQuery):
    """Обработчик для немедленного начала парсинга"""
    try:
        await callback_query.answer("Начинаю парсинг...")
        
        # Получаем ID файла из callback_data
        file_id = callback_query.data.split(":", 1)[1]
        
        # Проверяем наличие токена VK API
        vk_token = await load_vk_token_async()
        if not vk_token:
            await callback_query.message.reply(
                "⚠️ Не настроен токен VK API. Пожалуйста, настройте токен в разделе настроек.",
                reply_markup=get_settings_menu()
            )
            return
            
        # Запускаем парсинг
        chat_id = callback_query.message.chat.id
        logger.info(f"Запуск немедленного парсинга для файла с ID={file_id} через callback")
        
        # Используем функцию parse_now с фейковым сообщением
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat = type('obj', (object,), {'id': chat_id})
                
            async def reply(self, text, reply_markup=None):
                return await bot.send_message(self.chat.id, text, reply_markup=reply_markup)
                
        fake_message = FakeMessage(chat_id)
        await parse_now(fake_message, file_id)
        
    except Exception as e:
        logger.error(f"Ошибка при запуске немедленного парсинга через callback: {e}")
        await callback_query.message.reply(f"❌ Ошибка: {str(e)[:100]}")

async def schedule_parse_by_time(message, file_id: str, parse_option: str = PARSE_OPTION_STANDARD):
    """Планирует парсинг файла по времени публикации постов с учетом выбранной опции"""
    try:
        logger.info(f"Планирование парсинга для файла {file_id} с опцией {parse_option}")
        
        # Получаем ID чата из сообщения
        chat_id = message.chat.id
        
        # Получаем информацию о файле из БД
        file_data = await get_file_by_id(file_id)
        if not file_data:
            logger.error(f"Файл с ID {file_id} не найден при планировании парсинга")
            return False
            
        # Проверяем наличие файла на диске
        file_path = file_data.get('file_path')
        if not file_path or not os.path.exists(file_path):
            logger.error(f"Файл не найден на диске: {file_path}")
            return False
        
        try:
            # Извлекаем ссылки и время из файла с учетом данных в БД
            posts = await extract_links_and_time_with_db(file_path, file_id)
            
            if not posts:
                # Если основной метод не дал результатов, пробуем резервный
                posts = extract_links_and_time(file_path)
        except Exception as e:
            logger.error(f"Ошибка при извлечении ссылок из файла: {e}", exc_info=True)
            # Пробуем использовать обычный метод извлечения
            posts = extract_links_and_time(file_path)
        
        if not posts:
            logger.warning(f"В файле {file_path} не найдены посты для планирования")
            return False
        
        # Находим самый ранний пост
        earliest_publish_time = None
        valid_posts = []
        
        # Валидация и нормализация данных постов
        for post in posts:
            try:
                # Проверяем наличие обязательных полей
                if 'link' not in post:
                    logger.warning(f"Пропущен пост без ссылки")
                    continue
                
                # Проверяем и нормализуем publish_time
                if 'publish_time' not in post or post['publish_time'] is None:
                    post['publish_time'] = datetime.now()
                elif isinstance(post['publish_time'], str):
                    try:
                        post['publish_time'] = datetime.fromisoformat(post['publish_time'])
                    except ValueError:
                        logger.warning(f"Не удалось преобразовать строку времени: {post['publish_time']}")
                        post['publish_time'] = datetime.now()
                
                # Проверяем валидность ссылки
                if not re.match(r'https?://(?:www\.)?vk\.com/', post['link']):
                    logger.warning(f"Невалидная ссылка: {post['link']}")
                    continue
                
                valid_posts.append(post)
                
                # Обновляем самое раннее время публикации
                if earliest_publish_time is None or post['publish_time'] < earliest_publish_time:
                    earliest_publish_time = post['publish_time']
            except Exception as e:
                logger.warning(f"Ошибка при обработке поста: {e}")
                continue
        
        if not valid_posts:
            logger.warning(f"Нет валидных постов для планирования парсинга")
            return False
        
        # Получаем токен VK API для запросов
        vk_token = await load_vk_token_async()
        
        # Обновляем времена публикации через API
        for post in valid_posts:
            try:
                logger.info(f"Запрос актуального времени публикации для планирования, пост: {post['link']}")
                publish_time, _, error = await get_post_publish_time(post['link'])
                if publish_time:
                    post['publish_time'] = publish_time
                    logger.info(f"Получено актуальное время публикации для поста {post['link']}: {publish_time}")
                else:
                    logger.warning(f"Не удалось получить время публикации для поста {post['link']}: {error if error else 'Неизвестная ошибка'}")
            except Exception as e:
                logger.error(f"Ошибка при получении времени публикации для поста {post['link']}: {e}")
        
        # Если не удалось определить время публикации, используем текущее
        if not earliest_publish_time:
            earliest_publish_time = datetime.now()
        
        # Рассчитываем оптимальное время парсинга для каждого поста в зависимости от выбранной опции
        now = datetime.now()
        earliest_parse_time = None
        latest_parse_time = None  # Добавляем отслеживание самого позднего времени парсинга
        
        logger.info(f"Начинаем расчёт времени парсинга для {len(valid_posts)} постов в режиме {parse_option}")
        
        for post in valid_posts:
            try:
                logger.info(f"Расчёт времени парсинга для поста {post.get('link')} с временем публикации {post['publish_time']}")
                # Используем функцию расчета времени парсинга с учетом выбранной опции
                custom_parse_time = calculate_parse_time(post['publish_time'], parse_option)
                post['parse_time'] = custom_parse_time
                logger.info(f"Рассчитанное время парсинга для поста {post.get('link')}: {custom_parse_time}")
                
                # Отслеживаем самое раннее время парсинга для всего файла
                if earliest_parse_time is None or custom_parse_time < earliest_parse_time:
                    earliest_parse_time = custom_parse_time
                
                # Отслеживаем самое позднее время парсинга
                if latest_parse_time is None or custom_parse_time > latest_parse_time:
                    latest_parse_time = custom_parse_time
            except Exception as e:
                logger.warning(f"Ошибка при расчете времени парсинга для поста {post.get('link')}: {e}")
                # В случае ошибки используем стандартное время парсинга
                post['parse_time'] = calculate_standard_parse_time(post['publish_time'])
                
                if earliest_parse_time is None or post['parse_time'] < earliest_parse_time:
                    earliest_parse_time = post['parse_time']
                
                if latest_parse_time is None or post['parse_time'] > latest_parse_time:
                    latest_parse_time = post['parse_time']
        
        # Если не удалось определить время парсинга, используем время через 10 минут
        if not earliest_parse_time:
            earliest_parse_time = now + timedelta(minutes=DEFAULT_MIN_PARSE_DELAY_MINUTES)
            latest_parse_time = earliest_parse_time
            logger.warning(f"Не удалось определить время парсинга, установлено через {DEFAULT_MIN_PARSE_DELAY_MINUTES} минут")
        
        # Если самое раннее время парсинга в прошлом, устанавливаем на ближайшее будущее
        if earliest_parse_time <= now:
            time_shift = now + timedelta(minutes=DEFAULT_MIN_PARSE_DELAY_MINUTES) - earliest_parse_time
            earliest_parse_time = now + timedelta(minutes=DEFAULT_MIN_PARSE_DELAY_MINUTES)
            # Сдвигаем все времена парсинга вперед на такое же время
            if latest_parse_time:
                latest_parse_time = latest_parse_time + time_shift
            # Обновляем времена парсинга для всех постов
            for post in valid_posts:
                if 'parse_time' in post and post['parse_time'] <= now:
                    post['parse_time'] = post['parse_time'] + time_shift
            logger.info(f"Расчетное время парсинга в прошлом, установлено через {DEFAULT_MIN_PARSE_DELAY_MINUTES} минут")
        
        # Журналируем рассчитанное время парсинга
        logger.info(f"Рассчитано время парсинга от {earliest_parse_time} до {latest_parse_time}")
        
        try:
            # Для каждого поста сохраняем информацию в БД
            for post in valid_posts:
                try:
                    post_id = await check_post_exists(post['link'])
                    
                    if post_id:
                        # Обновляем время парсинга
                        await update_post_parse_time(post_id, post['parse_time'])
                    else:
                        # Добавляем новый пост с заданным временем парсинга
                        await add_post(post['link'], post['publish_time'], post['parse_time'])
                except Exception as e:
                    logger.warning(f"Ошибка при сохранении информации о посте {post.get('link')}: {e}")
                    continue
            
            # Подготавливаем данные для JSON (обеспечивая сериализуемость)
            serialized_posts = []
            for post in valid_posts:
                try:
                    serialized_post = {
                        "link": post['link'],
                        "publish_time": post['publish_time'].isoformat() if isinstance(post['publish_time'], datetime) else str(post['publish_time']),
                        "parse_time": post['parse_time'].isoformat() if isinstance(post['parse_time'], datetime) else str(post['parse_time'])
                    }
                    serialized_posts.append(serialized_post)
                except Exception as e:
                    logger.warning(f"Ошибка при сериализации поста {post.get('link')}: {e}")
                    continue
            
            # Добавляем информацию для планировщика, включая выбранную опцию парсинга и ID чата
            scheduled_parse_data = json.dumps({
                "parse_option": parse_option,
                "posts": serialized_posts,
                "chat_id": chat_id  # Сохраняем ID чата для автоматического запуска
            }, ensure_ascii=False)
            
            # Обновляем статус файла в БД
            update_result = await update_file_status(
                file_id=file_id,
                status="scheduled",
                scheduled_parse_time=earliest_parse_time,
                scheduled_parse_data=scheduled_parse_data,
                earliest_parse_time=earliest_parse_time,
                latest_parse_time=latest_parse_time
            )
            
            if not update_result:
                logger.error(f"Не удалось обновить статус файла в БД для ID {file_id}")
                return False
            
            # Если время парсинга очень близко (менее 1 минуты), запускаем парсинг сразу
            if (earliest_parse_time - now).total_seconds() < 60:
                logger.info(f"Время парсинга близко, запускаем парсинг немедленно для файла {file_id}")
                asyncio.create_task(parse_scheduled_file(file_id, load_vk_token(), chat_id))
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении информации в БД: {e}", exc_info=True)
            return False
        
        logger.info(f"Парсинг для файла {file_id} успешно запланирован на {earliest_parse_time}")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при планировании парсинга: {e}", exc_info=True)
        return False

# Функция для получения времени публикации поста через VK API
async def get_post_publish_time(link, content=None):
    """
    Получает время публикации поста через VK API
    
    Args:
        link: Ссылка на пост ВКонтакте
        content: Содержимое страницы (опционально)
        
    Returns:
        tuple: (publish_time, parse_time, error_reason)
            - publish_time: datetime объект с временем публикации
            - parse_time: datetime объект с расчетным временем парсинга
            - error_reason: причина ошибки, если время не удалось получить
    """
    try:
        logger.info(f"Получение времени публикации для поста: {link}")
        
        # Проверяем, что ссылка является ссылкой на пост ВКонтакте
        if not re.search(r'vk\.com/wall-?\d+_\d+', link):
            logger.warning(f"Ссылка {link} не является ссылкой на пост ВКонтакте")
            return None, None, "Неверный формат ссылки"
        
        # Извлекаем owner_id и post_id из ссылки
        match = re.search(r'vk\.com/wall(-?\d+)_(\d+)', link)
        if not match:
            logger.warning(f"Не удалось извлечь ID поста из ссылки {link}")
            return None, None, "Не удалось извлечь ID поста"
            
        owner_id = match.group(1)
        post_id = match.group(2)
        
        # Получаем токен VK API
        access_token = await load_vk_token_async()
        if not access_token:
            logger.warning(f"Отсутствует токен VK API для получения времени публикации поста {link}")
            return None, None, "Отсутствует токен VK API"
        
        # Формируем запрос к API
        api_url = f"https://api.vk.com/method/wall.getById"
        params = {
            "posts": f"{owner_id}_{post_id}",
            "access_token": access_token,
            "v": "5.131"
        }
        
        # Выполняем запрос к API
        response = await vk_api_request(api_url, params=params)
        
        if response and 'response' in response:
            # Проверяем наличие данных в ответе
            if 'response' in response and response['response'] and len(response['response']) > 0:
                # Получаем дату из timestamp
                timestamp = response['response'][0].get('date')
                logger.info(f"Извлеченный timestamp из ответа API: {timestamp}")
                
                if timestamp:
                    publish_time = datetime.fromtimestamp(timestamp)
                    logger.info(f"Успешно получено время публикации через API для {link}: {publish_time}")
                    parse_time = calculate_standard_parse_time(publish_time)
                    return publish_time, parse_time, None
                else:
                    error_reason = "API не вернул timestamp для поста"
                    logger.warning(f"{error_reason} {link}")
                    # Продолжаем выполнение и пробуем другие методы, не используем текущее время здесь
            else:
                error_reason = "API вернул пустой ответ"
                logger.warning(f"{error_reason} для {link}")
        elif response and 'error' in response:
            error_reason = f"Ошибка API: {response['error'].get('error_msg', 'неизвестная ошибка')}"
            logger.warning(f"{error_reason} для {link}")
        else:
            error_reason = "Неизвестная ошибка при запросе API"
            logger.warning(f"{error_reason} для {link}")
        
        # Если API не помог, пробуем извлечь время из контента страницы
        if content:
            logger.info(f"Пробуем извлечь время публикации из контента страницы для {link}")
            
            # Пробуем найти время публикации в тексте
            time_patterns = [
                r'(\d{1,2}:\d{2})\s+(\d{1,2}\s+\w+\s+\d{4})',  # 08:53 27 марта 2024
                r'(\d{1,2}\s+\w+\s+\d{4})\s+в\s+(\d{1,2}:\d{2})',  # 27 марта 2024 в 08:53
                r'сегодня\s+в\s+(\d{1,2}:\d{2})',  # сегодня в 08:53
                r'вчера\s+в\s+(\d{1,2}:\d{2})',  # вчера в 08:53
                r'Опубликована:\s+(\d{1,2}:\d{2})\s+(\d{1,2}\.\d{1,2}\.\d{4})'  # Опубликована: 08:53 27.03.2024
            ]
            
            # Ищем все вхождения паттернов времени в контенте
            found_times = []
            for pattern in time_patterns:
                matches = re.finditer(pattern, content, re.IGNORECASE)
                for match in matches:
                    found_times.append(match)
            
            # Если найдены вхождения времени, обрабатываем их
            for time_match in found_times:
                try:
                    time_text = time_match.group(0)
                    
                    # Проверяем обратный порядок даты
                    if re.search(r'(\d{1,2}):(\d{2})\s+(\d{1,2})\s+(\w{3})\s+(\d{4})', time_text):
                        time_parts = re.search(r'(\d{1,2}):(\d{2})\s+(\d{1,2})\s+(\w{3})\s+(\d{4})', time_text)
                        if time_parts:
                            hour = int(time_parts.group(1))
                            minute = int(time_parts.group(2))
                            day = int(time_parts.group(3))
                            month_abbr = time_parts.group(4).lower()
                            year = int(time_parts.group(5))
                            
                            # Словарь сокращений месяцев для преобразования в номер
                            month_abbr_to_num = {
                                'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4,
                                'май': 5, 'июн': 6, 'июл': 7, 'авг': 8,
                                'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12
                            }
                            
                            if month_abbr in month_abbr_to_num:
                                month = month_abbr_to_num[month_abbr]
                                publish_time = datetime(year, month, day, hour, minute)
                                logger.info(f"Время публикации (обратный порядок): {publish_time}")
                                return publish_time, None, None
                    
                    # Стандартная обработка паттернов
                    time_str = time_match.group(1)
                    
                    if "сегодня в" in time_text:
                        today = datetime.now().date()
                        time_parts = time_str.split(':')
                        hour = int(time_parts[0])
                        minute = int(time_parts[1])
                        publish_time = datetime.combine(today, datetime.min.time().replace(hour=hour, minute=minute))
                        logger.info(f"Время публикации (сегодня): {publish_time}")
                        return publish_time, None, None
                        
                    elif "вчера в" in time_text:
                        yesterday = datetime.now().date() - timedelta(days=1)
                        time_parts = time_str.split(':')
                        hour = int(time_parts[0])
                        minute = int(time_parts[1])
                        publish_time = datetime.combine(yesterday, datetime.min.time().replace(hour=hour, minute=minute))
                        logger.info(f"Время публикации (вчера): {publish_time}")
                        return publish_time, None, None
                        
                    else:
                        # Пробуем разные форматы даты
                        publish_time = parse_time_string(time_str)
                        if publish_time:
                            logger.info(f"Время публикации (из строки): {publish_time}")
                            return publish_time, None, None
                except Exception as e:
                    logger.warning(f"Ошибка при разборе времени в контексте: {e}")
                    continue
        
        # Если ничего не помогло, возвращаем None
        logger.warning(f"Не удалось определить время публикации для {link}")
        return None, None, "Время публикации не найдено"
        
    except Exception as e:
        logger.error(f"Ошибка при получении времени публикации для {link}: {e}")
        return None, None, str(e)

# Функция для создания меню выбора времени парсинга
def get_parse_time_options_menu(file_id):
    """
    Создает инлайн-клавиатуру для выбора времени парсинга
    
    Args:
        file_id: ID файла в архиве
        
    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с опциями времени парсинга
    """
    keyboard = InlineKeyboardBuilder()
    
    # Добавляем кнопки для различных опций парсинга
    keyboard.add(InlineKeyboardButton(
        text="📊 Стандарт (23:50 после публикации)",
        callback_data=f"parse_opt:{file_id}:{PARSE_OPTION_STANDARD}"
    ))
    
    keyboard.add(InlineKeyboardButton(
        text="⚡ Сразу (через 2 минуты)",
        callback_data=f"parse_opt:{file_id}:{PARSE_OPTION_2MIN}"
    ))
    
    keyboard.add(InlineKeyboardButton(
        text="⏱ За 5 минут до истечения 24ч",
        callback_data=f"parse_opt:{file_id}:{PARSE_OPTION_5MIN}"
    ))
    
    keyboard.add(InlineKeyboardButton(
        text="⏰ За 30 минут до истечения 24ч",
        callback_data=f"parse_opt:{file_id}:{PARSE_OPTION_30MIN}"
    ))
    
    keyboard.add(InlineKeyboardButton(
        text="🕰 За 1 час до истечения 24ч",
        callback_data=f"parse_opt:{file_id}:{PARSE_OPTION_1HOUR}"
    ))
    
    # Добавляем кнопку для возврата
    keyboard.add(InlineKeyboardButton(
        text="↩️ Отмена",
        callback_data=f"cancel_parse:{file_id}"
    ))
    
    # Делаем одну кнопку в строке
    keyboard.adjust(1)
    
    return keyboard.as_markup()

# Функция для выбора оптимального времени парсинга
def calculate_standard_parse_time(publish_time):
    """
    Рассчитывает время парсинга на основе времени публикации
    
    Args:
        publish_time: datetime объект с временем публикации
        
    Returns:
        datetime: Время, когда нужно выполнить парсинг
    """
    # Стандартное время парсинга - за 10 минут до истечения 24 часов
    # 23 часа 50 минут после публикации
    if not publish_time:
        logger.warning("Невозможно рассчитать время парсинга: время публикации не указано")
        return datetime.now() + timedelta(minutes=5)
        
    parse_time = publish_time + timedelta(hours=23, minutes=50)
    
    # Если время парсинга уже прошло, устанавливаем его на 5 минут вперед от текущего времени
    if parse_time < datetime.now():
        logger.warning(f"Рассчитанное время парсинга {parse_time} уже прошло, устанавливаем на 5 минут вперед")
        parse_time = datetime.now() + timedelta(minutes=5)
    
    return parse_time

# Функция для выполнения асинхронного запроса к API VK
async def vk_api_request(url, params=None, max_retries=3):
    """
    Асинхронно выполняет запрос к API ВКонтакте с повторными попытками
    
    Args:
        url: URL запроса
        params: Параметры запроса
        max_retries: Максимальное количество повторных попыток
        
    Returns:
        dict: Ответ API или словарь с ошибкой
    """
    retry_count = 0
    async with aiohttp.ClientSession() as session:
        while retry_count < max_retries:
            try:
                logger.debug(f"Выполнение запроса к {url} с параметрами {params}")
                async with session.get(url, params=params) as response:
                    response_json = await response.json()
                    
                    if 'error' in response_json:
                        error = response_json['error']
                        error_msg = error.get('error_msg', 'Неизвестная ошибка API')
                        error_code = error.get('error_code', 0)
                        
                        # Проверяем специфические ошибки
                        if error_code == 6:  # Too many requests
                            retry_count += 1
                            wait_time = 2 ** retry_count  # Экспоненциальная задержка
                            logger.warning(f"Слишком много запросов (код 6), ожидание {wait_time} сек.")
                            await asyncio.sleep(wait_time)
                            continue
                        
                        logger.warning(f"Ошибка API VK: {error_msg} (код: {error_code})")
                        return response_json
                    
                    return response_json
                
            except asyncio.TimeoutError:
                retry_count += 1
                wait_time = 2 ** retry_count
                logger.warning(f"Таймаут запроса к API, повторная попытка через {wait_time} сек. ({retry_count}/{max_retries})")
                await asyncio.sleep(wait_time)
                
            except Exception as e:
                retry_count += 1
                wait_time = 2 ** retry_count
                logger.error(f"Ошибка при запросе к API: {e}, повторная попытка через {wait_time} сек. ({retry_count}/{max_retries})")
                await asyncio.sleep(wait_time)
        
        logger.error(f"Не удалось выполнить запрос к {url} после {max_retries} попыток")
        return {"error": {"error_code": -1, "error_msg": f"Не удалось выполнить запрос после {max_retries} попыток"}}

# Функция для преобразования кода опции в читаемое название
def get_parse_option_name(parse_option: str) -> str:
    """
    Преобразует код опции парсинга в читаемое название
    
    Args:
        parse_option: Код опции парсинга (standard, 2min, 5min, 30min, 1hour)
        
    Returns:
        str: Читаемое название опции парсинга
    """
    options = {
        PARSE_OPTION_STANDARD: "Стандарт (23:50 после публикации)",
        PARSE_OPTION_2MIN: "Сразу (через 2 минуты)",
        PARSE_OPTION_5MIN: "За 5 минут до истечения 24ч",
        PARSE_OPTION_30MIN: "За 30 минут до истечения 24ч",
        PARSE_OPTION_1HOUR: "За 1 час до истечения 24ч"
    }
    return options.get(parse_option, "Неизвестная опция")

# Функция для отображения страницы архива
async def show_archive_page(message, page=1, items_per_page=5):
    """Показывает страницу архива файлов"""
    try:
        # Получаем все файлы из архива
        all_files = await get_archive_files()
        
        if not all_files:
            await message.reply(
                "📂 Архив файлов пуст.\n\n"
                "Загрузите файлы для парсинга через кнопку 'Загрузить файл'.",
                reply_markup=get_main_menu()
            )
            return
        
        # Выполняем пагинацию
        total_pages = math.ceil(len(all_files) / items_per_page)
        page = min(max(1, page), total_pages)  # Убеждаемся, что страница в допустимом диапазоне
        
        start_idx = (page - 1) * items_per_page
        end_idx = min(start_idx + items_per_page, len(all_files))
        
        # Получаем файлы для текущей страницы
        files_to_show = all_files[start_idx:end_idx]
        
        # Формируем текст сообщения
        text = f"📋 <b>Архив файлов</b> (страница {page}/{total_pages}):\n\n"
        
        for i, file in enumerate(files_to_show, start=1):
            # Получаем статус файла и форматируем его
            status = file.get('status', 'pending')
            status_emoji = {
                'pending': '⏳',
                'scheduled': '🕒',
                'parsing': '⚙️',
                'completed': '✅',
                'error': '❌'
            }.get(status, '❓')
            
            # Получаем дату загрузки и форматируем ее
            uploaded_at = file.get('uploaded_at') or file.get('upload_date', '')
            try:
                if uploaded_at:
                    upload_date = parse_iso_datetime(uploaded_at).strftime('%d.%m.%Y %H:%M')
                else:
                    upload_date = "Неизвестно"
            except:
                upload_date = "Неизвестно"
            
            # Получаем запланированное время парсинга
            scheduled_time = file.get('scheduled_parse_time', '')
            try:
                if scheduled_time:
                    schedule_date = parse_iso_datetime(scheduled_time).strftime('%d.%m.%Y %H:%M')
                    schedule_info = f"📅 Запланировано: {schedule_date}"
                else:
                    schedule_info = "📅 Не запланировано"
            except:
                schedule_info = "📅 Ошибка формата даты"
            
            # Добавляем информацию о файле
            text += f"{i}. <b>{file.get('file_name', 'Без имени')}</b> {status_emoji}\n"
            text += f"   📤 Загружен: {upload_date}\n"
            text += f"   {schedule_info}\n"
            
            # Если есть путь к результату, показываем его
            if status == 'completed' and file.get('result_file_path'):
                text += f"   📊 Результаты доступны\n"
            
            text += "\n"
        
        # Создаем клавиатуру с навигацией
        keyboard = InlineKeyboardBuilder()
        
        # Добавляем кнопки для просмотра и управления файлами
        for i, file in enumerate(files_to_show, start=1):
            file_id = file.get('id') or file.get('file_id', 'unknown')
            status = file.get('status', 'pending')
            
            # Добавляем кнопки в зависимости от статуса файла
            if status == 'completed':
                keyboard.add(InlineKeyboardButton(
                    text=f"📊 Результаты #{i}",
                    callback_data=f"results:{file_id}"
                ))
            elif status == 'scheduled':
                keyboard.add(InlineKeyboardButton(
                    text=f"🔍 Инфо #{i}",
                    callback_data=f"file_info:{file_id}"
                ))
            else:
                keyboard.add(InlineKeyboardButton(
                    text=f"🔥 Парсить сейчас #{i}",
                    callback_data=f"parse_now:{file_id}"
                ))
        
        # Добавляем кнопки навигации, если нужно
        row = []
        if page > 1:
            row.append(InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data=f"archive_page:{page-1}"
            ))
        
        if page < total_pages:
            row.append(InlineKeyboardButton(
                text="➡️ Вперед",
                callback_data=f"archive_page:{page+1}"
            ))
        
        if row:
            keyboard.row(*row)
        
        # Добавляем кнопки управления архивом
        keyboard.add(InlineKeyboardButton(
            text="🗑️ Удалить файл",
            callback_data="delete_file_prompt"
        ))
        
        keyboard.add(InlineKeyboardButton(
            text="🔄 Очистить архив",
            callback_data="clear_archive_prompt"
        ))
        
        keyboard.add(InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="back_to_main"
        ))
        
        # Регулируем размещение кнопок
        keyboard.adjust(1)
        
        # Отправляем сообщение
        await message.reply(
            text,
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        error_msg = f"❌ Ошибка при отображении архива: {e}"
        logger.error(error_msg)
        await message.reply(error_msg)

# Функция для отображения запланированных парсингов
async def show_scheduled_parsing(message):
    """Показывает список запланированных парсингов"""
    try:
        # Получаем список запланированных файлов из БД
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM archive_files WHERE status = 'scheduled' ORDER BY scheduled_parse_time ASC"
            )
            files_rows = await cursor.fetchall()
            
            # Преобразуем объекты Row в словари для удобства работы
            files = []
            for file in files_rows:
                files.append(dict(file))
        
        if not files:
            await message.reply(
                "📋 Нет запланированных парсингов.\n\n"
                "Загрузите файлы и выберите время парсинга.",
                reply_markup=get_main_menu()
            )
            return
        
        # Формируем текст с информацией о запланированных парсингах
        text = "🕒 <b>Запланированные парсинги:</b>\n\n"
        
        for i, file in enumerate(files, start=1):
            # Получаем запланированное время парсинга
            scheduled_time = file.get('scheduled_parse_time')
            try:
                if scheduled_time:
                    schedule_date = parse_iso_datetime(scheduled_time).strftime('%d.%m.%Y %H:%M')
                else:
                    schedule_date = "Не задано"
            except:
                schedule_date = "Ошибка формата даты"
            
            # Получаем имя файла
            file_name = file.get('file_name', 'Без имени')
            
            # Получаем информацию о постах
            posts_info = ""
            scheduled_parse_data = file.get('scheduled_parse_data')
            if scheduled_parse_data:
                try:
                    parse_data = json.loads(scheduled_parse_data)
                    if isinstance(parse_data, dict) and 'posts' in parse_data:
                        posts_count = len(parse_data['posts'])
                        posts_info = f"📝 Постов: {posts_count}\n"
                        
                        # Получаем информацию о режиме парсинга
                        if 'parse_option' in parse_data:
                            parse_option = parse_data['parse_option']
                            parse_mode = get_parse_option_name(parse_option)
                            posts_info += f"⚙️ Режим: {parse_mode}\n"
                except:
                    posts_info = "❌ Ошибка чтения данных\n"
            
            # Добавляем информацию о файле в текст
            text += f"{i}. <b>{file_name}</b>\n"
            text += f"   🕒 Запланировано на: {schedule_date}\n"
            text += f"   {posts_info}\n"
        
        # Создаем клавиатуру с действиями
        keyboard = InlineKeyboardBuilder()
        
        # Возврат в главное меню
        keyboard.add(InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="back_to_main"
        ))
        
        # Отправляем сообщение
        await message.reply(
            text,
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        
    except Exception as e:
        error_msg = f"❌ Ошибка при отображении запланированных парсингов: {e}"
        logger.error(error_msg)
        await message.reply(error_msg)

# Функция для отображения справки
async def show_faq(message):
    """Показывает справочную информацию (FAQ)"""
    faq_text = """❓ <b>Часто задаваемые вопросы:</b>

<b>Что такое стандартный режим парсинга?</b>
Стандартный режим парсит активности поста через 23 часа 50 минут после публикации (за 10 минут до истечения 24 часов).

<b>Как получить токен VK API?</b>
1. Перейдите на сайт https://vkhost.github.io/
2. Выберите Kate Mobile
3. Разрешите доступ
4. Скопируйте часть URL после access_token= и до &expires_in

<b>Какая информация парсится?</b>
• 👍 Лайки
• 💬 Комментарии
• 🔄 Репосты

<b>Как загрузить файл?</b>
1. Нажмите кнопку "Загрузить файл"
2. Отправьте TXT, HTML или PDF файл с ссылками на посты
3. Выберите режим парсинга

<b>Как узнать статус парсинга?</b>
Нажмите кнопку "Архив файлов" для просмотра всех загруженных файлов и их статусов.

<b>Где найти результаты парсинга?</b>
После завершения парсинга будут доступны:
• TXT-файл с общей статистикой
• CSV-файл с детальными данными
• Краткое сообщение со статистикой

<b>Что делать, если парсинг завершился с ошибкой?</b>
Попробуйте выполнить повторный парсинг через раздел "Архив файлов" нажав на кнопку "Парсить сейчас"."""

    # Создаем клавиатуру с действиями
    keyboard = InlineKeyboardBuilder()
    
    # Возврат в главное меню
    keyboard.add(InlineKeyboardButton(
        text="🏠 Главное меню",
        callback_data="back_to_main"
    ))
    
    # Отправляем сообщение
    await message.reply(
        faq_text,
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML"
    )

# Функция для обновления данных файла
async def update_file_data(file_id, data=None, **kwargs):
    """
    Обновляет данные файла в архиве
    
    Args:
        file_id: ID файла
        data: Словарь с данными для обновления
        **kwargs: Дополнительные параметры для обновления
        
    Returns:
        bool: Успешность операции
    """
    try:
        # Объединяем data и kwargs в один словарь
        update_data = {}
        if data:
            update_data.update(data)
        if kwargs:
            update_data.update(kwargs)
            
        logger.info(f"Обновление данных файла {file_id}: {update_data}")
        
        # Проверяем наличие файла в архиве
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute('SELECT id FROM archive_files WHERE id = ?', (file_id,))
            file = await cursor.fetchone()
            
            if not file:
                logger.warning(f"Файл с ID {file_id} не найден в архиве")
                return False
            
            # Формируем SQL запрос для обновления
            set_parts = []
            params = []
            
            for key, value in update_data.items():
                set_parts.append(f"{key} = ?")
                
                # Если значение - словарь или список, сериализуем его в JSON
                if isinstance(value, (dict, list)):
                    params.append(json.dumps(value))
                else:
                    params.append(value)
            
            if not set_parts:
                logger.warning(f"Нет данных для обновления файла {file_id}")
                return False
            
            # Добавляем ID файла в параметры
            params.append(file_id)
            
            # Выполняем запрос обновления
            await db.execute(
                f"UPDATE archive_files SET {', '.join(set_parts)} WHERE id = ?",
                params
            )
            await db.commit()
            
            logger.info(f"Данные файла {file_id} успешно обновлены")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при обновлении данных файла {file_id}: {e}")
        return False

# Функция для получения списка файлов из архива
async def get_archive_files() -> List[Dict[str, Any]]:
    """Получает список файлов из архива"""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute('SELECT * FROM archive_files ORDER BY id DESC')
            files = await cursor.fetchall()
            
            # Преобразуем Row в dict для удобства работы
            result = []
            for file in files:
                result.append(dict(file))
            
            return result
    except Exception as e:
        logger.error(f"Ошибка при получении списка файлов из архива: {e}")
        return []

# Функция для проверки существования столбца в таблице
async def check_column_exists(db, table, column):
    """
    Проверяет существование столбца в таблице
    
    Args:
        db: Соединение с базой данных
        table: Название таблицы
        column: Название столбца
        
    Returns:
        bool: True если столбец существует, иначе False
    """
    try:
        cursor = await db.execute(f"PRAGMA table_info({table})")
        columns = await cursor.fetchall()
        
        for col in columns:
            if col[1] == column:
                return True
                
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке столбца {column} в таблице {table}: {e}")
        return False

# Функция для парсинга запланированного файла
async def parse_scheduled_file(file_id, vk_token, chat_id):
    """
    Выполняет парсинг файла по расписанию
    
    Args:
        file_id: ID файла для парсинга
        vk_token: Токен ВКонтакте для парсинга
        chat_id: ID чата для отправки уведомлений
        
    Returns:
        bool: True если парсинг успешно выполнен, иначе False
    """
    try:
        logger.info(f"Запуск запланированного парсинга для файла с ID={file_id}")
        
        # Если chat_id не указан, используем ID админа или другое значение по умолчанию
        if not chat_id and ADMIN_CHAT_ID:
            chat_id = ADMIN_CHAT_ID
            logger.info(f"Используем ID админа для уведомлений: {chat_id}")
            
        # Создаем фейковый объект сообщения для работы с функцией parse_now
        class FakeMessage:
            def __init__(self, chat_id):
                self.chat = types.Chat(id=chat_id, type='private')
                
            async def reply(self, text, reply_markup=None):
                await bot.send_message(self.chat.id, text, reply_markup=reply_markup)
                return self
                
            async def edit_text(self, text, reply_markup=None):
                # Этот метод обычно не используется, но добавлен для совместимости
                await bot.send_message(self.chat.id, text, reply_markup=reply_markup)
                return self
        
        # Создаем фейковое сообщение
        fake_message = FakeMessage(chat_id)
        
        # Вызываем функцию парсинга
        await parse_now(fake_message, file_id)
        
        logger.info(f"Запланированный парсинг для файла {file_id} выполнен")
        return True
        
    except Exception as e:
        logger.error(f"Ошибка при запуске запланированного парсинга для файла {file_id}: {e}")
        
        # Отправляем уведомление об ошибке, если возможно
        if chat_id:
            try:
                error_message = f"❌ Ошибка при запланированном парсинге файла: {e}"
                await bot.send_message(chat_id, error_message)
            except:
                pass
                
        return False

# Основная функция планирования парсинга на определенное время
async def schedule_parse_by_time(message, file_id: str, parse_option: str = PARSE_OPTION_STANDARD):
    """
    Планирует парсинг файла на указанное время в зависимости от выбранной опции
    
    Args:
        message: Сообщение пользователя
        file_id: ID файла для парсинга
        parse_option: Опция парсинга (standard, 2min, 5min, 30min, 1hour)
        
    Returns:
        bool: True если парсинг успешно запланирован, иначе False
    """
    try:
        logger.info(f"Планирование парсинга для файла {file_id} с опцией {parse_option}")
        
        # Получаем информацию о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            await message.reply(f"❌ Файл с ID {file_id} не найден в архиве.")
            return False
        
        # Извлекаем ссылки и время публикации из файла (без использования кеша)
        logger.info(f"Извлечение ссылок из файла {file_data['file_path']} (без кеша)")
        posts = extract_links_and_time(file_data['file_path'])
        
        if not posts or len(posts) == 0:
            await message.reply(f"❌ Не удалось извлечь ссылки из файла {file_data['file_name']}")
            return False
        
        # Статусное сообщение
        status_message = await message.reply("🔍 Получение точного времени публикации постов...")
        
        # Получаем реальное время публикации для каждого поста через API
        posts_with_real_time = []
        failed_posts = []
        
        for post in posts:
            try:
                link = post['link']
                logger.info(f"Получение реального времени публикации для поста {link}")
                
                # Запрашиваем время публикации через API
                publish_time, parse_time, error_reason = await get_post_publish_time(link)
                
                if publish_time:
                    # Если время получено успешно, используем его
                    logger.info(f"Успешно получено время публикации: {publish_time} для {link}")
                    
                    # Рассчитываем время парсинга в зависимости от выбранной опции
                    parse_time = calculate_parse_time(publish_time, parse_option)
                    logger.info(f"Рассчитанное время парсинга: {parse_time} для {link}")
                    
                    posts_with_real_time.append({
                        'link': link,
                        'publish_time': publish_time,
                        'parse_time': parse_time
                    })
                else:
                    # Если не удалось получить время публикации, используем текущее время
                    logger.warning(f"Не удалось получить время публикации для {link}: {error_reason}")
                    failed_posts.append(f"{link} - {error_reason}")
                    
                    # Используем текущее время как запасной вариант
                    now = datetime.now()
                    parse_time = calculate_parse_time(now, parse_option)
                    
                    posts_with_real_time.append({
                        'link': link,
                        'publish_time': now,
                        'parse_time': parse_time
                    })
            except Exception as e:
                logger.error(f"Ошибка при получении времени публикации для {post['link']}: {e}")
                failed_posts.append(f"{post['link']} - {str(e)}")
                
                # Используем текущее время как запасной вариант
                now = datetime.now()
                parse_time = calculate_parse_time(now, parse_option)
                
                posts_with_real_time.append({
                    'link': post['link'],
                    'publish_time': now,
                    'parse_time': parse_time
                })
        
        # Обновляем статусное сообщение
        if failed_posts:
            await status_message.edit_text(
                f"⚠️ Не удалось получить время публикации для {len(failed_posts)} из {len(posts)} постов.\n"
                f"Продолжаем планирование парсинга..."
            )
        else:
            await status_message.edit_text(
                f"✅ Успешно получено время публикации для всех {len(posts)} постов.\n"
                f"Продолжаем планирование парсинга..."
            )
        
        # Рассчитываем время парсинга для каждого поста
        logger.info(f"Начинаем расчёт времени парсинга для {len(posts_with_real_time)} постов в режиме {parse_option}")
        
        earliest_parse_time = None
        latest_parse_time = None
        chat_id = message.chat.id if hasattr(message, 'chat') else None
        
        # Создаем список для хранения готовых к планированию постов
        scheduled_posts = []
        
        for post in posts_with_real_time:
            try:
                # Получаем время публикации и парсинга
                publish_time = post['publish_time']
                parse_time = post['parse_time']
                
                logger.info(f"Пост {post['link']}: публикация в {publish_time}, парсинг в {parse_time}")
                
                # Обновляем самое раннее и позднее время парсинга
                if not earliest_parse_time or parse_time < earliest_parse_time:
                    earliest_parse_time = parse_time
                    
                if not latest_parse_time or parse_time > latest_parse_time:
                    latest_parse_time = parse_time
                
                # Добавляем пост в список запланированных
                scheduled_posts.append({
                    'link': post['link'],
                    'publish_time': publish_time.isoformat() if isinstance(publish_time, datetime) else publish_time,
                    'parse_time': parse_time.isoformat() if isinstance(parse_time, datetime) else parse_time
                })
                
            except Exception as e:
                logger.error(f"Ошибка при расчёте времени парсинга для {post['link']}: {e}")
                continue
        
        if not scheduled_posts:
            await message.reply(f"❌ Не удалось запланировать парсинг ни для одного поста в файле {file_data['file_name']}")
            return False
        
        # Формируем данные для запланированного парсинга
        logger.info(f"Рассчитано время парсинга от {earliest_parse_time} до {latest_parse_time}")
        scheduled_data = {
            'parse_option': parse_option,
            'posts': scheduled_posts,
            'chat_id': chat_id
        }
        
        # Обновляем статус файла на "scheduled"
        earliest_parse_time_str = earliest_parse_time.isoformat() if isinstance(earliest_parse_time, datetime) else earliest_parse_time
        latest_parse_time_str = latest_parse_time.isoformat() if isinstance(latest_parse_time, datetime) else latest_parse_time
        
        success = await update_file_status(
            file_id, 
            'scheduled', 
            scheduled_parse_time=earliest_parse_time_str,
            scheduled_parse_data=scheduled_data,
            earliest_parse_time=earliest_parse_time_str,
            latest_parse_time=latest_parse_time_str
        )
        
        if not success:
            await message.reply(f"❌ Не удалось обновить статус файла {file_data['file_name']}")
            return False
        
        # Форматируем время для отображения пользователю
        earliest_time_display = earliest_parse_time.strftime('%d.%m.%Y %H:%M') if isinstance(earliest_parse_time, datetime) else str(earliest_parse_time)
        latest_time_display = latest_parse_time.strftime('%d.%m.%Y %H:%M') if isinstance(latest_parse_time, datetime) else str(latest_parse_time)
        
        # Определяем режим парсинга для отображения
        parse_mode_name = get_parse_option_name(parse_option)
        
        # Формируем сообщение в зависимости от выбранного режима
        if parse_option == PARSE_OPTION_2MIN:
            # Для режима "через 2 минуты" создаем задачу на немедленный парсинг
            schedule_msg = f"⏱ Парсинг будет выполнен через 2 минуты"
            
            # Запланировать немедленный парсинг через 2 минуты
            asyncio.create_task(delayed_parse(file_id, 120, chat_id))
        elif earliest_parse_time == latest_parse_time:
            schedule_msg = f"⏱ Парсинг запланирован на {earliest_time_display}"
        else:
            schedule_msg = f"⏱ Парсинг запланирован с {earliest_time_display} до {latest_time_display}"
        
        # Отправляем сообщение о успешном планировании
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
        keyboard.add(InlineKeyboardButton(text="📊 Посмотреть статус", callback_data=f"file_info:{file_id}"))
        keyboard.add(InlineKeyboardButton(text="🔥 Парсить сейчас", callback_data=f"parse_now:{file_id}"))
        keyboard.add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main"))
        keyboard.adjust(1)
        
        await message.reply(
            f"✅ Парсинг для файла «{file_data['file_name']}» успешно запланирован!\n\n"
            f"📄 Найдено постов: {len(scheduled_posts)}\n"
            f"⚙️ Режим парсинга: {parse_mode_name}\n"
            f"{schedule_msg}\n\n"
            f"ℹ️ Бот автоматически запустит парсинг в указанное время",
            reply_markup=keyboard.as_markup()
        )
        
        logger.info(f"Парсинг для файла {file_id} успешно запланирован на {earliest_parse_time}")
        return True
        
    except Exception as e:
        error_msg = f"❌ Ошибка при планировании парсинга: {e}"
        logger.error(error_msg)
        
        try:
            await message.reply(error_msg)
        except:
            pass
            
        return False

# Функция для отложенного парсинга
async def delayed_parse(file_id, delay_seconds, chat_id):
    """
    Выполняет парсинг файла с задержкой
    
    Args:
        file_id: ID файла для парсинга
        delay_seconds: Задержка в секундах
        chat_id: ID чата для отправки уведомлений
        
    Returns:
        None
    """
    try:
        logger.info(f"Запланирован отложенный парсинг файла {file_id} через {delay_seconds} секунд")
        
        # Ждем указанное количество секунд
        await asyncio.sleep(delay_seconds)
        
        # Получаем токен VK API
        vk_token = await load_vk_token_async()
        if not vk_token:
            logger.error(f"Не удалось получить токен VK API для отложенного парсинга файла {file_id}")
            return
        
        # Вызываем функцию парсинга
        logger.info(f"Запуск отложенного парсинга для файла {file_id}")
        await parse_scheduled_file(file_id, vk_token, chat_id)
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении отложенного парсинга файла {file_id}: {e}")

# Функция для расчета времени парсинга в зависимости от выбранной опции
def calculate_parse_time(publish_time: datetime, parse_option: str = PARSE_OPTION_STANDARD) -> datetime:
    """
    Рассчитывает время парсинга на основе времени публикации и выбранной опции
    
    Args:
        publish_time: Время публикации поста
        parse_option: Опция парсинга (standard, 2min, 5min, 30min, 1hour)
        
    Returns:
        datetime: Рассчитанное время парсинга
    """
    if not publish_time:
        logger.warning("Невозможно рассчитать время парсинга: время публикации не указано")
        return datetime.now() + timedelta(minutes=2)
    
    now = datetime.now()
    
    # Преобразуем publish_time в datetime, если это строка
    if isinstance(publish_time, str):
        try:
            publish_time = parse_iso_datetime(publish_time)
        except:
            logger.warning(f"Ошибка при преобразовании времени публикации из строки: {publish_time}")
            return now + timedelta(minutes=2)
    
    # Рассчитываем время парсинга в зависимости от выбранной опции
    if parse_option == PARSE_OPTION_2MIN:
        # Через 2 минуты после текущего времени
        parse_time = now + timedelta(minutes=2)
    elif parse_option == PARSE_OPTION_5MIN:
        # За 5 минут до истечения 24ч
        parse_time = publish_time + timedelta(hours=23, minutes=55)
    elif parse_option == PARSE_OPTION_30MIN:
        # За 30 минут до истечения 24ч
        parse_time = publish_time + timedelta(hours=23, minutes=30)
    elif parse_option == PARSE_OPTION_1HOUR:
        # За 1 час до истечения 24ч
        parse_time = publish_time + timedelta(hours=23)
    else:
        # Стандартный вариант - за 10 минут до истечения 24ч
        parse_time = publish_time + timedelta(hours=23, minutes=50)
    
    # Если время парсинга уже прошло, устанавливаем его на 2 минуты вперед от текущего времени
    if parse_time < now:
        logger.warning(f"Рассчитанное время парсинга {parse_time} уже прошло, устанавливаем на 2 минуты вперед")
        parse_time = now + timedelta(minutes=2)
    
    return parse_time

# Функция для получения информации о файле по ID
async def get_file_by_id(file_id: str) -> Optional[Dict[str, Any]]:
    """
    Получает информацию о файле по его ID
    
    Args:
        file_id: ID файла
        
    Returns:
        dict или None: Информация о файле или None, если файл не найден
    """
    try:
        logger.info(f"Получение информации о файле с ID: {file_id}")
        
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            
            # Проверяем, является ли file_id числом или строкой
            if file_id.isdigit():
                cursor = await db.execute('SELECT * FROM archive_files WHERE id = ?', (file_id,))
            else:
                cursor = await db.execute('SELECT * FROM archive_files WHERE file_id = ?', (file_id,))
                
            file = await cursor.fetchone()
            
            if file:
                # Преобразуем Row в dict
                return dict(file)
            else:
                logger.warning(f"Файл с ID {file_id} не найден в архиве")
                return None
                
    except Exception as e:
        logger.error(f"Ошибка при получении информации о файле с ID {file_id}: {e}")
        return None

# Обработчик выбора времени парсинга
@dp.callback_query(lambda c: c.data.startswith("parse_opt:"))
async def callback_select_parse_option(callback_query: types.CallbackQuery):
    """Обрабатывает выбор времени парсинга"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 3:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        file_id = parts[1]
        parse_option = parts[2]
        chat_id = callback_query.message.chat.id
        
        # Получаем информацию о файле
        file_data = await get_file_by_id(file_id)
        
        # Отправляем callback-ответ
        logger.info(f"Получен callback выбора времени парсинга: {callback_query.data}")
        
        # Перед планированием обновляем опцию парсинга
        await update_file_data(file_id, {'parse_option': parse_option, 'status': 'pending'})
        
        # Планируем парсинг
        logger.info(f"Получение информации о файле с ID: {file_id}")
        file_data = await get_file_by_id(file_id)
        
        # Обновляем информацию о файле с выбранной опцией парсинга
        await update_file_data(file_id, {'parse_option': parse_option, 'status': 'pending'})
        
        # Отвечаем на callback
        await callback_query.answer(f"✅ Выбрана опция парсинга: {get_parse_option_name(parse_option)}")
        
        # Планируем парсинг
        logger.info(f"Успешно установлена опция парсинга {parse_option} для файла {file_id}")
        result = await schedule_parse_by_time(callback_query.message, file_id, parse_option)
        
        if result:
            # Получаем файл с обновленной информацией
            file_data = await get_file_by_id(file_id)
            
            # Форматируем время для отображения
            scheduled_time = "Неизвестно"
            if file_data and 'scheduled_parse_time' in file_data and file_data['scheduled_parse_time']:
                try:
                    scheduled_date = parse_iso_datetime(file_data['scheduled_parse_time'])
                    scheduled_time = scheduled_date.strftime('%d.%m.%Y %H:%M')
                except:
                    pass
            
            # Форматируем диапазон парсинга, если доступен
            time_range = ""
            if file_data and 'earliest_parse_time' in file_data and 'latest_parse_time' in file_data:
                try:
                    earliest = parse_iso_datetime(file_data['earliest_parse_time']).strftime('%d.%m.%Y %H:%M')
                    latest = parse_iso_datetime(file_data['latest_parse_time']).strftime('%d.%m.%Y %H:%M')
                    
                    if earliest == latest:
                        time_range = f"(в {earliest})"
                    else:
                        time_range = f"(с {earliest} до {latest})"
                except:
                    pass
            
            # Создаем клавиатуру для действий
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
            keyboard.add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main"))
            keyboard.adjust(1)
            
            # Отправляем сообщение об успешном планировании
            await callback_query.message.edit_text(
                f"✅ Парсинг файла успешно запланирован на {scheduled_time} {time_range}!\n\n"
                f"⚙️ Режим парсинга: {get_parse_option_name(parse_option)}\n\n"
                f"Парсинг будет выполнен автоматически в указанное время.\n"
                f"Результаты будут доступны в разделе 'Архив файлов'.",
                reply_markup=keyboard.as_markup()
            )
        else:
            # Сообщаем об ошибке
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
            keyboard.add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main"))
            keyboard.adjust(1)
            
            await callback_query.message.edit_text(
                f"❌ Не удалось запланировать парсинг файла.\n"
                f"Попробуйте другое время или режим парсинга.",
                reply_markup=keyboard.as_markup()
            )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке выбора времени парсинга: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")
        
        # Клавиатура для возврата
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
        keyboard.add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main"))
        keyboard.adjust(1)
        
        # Отправляем сообщение об ошибке
        await callback_query.message.edit_text(
            f"❌ Произошла ошибка при планировании парсинга: {str(e)}",
            reply_markup=keyboard.as_markup()
        )

# Обработчик для отмены выбора времени парсинга
@dp.callback_query(lambda c: c.data.startswith("cancel_parse:"))
async def callback_cancel_parse(callback_query: types.CallbackQuery):
    """Обрабатывает отмену выбора времени парсинга"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 2:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        file_id = parts[1]
        
        # Получаем данные о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            await callback_query.answer("❌ Файл не найден")
            return
        
        # Возвращаемся в архив
        await show_archive_page(callback_query.message)
        
        # Отвечаем на callback
        await callback_query.answer("✅ Выбор отменен")
        
    except Exception as e:
        logger.error(f"Ошибка при отмене выбора времени парсинга: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")

# Обработчик для просмотра информации о файле
@dp.callback_query(lambda c: c.data.startswith("file_info:"))
async def callback_file_info(callback_query: types.CallbackQuery):
    """Обрабатывает запрос на просмотр информации о файле"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 2:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        file_id = parts[1]
        
        # Получаем данные о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            await callback_query.answer("❌ Файл не найден")
            return
        
        # Формируем информацию о файле
        file_name = file_data.get('file_name', 'Без имени')
        status = file_data.get('status', 'pending')
        
        # Эмодзи статуса
        status_emoji = {
            'pending': '⏳ Ожидает',
            'scheduled': '🕒 Запланирован',
            'parsing': '⚙️ Парсится',
            'completed': '✅ Завершен',
            'error': '❌ Ошибка'
        }.get(status, '❓ Неизвестно')
        
        # Получаем дату загрузки
        uploaded_at = file_data.get('uploaded_at') or file_data.get('upload_date', '')
        try:
            if uploaded_at:
                upload_date = parse_iso_datetime(uploaded_at).strftime('%d.%m.%Y %H:%M')
            else:
                upload_date = "Неизвестно"
        except:
            upload_date = "Неизвестно"
        
        # Получаем запланированное время парсинга
        scheduled_time = file_data.get('scheduled_parse_time', '')
        scheduled_info = ""
        
        try:
            if scheduled_time:
                schedule_date = parse_iso_datetime(scheduled_time).strftime('%d.%m.%Y %H:%M')
                scheduled_info = f"📅 Запланировано на: {schedule_date}\n\n"
            else:
                scheduled_info = "📅 Не запланировано\n\n"
        except:
            scheduled_info = "📅 Ошибка формата даты\n\n"
        
        # Получаем диапазон времени парсинга
        earliest_time = file_data.get('earliest_parse_time', '')
        latest_time = file_data.get('latest_parse_time', '')
        time_range_info = ""
        
        try:
            if earliest_time and latest_time:
                earliest_date = parse_iso_datetime(earliest_time).strftime('%d.%m.%Y %H:%M')
                latest_date = parse_iso_datetime(latest_time).strftime('%d.%m.%Y %H:%M')
                
                if earliest_date == latest_date:
                    time_range_info = f"⏱ Время парсинга: {earliest_date}\n\n"
                else:
                    time_range_info = f"⏱ Время парсинга: с {earliest_date} до {latest_date}\n\n"
        except:
            pass
        
        # Получаем информацию о постах
        posts_info = ""
        scheduled_parse_data = file_data.get('scheduled_parse_data', '')
        posts_count = 0
        parse_option = ""
        
        try:
            if scheduled_parse_data:
                parse_data = json.loads(scheduled_parse_data)
                if isinstance(parse_data, dict) and 'posts' in parse_data:
                    posts_count = len(parse_data['posts'])
                    posts_info = f"📝 Постов: {posts_count}\n\n"
                    
                    # Получаем информацию о режиме парсинга
                    if 'parse_option' in parse_data:
                        parse_option = parse_data['parse_option']
                        parse_mode = get_parse_option_name(parse_option)
                        posts_info += f"⚙️ Режим парсинга: {parse_mode}\n\n"
        except:
            posts_info = "❌ Ошибка чтения данных\n\n"
        
        # Формируем сообщение
        message_text = f"📋 <b>Информация о файле:</b>\n\n"
        message_text += f"📁 Имя файла: <b>{file_name}</b>\n"
        message_text += f"📤 Загружен: {upload_date}\n"
        message_text += f"🔄 Статус: {status_emoji}\n\n"
        message_text += scheduled_info
        message_text += time_range_info
        message_text += posts_info
        
        # Создаем клавиатуру с действиями
        keyboard = InlineKeyboardBuilder()
        
        # Кнопки в зависимости от статуса файла
        if status == 'pending':
            keyboard.add(InlineKeyboardButton(
                text="🕒 Запланировать парсинг",
                callback_data=f"schedule_parse:{file_id}"
            ))
            keyboard.add(InlineKeyboardButton(
                text="🔥 Парсить сейчас",
                callback_data=f"parse_now:{file_id}"
            ))
        elif status == 'scheduled':
            keyboard.add(InlineKeyboardButton(
                text="🔥 Парсить сейчас",
                callback_data=f"parse_now:{file_id}"
            ))
            keyboard.add(InlineKeyboardButton(
                text="❌ Отменить парсинг",
                callback_data=f"cancel_scheduled:{file_id}"
            ))
        elif status == 'completed' and file_data.get('result_file_path'):
            keyboard.add(InlineKeyboardButton(
                text="📊 Результаты",
                callback_data=f"results:{file_id}"
            ))
            keyboard.add(InlineKeyboardButton(
                text="🔄 Парсить снова",
                callback_data=f"parse_now:{file_id}"
            ))
        
        # Общие кнопки
        keyboard.add(InlineKeyboardButton(
            text="🗑️ Удалить файл",
            callback_data=f"delete_file:{file_id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="📋 Архив файлов",
            callback_data="archive_list"
        ))
        keyboard.add(InlineKeyboardButton(
            text="🏠 Главное меню",
            callback_data="back_to_main"
        ))
        
        # Регулируем размещение кнопок
        keyboard.adjust(1)
        
        # Отправляем сообщение
        await callback_query.message.edit_text(
            message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
        
        # Отвечаем на callback
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка при просмотре информации о файле: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")

# Обработчик для отмены запланированного парсинга
@dp.callback_query(lambda c: c.data.startswith("cancel_scheduled:"))
async def callback_cancel_scheduled(callback_query: types.CallbackQuery):
    """Обрабатывает отмену запланированного парсинга"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 2:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        file_id = parts[1]
        
        # Получаем данные о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            await callback_query.answer("❌ Файл не найден")
            return
        
        # Проверяем, что файл запланирован
        if file_data.get('status') != 'scheduled':
            await callback_query.answer("❌ Файл не запланирован для парсинга")
            return
        
        # Обновляем статус файла на "pending"
        success = await update_file_status(file_id, 'pending')
        
        if not success:
            await callback_query.answer("❌ Не удалось отменить запланированный парсинг")
            return
        
        # Обновляем сообщение
        await callback_query.message.edit_text(
            f"✅ Запланированный парсинг для файла «{file_data.get('file_name', 'Без имени')}» успешно отменен!",
            reply_markup=InlineKeyboardBuilder()
                .add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
                .add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main"))
                .adjust(1)
                .as_markup()
        )
        
        # Отвечаем на callback
        await callback_query.answer("✅ Запланированный парсинг отменен")
        
    except Exception as e:
        logger.error(f"Ошибка при отмене запланированного парсинга: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")

# Обработчик для кнопки удаления конкретного файла
@dp.callback_query(lambda c: c.data.startswith("delete_file:"))
async def callback_delete_specific_file(callback_query: types.CallbackQuery):
    """Обрабатывает запрос на удаление конкретного файла"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 2:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        file_id = parts[1]
        
        # Получаем данные о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            await callback_query.answer("❌ Файл не найден")
            return
        
        # Создаем клавиатуру для подтверждения
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(
            text="✅ Да, удалить",
            callback_data=f"confirm_delete:{file_id}"
        ))
        keyboard.add(InlineKeyboardButton(
            text="❌ Нет, отмена",
            callback_data=f"file_info:{file_id}"
        ))
        keyboard.adjust(1)
        
        # Отправляем сообщение для подтверждения
        await callback_query.message.edit_text(
            f"❓ Вы уверены, что хотите удалить файл «{file_data.get('file_name', 'Без имени')}»?",
            reply_markup=keyboard.as_markup()
        )
        
        # Отвечаем на callback
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка при запросе на удаление файла: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")

# Обработчик для подтверждения удаления файла
@dp.callback_query(lambda c: c.data.startswith("confirm_delete:"))
async def callback_confirm_delete_file(callback_query: types.CallbackQuery):
    """Обрабатывает подтверждение удаления файла"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 2:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        file_id = parts[1]
        
        # Получаем данные о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            await callback_query.answer("❌ Файл не найден")
            return
        
        # Удаляем файл из архива
        success = await delete_file_from_archive(file_id)
        
        if not success:
            await callback_query.answer("❌ Не удалось удалить файл")
            return
        
        # Обновляем сообщение
        await callback_query.message.edit_text(
            f"✅ Файл «{file_data.get('file_name', 'Без имени')}» успешно удален!",
            reply_markup=InlineKeyboardBuilder()
                .add(InlineKeyboardButton(text="📋 Архив файлов", callback_data="archive_list"))
                .add(InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main"))
                .adjust(1)
                .as_markup()
        )
        
        # Отвечаем на callback
        await callback_query.answer("✅ Файл удален")
        
    except Exception as e:
        logger.error(f"Ошибка при удалении файла: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")

# Обработчик для кнопки "Запланировать парсинг"
@dp.callback_query(lambda c: c.data.startswith("schedule_parse:"))
async def callback_schedule_parse(callback_query: types.CallbackQuery):
    """Обрабатывает запрос на планирование парсинга"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 2:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        file_id = parts[1]
        
        # Получаем данные о файле
        file_data = await get_file_by_id(file_id)
        if not file_data:
            await callback_query.answer("❌ Файл не найден")
            return
        
        # Отправляем меню выбора времени парсинга
        keyboard = get_parse_time_options_menu(file_id)
        
        await callback_query.message.edit_text(
            f"⏱ Выберите время парсинга для файла «{file_data.get('file_name', 'Без имени')}»:",
            reply_markup=keyboard
        )
        
        # Отвечаем на callback
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка при запросе на планирование парсинга: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")

# Обработчик для пагинации архива
@dp.callback_query(lambda c: c.data.startswith("archive_page:"))
async def callback_archive_page(callback_query: types.CallbackQuery):
    """Обрабатывает запрос на переход к определенной странице архива"""
    try:
        # Разбираем callback data
        parts = callback_query.data.split(":")
        if len(parts) < 2:
            await callback_query.answer("❌ Неверный формат callback данных")
            return
        
        page = int(parts[1])
        
        # Отображаем страницу архива
        await show_archive_page(callback_query.message, page)
        
        # Отвечаем на callback
        await callback_query.answer()
        
    except Exception as e:
        logger.error(f"Ошибка при переходе к странице архива: {e}")
        await callback_query.answer(f"❌ Ошибка: {str(e)}")

# Функция для выполнения запланированных парсингов
async def check_and_run_scheduled_tasks():
    """Проверяет и запускает запланированные задачи парсинга"""
    try:
        logger.info("Проверка запланированных задач парсинга")
        
        # Получаем текущее время
        now = datetime.now()
        
        # Ищем файлы с запланированным парсингом, время которых уже наступило
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT * FROM archive_files 
                WHERE status = 'scheduled' 
                AND scheduled_parse_time IS NOT NULL 
                AND datetime(scheduled_parse_time) <= datetime('now', 'localtime')
                """
            )
            scheduled_files = await cursor.fetchall()
        
        if not scheduled_files:
            logger.info("Нет запланированных задач для запуска в данный момент")
            return
        
        # Запускаем парсинг для каждого файла
        for file in scheduled_files:
            # Преобразуем file в словарь, если это не словарь
            if not isinstance(file, dict):
                file_dict = dict(file)
            else:
                file_dict = file
            
            file_id = file_dict.get('id')
            parse_option = None
            chat_id = None
            
            # Получаем данные для запланированного парсинга
            scheduled_parse_data = file_dict.get('scheduled_parse_data', '')
            try:
                if scheduled_parse_data:
                    parse_data = json.loads(scheduled_parse_data)
                    if isinstance(parse_data, dict):
                        parse_option = parse_data.get('parse_option', PARSE_OPTION_STANDARD)
                        chat_id = parse_data.get('chat_id')
            except Exception as e:
                logger.error(f"Ошибка при разборе данных запланированного парсинга: {e}")
            
            # Если не удалось извлечь chat_id, используем ADMIN_CHAT_ID
            if not chat_id and ADMIN_CHAT_ID:
                chat_id = ADMIN_CHAT_ID
                
            # Если chat_id все еще не определен, пропускаем файл
            if not chat_id:
                logger.warning(f"Не удалось определить chat_id для файла {file_id}, пропускаем")
                continue
                
            # Запускаем задачу парсинга
            logger.info(f"Запуск отложенного парсинга для файла {file_id}")
            asyncio.create_task(parse_scheduled_file(file_id, load_vk_token(), chat_id))
            
    except Exception as e:
        logger.error(f"Ошибка при проверке запланированных задач: {e}")

# Функция для исполнения задач планировщика
def scheduler_job():
    """Запускает проверку запланированных задач каждые 30 секунд"""
    while True:
        try:
            # Создаем новый event loop для асинхронных вызовов
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Запускаем проверку запланированных задач
            loop.run_until_complete(check_and_run_scheduled_tasks())
            
            # Закрываем loop
            loop.close()
        except Exception as e:
            logger.error(f"Ошибка в работе планировщика: {e}")
        
        # Спим 30 секунд перед следующей проверкой
        time.sleep(30)

# Функция для выполнения отложенного парсинга
async def process_scheduled_file(file_id, chat_id=None):
    """Выполняет отложенный парсинг файла"""
    try:
        # Получаем токен VK API
        vk_token = await load_vk_token_async()
        if not vk_token:
            logger.error(f"Не удалось получить токен VK API для отложенного парсинга файла {file_id}")
            return
        
        # Вызываем функцию парсинга
        logger.info(f"Запуск отложенного парсинга для файла {file_id}")
        await parse_scheduled_file(file_id, vk_token, chat_id)
        
    except Exception as e:
        logger.error(f"Ошибка при выполнении отложенного парсинга файла {file_id}: {e}")

if __name__ == '__main__':
    try:
        # Указываем явно SelectorEventLoop для правильной работы aiodns на Windows
        import asyncio
        import sys
        
        if sys.platform.startswith('win'):
            # На Windows нужно явно использовать SelectorEventLoop
            from asyncio import SelectorEventLoop
            loop = SelectorEventLoop()
            asyncio.set_event_loop(loop)
        else:
            # На других платформах используем стандартный loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        # Запускаем основную функцию
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
    except Exception as e:
        logger.error(f"Критическая ошибка при запуске бота: {e}")
        # Выводим полный трейсбек для отладки
        import traceback
        logger.error(traceback.format_exc())