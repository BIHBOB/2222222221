from datetime import datetime
from app import db
from config import get_now_moscow

class Settings(db.Model):
    """Settings model for storing parser configuration"""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    value = db.Column(db.Text)
    updated_at = db.Column(db.DateTime, default=lambda: get_now_moscow().replace(tzinfo=None))

    def __repr__(self):
        return f'<Setting {self.key}>'


class Post(db.Model):
    """Model for posts scheduled for parsing"""
    id = db.Column(db.Integer, primary_key=True)
    link = db.Column(db.String(255), nullable=False)
    file_id = db.Column(db.Integer, db.ForeignKey('file.id'), nullable=True)
    publish_time = db.Column(db.DateTime, nullable=False)
    parse_time = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default='pending')  # pending, completed, failed
    created_at = db.Column(db.DateTime, default=lambda: get_now_moscow().replace(tzinfo=None))
    
    # Relationship with the File model
    file = db.relationship('File', backref=db.backref('posts', lazy=True))

    def __repr__(self):
        return f'<Post {self.link}>'
        
    @property
    def created_at_moscow(self):
        """Возвращает время создания в московском часовом поясе"""
        from config import to_moscow_time, MOSCOW_TZ
        # Добавляем московский часовой пояс, если его нет (данные из БД)
        if self.created_at and self.created_at.tzinfo is None:
            return self.created_at.replace(tzinfo=MOSCOW_TZ)
        return to_moscow_time(self.created_at) if self.created_at else None
        
    @property
    def publish_time_moscow(self):
        """Возвращает время публикации в московском часовом поясе"""
        from config import to_moscow_time, MOSCOW_TZ
        # Добавляем московский часовой пояс, если его нет (данные из БД)
        if self.publish_time and self.publish_time.tzinfo is None:
            return self.publish_time.replace(tzinfo=MOSCOW_TZ)
        return to_moscow_time(self.publish_time) if self.publish_time else None
        
    @property
    def parse_time_moscow(self):
        """Возвращает время парсинга в московском часовом поясе"""
        from config import to_moscow_time, MOSCOW_TZ
        # Добавляем московский часовой пояс, если его нет (данные из БД)
        if self.parse_time and self.parse_time.tzinfo is None:
            return self.parse_time.replace(tzinfo=MOSCOW_TZ)
        return to_moscow_time(self.parse_time) if self.parse_time else None


class File(db.Model):
    """Model for uploaded files containing post links"""
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    file_path = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(10), nullable=False)  # html, pdf, txt
    status = db.Column(db.String(20), default='processing')  # processing, processed, failed
    parse_option = db.Column(db.String(20), default='standard')
    uploaded_at = db.Column(db.DateTime, default=lambda: get_now_moscow().replace(tzinfo=None))
    
    def __repr__(self):
        return f'<File {self.filename}>'
        
    @property
    def uploaded_at_moscow(self):
        """Возвращает время загрузки в московском часовом поясе"""
        from config import to_moscow_time, MOSCOW_TZ
        # Добавляем московский часовой пояс, если его нет (данные из БД)
        if self.uploaded_at and self.uploaded_at.tzinfo is None:
            return self.uploaded_at.replace(tzinfo=MOSCOW_TZ)
        return to_moscow_time(self.uploaded_at) if self.uploaded_at else None


class ParseResult(db.Model):
    """Model for storing parsing results"""
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey('post.id'), nullable=False)
    likes_count = db.Column(db.Integer, default=0)
    comments_count = db.Column(db.Integer, default=0)
    reposts_count = db.Column(db.Integer, default=0)
    likes_data = db.Column(db.Text)  # JSON data of users who liked
    comments_data = db.Column(db.Text)  # JSON data of users who commented
    reposts_data = db.Column(db.Text)  # JSON data of users who reposted
    created_at = db.Column(db.DateTime, default=lambda: get_now_moscow().replace(tzinfo=None))
    
    # Relationship with the Post model
    post = db.relationship('Post', backref=db.backref('results', lazy=True))

    def __repr__(self):
        return f'<ParseResult for post {self.post_id}>'
        
    @property
    def created_at_moscow(self):
        """Возвращает время создания в московском часовом поясе"""
        from config import to_moscow_time, MOSCOW_TZ
        # Добавляем московский часовой пояс, если его нет (данные из БД)
        if self.created_at and self.created_at.tzinfo is None:
            return self.created_at.replace(tzinfo=MOSCOW_TZ)
        return to_moscow_time(self.created_at) if self.created_at else None
