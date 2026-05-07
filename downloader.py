# downloader.py - Модуль для скачивания видео с YouTube и TikTok
import os
import re
import time
import threading
from typing import Optional, Tuple

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

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
DOWNLOAD_FOLDER = 'downloads'
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')

# Минимальная длительность видео (только для YouTube)
MIN_DURATION_SECONDS = 78  # 1.3 минуты

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# ============================================================
# КАЧЕСТВО И ФОРМАТЫ
# ============================================================

QUALITY_OPTIONS = {
    '360': {
        'format_youtube': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]/18',
        'format_tiktok': 'bestvideo+bestaudio/best[ext=mp4]/best',
        'label': '📺 360p',
        'quality_label': '360p',
        'resolution': (640, 360),
        'description': '360p',
        'audio_only': False,
    },
    '480': {
        'format_youtube': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]/18',
        'format_tiktok': 'bestvideo+bestaudio/best[ext=mp4]/best',
        'label': '📺 480p',
        'quality_label': '480p',
        'resolution': (854, 480),
        'description': '480p',
        'audio_only': False,
    },
    '720': {
        'format_youtube': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/136+140/18',
        'format_tiktok': 'bestvideo+bestaudio/best[ext=mp4]/best',
        'label': '📺 720p HD',
        'quality_label': '720p HD',
        'resolution': (1280, 720),
        'description': '720p HD',
        'audio_only': False,
    },
    '1080': {
        'format_youtube': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/137+140/18',
        'format_tiktok': 'bestvideo+bestaudio/best[ext=mp4]/best',
        'label': '📺 1080p Full HD',
        'quality_label': '1080p Full HD',
        'resolution': (1920, 1080),
        'description': '1080p Full HD',
        'audio_only': False,
    },
    'mp3': {
        'format_youtube': 'bestaudio[ext=m4a]/140',
        'format_tiktok': 'bestaudio/best',
        'label': '🎵 MP3',
        'quality_label': 'MP3',
        'resolution': None,
        'description': 'Аудио 192 kbps',
        'audio_only': True,
    },
}


def check_aria2() -> bool:
    """Проверяет, установлен ли aria2c"""
    import subprocess
    try:
        subprocess.run(['aria2c', '--version'], capture_output=True, timeout=2)
        return True
    except:
        return False


ARIA2_AVAILABLE = check_aria2()


