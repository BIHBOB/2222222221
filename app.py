import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase
from werkzeug.utils import secure_filename
import json
from datetime import datetime, timedelta
import logging

# Create Flask app
class Base(DeclarativeBase):
    pass

db = SQLAlchemy(model_class=Base)
app = Flask(__name__)

# Настройка секретного ключа из переменных окружения
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.environ.get("SESSION_SECRET", "dev-secret-key"))

# Сделаем db доступным через app.db для удобства
app.db = db

# Import config - импортируем после создания приложения
from config import DB_URI, UPLOAD_DIR, RESULTS_DIR, logger, ENV, DEBUG

# Настройка Flask-приложения для разных сред
app.config["ENV"] = ENV
app.config["DEBUG"] = DEBUG

# Configure database - приоритет у переменной окружения
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_recycle": 300,
    "pool_pre_ping": True,
    "pool_size": 10,
    "max_overflow": 20
}

# Инициализация соединения с базой данных
db.init_app(app)

# Логируем важную информацию при запуске
logger.info(f"Flask app initialized: DEBUG={DEBUG}, ENV={ENV}")
logger.info(f"Database URI: {app.config['SQLALCHEMY_DATABASE_URI']}")

# Initialize database
with app.app_context():
    import models
    db.create_all()
    
    # Initialize settings if needed
    from config import DEFAULT_SETTINGS
    from models import Settings
    
    # Проверяем наличие устаревшей настройки result_format и миграция на export_format
    old_format_setting = Settings.query.filter_by(key='result_format').first()
    if old_format_setting:
        # Если есть старая настройка, но нет новой - создаем export_format
        if not Settings.query.filter_by(key='export_format').first():
            new_setting = Settings(key='export_format', value=old_format_setting.value)
            db.session.add(new_setting)
            logger.info(f"Мигрированы настройки формата экспорта: {old_format_setting.value}")
    
    # Инициализируем недостающие настройки по умолчанию
    for key, value in DEFAULT_SETTINGS.items():
        if not Settings.query.filter_by(key=key).first():
            setting = Settings(key=key, value=str(value))
            db.session.add(setting)
            logger.info(f"Инициализирована настройка: {key} = {value}")
    
    db.session.commit()

# Import utility modules after initializing app and db
from utils.file_processor import process_file, extract_vk_links
from utils.vk_parser import parse_vk_post
from utils.scheduler import initialize_scheduler, schedule_post_parsing

# Initialize the scheduler
scheduler = initialize_scheduler(app)

# Routes
@app.route('/')
def index():
    """Main dashboard page"""
    from models import Post, File, ParseResult
    
    # Get quick stats
    total_files = File.query.count()
    pending_posts = Post.query.filter_by(status='pending').count()
    completed_posts = Post.query.filter_by(status='completed').count()
    failed_posts = Post.query.filter_by(status='failed').count()
    
    # Get recent activities
    recent_uploads = File.query.order_by(File.uploaded_at.desc()).limit(5).all()
    recent_results = ParseResult.query.order_by(ParseResult.created_at.desc()).limit(5).all()
    
    return render_template('index.html', 
                          total_files=total_files,
                          pending_posts=pending_posts,
                          completed_posts=completed_posts,
                          failed_posts=failed_posts,
                          recent_uploads=recent_uploads,
                          recent_results=recent_results)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    """Страница загрузки файлов"""
    from models import File
    from config import PARSE_OPTION_STANDARD, PARSE_OPTION_NOW, PARSE_OPTION_5MIN, PARSE_OPTION_30MIN, PARSE_OPTION_1HOUR
    
    if request.method == 'POST':
        # Проверка наличия файла в запросе
        if 'file' not in request.files:
            flash('Отсутствует часть с файлом', 'danger')
            return redirect(request.url)
        
        file = request.files['file']
        
        # Проверка выбора файла
        if file.filename == '':
            flash('Не выбран файл', 'danger')
            return redirect(request.url)
        
        # Получаем опцию парсинга из формы
        parse_option = request.form.get('parse_option', PARSE_OPTION_STANDARD)
        
        # Проверка расширения файла
        allowed_extensions = {'html', 'txt', 'pdf'}
        file_ext = file.filename.rsplit('.', 1)[-1].lower()
        
        if file_ext not in allowed_extensions:
            flash(f'Разрешены только файлы типа {", ".join(allowed_extensions)}', 'danger')
            return redirect(request.url)
        
        try:
            # Сохраняем файл
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_filename = f"{timestamp}_{filename}"
            file_path = os.path.join(UPLOAD_DIR, unique_filename)
            file.save(file_path)
            
            # Создаем запись в БД
            db_file = File(
                filename=filename,
                file_path=file_path,
                file_type=file_ext,
                parse_option=parse_option,
                status='processing'
            )
            db.session.add(db_file)
            db.session.commit()
            
            # Обрабатываем файл в фоне
            process_file(db_file.id, app)
            
            flash(f'Файл успешно загружен и начата обработка', 'success')
            return redirect(url_for('archive'))
            
        except Exception as e:
            logger.error(f"Ошибка загрузки файла: {str(e)}")
            flash(f'Ошибка загрузки файла: {str(e)}', 'danger')
            return redirect(request.url)
    
    # GET запрос - отображаем форму загрузки
    parse_options = [
        {'value': PARSE_OPTION_STANDARD, 'label': 'Стандартно (23:50 от времени публикации)'},
        {'value': PARSE_OPTION_NOW, 'label': 'Немедленный парсинг (прямо сейчас)'},
        {'value': PARSE_OPTION_5MIN, 'label': 'За 5 минут до истечения 24 часов'},
        {'value': PARSE_OPTION_30MIN, 'label': 'За 30 минут до истечения 24 часов'},
        {'value': PARSE_OPTION_1HOUR, 'label': 'За 1 час до истечения 24 часов'}
    ]
    
    return render_template('upload.html', parse_options=parse_options)

