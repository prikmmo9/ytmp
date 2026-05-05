# bot.py - YouTube/TikTok Download Bot с выбором качества
import os
import asyncio
import re
import logging
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

import yt_dlp
from telethon import TelegramClient, events, Button
import telethon
import requests
from logger_config import create_logger

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
API_ID = int(os.getenv('API_ID', '22268845'))
API_HASH = os.getenv('API_HASH', 'ffbeffdfb86784e12b39aea5f53857d2')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8566350925:AAEOwpPgXhmR3SE_7TapSbzMJnqImnMA-Js')

DOWNLOAD_FOLDER = 'downloads'
MAX_FILE_SIZE_MB = 2000
DOWNLOAD_TIMEOUT = 600

COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')

console_logger = create_logger(
    name='MediaBot',
    level=logging.DEBUG,
    detailed=True,
    show_separators=True
)
logger = console_logger.get_logger()

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

client = TelegramClient('bot_session', API_ID, API_HASH)

user_selections = {}
user_downloads = {}

# ============================================================
# КАЧЕСТВО И ФОРМАТЫ
# ============================================================

QUALITY_OPTIONS = {
    '360': {
        'format_youtube': '134+140/18',
        'format_tiktok': 'best[height<=360]/best',
        'label': '📺 360p',
        'resolution': (640, 360),
        'description': '360p (низкое)'
    },
    '480': {
        'format_youtube': '135+140/18',
        'format_tiktok': 'best[height<=480]/best',
        'label': '📺 480p',
        'resolution': (854, 480),
        'description': '480p (среднее)'
    },
    '720': {
        'format_youtube': '136+140/18',
        'format_tiktok': 'best[height<=720]/best',
        'label': '📺 720p',
        'resolution': (1280, 720),
        'description': '720p (HD)'
    },
    '1080': {
        'format_youtube': '137+140/18',
        'format_tiktok': 'best[height<=1080]/best',
        'label': '📺 1080p',
        'resolution': (1920, 1080),
        'description': '1080p (Full HD)'
    },
    'mp3': {
        'format_youtube': '140',
        'format_tiktok': 'bestaudio/best',
        'label': '🎵 MP3',
        'resolution': None,
        'description': 'Аудио 192 kbps',
        'audio_only': True
    },
}


def check_aria2():
    import subprocess
    try:
        subprocess.run(['aria2c', '--version'], capture_output=True, timeout=2)
        return True
    except:
        return False


ARIA2_AVAILABLE = check_aria2()


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


def download_thumbnail_tiktok(thumbnail_url: str, video_id: str) -> Optional[str]:
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