def detect_platform(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Определяет платформу по URL.
    Возвращает: (platform, clean_url, video_id)
    """
    # YouTube
    youtube_patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in youtube_patterns:
        match = re.match(pattern, url)
        if match:
            video_id = match.group(1)
            return 'youtube', f"https://www.youtube.com/watch?v={video_id}", video_id
    
    # YouTube ID (только 11 символов)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        video_id = url.strip()
        return 'youtube', f"https://www.youtube.com/watch?v={video_id}", video_id
    
    # TikTok - полная ссылка: tiktok.com/@user/video/123456789
    match = re.search(r'/video/(\d+)', url)
    if match:
        return 'tiktok', url, match.group(1)
    
    # TikTok - короткие ссылки: vm.tiktok.com/XXXX или vt.tiktok.com/XXXX
    if 'tiktok.com/' in url:
        parts = url.rstrip('/').split('/')
        temp_id = parts[-1].split('?')[0] if parts else 'tiktok_unknown'
        return 'tiktok', url, temp_id
    
    return None, None, None


def download_thumbnail_youtube(video_id: str) -> Optional[str]:
    """Скачивает превью для YouTube"""
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


def download_thumbnail_tiktok(thumbnail_url: str, video_id: str) -> Optional[str]:
    """Скачивает превью для TikTok"""
    thumb_path = os.path.join(DOWNLOAD_FOLDER, f"thumb_{video_id}.jpg")
    try:
        response = requests.get(thumbnail_url, timeout=10)
        if response.status_code == 200 and len(response.content) > 1000:
            with open(thumb_path, 'wb') as f:
                f.write(response.content)
            return thumb_path
    except:
        pass
    return None


def get_video_info(url: str, platform: str) -> Optional[dict]:
    """Получает информацию о видео БЕЗ скачивания."""
    logger.info(f"🔍 Получаю информацию: {platform.upper()}")
    
    if platform == 'youtube':
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'skip_download': True,
            'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
            'extractor_args': {'youtube': {'player_client': 'android', 'player_skip': ['web', 'web_safari']}},
            'remote_components': ['ejs:github'],
            'youtube_include_hls_manifest': False,
            'youtube_include_dash_manifest': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
            }
        }
    else:
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'skip_download': True,
            'extractor_args': {'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}},
            'http_headers': {'User-Agent': 'Mozilla/5.0'}
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


def download_video(url: str, platform: str, quality: str, 
                   progress_callback=None, cancel_event: threading.Event = None) -> Optional[dict]:
    """
    Скачивает видео с выбранным качеством.
    """
    if cancel_event and cancel_event.is_set():
        logger.info("🛑 Загрузка отменена")
        return None
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    logger.info(f"⬇️ Скачиваю: {quality_config['description']} | {platform.upper()}")
    
    if cancel_event and cancel_event.is_set():
        return None
    
    start_time = time.time()
    is_audio = quality_config['audio_only']
    format_str = quality_config['format_youtube'] if platform == 'youtube' else quality_config['format_tiktok']
    
    if platform == 'youtube':
        cookies_exists = os.path.exists(COOKIES_FILE)
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 5,
            'fragment_retries': 5,
            'skip_unavailable_fragments': True,
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
            'format': format_str,
            'cookiefile': COOKIES_FILE if cookies_exists else None,
            'extractor_args': {'youtube': {'player_client': 'android,web', 'player_skip': []}},
            'remote_components': ['ejs:github'],
            'youtube_include_hls_manifest': False,
            'youtube_include_dash_manifest': True,
            'format_sort': ['res:1080', 'ext:mp4:m4a'],
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
            }
        }
        
        if ARIA2_AVAILABLE:
            ydl_opts.update({
                'external_downloader': 'aria2c',
                'external_downloader_args': [
                    '-x', '16', '-s', '16', '-k', '1M',
                    '--max-connection-per-server=16', '--min-split-size=1M',
                    '--file-allocation=none', '--async-dns=true',
                    '--max-tries=5', '--retry-wait=1',
                ],
            })
        else:
            ydl_opts.update({
                'concurrent_fragment_downloads': 16,
                'buffersize': 2 * 1024 * 1024,
                'http_chunk_size': 20 * 1024 * 1024,
            })
        
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
            
    else:  # TikTok
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'format': format_str,
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(uploader)s_%(title).100s_%(id)s.%(ext)s',
            'extractor_args': {'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}},
            'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        }
        
        if ARIA2_AVAILABLE:
            ydl_opts.update({
                'external_downloader': 'aria2c',
                'external_downloader_args': ['-x', '8', '-s', '8', '-k', '1M', '--file-allocation=none'],
            })
        else:
            ydl_opts['concurrent_fragment_downloads'] = 8
        
        if is_audio:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
    
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
                return None
            
            total_time = time.time() - start_time
            
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
                        logger.error("Файл не найден!")
                        return None
            
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
            
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
            
            # Проверка минимальной длительности ТОЛЬКО для YouTube
            if not is_audio and platform == 'youtube' and duration < MIN_DURATION_SECONDS:
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
                    'platform': platform,
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
                }
            
            if not is_audio:
                width = info.get('width') or quality_config['resolution'][0] or 640
                height = info.get('height') or quality_config['resolution'][1] or 360
                if platform == 'tiktok' and not info.get('width'):
                    width = 576
                    height = 1024
            else:
                width, height = 0, 0
            
            full_info['width'] = int(width)
            full_info['height'] = int(height)
            
            thumb_path = None
            if not is_audio:
                if platform == 'youtube':
                    thumb_path = download_thumbnail_youtube(info.get('id', ''))
                elif platform == 'tiktok':
                    thumb_path = download_thumbnail_tiktok(info.get('thumbnail', ''), info.get('id', ''))
            
            logger.info(f"✅ Скачано: {os.path.basename(file_path)} | {file_size_mb:.1f} MB | {total_time:.1f}с")
            
            return {
                'title': full_info['title'],
                'fulltitle': full_info['fulltitle'],
                'uploader': full_info['uploader'],
                'channel': full_info['channel'],
                'duration': duration,
                'file_path': file_path,
                'file_size_mb': file_size_mb,
                'url': url,
                'platform': platform,
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
            }
    
    except Exception as e:
        if str(e) == "DOWNLOAD_CANCELLED" or (cancel_event and cancel_event.is_set()):
            logger.info("🛑 Загрузка отменена")
            return None
        logger.error(f"Ошибка скачивания: {str(e)[:200]}")
        raise


if __name__ == '__main__':
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    platform, clean_url, video_id = detect_platform(test_url)
    print(f"URL: {test_url}")
    print(f"Platform: {platform}")
    print(f"Video ID: {video_id}")
    print(f"aria2: {ARIA2_AVAILABLE}")
    print(f"Min duration (YouTube only): {MIN_DURATION_SECONDS}с")