@app.route('/archive')
def archive():
    """Страница архива файлов"""
    from models import File
    
    # Получаем все файлы с пагинацией
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    files = File.query.order_by(File.uploaded_at.desc()).paginate(page=page, per_page=per_page)
    
    return render_template('archive.html', files=files)

@app.route('/archive/file/<int:file_id>')
def file_detail(file_id):
    """Страница детальной информации о файле с извлеченными постами"""
    from models import File, Post
    
    file = File.query.get_or_404(file_id)
    posts = Post.query.filter_by(file_id=file_id).all()
    
    return render_template('file_detail.html', file=file, posts=posts)

@app.route('/archive/file/<int:file_id>/delete', methods=['POST'])
def delete_file(file_id):
    """Удаление файла и связанных постов"""
    from models import File, Post
    
    file = File.query.get(file_id)
    if not file:
        flash('Файл не найден', 'danger')
        return redirect(url_for('archive'))
    
    try:
        # Удаляем связанные посты
        posts = Post.query.filter_by(file_id=file_id).all()
        for post in posts:
            # Удаляем результаты постов, если есть
            results = post.results
            for result in results:
                db.session.delete(result)
            
            db.session.delete(post)
        
        # Удаляем физический файл, если существует
        if os.path.exists(file.file_path):
            try:
                os.remove(file.file_path)
            except OSError as e:
                logger.warning(f"Не удалось удалить физический файл: {str(e)}")
        
        # Удаляем запись о файле
        db.session.delete(file)
        db.session.commit()
        
        flash('Файл и связанные данные успешно удалены', 'success')
    except Exception as e:
        db.session.rollback()
        logger.error(f"Ошибка удаления файла: {str(e)}")
        flash(f'Ошибка удаления файла: {str(e)}', 'danger')
    
    return redirect(url_for('archive'))

@app.route('/results')
def results():
    """Страница результатов парсинга"""
    from models import ParseResult, Post
    
    # Получаем все результаты с пагинацией
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    results = ParseResult.query.join(Post).order_by(ParseResult.created_at.desc()).paginate(page=page, per_page=per_page)
    
    return render_template('results.html', results=results)

