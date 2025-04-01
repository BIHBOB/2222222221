import requests
import json
import logging
import re
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# Import config
from config import logger, VK_TOKEN

# VK API error class
class VKAPIError(Exception):
    """Error in VK API response"""
    def __init__(self, message, error_code=None):
        self.message = message
        self.error_code = error_code
        super().__init__(self.message)

# API cache
class VKAPICache:
    """Class for caching VK API results"""
    def __init__(self, cache_ttl=3600):  # TTL default 1 hour
        self.cache = {}
        self.cache_ttl = cache_ttl

    def get(self, cache_key):
        """Get value from cache by key"""
        if cache_key in self.cache:
            entry = self.cache[cache_key]
            # Check if cache is still valid
            if datetime.now().timestamp() - entry['timestamp'] < self.cache_ttl:
                logger.debug(f"Got data from cache for key {cache_key}")
                return entry['data']
            else:
                # Remove expired entry
                del self.cache[cache_key]
                logger.debug(f"Removed expired cache entry for key {cache_key}")
        return None

    def set(self, cache_key, data):
        """Save value to cache by key"""
        self.cache[cache_key] = {
            'data': data,
            'timestamp': datetime.now().timestamp()
        }
        logger.debug(f"Data saved to cache for key {cache_key}")

    def clear(self):
        """Clear cache"""
        self.cache = {}
        logger.info("Cache cleared")

    def clear_expired(self):
        """Clear expired cache entries"""
        current_time = datetime.now().timestamp()
        expired_keys = [
            key for key, entry in self.cache.items() 
            if current_time - entry['timestamp'] >= self.cache_ttl
        ]
        for key in expired_keys:
            del self.cache[key]
        if expired_keys:
            logger.info(f"Cleared {len(expired_keys)} expired cache entries")

# Create cache instance
vk_api_cache = VKAPICache()

def get_vk_token(app):
    """Get VK API token from settings"""
    with app.app_context():
        from models import Settings

        # Получаем экземпляр db через app.db
        # Здесь это не используется, но добавлено для полноты
        db = app.db

        setting = Settings.query.filter_by(key='vk_token').first()
        if setting and setting.value:
            return setting.value
    return None

def extract_post_ids(link):
    """Extract owner_id and post_id from VK post link"""
    # Regular post pattern
    wall_pattern = r'wall(-?\d+)_(\d+)'
    # Market post pattern
    market_pattern = r'market(-?\d+)_(\d+)'

    wall_match = re.search(wall_pattern, link)
    if wall_match:
        owner_id = int(wall_match.group(1))
        post_id = int(wall_match.group(2))
        return owner_id, post_id, 'wall'

    market_match = re.search(market_pattern, link)
    if market_match:
        owner_id = int(market_match.group(1))
        post_id = int(market_match.group(2))
        return owner_id, post_id, 'market'

    # AdBlogger pattern (needs special handling)
    if 'adblogger' in link:
        # For AdBlogger, we'll need to parse the page to find the post ID
        return None, None, 'adblogger'

    return None, None, None

def make_vk_api_request(method, params, token=None):
    """Make a request to VK API"""
    if not token:
        token = VK_TOKEN

    if not token:
        raise VKAPIError("VK API token not found", error_code=401)

    # Check cache
    cache_key = f"{method}_{json.dumps(params, sort_keys=True)}"
    cached_result = vk_api_cache.get(cache_key)
    if cached_result:
        return cached_result

    # Add access token to params
    params['access_token'] = token
    params['v'] = '5.131'  # API version

    # Make request
    try:
        response = requests.get(f"https://api.vk.com/method/{method}", params=params)
        response.raise_for_status()
        data = response.json()

        if 'error' in data:
            raise VKAPIError(
                data['error'].get('error_msg', 'Unknown API error'),
                error_code=data['error'].get('error_code')
            )

        # Cache result
        vk_api_cache.set(cache_key, data)

        return data
    except requests.exceptions.RequestException as e:
        raise VKAPIError(f"Request error: {str(e)}")