def detect_platform(url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    youtube_patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    for pattern in youtube_patterns:
        match = re.match(pattern, url)
        if match:
            return 'youtube', f"https://www.youtube.com/watch?v={match.group(1)}", match.group(1)
    
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        video_id = url.strip()
        return 'youtube', f"https://www.youtube.com/watch?v={video_id}", video_id
    
    tiktok_patterns = [
        r'(?:https?://)?(?:www\.)?tiktok\.com/@[\w.-]+/video/(\d+)',
        r'(?:https?://)?(?:www\.)?tiktok\.com/t/(\w+)',
        r'(?:https?://)?vm\.tiktok\.com/(\w+)',
        r'(?:https?://)?vt\.tiktok\.com/(\w+)',
    ]
    for pattern in tiktok_patterns:
        match = re.match(pattern, url)
        if match:
            return 'tiktok', url, None
    
    return None, None, None


def get_video_info_sync(url: str, platform: str) -> Optional[dict]:
    """ТОЛЬКО получает информацию о видео, НЕ скачивает"""
    logger.info("🔍 Получаю информацию о видео (без скачивания)...")
    
    if platform == 'youtube':
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'skip_download': True,
            'cookiefile': COOKIES_FILE if os.path.exists(COOKIES_FILE) else None,
            'extractor_args': {
                'youtube': {
                    'player_client': 'android',
                    'player_skip': ['web', 'web_safari'],
                }
            },
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
            'extractor_args': {
                'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
        }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                return None
            
            # Проверяем доступные форматы
            formats = info.get('formats', [])
            available_qualities = set()
            
            if platform == 'youtube':
                for fmt in formats:
                    format_id = fmt.get('format_id', '')
                    if format_id in ['134', '18']:
                        available_qualities.add('360')
                    if format_id in ['135']:
                        available_qualities.add('480')
                    if format_id in ['136']:
                        available_qualities.add('720')
                    if format_id in ['137']:
                        available_qualities.add('1080')
                    if format_id in ['140']:
                        available_qualities.add('mp3')
                # Минимум
                available_qualities.update(['360', 'mp3'])
            else:
                # TikTok - проверяем по высоте
                heights = set()
                has_audio = False
                for fmt in formats:
                    height = fmt.get('height')
                    if height:
                        heights.add(height)
                    if fmt.get('acodec') != 'none':
                        has_audio = True
                
                if heights:
                    max_height = max(heights)
                    if max_height >= 360:
                        available_qualities.add('360')
                    if max_height >= 480:
                        available_qualities.add('480')
                    if max_height >= 720:
                        available_qualities.add('720')
                    if max_height >= 1080:
                        available_qualities.add('1080')
                
                if has_audio:
                    available_qualities.add('mp3')
                
                # Минимум для TikTok
                if not available_qualities:
                    available_qualities.update(['360', 'mp3'])
            
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
            
            logger.info(f"✅ Информация получена: {info.get('title', 'N/A')[:50]}")
            logger.info(f"📊 Доступные форматы: {sorted(available_qualities)}")
            
            return {
                'title': info.get('title', 'Видео')[:80],
                'fulltitle': info.get('fulltitle', info.get('title', 'Видео')),
                'uploader': info.get('uploader', 'Неизвестный автор'),
                'channel': info.get('channel', ''),
                'duration': duration,
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'thumbnail': info.get('thumbnail', ''),
                'video_id': info.get('id', ''),
                'available_qualities': sorted(list(available_qualities)),
            }
    
    except Exception as e:
        logger.error(f"Ошибка получения информации: {str(e)[:200]}")
        return None


def download_video_sync(url: str, platform: str, quality: str, cancel_event: threading.Event) -> Optional[dict]:
    """Скачивает видео ТОЛЬКО после выбора качества"""
    if cancel_event.is_set():
        logger.info("🛑 Загрузка отменена до начала")
        return None
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    logger.info(f"⬇️ Начинаю скачивание: {quality_config['label']}")
    console_logger.step(f"Скачивание: {quality_config['description']}...", current=2, total=2)
    
    if cancel_event.is_set():
        logger.info("🛑 Загрузка отменена")
        return None
    
    start_time = time.time()
    is_audio = (quality == 'mp3')
    
    if platform == 'youtube':
        format_str = quality_config['format_youtube']
    else:
        format_str = quality_config['format_tiktok']
    
    if platform == 'youtube':
        cookies_exists = os.path.exists(COOKIES_FILE)
        
        if ARIA2_AVAILABLE:
            logger.info("🚀 aria2c: 16 потоков")
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 30,
                'retries': 3,
                'fragment_retries': 3,
                'skip_unavailable_fragments': True,
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
                'external_downloader': 'aria2c',
                'external_downloader_args': [
                    '-x', '16', '-s', '16', '-k', '1M',
                    '--max-connection-per-server=16',
                    '--min-split-size=1M',
                    '--file-allocation=none',
                    '--async-dns=true',
                    '--max-tries=5',
                    '--retry-wait=1',
                ],
                'format': format_str,
                'cookiefile': COOKIES_FILE if cookies_exists else None,
                'extractor_args': {
                    'youtube': {
                        'player_client': 'android',
                        'player_skip': ['web', 'web_safari'],
                    }
                },
                'remote_components': ['ejs:github'],
                'youtube_include_hls_manifest': False,
                'youtube_include_dash_manifest': False,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
                }
            }
        else:
            logger.info("⚡ Встроенный загрузчик")
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 30,
                'retries': 3,
                'fragment_retries': 3,
                'skip_unavailable_fragments': True,
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
                'concurrent_fragment_downloads': 16,
                'buffersize': 2 * 1024 * 1024,
                'http_chunk_size': 20 * 1024 * 1024,
                'format': format_str,
                'cookiefile': COOKIES_FILE if cookies_exists else None,
                'extractor_args': {
                    'youtube': {
                        'player_client': 'android',
                        'player_skip': ['web', 'web_safari'],
                    }
                },
                'remote_components': ['ejs:github'],
                'youtube_include_hls_manifest': False,
                'youtube_include_dash_manifest': False,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept-Language': 'en-US,en;q=0.9,ru;q=0.8',
                }
            }
        
        if is_audio:
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
            ydl_opts['merge_output_format'] = None
            ydl_opts['postprocessor_args'] = []
            ydl_opts['prefer_ffmpeg'] = True
        else:
            ydl_opts['merge_output_format'] = 'mp4'
            ydl_opts['postprocessor_args'] = ['-c', 'copy', '-movflags', '+faststart']
            ydl_opts['prefer_ffmpeg'] = True
            
    else:  # TikTok
        if ARIA2_AVAILABLE:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 30,
                'retries': 3,
                'format': format_str,
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(uploader)s_%(title).100s_%(id)s.%(ext)s',
                'external_downloader': 'aria2c',
                'external_downloader_args': ['-x', '8', '-s', '8', '-k', '1M', '--file-allocation=none'],
                'extractor_args': {
                    'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
            }
        else:
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 30,
                'retries': 3,
                'format': format_str,
                'outtmpl': f'{DOWNLOAD_FOLDER}/%(uploader)s_%(title).100s_%(id)s.%(ext)s',
                'concurrent_fragment_downloads': 8,
                'extractor_args': {
                    'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}
                },
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                }
            }
        
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
                    speed = d.get('_speed_str', '')
                    eta = d.get('_eta_str', '')
                    
                    if cancel_event.is_set():
                        raise Exception("DOWNLOAD_CANCELLED")
                    
                    console_logger.download_progress(percent, speed=speed, eta=eta)
                except Exception as e:
                    if str(e) == "DOWNLOAD_CANCELLED":
                        raise
                    pass
            elif d['status'] == 'finished':
                logger.info(f"✅ Часть загружена: {os.path.basename(d.get('filename', 'unknown'))}")
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            if not info:
                logger.error("Не удалось получить информацию")
                return None
            
            total_time = time.time() - start_time
            logger.info(f"⏱ Общее время скачивания: {total_time:.1f}с")
            
            file_path = ydl.prepare_filename(info)
            
            if is_audio:
                file_path = os.path.splitext(file_path)[0] + '.mp3'
            
            if not os.path.exists(file_path):
                base = os.path.splitext(file_path)[0]
                search_exts = ['.mp3', '.m4a', '.webm'] if is_audio else ['.mp4', '.webm', '.mkv', '.mov', '.flv']
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
                    pattern = f"{DOWNLOAD_FOLDER}/*{info.get('id', '')}*"
                    possible = glob.glob(pattern)
                    if possible:
                        file_path = possible[0]
                        if is_audio:
                            import shutil
                            mp3_path = os.path.splitext(file_path)[0] + '.mp3'
                            shutil.move(file_path, mp3_path)
                            file_path = mp3_path
                    else:
                        logger.error(f"Файл не найден!")
                        return None
            
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            download_speed = file_size_mb / total_time if total_time > 0 else 0
            
            logger.info(f"📁 Файл: {os.path.basename(file_path)}")
            logger.info(f"💾 Размер: {file_size_mb:.1f} MB")
            logger.info(f"⚡ Скорость: {download_speed:.2f} MB/s")
            
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
            
            thumb_path = None
            if not is_audio:
                if platform == 'youtube':
                    thumb_path = download_thumbnail_youtube(info.get('id', ''))
                elif platform == 'tiktok':
                    thumb_path = download_thumbnail_tiktok(info.get('thumbnail', ''), info.get('id', ''))
            
            return {
                'title': info.get('title', 'Видео'),
                'fulltitle': info.get('fulltitle', info.get('title', 'Видео')),
                'uploader': info.get('uploader') or 'Неизвестный автор',
                'channel': info.get('channel', ''),
                'duration': duration,
                'file_path': file_path,
                'file_size_mb': file_size_mb,
                'url': url,
                'platform': platform,
                'quality': quality_config['label'],
                'is_audio': is_audio,
                'width': quality_config['resolution'][0] if quality_config['resolution'] else 0,
                'height': quality_config['resolution'][1] if quality_config['resolution'] else 0,
                'thumb_path': thumb_path,
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
            }
    
    except Exception as e:
        if str(e) == "DOWNLOAD_CANCELLED" or cancel_event.is_set():
            logger.info("🛑 Загрузка отменена")
            return None
        logger.error(f"Ошибка: {str(e)[:200]}")
        raise


