# downloader.py - Модуль для скачивания видео с YouTube с автоперебором клиентов
import os
import re
import time
import threading
from typing import Optional, Tuple, List, Dict

import yt_dlp
import requests

from logger_config import create_logger

# Логгер для этого модуля
logger = create_logger(
    name='Downloader',
    level=20,
    detailed=False,
    show_separators=False
).get_logger()

DOWNLOAD_FOLDER = 'downloads'
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
MIN_DURATION_SECONDS = 78

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

QUALITY_OPTIONS = {
    '360': {
        'format': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]/18',
        'label': '📺 360p',
        'quality_label': '360p',
        'resolution': (640, 360),
        'description': '360p',
        'audio_only': False,
    },
    '480': {
        'format': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]/18',
        'label': '📺 480p',
        'quality_label': '480p',
        'resolution': (854, 480),
        'description': '480p',
        'audio_only': False,
    },
    '720': {
        'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/136+140/18',
        'label': '📺 720p HD',
        'quality_label': '720p HD',
        'resolution': (1280, 720),
        'description': '720p HD',
        'audio_only': False,
    },
    '1080': {
        'format': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/137+140/18',
        'label': '📺 1080p Full HD',
        'quality_label': '1080p Full HD',
        'resolution': (1920, 1080),
        'description': '1080p Full HD',
        'audio_only': False,
    },
    'mp3': {
        'format': 'bestaudio[ext=m4a]/140',
        'label': '🎵 MP3',
        'quality_label': 'MP3',
        'resolution': None,
        'description': 'Аудио 192 kbps',
        'audio_only': True,
    },
}

# ============================================================
# СПИСОК СТРАТЕГИЙ ДЛЯ ПЕРЕБОРА
# ============================================================
STRATEGIES: List[Dict] = [
    {
        'name': 'Android клиент',
        'player_client': ['android'],
        'player_skip': ['webpage', 'configs'],
        'user_agent': 'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36',
    },
    {
        'name': 'Web клиент',
        'player_client': ['web'],
        'player_skip': ['webpage', 'configs'],
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    },
    {
        'name': 'Android + Web',
        'player_client': ['android', 'web'],
        'player_skip': ['webpage', 'configs'],
        'user_agent': 'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36',
    },
    {
        'name': 'iOS клиент (без PO токена)',
        'player_client': ['ios'],
        'player_skip': ['webpage', 'configs'],
        'user_agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
    },
    {
        'name': 'TV клиент',
        'player_client': ['tv'],
        'player_skip': ['webpage', 'configs'],
        'user_agent': 'Mozilla/5.0 (ChromiumStylePlatform) AppleWebKit/535.1 (KHTML, like Gecko) Qt/5.0 Alpha Safari/535.1',
    },
    {
        'name': 'Android с пропуском всех проверок',
        'player_client': ['android'],
        'player_skip': ['webpage', 'configs', 'hls', 'dash'],
        'user_agent': 'Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36',
    },
    {
        'name': 'Без указания клиента (автовыбор)',
        'player_client': None,
        'player_skip': [],
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    },
]


def check_aria2() -> bool:
    import subprocess
    try:
        result = subprocess.run(['aria2c', '--version'], capture_output=True, timeout=2)
        return result.returncode == 0
    except:
        return False


ARIA2_AVAILABLE = check_aria2()


def detect_youtube(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    youtube_patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/live/([a-zA-Z0-9_-]{11})',
    ]
    for pattern in youtube_patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(1)
            return 'youtube', f"https://www.youtube.com/watch?v={video_id}", video_id
    
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        video_id = url.strip()
        return 'youtube', f"https://www.youtube.com/watch?v={video_id}", video_id
    
    return None, None, None


def download_thumbnail_youtube(video_id: str) -> Optional[str]:
    thumb_path = os.path.join(DOWNLOAD_FOLDER, f"thumb_{video_id}.jpg")
    thumb_urls = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
    ]
    for thumb_url in thumb_urls:
        try:
            response = requests.get(thumb_url, timeout=10)
            if response.status_code == 200 and len(response.content) > 1000:
                with open(thumb_path, 'wb') as f:
                    f.write(response.content)
                return thumb_path
        except:
            continue
    return None


