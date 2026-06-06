# downloader.py - ФИНАЛЬНАЯ ВЕРСИЯ с fix для event loop
import os
import re
import time
import random
import threading
import asyncio
from typing import Optional, Tuple

import yt_dlp
import requests

from logger_config import create_logger

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

# Всегда используем формат 18 (360p) - работает везде
QUALITY_OPTIONS = {
    '360': {
        'format': '18',
        'label': '📺 360p',
        'quality_label': '360p',
        'resolution': (640, 360),
        'description': '360p',
        'audio_only': False,
    },
    '480': {
        'format': '18',
        'label': '📺 480p',
        'quality_label': '480p',
        'resolution': (854, 480),
        'description': '480p',
        'audio_only': False,
    },
    '720': {
        'format': '18',
        'label': '📺 720p HD',
        'quality_label': '720p HD',
        'resolution': (1280, 720),
        'description': '720p HD',
        'audio_only': False,
    },
    '1080': {
        'format': '18',
        'label': '📺 1080p Full HD',
        'quality_label': '1080p Full HD',
        'resolution': (1920, 1080),
        'description': '1080p Full HD',
        'audio_only': False,
    },
    'mp3': {
        'format': '18',
        'label': '🎵 MP3',
        'quality_label': 'MP3',
        'resolution': None,
        'description': 'Аудио 192 kbps',
        'audio_only': True,
    },
}


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


def get_video_info(url: str) -> Optional[dict]:
    """Получает информацию о YouTube видео БЕЗ скачивания."""
    logger.info(f"🔍 Получаю информацию: YouTube")
    
    time.sleep(random.uniform(1, 2))
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'skip_download': True,
        'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
            
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
            
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
        logger.error(f"Ошибка получения информации: {str(e)[:200]}")
        return None


def download_video_sync(url: str, quality: str, 
                        progress_callback=None, 
                        cancel_event: threading.Event = None) -> Optional[dict]:
    """
    СИНХРОННАЯ версия скачивания (для запуска в отдельном потоке)
    """
    if cancel_event and cancel_event.is_set():
        logger.info("🛑 Загрузка отменена")
        return None
    
    # Всегда используем 360p для скачивания
    requested_quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    is_audio = requested_quality_config['audio_only']
    
    # Фактически используем формат 18 (360p)
    actual_format = '18'
    
    logger.info(f"⬇️ Скачиваю YouTube: {requested_quality_config['description']}")
    
    if cancel_event and cancel_event.is_set():
        return None
    
    start_time = time.time()
    cookies_exists = os.path.exists(COOKIES_FILE)
    
    # Задержка перед скачиванием
    delay = random.uniform(1, 2)
    logger.info(f"⏳ Пауза {delay:.1f} сек...")
    time.sleep(delay)
    
    # Простые настройки
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 10,
        'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
        'format': actual_format,
        'cookiefile': COOKIES_FILE if cookies_exists else None,
    }
    
    if is_audio:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        ydl_opts['keepvideo'] = False
    
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
            elif d['status'] == 'finished' and is_audio:
                if progress_callback:
                    progress_callback(
                        percent=95,
                        speed='',
                        eta='Конвертация в MP3...',
                    )
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            if not info:
                return None
            
            total_time = time.time() - start_time
            
            if is_audio:
                base_path = ydl.prepare_filename(info)
                file_path = os.path.splitext(base_path)[0] + '.mp3'
            else:
                file_path = ydl.prepare_filename(info)
            
            if not os.path.exists(file_path):
                base = os.path.splitext(file_path)[0]
                search_exts = ['.mp3'] if is_audio else ['.mp4', '.webm', '.mkv']
                for ext in search_exts:
                    alt_path = base + ext
                    if os.path.exists(alt_path):
                        file_path = alt_path
                        break
                else:
                    video_id = info.get('id', '')
                    import glob
                    possible = glob.glob(f"{DOWNLOAD_FOLDER}/*{video_id}*")
                    if possible:
                        file_path = possible[0]
                    else:
                        logger.error("Файл не найден!")
                        return None
            
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
            
            display_quality = requested_quality_config['description']
            
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
                'width': 0 if is_audio else 640,
                'height': 0 if is_audio else 360,
                'format_id': info.get('format_id', actual_format),
            }
            
            if not is_audio and duration < MIN_DURATION_SECONDS:
                logger.info(f"⏱ Видео слишком короткое ({duration}с). Пропускаем.")
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
                    'quality': display_quality,
                    'quality_code': quality,
                    'is_audio': is_audio,
                    'width': 0,
                    'height': 0,
                    'thumb_path': None,
                    'view_count': full_info['view_count'],
                    'like_count': full_info['like_count'],
                    'full_info': full_info,
                    'too_short': True,
                }
            
            thumb_path = None
            if not is_audio:
                thumb_path = download_thumbnail_youtube(info.get('id', ''))
            
            logger.info(f"✅ {'MP3' if is_audio else 'YouTube'} скачан: {os.path.basename(file_path)} | {file_size_mb:.1f} MB | {total_time:.1f}с")
            
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
                'quality': display_quality,
                'quality_code': quality,
                'is_audio': is_audio,
                'width': full_info['width'],
                'height': full_info['height'],
                'thumb_path': thumb_path,
                'view_count': full_info['view_count'],
                'like_count': full_info['like_count'],
                'full_info': full_info,
                'too_short': False,
            }
    
    except Exception as e:
        if str(e) == "DOWNLOAD_CANCELLED" or (cancel_event and cancel_event.is_set()):
            logger.info("🛑 Загрузка отменена")
            return None
        logger.error(f"Ошибка скачивания YouTube: {str(e)[:200]}")
        raise


# Асинхронная обёртка (для совместимости с существующим event loop)
async def download_video(url: str, quality: str, 
                         progress_callback=None, 
                         cancel_event: threading.Event = None) -> Optional[dict]:
    """
    Асинхронная обёртка для вызова в боте
    """
    loop = asyncio.get_running_loop()
    
    def run_sync():
        return download_video_sync(url, quality, progress_callback, cancel_event)
    
    return await loop.run_in_executor(None, run_sync)


if __name__ == '__main__':
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    platform, clean_url, video_id = detect_youtube(test_url)
    print(f"URL: {test_url}")
    print(f"Platform: {platform}")
    print(f"Video ID: {video_id}")
    
    # Тест синхронной версии
    result = download_video_sync(test_url, '360')
    if result:
        print(f"✅ Тест пройден: {result['title'][:50]}")