# ============================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        user_name = sender.first_name or f"User {user_id}"
    except:
        user_name = f"User {user_id}"
    
    logger.info(f"📱 /start от {user_name}")
    
    cookies_status = "✅ Cookies загружены" if os.path.exists(COOKIES_FILE) else "⚠️ Без cookies"
    
    welcome = (
        f"🎬 **Привет, {user_name}!**\n\n"
        "Я - Media Download Bot! 🤖\n\n"
        "⚡ **Как я работаю:**\n"
        "1️⃣ Отправляешь ссылку\n"
        "2️⃣ Я показываю информацию о видео\n"
        "3️⃣ Ты выбираешь качество\n"
        "4️⃣ Я скачиваю и отправляю\n\n"
        f"📺 **YouTube:** 360p | 480p | 720p | 1080p | MP3\n"
        f"🎵 **TikTok:** 360p | 480p | 720p | 1080p | MP3\n"
        f"• {'🚀 aria2c 16 потоков' if ARIA2_AVAILABLE else '⚡ Оптимизированная загрузка'}\n"
        f"• {cookies_status}\n"
        f"• 🖼 С превью\n\n"
        "⚠️ Макс. размер: 2GB | Отмена: /cancel"
    )
    
    await event.reply(welcome)


@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    user_id = event.sender_id
    if user_id in user_downloads:
        user_downloads[user_id].set()
        await event.reply("🛑 **Загрузка отменена**")
        logger.info(f"🛑 Пользователь {user_id} отменил загрузку")
    else:
        await event.reply("ℹ️ Нет активных загрузок")