def get_ydl_opts_for_strategy(strategy: Dict, format_str: str, is_audio: bool, cookies_exists: bool) -> Dict:
    """Создает конфигурацию yt-dlp для конкретной стратегии"""
    
    # Базовые опции
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'socket_timeout': 30,
        'retries': 15,
        'fragment_retries': 15,
        'skip_unavailable_fragments': True,
        'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
        'format': format_str,
        'cookiefile': COOKIES_FILE if cookies_exists else None,
        'http_headers': {
            'User-Agent': strategy['user_agent'],
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Encoding': 'gzip, deflate, br',
            'Content-Type': 'application/x-www-form-urlencoded',
            'Origin': 'https://www.youtube.com',
            'Referer': 'https://www.youtube.com',
        },
        'external_downloader': None,
        'concurrent_fragment_downloads': 1,
        'throttledratelimit': 100000000,
        'extractor_retries': 10,
        'file_access_retries': 10,
    }
    
    # Настройки extractor_args
    if strategy['player_client'] is not None:
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_client': strategy['player_client'],
                'player_skip': strategy['player_skip'],
            }
        }
    else:
        ydl_opts['extractor_args'] = {
            'youtube': {
                'player_skip': strategy['player_skip'],
            }
        }
    
    # Настройки для аудио/видео
    if is_audio:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        ydl_opts['merge_output_format'] = None
    else:
        ydl_opts['merge_output_format'] = 'mp4'
        ydl_opts['postprocessor_args'] = ['-c', 'copy', '-movflags', '+faststart']
    
    ydl_opts['prefer_ffmpeg'] = True
    
    return ydl_opts


