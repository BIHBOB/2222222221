import os
import re
from bs4 import BeautifulSoup
import pdfplumber
from datetime import datetime, timedelta
import logging
import threading
from urllib.parse import urlparse
import json

# Import config
from config import logger

def extract_vk_links(text):
    """Extract VK post links from text"""
    # Pattern to match VK post links
    patterns = [
        r'https?://(?:www\.)?vk\.com/\S+?wall-?\d+_\d+',  # Regular wall post
        r'https?://(?:www\.)?vk\.com/wall-?\d+_\d+',      # Wall post without username
        r'https?://(?:www\.)?vk\.com/market-?\d+_\d+',    # Market post
        r'https?://(?:www\.)?vk\.com/adblogger\S+',       # AdBlogger
    ]

    links = []
    for pattern in patterns:
        links.extend(re.findall(pattern, text))

    # Remove duplicates while preserving order
    unique_links = []
    for link in links:
        if link not in unique_links:
            unique_links.append(link)

    return unique_links

def get_publish_time_from_text(text):
    """Try to extract publication time from text"""
    # Look for date patterns like DD.MM.YYYY or YYYY-MM-DD
    date_patterns = [
        r'(\d{2})\.(\d{2})\.(\d{4})',  # DD.MM.YYYY
        r'(\d{4})-(\d{2})-(\d{2})',     # YYYY-MM-DD
    ]

    dates = []
    for pattern in date_patterns:
        matches = re.findall(pattern, text)
        if matches:
            for match in matches:
                if len(match) == 3:
                    try:
                        if '.' in pattern:  # DD.MM.YYYY
                            day, month, year = match
                            dates.append(datetime(int(year), int(month), int(day)))
                        else:  # YYYY-MM-DD
                            year, month, day = match
                            dates.append(datetime(int(year), int(month), int(day)))
                    except ValueError:
                        # Invalid date, ignore
                        pass

    # Look for time patterns like HH:MM or HH:MM:SS
    time_patterns = [
        r'(\d{1,2}):(\d{2})(?::(\d{2}))?',  # HH:MM or HH:MM:SS
    ]

    times = []
    for pattern in time_patterns:
        matches = re.findall(pattern, text)
        if matches:
            for match in matches:
                try:
                    hour = int(match[0])
                    minute = int(match[1])
                    second = int(match[2]) if match[2] else 0

                    if 0 <= hour < 24 and 0 <= minute < 60 and 0 <= second < 60:
                        times.append((hour, minute, second))
                except ValueError:
                    # Invalid time, ignore
                    pass

    # If we have both dates and times, combine them
    if dates and times:
        # Use the first date and first time for simplicity
        date = dates[0]
        hour, minute, second = times[0]
        return date.replace(hour=hour, minute=minute, second=second)
    elif dates:
        # If we only have a date, use midnight
        return dates[0]
    else:
        # No date found, return None
        return None

def calculate_parse_time(publish_time, parse_option):
    """Calculate parse time based on publish time and option.

    Args:
        publish_time: DateTime объект с временем публикации (в московском времени без tzinfo)
        parse_option: Опция парсинга (standard, now, 5min, 30min, 1hour)

    Returns:
        DateTime объект с временем парсинга (в московском времени без tzinfo)
    """
    from config import (
        PARSE_OPTION_STANDARD, PARSE_OPTION_NOW, 
        PARSE_OPTION_5MIN, PARSE_OPTION_30MIN, PARSE_OPTION_1HOUR,
        get_now_moscow, MOSCOW_TZ, UTC_TZ
    )

    if not publish_time:
        # Если время публикации не задано, используем текущее московское
        publish_time = get_now_moscow()
    elif isinstance(publish_time, datetime) and publish_time.tzinfo is None:
        # Если время без зоны - считаем его московским
        publish_time = publish_time.replace(tzinfo=MOSCOW_TZ)

    # Конвертируем в московское время если оно в другом часовом поясе
    if publish_time.tzinfo != MOSCOW_TZ:
        publish_time = publish_time.astimezone(MOSCOW_TZ)

    # Для стандартного режима всегда добавляем 23 часа 50 минут к времени публикации
    if parse_option == PARSE_OPTION_STANDARD:
        parse_time = publish_time + timedelta(hours=23, minutes=50)
    elif parse_option == PARSE_OPTION_NOW:
        parse_time = get_now_moscow() + timedelta(seconds=10)
    elif parse_option == PARSE_OPTION_5MIN:
        parse_time = publish_time + timedelta(hours=23, minutes=55)
    elif parse_option == PARSE_OPTION_30MIN:
        parse_time = publish_time + timedelta(hours=23, minutes=30)
    elif parse_option == PARSE_OPTION_1HOUR:
        parse_time = publish_time + timedelta(hours=23)
    else:
        parse_time = publish_time + timedelta(hours=23, minutes=50)

    # Убираем tzinfo для хранения в БД
    return parse_time.replace(tzinfo=None)