def extract_post_timestamp(post_data):
    """Extract timestamp from VK API post response and convert it to Moscow time"""
    if not post_data or not isinstance(post_data, dict):
        return None

    # VK API returns Unix timestamp in 'date' field
    timestamp = post_data.get('date')
    if timestamp:
        # VK API timestamps are in UTC, конвертируем их в московское время
        from config import UTC_TZ, MOSCOW_TZ

        # Создаем UTC время с временной зоной
        utc_time = datetime.fromtimestamp(timestamp, UTC_TZ)

        # Конвертируем в московское время (UTC+3)
        moscow_time = utc_time.astimezone(MOSCOW_TZ)

        # Убираем информацию о часовом поясе для хранения в БД
        return moscow_time.replace(tzinfo=None)

    return None

def parse_wall_post(owner_id, post_id, token=None):
    """Parse a wall post to get likes, comments, and reposts"""
    # Get post info
    post_data = make_vk_api_request('wall.getById', {
        'posts': f"{owner_id}_{post_id}",
        'extended': 1
    }, token)

    if not post_data.get('response') or not post_data['response'].get('items'):
        raise VKAPIError("Post not found")

    post = post_data['response']['items'][0]

    # Get post timestamp if available
    post_timestamp = extract_post_timestamp(post)

    # Get likes
    likes_data = []
    likes_count = post.get('likes', {}).get('count', 0)

    if likes_count > 0:
        offset = 0
        while offset < likes_count:
            likes_request = make_vk_api_request('likes.getList', {
                'type': 'post',
                'owner_id': owner_id,
                'item_id': post_id,
                'count': 1000,
                'offset': offset,
                'extended': 1
            }, token)

            if likes_request.get('response') and likes_request['response'].get('items'):
                for user in likes_request['response']['items']:
                    likes_data.append({
                        'id': user.get('id'),
                        'name': f"{user.get('first_name', '')} {user.get('last_name', '')}"
                    })

            offset += 1000

    # Get comments
    comments_data = []
    comments_count = post.get('comments', {}).get('count', 0)

    if comments_count > 0:
        offset = 0
        while offset < comments_count:
            comments_request = make_vk_api_request('wall.getComments', {
                'owner_id': owner_id,
                'post_id': post_id,
                'count': 100,
                'offset': offset,
                'extended': 1,
                'fields': 'first_name,last_name'
            }, token)

            if comments_request.get('response') and comments_request['response'].get('items'):
                for comment in comments_request['response']['items']:
                    from_id = comment.get('from_id')
                    # Find user in profiles
                    user_name = "Unknown"
                    for profile in comments_request['response'].get('profiles', []):
                        if profile.get('id') == from_id:
                            user_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}"
                            break

                    comments_data.append({
                        'id': from_id,
                        'name': user_name,
                        'text': comment.get('text', '')
                    })

            offset += 100

    # Get reposts
    reposts_data = []
    reposts_count = post.get('reposts', {}).get('count', 0)

    # For reposts, we can use a better API method
    if reposts_count > 0:
        try:
            # First try with the more direct method
            logger.info(f"Getting reposts data for wall{owner_id}_{post_id}")
            repost_request = make_vk_api_request('wall.getReposts', {
                'owner_id': owner_id,
                'post_id': post_id,
                'count': 1000,
                'offset': 0
            }, token)

            if repost_request.get('response') and repost_request['response'].get('items'):
                for item in repost_request['response']['items']:
                    from_id = item.get('from_id')
                    if from_id:
                        reposts_data.append({
                            'id': from_id,
                            'name': f"User ID {from_id}"  # Placeholder name
                        })
        except VKAPIError as e:
            logger.error(f"Error getting reposts with wall.getReposts: {str(e)}")

            # Fallback to search method
            repost_query = f"wall{owner_id}_{post_id}"
            offset = 0

            while len(reposts_data) < reposts_count and offset < 1000:  # Limit to 1000 reposts
                try:
                    reposts_request = make_vk_api_request('newsfeed.search', {
                        'q': repost_query,
                        'count': 200,
                        'offset': offset,
                        'extended': 1
                    }, token)

                    if reposts_request.get('response') and reposts_request['response'].get('items'):
                        for item in reposts_request['response']['items']:
                            if 'copy_history' in item and item['copy_history']:
                                for copy in item['copy_history']:
                                    if (str(copy.get('owner_id')) == str(owner_id) and 
                                        str(copy.get('id')) == str(post_id)):
                                        # This is a repost of our post
                                        from_id = item.get('from_id')
                                        if from_id:
                                            # Check if this repost is already in the list
                                            if not any(r.get('id') == from_id for r in reposts_data):
                                                reposts_data.append({
                                                    'id': from_id,
                                                    'name': f"User ID {from_id}"  # Placeholder name
                                                })
                                        break

                    offset += 200
                except VKAPIError as e:
                    # If we hit rate limits, stop here
                    logger.error(f"Error in newsfeed.search for reposts: {str(e)}")
                    if e.error_code in (6, 29):  # Rate limit errors
                        break
                    raise

    # Get user names for reposts if needed
    if reposts_data:
        user_ids = [str(user['id']) for user in reposts_data if isinstance(user['id'], (int, str))]
        if user_ids:
            # Split into chunks of 1000 to avoid API limits
            for i in range(0, len(user_ids), 1000):
                chunk = user_ids[i:i+1000]
                try:
                    logger.debug(f"Getting user info for repost users: {chunk}")
                    users_request = make_vk_api_request('users.get', {
                        'user_ids': ','.join(chunk),
                        'fields': 'first_name,last_name'
                    }, token)

                    if users_request.get('response'):
                        for user in users_request['response']:
                            # Update name in reposts_data
                            user_id = user.get('id')
                            if user_id:
                                for repost in reposts_data:
                                    if str(repost['id']) == str(user_id):
                                        repost['name'] = f"{user.get('first_name', '')} {user.get('last_name', '')}"
                except VKAPIError as e:
                    logger.error(f"Failed to get user info for reposts: {str(e)}")
                    # If we can't get user info, continue with what we have
                    pass

    return {
        'likes': {
            'count': likes_count,
            'data': likes_data
        },
        'comments': {
            'count': comments_count,
            'data': comments_data
        },
        'reposts': {
            'count': reposts_count,
            'data': reposts_data
        }
    }

