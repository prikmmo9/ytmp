# tiktok_handler.py - Модуль для работы с TikTok
import os
import re
import time
import threading
from typing import Optional, Tuple

import yt_dlp
import requests

from logger_config import create_logger

logger = create_logger(
    name='TikTokHandler',
    level=20,
    detailed=False,
    show_separators=False
).get_logger()

DOWNLOAD_FOLDER = 'downloads'
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# ============================================================
# ФОРМАТЫ ДЛЯ TIKTOK
# ============================================================

TIKTOK_QUALITY_OPTIONS = {
    '360': {
        'format': 'bestvideo+bestaudio/best[ext=mp4]/best',
        'label': '🎵 Видео (со звуком)',
        'quality_label': 'Видео',
        'resolution': (576, 1024),
        'description': 'Видео со звуком',
        'audio_only': False,
    },
    'mp3': {
        'format': 'bestaudio/best',
        'label': '🎵 MP3',
        'quality_label': 'MP3',
        'resolution': None,
        'description': 'Аудио 192 kbps',
        'audio_only': True,
    },
}


def detect_tiktok(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Определяет, является ли URL ссылкой на TikTok.
    Возвращает: (clean_url, video_id) или (None, None)
    """
    # Полная ссылка: tiktok.com/@user/video/123456789
    match = re.search(r'/video/(\d+)', url)
    if match:
        return url, match.group(1)
    
    # Короткие ссылки: vm.tiktok.com/XXXX или vt.tiktok.com/XXXX
    if 'tiktok.com/' in url:
        parts = url.rstrip('/').split('/')
        temp_id = parts[-1].split('?')[0] if parts else 'tiktok_unknown'
        return url, temp_id
    
    return None, None


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


def get_tiktok_video_info(url: str) -> Optional[dict]:
    """Получает информацию о TikTok видео БЕЗ скачивания."""
    logger.info(f"🔍 Получаю информацию: TikTok")
    
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
                'channel': info.get('uploader', ''),
                'channel_id': info.get('uploader_id', ''),
                'channel_url': info.get('uploader_url', ''),
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
        logger.error(f"Ошибка получения информации TikTok: {str(e)[:200]}")
        return None


def download_tiktok_video(url: str, quality: str, 
                          progress_callback=None, 
                          cancel_event: threading.Event = None) -> Optional[dict]:
    """
    Скачивает TikTok видео.
    
    Args:
        url: ссылка на видео
        quality: '360' (видео) или 'mp3' (аудио)
        progress_callback: функция для прогресса (percent, speed, eta)
        cancel_event: threading.Event для отмены
    
    Returns:
        dict с информацией о скачанном видео или None
    """
    if cancel_event and cancel_event.is_set():
        logger.info("🛑 Загрузка отменена")
        return None
    
    quality_config = TIKTOK_QUALITY_OPTIONS.get(quality, TIKTOK_QUALITY_OPTIONS['360'])
    logger.info(f"⬇️ Скачиваю TikTok: {quality_config['description']}")
    
    if cancel_event and cancel_event.is_set():
        return None
    
    start_time = time.time()
    is_audio = quality_config['audio_only']
    format_str = quality_config['format']
    
    # Проверка aria2c
    import subprocess
    aria2_available = False
    try:
        subprocess.run(['aria2c', '--version'], capture_output=True, timeout=2)
        aria2_available = True
    except:
        pass
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'format': format_str,
        'outtmpl': f'{DOWNLOAD_FOLDER}/%(uploader)s_%(title).100s_%(id)s.%(ext)s',
        'extractor_args': {'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}},
        'http_headers': {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'},
        'ignoreerrors': True,
    }
    
    if aria2_available:
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
        ydl_opts['prefer_ffmpeg'] = True
    
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
                search_exts = ['.mp3', '.m4a', '.webm', '.opus', '.aac'] if is_audio else ['.mp4', '.webm', '.mkv', '.mov']
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
                        if is_audio and not file_path.endswith('.mp3'):
                            import shutil
                            shutil.move(file_path, os.path.splitext(file_path)[0] + '.mp3')
                            file_path = os.path.splitext(file_path)[0] + '.mp3'
                    else:
                        logger.error("Файл не найден!")
                        return None
            
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
            
            if not is_audio:
                width = info.get('width') or quality_config['resolution'][0] or 576
                height = info.get('height') or quality_config['resolution'][1] or 1024
            else:
                width, height = 0, 0
            
            thumb_path = None
            if not is_audio:
                thumb_path = download_thumbnail_tiktok(info.get('thumbnail', ''), info.get('id', ''))
            
            full_info = {
                'title': info.get('title', 'Видео'),
                'fulltitle': info.get('fulltitle', info.get('title', 'Видео')),
                'channel': info.get('uploader', 'Неизвестный'),
                'channel_id': info.get('uploader_id', ''),
                'channel_url': info.get('uploader_url', ''),
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
                'width': int(width),
                'height': int(height),
                'format_id': info.get('format_id', '?'),
            }
            
            logger.info(f"✅ TikTok скачан: {os.path.basename(file_path)} | {file_size_mb:.1f} MB | {total_time:.1f}с")
            
            return {
                'title': full_info['title'],
                'fulltitle': full_info['fulltitle'],
                'uploader': full_info['uploader'],
                'channel': full_info['channel'],
                'duration': duration,
                'file_path': file_path,
                'file_size_mb': file_size_mb,
                'url': url,
                'platform': 'tiktok',
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
        logger.error(f"Ошибка скачивания TikTok: {str(e)[:200]}")
        raise