def parse_html_file(file_path):
    """Parse HTML file to extract VK links and publication time"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text()

        links = extract_vk_links(html_content)
        publish_time = get_publish_time_from_text(text)

        return links, publish_time
    except Exception as e:
        logger.error(f"Error parsing HTML file {file_path}: {str(e)}")
        raise

def parse_pdf_file(file_path):
    """Parse PDF file to extract VK links and publication time"""
    try:
        text = ""
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                text += page.extract_text() or ""

        links = extract_vk_links(text)
        publish_time = get_publish_time_from_text(text)

        return links, publish_time
    except Exception as e:
        logger.error(f"Error parsing PDF file {file_path}: {str(e)}")
        raise

def parse_txt_file(file_path):
    """Parse TXT file to extract VK links and publication time"""
    try:
        # Попробуем разные кодировки
        encodings = ['utf-8', 'cp1251', 'windows-1251', 'latin-1']
        text = None

        for encoding in encodings:
            try:
                with open(file_path, 'r', encoding=encoding) as f:
                    text = f.read()
                logger.info(f"Успешно открыт файл с кодировкой {encoding}")
                break  # Если успешно прочитали, прекращаем цикл
            except UnicodeDecodeError:
                logger.warning(f"Не удалось прочитать файл с кодировкой {encoding}, пробуем следующую")
                continue

        if text is None:
            # Если ни одна кодировка не сработала, пробуем как бинарный и игнорируем ошибки
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                text = f.read()
            logger.warning("Файл открыт с игнорированием ошибок кодировки")

        links = extract_vk_links(text)
        publish_time = get_publish_time_from_text(text)

        return links, publish_time
    except Exception as e:
        logger.error(f"Error parsing TXT file {file_path}: {str(e)}")
        raise

def process_file(file_id, app):
    """Process uploaded file to extract VK links and schedule parsing"""
    with app.app_context():
        from models import File, Post, Settings
        from utils.scheduler import schedule_post_parsing
        from utils.vk_parser import make_vk_api_request, extract_post_ids
        from config import get_now_moscow, MOSCOW_TZ, UTC_TZ

        # Получаем экземпляр db через app.db
        db = app.db

        file = File.query.get(file_id)
        if not file:
            logger.error(f"File with ID {file_id} not found")
            return

        try:
            # Update file status
            file.status = 'processing'
            db.session.commit()

            # Extract links based on file type
            links = []
            file_publish_time = None

            file_ext = file.filename.rsplit('.', 1)[-1].lower()
            if file_ext == 'html':
                links, file_publish_time = parse_html_file(file.file_path)
            elif file_ext == 'pdf':
                links, file_publish_time = parse_pdf_file(file.file_path)
            elif file_ext == 'txt':
                links, file_publish_time = parse_txt_file(file.file_path)
            else:
                raise ValueError(f"Unsupported file type: {file_ext}")

            # Получаем токен для VK API
            setting = Settings.query.filter_by(key='vk_token').first()
            token = setting.value if setting else None

            if not token:
                logger.error("VK API token not found")
                raise ValueError("VK API token not found")

            from utils.vk_parser import extract_post_ids, make_vk_api_request
            from config import get_now_moscow, MOSCOW_TZ, UTC_TZ

            # Create Post records for each link
            for link in links:
                owner_id, post_id, post_type = extract_post_ids(link)
                post_publish_time = None

                if owner_id and post_id:
                    try:
                        # Делаем один запрос к API для получения информации о посте
                        post_data = make_vk_api_request('wall.getById', {
                            'posts': f"{owner_id}_{post_id}",
                            'extended': 1
                        }, token)
                        
                        if post_data.get('response') and post_data['response'].get('items'):
                            timestamp = post_data['response']['items'][0].get('date')
                            if timestamp:
                                # Создаем UTC время и конвертируем в московское
                                utc_time = datetime.fromtimestamp(timestamp, UTC_TZ)
                                post_publish_time = utc_time.astimezone(MOSCOW_TZ)
                                logger.info(f"Получено время публикации из API для {link}: {post_publish_time}")
                            else:
                                logger.warning(f"Timestamp not found in API response for {link}")
                        else:
                            logger.warning(f"No valid response from API for {link}")
                    except Exception as e:
                        logger.error(f"Ошибка получения времени публикации из VK API для {link}: {e}")

                # Если не удалось получить время из API или из файла, используем текущее время
                if not post_publish_time:
                    if file_publish_time:
                        post_publish_time = file_publish_time
                        logger.info(f"Используем время публикации из файла для {link}: {post_publish_time}")
                    else:
                        post_publish_time = get_now_moscow()
                        logger.warning(f"Используем текущее время для {link}: {post_publish_time}")

                # Убираем tzinfo для хранения в БД
                db_publish_time = post_publish_time.replace(tzinfo=None)
                
                # Рассчитываем время парсинга (всегда +23:50 от времени публикации)
                parse_time = db_publish_time + timedelta(hours=23, minutes=50)
                logger.info(f"Установлено время парсинга для {link}: {parse_time} (МСК)")

                # Create post record with actual publish time
                post = Post(
                    link=link,
                    file_id=file.id,
                    publish_time=post_publish_time.replace(tzinfo=None),  # Убираем tzinfo для сохранения в БД
                    parse_time=parse_time,
                    status='pending'
                )
                db.session.add(post)

            # Update file status
            file.status = 'processed'
            db.session.commit()

            # Schedule parsing tasks
            for post in Post.query.filter_by(file_id=file.id).all():
                schedule_post_parsing(post.id, app)

            logger.info(f"File {file.filename} processed successfully. Found {len(links)} VK links.")
        except Exception as e:
            logger.error(f"Error processing file {file.filename}: {str(e)}")
            file.status = 'failed'
            db.session.commit()
            raise