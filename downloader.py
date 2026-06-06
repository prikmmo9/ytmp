# downloader.py - ВЕРСИЯ С ПОДДЕРЖКОЙ 720p
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

# ФОРМАТЫ ДЛЯ РАЗНЫХ КАЧЕСТВ
# 18 = 360p mp4 (работает всегда)
# 22 = 720p mp4 (работает для большинства видео)
# 140 = m4a аудио
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
        'format': '18',  # 480p нет в простых форматах, используем 360p
        'label': '📺 480p',
        'quality_label': '480p',
        'resolution': (854, 480),
        'description': '480p',
        'audio_only': False,
    },
    '720': {
        'format': '22',  # 720p mp4
        'label': '📺 720p HD',
        'quality_label': '720p HD',
        'resolution': (1280, 720),
        'description': '720p HD',
        'audio_only': False,
    },
    '1080': {
        'format': '22',  # 1080p требует токен, используем 720p
        'label': '📺 1080p Full HD',
        'quality_label': '1080p Full HD',
        'resolution': (1920, 1080),
        'description': '1080p Full HD',
        'audio_only': False,
    },
    'mp3': {
        'format': '18',  # Скачиваем 360p и извлекаем аудио
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


async def download_video(url: str, quality: str, 
                         progress_callback=None, 
                         cancel_event: threading.Event = None) -> Optional[dict]:
    """
    Асинхронная версия для вызова из бота.
    Поддерживает 720p (формат 22) и 360p (формат 18).
    """
    if cancel_event and cancel_event.is_set():
        logger.info("🛑 Загрузка отменена")
        return None
    
    requested_quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    is_audio = requested_quality_config['audio_only']
    
    # Выбираем формат в зависимости от качества
    if is_audio:
        actual_format = '18'  # Для MP3 скачиваем 360p
    else:
        actual_format = requested_quality_config['format']  # 18 или 22
    
    quality_name = requested_quality_config['description']
    logger.info(f"⬇️ Скачиваю YouTube: {quality_name} (формат {actual_format})")
    
    if cancel_event and cancel_event.is_set():
        return None
    
    start_time = time.time()
    
    # Создаём очередь для прогресса
    progress_queue = asyncio.Queue()
    
    # Функция для синхронного скачивания
    def sync_download():
        nonlocal start_time
        cookies_exists = os.path.exists(COOKIES_FILE)
        
        # Задержка перед скачиванием
        delay = random.uniform(1, 2)
        logger.info(f"⏳ Пауза {delay:.1f} сек...")
        time.sleep(delay)
        
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
        
        last_percent = 0
        
        def progress_hook(d):
            nonlocal last_percent
            if d['status'] == 'downloading':
                try:
                    percent_str = d.get('_percent_str', '0%').strip().replace('%', '')
                    percent = float(percent_str) if percent_str else 0
                    
                    if cancel_event and cancel_event.is_set():
                        raise Exception("DOWNLOAD_CANCELLED")
                    
                    if int(percent) > last_percent and progress_callback:
                        last_percent = int(percent)
                        # Отправляем прогресс через очередь
                        asyncio.run_coroutine_threadsafe(
                            progress_queue.put((percent, d.get('_speed_str', ''), d.get('_eta_str', ''))),
                            loop
                        )
                except Exception as e:
                    if str(e) == "DOWNLOAD_CANCELLED":
                        raise
                    pass
            elif d['status'] == 'finished' and is_audio:
                if progress_callback:
                    asyncio.run_coroutine_threadsafe(
                        progress_queue.put((95, '', 'Конвертация в MP3...')),
                        loop
                    )
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        try:
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
                
                # Определяем реальное разрешение видео
                width, height = requested_quality_config['resolution'] if requested_quality_config['resolution'] else (640, 360)
                if actual_format == '22':
                    width, height = 1280, 720
                
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
                    'width': 0 if is_audio else width,
                    'height': 0 if is_audio else height,
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
            error_msg = str(e)
            # Если 720p не доступен, пробуем 360p
            if actual_format == '22' and 'Requested format is not available' in error_msg:
                logger.warning("⚠️ 720p не доступен, пробую 360p...")
                # Рекурсивно пробуем 360p
                return asyncio.run_coroutine_threadsafe(
                    download_video(url, '360', progress_callback, cancel_event),
                    loop
                ).result()
            raise
    
    # Запускаем синхронную загрузку в потоке
    loop = asyncio.get_running_loop()
    
    # Задача для обработки прогресса
    async def handle_progress():
        while True:
            try:
                percent, speed, eta = await asyncio.wait_for(progress_queue.get(), timeout=0.5)
                if progress_callback:
                    # Вызываем callback в executor, чтобы не блокировать event loop
                    await asyncio.get_running_loop().run_in_executor(
                        None, progress_callback, percent, speed, eta
                    )
            except asyncio.TimeoutError:
                if cancel_event and cancel_event.is_set():
                    break
                continue
            except Exception as e:
                logger.error(f"Ошибка обработки прогресса: {e}")
                break
    
    # Запускаем обе задачи
    progress_task = asyncio.create_task(handle_progress())
    
    try:
        # Запускаем скачивание в потоке
        result = await asyncio.get_running_loop().run_in_executor(None, sync_download)
        progress_task.cancel()
        return result
    except Exception as e:
        progress_task.cancel()
        if "DOWNLOAD_CANCELLED" in str(e) or (cancel_event and cancel_event.is_set()):
            logger.info("🛑 Загрузка отменена")
            return None
        raise


if __name__ == '__main__':
    async def test():
        # Тест 360p
        print("Тест 360p...")
        result = await download_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", '360')
        if result:
            print(f"✅ 360p: {result['title'][:50]} ({result['file_size_mb']:.1f} MB)")
        
        # Тест 720p
        print("\nТест 720p...")
        result = await download_video("https://www.youtube.com/watch?v=dQw4w9WgXcQ", '720')
        if result:
            print(f"✅ 720p: {result['title'][:50]} ({result['file_size_mb']:.1f} MB)")
    
    asyncio.run(test())