# ============================================================
# ОБРАБОТЧИК ВЫБОРА КАЧЕСТВА
# ============================================================

@client.on(events.CallbackQuery)
async def callback_handler(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    
    if not data.startswith('quality:'):
        return
    
    quality = data.split(':')[1]
    
    if user_id not in user_selections:
        await event.answer("❌ Сессия истекла. Отправьте ссылку заново", alert=True)
        return
    
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        await event.answer("⚠️ У вас уже есть активная загрузка!", alert=True)
        return
    
    selection = user_selections[user_id]
    url = selection['url']
    platform = selection['platform']
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    console_logger.separator(f"НАЧАЛО ЗАГРУЗКИ для {user_id}")
    logger.info(f"📥 Платформа: {platform.upper()}")
    logger.info(f"📥 Качество: {quality_config['label']}")
    logger.info(f"🔗 URL: {url}")
    
    await event.edit(
        f"🔄 **Начинаю загрузку...**\n"
        f"🌐 {platform.upper()}\n"
        f"📊 {quality_config['label']}\n"
        f"⏳ Пожалуйста, подождите...",
        buttons=None
    )
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    start_time = datetime.now()
    
    try:
        loop = asyncio.get_event_loop()
        
        download_task = loop.run_in_executor(
            None, download_video_sync, url, platform, quality, cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            await event.edit("⏰ **Таймаут загрузки**\nПопробуйте позже или другое качество")
            return
        
        if cancel_event.is_set() or video_info is None:
            await event.edit("🛑 **Загрузка отменена**")
            return
        
        file_path = video_info['file_path']
        file_size_mb = video_info['file_size_mb']
        is_audio = video_info['is_audio']
        thumb_path = video_info.get('thumb_path')
        duration = video_info.get('duration', 0)
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            await event.edit(f"❌ **Файл слишком большой:** {file_size_mb:.1f} MB\nМаксимум: {MAX_FILE_SIZE_MB} MB")
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except:
                pass
            return
        
        await event.edit(f"✅ **Скачано!** ({file_size_mb:.1f} MB)\n📤 **Отправляю...**")
        
        if duration > 0:
            minutes, secs = divmod(int(duration), 60)
            duration_str = f"{minutes}:{secs:02d}"
        else:
            duration_str = "Неизвестно"
        
        if is_audio:
            caption = (
                f"🎵 **{video_info.get('fulltitle', video_info['title'])}**\n\n"
                f"👤 **{'Канал' if platform == 'youtube' else 'Автор'}:** {video_info.get('channel') or video_info.get('uploader', 'N/A')}\n"
                f"⏱ **Длительность:** {duration_str}\n"
                f"💾 **Размер:** {file_size_mb:.1f} MB\n"
                f"📊 **Формат:** MP3 (192 kbps)\n"
                f"🔗 {video_info['url']}"
            )
        else:
            if platform == 'youtube':
                caption = (
                    f"📺 **{video_info.get('fulltitle', video_info['title'])}**\n\n"
                    f"👤 **Канал:** {video_info.get('channel') or video_info.get('uploader', 'N/A')}\n"
                    f"⏱ **Длительность:** {duration_str}\n"
                )
            else:
                caption = (
                    f"🎵 **{video_info.get('fulltitle', video_info['title'])}**\n\n"
                    f"👤 **Автор:** @{video_info.get('uploader', 'N/A')}\n"
                    f"⏱ **Длительность:** {duration_str}\n"
                )
            
            if video_info.get('view_count'):
                caption += f"👁 **Просмотров:** {video_info['view_count']:,}\n"
            if video_info.get('like_count'):
                caption += f"❤️ **Лайков:** {video_info['like_count']:,}\n"
            
            caption += (
                f"💾 **Размер:** {file_size_mb:.1f} MB\n"
                f"📊 **Качество:** {video_info['quality']}\n"
                f"🔗 {video_info['url']}"
            )
        
        if len(caption) > 1000:
            caption = caption[:997] + '...'
        
        if is_audio:
            await client.send_file(
                entity=event.chat_id,
                file=file_path,
                caption=caption,
                attributes=[
                    telethon.types.DocumentAttributeAudio(
                        duration=duration if duration > 0 else 0,
                        title=video_info.get('fulltitle', video_info['title']),
                        performer=video_info.get('uploader', 'Unknown'),
                    )
                ],
                part_size_kb=512,
            )
        else:
            await client.send_file(
                entity=event.chat_id,
                file=file_path,
                caption=caption,
                force_document=False,
                thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                attributes=[
                    telethon.types.DocumentAttributeVideo(
                        duration=duration if duration > 0 else 0,
                        w=video_info.get('width', 640),
                        h=video_info.get('height', 360),
                        supports_streaming=True,
                        round_message=False
                    )
                ],
                supports_streaming=True,
                part_size_kb=512,
                allow_cache=True,
            )
        
        total_time = (datetime.now() - start_time).total_seconds()
        await event.delete()
        
        logger.info(f"✅ УСПЕШНО: {video_info['title'][:50]}... | {file_size_mb:.1f}MB | {quality_config['label']} | {total_time:.1f}s")
        
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
        except Exception as e:
            logger.warning(f"Не удалось удалить файл: {e}")
    
    except Exception as e:
        if not cancel_event.is_set():
            logger.error(f"Ошибка: {str(e)[:200]}")
            await event.edit(f"❌ **Ошибка при загрузке:** {str(e)[:200]}")
    
    finally:
        if user_id in user_downloads:
            del user_downloads[user_id]
        if user_id in user_selections:
            del user_selections[user_id]


# ============================================================
# ОСНОВНОЙ ОБРАБОТЧИК ССЫЛОК
# ============================================================

@client.on(events.NewMessage)
async def message_handler(event):
    text = event.text.strip() if event.text else ""
    user_id = event.sender_id
    chat_id = event.chat_id
    
    if text.startswith('/'):
        return
    
    platform, clean_url, video_id = detect_platform(text)
    
    if not platform:
        return
    
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        await event.reply("⚠️ **У вас уже есть активная загрузка!**\nДождитесь завершения или /cancel")
        return
    
    if user_id in user_selections:
        del user_selections[user_id]
    
    platform_emoji = "📺" if platform == "youtube" else "🎵"
    platform_name = "YouTube" if platform == "youtube" else "TikTok"
    
    console_logger.separator(f"НОВЫЙ ЗАПРОС от {user_id}")
    logger.info(f"{platform_emoji} Платформа: {platform_name}")
    logger.info(f"🔗 URL: {clean_url}")
    
    # Показываем статус получения информации
    status_msg = await event.reply(
        f"🔍 **Получаю информацию о видео...**\n"
        f"🌐 {platform_name}\n"
        f"⏳ Пожалуйста, подождите..."
    )
    
    # Получаем информацию (БЕЗ скачивания)
    video_info = get_video_info_sync(clean_url, platform)
    
    if not video_info:
        await status_msg.edit(
            f"❌ **Не удалось получить информацию**\n"
            f"Проверьте ссылку или попробуйте позже"
        )
        return
    
    # Сохраняем для callback
    user_selections[user_id] = {
        'url': clean_url,
        'platform': platform,
        'info': video_info,
    }
    
    # Формируем информацию о видео
    duration = video_info.get('duration', 0)
    if duration > 0:
        minutes, secs = divmod(int(duration), 60)
        duration_str = f"{minutes}:{secs:02d}"
    else:
        duration_str = "Неизвестно"
    
    if platform == 'youtube':
        info_text = (
            f"📺 **{video_info.get('title', 'Видео')}**\n\n"
            f"👤 **Канал:** {video_info.get('uploader', 'N/A')}\n"
            f"⏱ **Длительность:** {duration_str}\n"
        )
    else:
        info_text = (
            f"🎵 **{video_info.get('title', 'Видео')}**\n\n"
            f"👤 **Автор:** @{video_info.get('uploader', 'N/A')}\n"
            f"⏱ **Длительность:** {duration_str}\n"
        )
    
    if video_info.get('view_count'):
        info_text += f"👁 **Просмотров:** {video_info['view_count']:,}\n"
    
    info_text += f"\n🎯 **Выберите качество:** 👇"
    
    # Создаем кнопки
    available = video_info.get('available_qualities', ['360', '480', '720', '1080', 'mp3'])
    buttons = []
    
    video_qualities = ['360', '480', '720', '1080']
    video_buttons = []
    for q in video_qualities:
        if q in available:
            q_config = QUALITY_OPTIONS[q]
            video_buttons.append(Button.inline(q_config['label'], data=f"quality:{q}"))
    
    for i in range(0, len(video_buttons), 2):
        row = video_buttons[i:i+2]
        buttons.append(row)
    
    if 'mp3' in available:
        buttons.append([Button.inline("🎵 MP3 (аудио 192 kbps)", data="quality:mp3")])
    
    await status_msg.edit(info_text, buttons=buttons)
    
    logger.info(f"📋 Показан выбор качества для: {video_info.get('title', 'N/A')[:50]}")


# ============================================================
# ЗАПУСК БОТА
# ============================================================

async def main():
    console_logger.separator("ЗАПУСК БОТА", char="=")
    
    logger.info(f"📺 YouTube: 1. Инфо → 2. Выбор качества → 3. Скачивание")
    logger.info(f"🎵 TikTok: 1. Инфо → 2. Выбор качества → 3. Скачивание")
    logger.info(f"📊 Качество: 360p | 480p | 720p | 1080p | MP3")
    logger.info(f"🍪 Cookies: {'загружены' if os.path.exists(COOKIES_FILE) else 'НЕТ'}")
    logger.info(f"🚀 aria2c: {'16 потоков' if ARIA2_AVAILABLE else 'встроенный загрузчик'}")
    logger.info(f"📤 Видео: стриминг + полный экран + превью")
    
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    
    logger.info(f"✅ Бот запущен: @{me.username}")
    
    print()
    print("=" * 60)
    print(f"  🤖 БОТ: @{me.username}")
    print(f"  📺 YouTube: 1. Инфо → 2. Выбор → 3. Скачивание")
    print(f"  🎵 TikTok: 1. Инфо → 2. Выбор → 3. Скачивание")
    print(f"     Качество: 360p | 480p | 720p | 1080p | MP3")
    print(f"  ⚡ Оптимизированная загрузка")
    print(f"  🍪 Cookies: {'✅ Да' if os.path.exists(COOKIES_FILE) else '❌ Нет'}")
    print(f"  🚫 /cancel для отмены")
    print("=" * 60)
    print()
    
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        import yt_dlp
        import telethon
        import requests
    except ImportError as e:
        print(f"❌ Установите: pip install yt-dlp telethon requests")
        exit(1)
    
    client.loop.run_until_complete(main())
