#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys

# Добавляем путь к проекту в системные пути
sys.path.insert(0, os.path.dirname(__file__))

# Импортируем Flask-приложение
from app import app as application

# Настройка логирования
if __name__ != '__main__':
    import logging
    gunicorn_logger = logging.getLogger('gunicorn.error')
    application.logger.handlers = gunicorn_logger.handlers
    application.logger.setLevel(gunicorn_logger.level)

# Для локального запуска
if __name__ == '__main__':
    application.run(host='0.0.0.0', port=5000)