def download_video_with_fallback(url: str, quality: str, 
                                  progress_callback=None, 
                                  cancel_event: threading.Event = None,
                                  progress_update_func=None) -> Optional[dict]:
    """
    Скачивает YouTube видео с автоматическим перебором стратегий при ошибке
    """
    if cancel_event and cancel_event.is_set():
        logger.info("🛑 Загрузка отменена")
        return None
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    is_audio = quality_config['audio_only']
    format_str = quality_config['format']
    cookies_exists = os.path.exists(COOKIES_FILE)
    
    start_time = time.time()
    last_error = None
    
    # Перебираем все стратегии
    for idx, strategy in enumerate(STRATEGIES):
        if cancel_event and cancel_event.is_set():
            logger.info("🛑 Загрузка отменена")
            return None
        
        strategy_name = strategy['name']
        logger.info(f"🔄 Попытка {idx + 1}/{len(STRATEGIES)}: {strategy_name}")
        
        # Обновляем прогресс через callback, если передан
        if progress_update_func:
            progress_update_func(f"Пробую {strategy_name}...")
        
        # Получаем конфигурацию для этой стратегии
        ydl_opts = get_ydl_opts_for_strategy(
            strategy=strategy,
            format_str=format_str,
            is_audio=is_audio,
            cookies_exists=cookies_exists
        )
        
        try:
            def progress_hook(d):
                if d['status'] == 'downloading':
                    try:
                        percent_str = d.get('_percent_str', '0%').strip().replace('%', '')
                        percent = float(percent_str) if percent_str else 0
                        
                        if cancel_event and cancel_event.is_set():
                            raise Exception("DOWNLOAD_CANCELLED")
                        
                        if progress_callback:
                            progress_callback(
                                percent=percent,
                                speed=d.get('_speed_str', ''),
                                eta=d.get('_eta_str', ''),
                            )
                    except Exception as e:
                        if str(e) == "DOWNLOAD_CANCELLED":
                            raise
                        pass
            
            ydl_opts['progress_hooks'] = [progress_hook]
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                
                if not info:
                    logger.warning(f"⚠️ {strategy_name}: Не удалось получить информацию")
                    continue
                
                total_time = time.time() - start_time
                
                # Находим файл
                file_path = ydl.prepare_filename(info)
                if is_audio:
                    file_path = os.path.splitext(file_path)[0] + '.mp3'
                
                if not os.path.exists(file_path):
                    base = os.path.splitext(file_path)[0]
                    search_exts = ['.mp3', '.m4a', '.webm'] if is_audio else ['.mp4', '.webm', '.mkv', '.mov']
                    for ext in search_exts:
                        alt_path = base + ext
                        if os.path.exists(alt_path):
                            if is_audio and ext != '.mp3':
                                import shutil
                                shutil.move(alt_path, file_path)
                            else:
                                file_path = alt_path
                            break
                    else:
                        import glob
                        possible = glob.glob(f"{DOWNLOAD_FOLDER}/*{info.get('id', '')}*")
                        if possible:
                            file_path = possible[0]
                        else:
                            logger.error(f"Файл не найден для {strategy_name}!")
                            continue
                
                file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
                duration = info.get('duration', 0)
                if duration:
                    duration = int(duration)
                
                # Формируем full_info
                full_info = {
                    'title': info.get('title', 'Видео'),
                    'fulltitle': info.get('fulltitle', info.get('title', 'Видео')),
                    'channel': info.get('channel', '') or info.get('uploader', 'Неизвестный'),
                    'channel_id': info.get('channel_id', '') or info.get('uploader_id', ''),
                    'channel_url': info.get('channel_url', '') or info.get('uploader_url', ''),
                    'uploader': info.get('uploader', 'Неизвестный'),
                    'duration': duration,
                    'view_count': info.get('view_count', 0),
                    'like_count': info.get('like_count', 0),
                    'comment_count': info.get('comment_count', 0),
                    'description': info.get('description', ''),
                    'thumbnail': info.get('thumbnail', ''),
                    'upload_date': info.get('upload_date', ''),
                    'age_limit': info.get('age_limit', 0),
                    'tags': info.get('tags', []),
                    'categories': info.get('categories', []),
                    'url': url,
                    'width': 0,
                    'height': 0,
                    'format_id': info.get('format_id', '?'),
                }
                
                # Проверка минимальной длительности
                if not is_audio and duration < MIN_DURATION_SECONDS:
                    logger.info(f"⏱ Видео слишком короткое ({duration}с < {MIN_DURATION_SECONDS}с). Пропускаем.")
                    try:
                        if os.path.exists(file_path):
                            os.remove(file_path)
                    except:
                        pass
                    
                    return {
                        'title': full_info['title'],
                        'fulltitle': full_info['fulltitle'],
                        'uploader': full_info['uploader'],
                        'channel': full_info['channel'],
                        'duration': duration,
                        'file_path': None,
                        'file_size_mb': 0,
                        'url': url,
                        'platform': 'youtube',
                        'quality': quality_config['description'],
                        'quality_code': quality,
                        'is_audio': is_audio,
                        'width': 0,
                        'height': 0,
                        'thumb_path': None,
                        'view_count': full_info['view_count'],
                        'like_count': full_info['like_count'],
                        'full_info': full_info,
                        'too_short': True,
                        'used_strategy': strategy_name,
                    }
                
                # Получаем размеры видео
                if not is_audio:
                    width = info.get('width') or quality_config['resolution'][0] or 640
                    height = info.get('height') or quality_config['resolution'][1] or 360
                else:
                    width, height = 0, 0
                
                full_info['width'] = int(width)
                full_info['height'] = int(height)
                
                # Скачиваем превью
                thumb_path = None
                if not is_audio:
                    thumb_path = download_thumbnail_youtube(info.get('id', ''))
                
                logger.info(f"✅ YouTube скачан [{strategy_name}]: {os.path.basename(file_path)} | {file_size_mb:.1f} MB | {total_time:.1f}с")
                
                return {
                    'title': full_info['title'],
                    'fulltitle': full_info['fulltitle'],
                    'uploader': full_info['uploader'],
                    'channel': full_info['channel'],
                    'duration': duration,
                    'file_path': file_path,
                    'file_size_mb': file_size_mb,
                    'url': url,
                    'platform': 'youtube',
                    'quality': quality_config['description'],
                    'quality_code': quality,
                    'is_audio': is_audio,
                    'width': int(width),
                    'height': int(height),
                    'thumb_path': thumb_path,
                    'view_count': full_info['view_count'],
                    'like_count': full_info['like_count'],
                    'full_info': full_info,
                    'too_short': False,
                    'used_strategy': strategy_name,
                }
                
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"⚠️ {strategy_name} не сработал: {error_msg[:100]}")
            last_error = error_msg
            
            if "DOWNLOAD_CANCELLED" in error_msg or (cancel_event and cancel_event.is_set()):
                logger.info("🛑 Загрузка отменена")
                return None
            
            # Если это не последняя стратегия, продолжаем
            if idx < len(STRATEGIES) - 1:
                logger.info(f"🔄 Пробую следующую стратегию...")
                continue
            else:
                # Последняя стратегия тоже не сработала
                logger.error(f"❌ Все {len(STRATEGIES)} стратегий не сработали")
                raise Exception(last_error)
    
    return None