def parse_market_post(owner_id, post_id, token=None):
    """Parse a market post to get likes, comments, and reposts"""
    # Market posts have a different API
    item_data = make_vk_api_request('market.getById', {
        'item_ids': f"{owner_id}_{post_id}",
        'extended': 1
    }, token)

    if not item_data.get('response') or not item_data['response'].get('items'):
        raise VKAPIError("Market item not found")

    # Comments for market items
    comments_data = []
    comments_count = 0

    try:
        comments_request = make_vk_api_request('market.getComments', {
            'owner_id': owner_id,
            'item_id': post_id,
            'count': 100,
            'extended': 1,
            'fields': 'first_name,last_name'
        }, token)

        if comments_request.get('response'):
            comments_count = comments_request['response'].get('count', 0)
            if comments_request['response'].get('items'):
                for comment in comments_request['response']['items']:
                    from_id = comment.get('from_id')
                    # Find user in profiles
                    user_name = "Unknown"
                    for profile in comments_request['response'].get('profiles', []):
                        if profile.get('id') == from_id:
                            user_name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}"
                            break

                    comments_data.append({
                        'id': from_id,
                        'name': user_name,
                        'text': comment.get('text', '')
                    })
    except VKAPIError:
        # If we can't get comments, continue with empty list
        pass

    # For market items, there are no direct likes or reposts
    return {
        'likes': {
            'count': 0,
            'data': []
        },
        'comments': {
            'count': comments_count,
            'data': comments_data
        },
        'reposts': {
            'count': 0,
            'data': []
        }
    }