@app.route('/results/<int:result_id>')
def result_detail(result_id):
    """Детальный просмотр результата парсинга"""
    from models import ParseResult, Settings
    
    result = ParseResult.query.get_or_404(result_id)
    
    # Подготавливаем данные для отображения
    likes_data = json.loads(result.likes_data) if result.likes_data else []
    comments_data = json.loads(result.comments_data) if result.comments_data else []
    reposts_data = json.loads(result.reposts_data) if result.reposts_data else []
    
    # Получаем текущий формат экспорта
    export_format_setting = Settings.query.filter_by(key='export_format').first()
    export_format = export_format_setting.value if export_format_setting else 'txt'
    
    # Форматы для отображения
    format_labels = {
        'txt': 'TXT',
        'csv': 'CSV',
        'excel': 'Excel'
    }
    
    export_format_label = format_labels.get(export_format, 'TXT')
    
    return render_template('result_detail.html', 
                          result=result, 
                          likes_data=likes_data,
                          comments_data=comments_data,
                          reposts_data=reposts_data,
                          export_format=export_format_label)

@app.route('/results/<int:result_id>/export')
def export_result(result_id):
    """Экспорт результата парсинга в выбранном формате (TXT, CSV или Excel)"""
    from models import ParseResult, Settings
    import pandas as pd
    
    result = ParseResult.query.get_or_404(result_id)
    
    # Получаем предпочтительный формат из настроек
    export_format_setting = Settings.query.filter_by(key='export_format').first()
    export_format = export_format_setting.value if export_format_setting else 'txt'
    
    # Формат может быть 'txt', 'csv' или 'excel'
    # Используем московское время для метки времени в имени файла
    from config import get_now_moscow
    timestamp = get_now_moscow().strftime('%Y%m%d_%H%M%S')
    
    if export_format == 'txt':
        # TXT формат (исходный вариант)
        export_filename = f"result_{result_id}_{timestamp}.txt"
        export_path = os.path.join(RESULTS_DIR, export_filename)
        
        # Генерируем содержимое экспорта в текстовом формате
        with open(export_path, 'w', encoding='utf-8') as f:
            f.write(f"Результаты парсинга поста ВКонтакте\n")
            f.write(f"Ссылка на пост: {result.post.link}\n")
            f.write(f"Время парсинга: {result.created_at_moscow.strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n\n")
            
            # Лайки
            f.write(f"ЛАЙКИ ({result.likes_count}):\n")
            if result.likes_data:
                likes = json.loads(result.likes_data)
                for i, user in enumerate(likes, 1):
                    f.write(f"{i}. {user.get('name', 'Неизвестно')} - https://vk.com/id{user.get('id', '')}\n")
            f.write("\n")
            
            # Комментарии
            f.write(f"КОММЕНТАРИИ ({result.comments_count}):\n")
            if result.comments_data:
                comments = json.loads(result.comments_data)
                for i, comment in enumerate(comments, 1):
                    f.write(f"{i}. {comment.get('name', 'Неизвестно')} - https://vk.com/id{comment.get('id', '')}\n")
                    if 'text' in comment and comment['text']:
                        f.write(f"   Комментарий: {comment['text']}\n")
            f.write("\n")
            
            # Репосты
            f.write(f"РЕПОСТЫ ({result.reposts_count}):\n")
            if result.reposts_data:
                reposts = json.loads(result.reposts_data)
                for i, user in enumerate(reposts, 1):
                    f.write(f"{i}. {user.get('name', 'Неизвестно')} - https://vk.com/id{user.get('id', '')}\n")
        
    elif export_format == 'csv':
        # CSV формат
        export_filename = f"result_{result_id}_{timestamp}.csv"
        export_path = os.path.join(RESULTS_DIR, export_filename)
        
        # Подготавливаем данные для CSV
        data = {
            'Тип': [],
            'ID пользователя': [],
            'Имя пользователя': [],
            'Профиль ВК': [],
            'Комментарий': []
        }
        
        # Лайки
        if result.likes_data:
            likes = json.loads(result.likes_data)
            for user in likes:
                data['Тип'].append('Лайк')
                data['ID пользователя'].append(user.get('id', ''))
                data['Имя пользователя'].append(user.get('name', 'Неизвестно'))
                data['Профиль ВК'].append(f"https://vk.com/id{user.get('id', '')}")
                data['Комментарий'].append('')
        
        # Комментарии
        if result.comments_data:
            comments = json.loads(result.comments_data)
            for comment in comments:
                data['Тип'].append('Комментарий')
                data['ID пользователя'].append(comment.get('id', ''))
                data['Имя пользователя'].append(comment.get('name', 'Неизвестно'))
                data['Профиль ВК'].append(f"https://vk.com/id{comment.get('id', '')}")
                data['Комментарий'].append(comment.get('text', ''))
        
        # Репосты
        if result.reposts_data:
            reposts = json.loads(result.reposts_data)
            for user in reposts:
                data['Тип'].append('Репост')
                data['ID пользователя'].append(user.get('id', ''))
                data['Имя пользователя'].append(user.get('name', 'Неизвестно'))
                data['Профиль ВК'].append(f"https://vk.com/id{user.get('id', '')}")
                data['Комментарий'].append('')
        
        # Создаем DataFrame и сохраняем в CSV
        df = pd.DataFrame(data)
        df.to_csv(export_path, index=False, encoding='utf-8-sig')  # utf-8-sig для корректного отображения в Excel
        
    elif export_format == 'excel':
        # Excel формат
        export_filename = f"result_{result_id}_{timestamp}.xlsx"
        export_path = os.path.join(RESULTS_DIR, export_filename)
        
        # Создаем Excel-файл с несколькими листами
        with pd.ExcelWriter(export_path, engine='xlsxwriter') as writer:
            # Лист с основной информацией
            info_data = {
                'Параметр': ['Ссылка на пост', 'Время парсинга', 'Количество лайков', 'Количество комментариев', 'Количество репостов'],
                'Значение': [
                    result.post.link,
                    f"{result.created_at_moscow.strftime('%d.%m.%Y %H:%M:%S')} (МСК)",
                    result.likes_count,
                    result.comments_count,
                    result.reposts_count
                ]
            }
            pd.DataFrame(info_data).to_excel(writer, sheet_name='Информация', index=False)
            
            # Лист с лайками
            if result.likes_data:
                likes = json.loads(result.likes_data)
                likes_data = {
                    'ID пользователя': [user.get('id', '') for user in likes],
                    'Имя пользователя': [user.get('name', 'Неизвестно') for user in likes],
                    'Профиль ВК': [f"https://vk.com/id{user.get('id', '')}" for user in likes]
                }
                pd.DataFrame(likes_data).to_excel(writer, sheet_name='Лайки', index=False)
            
            # Лист с комментариями
            if result.comments_data:
                comments = json.loads(result.comments_data)
                comments_data = {
                    'ID пользователя': [comment.get('id', '') for comment in comments],
                    'Имя пользователя': [comment.get('name', 'Неизвестно') for comment in comments],
                    'Комментарий': [comment.get('text', '') for comment in comments],
                    'Профиль ВК': [f"https://vk.com/id{comment.get('id', '')}" for comment in comments]
                }
                pd.DataFrame(comments_data).to_excel(writer, sheet_name='Комментарии', index=False)
            
            # Лист с репостами
            if result.reposts_data:
                reposts = json.loads(result.reposts_data)
                reposts_data = {
                    'ID пользователя': [user.get('id', '') for user in reposts],
                    'Имя пользователя': [user.get('name', 'Неизвестно') for user in reposts],
                    'Профиль ВК': [f"https://vk.com/id{user.get('id', '')}" for user in reposts]
                }
                pd.DataFrame(reposts_data).to_excel(writer, sheet_name='Репосты', index=False)
    
    else:
        # Если формат неизвестен, используем TXT по умолчанию
        export_filename = f"result_{result_id}_{timestamp}.txt"
        export_path = os.path.join(RESULTS_DIR, export_filename)
        with open(export_path, 'w', encoding='utf-8') as f:
            f.write(f"Результаты парсинга поста ВКонтакте\n")
            f.write(f"Ссылка на пост: {result.post.link}\n")
            f.write(f"Время парсинга: {result.created_at_moscow.strftime('%d.%m.%Y %H:%M:%S')} (МСК)\n")
    
    # Возвращаем файл для скачивания
    return send_file(export_path, as_attachment=True, download_name=export_filename)

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Страница настроек"""
    from models import Settings
    
    if request.method == 'POST':
        # Обновляем настройки
        try:
            vk_token = request.form.get('vk_token', '')
            parse_option = request.form.get('default_parse_option', 'standard')
            export_format = request.form.get('export_format', 'txt')
            
            # Обновляем настройки в базе данных
            vk_token_setting = Settings.query.filter_by(key='vk_token').first()
            vk_token_setting.value = vk_token
            
            parse_option_setting = Settings.query.filter_by(key='default_parse_option').first()
            parse_option_setting.value = parse_option
            
            # Обновляем или создаем настройку формата экспорта
            export_format_setting = Settings.query.filter_by(key='export_format').first()
            if export_format_setting:
                export_format_setting.value = export_format
            else:
                export_format_setting = Settings(key='export_format', value=export_format)
                db.session.add(export_format_setting)
            
            db.session.commit()
            
            flash('Настройки успешно обновлены', 'success')
        except Exception as e:
            logger.error(f"Ошибка обновления настроек: {str(e)}")
            flash(f'Ошибка обновления настроек: {str(e)}', 'danger')
    
    # Получаем текущие настройки
    settings_dict = {}
    settings = Settings.query.all()
    for setting in settings:
        settings_dict[setting.key] = setting.value
    
    from config import PARSE_OPTION_STANDARD, PARSE_OPTION_NOW, PARSE_OPTION_5MIN, PARSE_OPTION_30MIN, PARSE_OPTION_1HOUR
    parse_options = [
        {'value': PARSE_OPTION_STANDARD, 'label': 'Стандартно (23:50 от времени публикации)'},
        {'value': PARSE_OPTION_NOW, 'label': 'Немедленный парсинг (прямо сейчас)'},
        {'value': PARSE_OPTION_5MIN, 'label': 'За 5 минут до истечения 24 часов'},
        {'value': PARSE_OPTION_30MIN, 'label': 'За 30 минут до истечения 24 часов'},
        {'value': PARSE_OPTION_1HOUR, 'label': 'За 1 час до истечения 24 часов'}
    ]
    
    return render_template('settings.html', settings=settings_dict, parse_options=parse_options)

@app.route('/scheduled')
def scheduled():
    """Страница запланированных постов для парсинга"""
    from models import Post
    from config import get_now_moscow
    
    # Получаем все запланированные посты с пагинацией
    page = request.args.get('page', 1, type=int)
    per_page = 10
    
    # Получаем текущее московское время
    now_moscow = get_now_moscow()
    
    # Убираем tzinfo для сравнения с полями в БД (наивное время)
    now_moscow_naive = now_moscow.replace(tzinfo=None)
    
    # Получаем список задач для парсинга, которые ещё не обработаны и 
    # время парсинга которых ещё не наступило
    pending_posts = Post.query.filter(
        Post.status == 'pending',
        Post.parse_time > now_moscow_naive
    ).order_by(Post.parse_time).paginate(page=page, per_page=per_page)
    
    # Возвращаем в шаблон текущее московское время С часовым поясом
    # для правильного отображения в UI
    return render_template('scheduled.html', posts=pending_posts, current_time=now_moscow)

@app.route('/scheduled/<int:post_id>/cancel', methods=['POST'])
def cancel_scheduled(post_id):
    """Отмена запланированного парсинга поста"""
    from models import Post
    
    post = Post.query.get_or_404(post_id)
    
    try:
        post.status = 'cancelled'
        db.session.commit()
        
        flash('Запланированный парсинг успешно отменен', 'success')
    except Exception as e:
        logger.error(f"Ошибка отмены запланированного парсинга: {str(e)}")
        flash(f'Ошибка отмены запланированного парсинга: {str(e)}', 'danger')
    
    return redirect(url_for('scheduled'))

# API endpoints
@app.route('/api/process-file/<int:file_id>', methods=['POST'])
def api_process_file(file_id):
    """API endpoint для обработки файла"""
    from models import File
    
    file = File.query.get_or_404(file_id)
    
    try:
        # Обрабатываем файл
        process_file(file_id, app)
        return jsonify({'status': 'success', 'message': 'Файл успешно обработан'})
    except Exception as e:
        logger.error(f"Ошибка обработки файла: {str(e)}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/parse-post/<int:post_id>', methods=['POST'])
def api_parse_post(post_id):
    """API endpoint для ручного запуска парсинга поста"""
    from models import Post
    
    post = Post.query.get_or_404(post_id)
    
    try:
        # Парсим пост
        result = parse_vk_post(post_id, app)
        if isinstance(result, dict) and result.get('status') == 'success':
            return jsonify(result)
        else:
            raise Exception("Неверный формат результата парсинга")
    except Exception as e:
        logger.error(f"Ошибка парсинга поста: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

# Обработчики ошибок
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