# Сохраняем старую функцию для обратной совместимости
def download_video(url: str, quality: str, 
                   progress_callback=None, 
                   cancel_event: threading.Event = None) -> Optional[dict]:
    """Обертка для download_video_with_fallback с обратной совместимостью"""
    return download_video_with_fallback(url, quality, progress_callback, cancel_event)


def get_video_info(url: str) -> Optional[dict]:
    """Получает информацию о YouTube видео БЕЗ скачивания с перебором стратегий"""
    logger.info(f"🔍 Получаю информацию: YouTube")
    
    cookies_exists = os.path.exists(COOKIES_FILE)
    last_error = None
    
    for idx, strategy in enumerate(STRATEGIES):
        strategy_name = strategy['name']
        logger.info(f"🔄 Попытка {idx + 1}/{len(STRATEGIES)}: {strategy_name}")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'skip_download': True,
            'cookiefile': COOKIES_FILE if cookies_exists else None,
            'http_headers': {
                'User-Agent': strategy['user_agent'],
                'Accept-Language': 'en-US,en;q=0.9',
            }
        }
        
        if strategy['player_client'] is not None:
            ydl_opts['extractor_args'] = {
                'youtube': {
                    'player_client': strategy['player_client'],
                    'player_skip': strategy['player_skip'],
                }
            }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                if not info:
                    continue
                
                duration = info.get('duration', 0)
                if duration:
                    duration = int(duration)
                
                logger.info(f"✅ Информация получена через {strategy_name}")
                
                return {
                    'title': info.get('title', 'Видео')[:80],
                    'fulltitle': info.get('fulltitle', info.get('title', 'Видео')),
                    'uploader': info.get('uploader', 'Неизвестный'),
                    'channel': info.get('channel', '') or info.get('uploader', ''),
                    'channel_id': info.get('channel_id', '') or info.get('uploader_id', ''),
                    'channel_url': info.get('channel_url', '') or info.get('uploader_url', ''),
                    'duration': duration,
                    'view_count': info.get('view_count', 0),
                    'like_count': info.get('like_count', 0),
                    'comment_count': info.get('comment_count', 0),
                    'description': info.get('description', ''),
                    'thumbnail': info.get('thumbnail', ''),
                    'upload_date': info.get('upload_date', ''),
                    'age_limit': info.get('age_limit', 0),
                    'tags': info.get('tags', []),
                    'categories': info.get('categories', []),
                    'url': url,
                }
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"⚠️ {strategy_name} не сработал: {error_msg[:100]}")
            last_error = error_msg
            continue
    
    logger.error(f"❌ Не удалось получить информацию ни одной стратегией")
    return None


if __name__ == '__main__':
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    platform, clean_url, video_id = detect_youtube(test_url)
    print(f"URL: {test_url}")
    print(f"Platform: {platform}")
    print(f"Video ID: {video_id}")
    print(f"aria2: {ARIA2_AVAILABLE}")
    print(f"Min duration: {MIN_DURATION_SECONDS}с")
    print(f"\nСтратегий для перебора: {len(STRATEGIES)}")
    for s in STRATEGIES:
        print(f"  - {s['name']}")
