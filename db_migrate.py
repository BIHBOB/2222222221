#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Скрипт для миграции базы данных при деплое на сервер.
Запускается после установки зависимостей, но перед запуском приложения.
Создает таблицы и необходимые первоначальные данные.
"""

import os
from pathlib import Path
import argparse
import logging

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger("db_migrate")

def init_database():
    """Функция для инициализации базы данных"""
    from app import app, db
    from models import Settings
    from config import DEFAULT_SETTINGS
    
    logger.info("Начало инициализации базы данных")
    
    with app.app_context():
        # Создаем таблицы, если их нет
        db.create_all()
        logger.info("Таблицы созданы или уже существуют")
        
        # Инициализируем настройки
        for key, value in DEFAULT_SETTINGS.items():
            if not Settings.query.filter_by(key=key).first():
                setting = Settings(key=key, value=str(value))
                db.session.add(setting)
                logger.info(f"Добавлена настройка: {key} = {value}")
        
        # Сохраняем изменения
        db.session.commit()
        logger.info("Инициализация базы данных успешно завершена")

def backup_database(backup_dir):
    """Функция для создания резервной копии базы данных"""
    from app import app
    from config import BASE_DIR
    
    # Проверяем, существует ли SQLite-файл для резервного копирования
    db_path = BASE_DIR / 'vk_parser.db'
    if db_path.exists():
        import shutil
        import datetime
        
        # Создаем директорию для резервных копий, если она не существует
        backup_path = Path(backup_dir)
        backup_path.mkdir(exist_ok=True)
        
        # Формируем имя файла резервной копии с временной меткой
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_path / f"vk_parser_backup_{timestamp}.db"
        
        # Копируем файл
        shutil.copy2(db_path, backup_file)
        logger.info(f"Создана резервная копия базы данных: {backup_file}")
        return True
    
    logger.info("SQLite файл базы данных не найден, резервная копия не создана")
    return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Миграция базы данных VK Parser')
    parser.add_argument('--backup', help='Директория для резервной копии', default='./backup')
    parser.add_argument('--skip-backup', action='store_true', help='Пропустить создание резервной копии')
    
    args = parser.parse_args()
    
    # Создаем резервную копию, если не пропущено
    if not args.skip_backup:
        backup_database(args.backup)
    
    # Инициализируем базу данных
    init_database()