def parse_adblogger_post(link, token=None):
    """Parse an AdBlogger post - this requires HTML scraping"""
    try:
        response = requests.get(link)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')

        # AdBlogger doesn't have standard VK API access
        # We'll need to extract what we can from the HTML

        # Get comments count from HTML
        comments_count = 0
        comments_el = soup.select_one('a.comments_count')
        if comments_el:
            comments_text = comments_el.text.strip()
            if comments_text.isdigit():
                comments_count = int(comments_text)

        # Get likes count from HTML
        likes_count = 0
        likes_el = soup.select_one('a.like_count')
        if likes_el:
            likes_text = likes_el.text.strip()
            if likes_text.isdigit():
                likes_count = int(likes_text)

        # For AdBlogger, we can't easily get the actual users
        return {
            'likes': {
                'count': likes_count,
                'data': []
            },
            'comments': {
                'count': comments_count,
                'data': []
            },
            'reposts': {
                'count': 0,
                'data': []
            }
        }
    except requests.exceptions.RequestException as e:
        raise VKAPIError(f"Failed to fetch AdBlogger post: {str(e)}")

def parse_vk_post(post_id, app):
    """Parse a VK post from the database"""
    with app.app_context():
        from models import Post, ParseResult

        # Получаем экземпляр db через app.db
        db = app.db

        try:
            post = Post.query.get(post_id)
            if not post:
                logger.error(f"Post with ID {post_id} not found")
                return None

            # Get VK token
            token = get_vk_token(app)

            # Extract post info from link
            link = post.link
            owner_id, item_id, post_type = extract_post_ids(link)

            # Get post info for timestamp
            if post_type == 'wall':
                # Получаем информацию о посте для получения timestamp
                post_info = make_vk_api_request('wall.getById', {
                    'posts': f"{owner_id}_{item_id}",
                    'extended': 1
                }, token)

                if post_info.get('response') and post_info['response'].get('items'):
                    vk_post = post_info['response']['items'][0]
                    # Извлекаем время публикации из ответа API
                    if vk_post.get('date'):
                        # Получаем время публикации из API в московском времени
                        from config import UTC_TZ, MOSCOW_TZ

                        # Создаем UTC время с временной зоной
                        utc_time = datetime.fromtimestamp(vk_post.get('date'), UTC_TZ)

                        # Конвертируем в московское время
                        moscow_time = utc_time.astimezone(MOSCOW_TZ)

                        # Убираем информацию о часовом поясе для хранения в БД
                        publish_time = moscow_time.replace(tzinfo=None)

                        logger.info(f"Получено время публикации из API (МСК): {publish_time}")

                        # Обновляем время публикации в БД (в московском времени без tzinfo)
                        if publish_time and publish_time != post.publish_time:
                            post.publish_time = publish_time
                            logger.info(f"Обновлено время публикации для поста {post_id} на {publish_time} (МСК)")

            # Parse based on post type
            if post_type == 'wall':
                parse_result = parse_wall_post(owner_id, item_id, token)
            elif post_type == 'market':
                parse_result = parse_market_post(owner_id, item_id, token)
            elif post_type == 'adblogger':
                parse_result = parse_adblogger_post(link, token)
            else:
                raise VKAPIError(f"Unknown post type: {post_type}")

            # Create result record
            result = ParseResult(
                post_id=post.id,
                likes_count=parse_result['likes']['count'],
                comments_count=parse_result['comments']['count'],
                reposts_count=parse_result['reposts']['count'],
                likes_data=json.dumps(parse_result['likes']['data']),
                comments_data=json.dumps(parse_result['comments']['data']),
                reposts_data=json.dumps(parse_result['reposts']['data'])
            )

            try:
                try:
                    db.session.add(result)
                    post.status = 'completed'
                    db.session.flush()

                    # Получаем ID результата до коммита
                    result_id = result.id

                    db.session.commit()

                    # Проверяем существование результата после коммита
                    saved_result = ParseResult.query.get(result_id)
                    if not saved_result:
                        raise Exception("Не удалось сохранить результат парсинга")

                    success_message = {
                        "message": "Пост успешно распарсен",
                        "result_id": result_id,
                        "status": "success"
                    }

                    logger.info(f"Успешно обработан пост {post.link} с ID результата {result_id}")
                    return success_message

                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Ошибка при сохранении результата: {str(e)}")
                    post.status = 'failed'
                    db.session.commit()
                    raise

            except Exception as e:
                db.session.rollback()
                logger.error(f"Database error while saving parse result: {str(e)}")
                raise

        except Exception as e:
            logger.error(f"Error parsing post {post.link}: {str(e)}")
            post.status = 'failed'
            db.session.commit()
            raise