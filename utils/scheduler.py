import schedule
import time
import threading
import logging
from datetime import datetime, timedelta
import traceback

# Import config
from config import logger

# Store scheduled jobs
jobs = {}

def initialize_scheduler(app):
    """Initialize scheduler and start background thread"""
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
    scheduler_thread.start()
    
    # Schedule a task to check for pending posts every 5 minutes
    with app.app_context():
        schedule.every(5).minutes.do(check_pending_posts, app)
    
    return scheduler_thread

def run_scheduler():
    """Run the scheduler loop"""
    while True:
        try:
            schedule.run_pending()
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in scheduler: {str(e)}")
            traceback.print_exc()

def check_pending_posts(app):
    """Check for pending posts that need to be parsed"""
    with app.app_context():
        from models import Post
        from utils.vk_parser import parse_vk_post
        from config import get_now_moscow, to_moscow_time
        
        # Получаем экземпляр db через app.db
        db = app.db
        
        # Get posts that should be parsed now (используем московское время)
        now = get_now_moscow().replace(tzinfo=None)  # Убираем tzinfo для совместимости с БД
        five_minutes_ahead = now + timedelta(minutes=5)
        
        # Find posts scheduled in the next 5 minutes that are still pending
        posts = Post.query.filter(
            Post.status == 'pending',
            Post.parse_time <= five_minutes_ahead
        ).all()
        
        for post in posts:
            # If post is scheduled for the future, schedule it
            if post.parse_time > now:
                # Schedule parsing at the exact time
                schedule_post_parsing(post.id, app)
            else:
                # Post should have been parsed already, do it now
                try:
                    parse_vk_post(post.id, app)
                except Exception as e:
                    logger.error(f"Error parsing post {post.id}: {str(e)}")

def schedule_post_parsing(post_id, app):
    """Schedule parsing of a post at the specified time"""
    with app.app_context():
        from models import Post
        from utils.vk_parser import parse_vk_post
        
        # Получаем экземпляр db через app.db
        db = app.db
        
        post = Post.query.get(post_id)
        if not post or post.status != 'pending':
            return
        
        # If job is already scheduled, cancel it
        if post_id in jobs:
            schedule.cancel_job(jobs[post_id])
        
        # Calculate time until parsing (используем московское время)
        from config import get_now_moscow, to_moscow_time
        
        now = get_now_moscow().replace(tzinfo=None)  # Убираем tzinfo для совместимости с БД
        if post.parse_time <= now:
            # If parse time is in the past, parse now
            try:
                parse_vk_post(post_id, app)
            except Exception as e:
                logger.error(f"Error parsing post {post_id}: {str(e)}")
        else:
            # Schedule for the future
            seconds_until_parse = (post.parse_time - now).total_seconds()
            
            # Use schedule's at method if less than a day away
            # Otherwise use more complex handling
            if seconds_until_parse < 86400:  # Less than a day
                # Schedule the job
                job = schedule.every(seconds_until_parse).seconds.do(
                    parse_with_context, post_id, app
                ).tag(f"post_{post_id}")
                
                # Store job reference
                jobs[post_id] = job
                
                logger.info(f"Scheduled parsing for post {post_id} at {post.parse_time}")
            else:
                # For times more than a day in the future, we'll rely on the periodic check
                logger.info(f"Post {post_id} will be checked periodically until parse time {post.parse_time}")

def parse_with_context(post_id, app):
    """Parse post with application context"""
    try:
        with app.app_context():
            from utils.vk_parser import parse_vk_post
            
            # Получаем экземпляр db через app.db
            db = app.db
            
            parse_vk_post(post_id, app)
    except Exception as e:
        logger.error(f"Error in scheduled parsing of post {post_id}: {str(e)}")
    
    # Remove the job from our tracking
    if post_id in jobs:
        del jobs[post_id]
    
    # Return schedule.CancelJob to prevent the job from running again
    return schedule.CancelJob
