# bot.py - YouTube/TikTok Download Bot с кэшированием через БД и чат-хранилище
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
from database import *

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

# Чат-хранилище для видео
STORAGE_CHAT = -1001776425232

# Настраиваем путь к БД
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_cache.db')

console_logger = create_logger(
    name='MediaBot',
    level=logging.DEBUG,
    detailed=True,
    show_separators=True
)
logger = console_logger.get_logger()

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Инициализируем БД при запуске
init_database()
logger.info("🗄 База данных инициализирована")

client = TelegramClient('bot_session', API_ID, API_HASH)

user_selections = {}
user_downloads = {}

# ID чата-хранилища (получим при запуске)
storage_chat_id = None

# ============================================================
# КАЧЕСТВО И ФОРМАТЫ
# ============================================================

QUALITY_OPTIONS = {
    '360': {
        'format_youtube': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]/18',
        'format_tiktok': 'best[height<=360]/best',
        'label': '📺 360p',
        'quality_label': '360p',
        'resolution': (640, 360),
        'description': '360p'
    },
    '480': {
        'format_youtube': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]/18',
        'format_tiktok': 'best[height<=480]/best',
        'label': '📺 480p',
        'quality_label': '480p',
        'resolution': (854, 480),
        'description': '480p'
    },
    '720': {
        'format_youtube': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/136+140/18',
        'format_tiktok': 'best[height<=720]/best',
        'label': '📺 720p HD',
        'quality_label': '720p HD',
        'resolution': (1280, 720),
        'description': '720p HD'
    },
    '1080': {
        'format_youtube': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]/137+140/18',
        'format_tiktok': 'best[height<=1080]/best',
        'label': '📺 1080p Full HD',
        'quality_label': '1080p Full HD',
        'resolution': (1920, 1080),
        'description': '1080p Full HD'
    },
    'mp3': {
        'format_youtube': 'bestaudio[ext=m4a]/140',
        'format_tiktok': 'bestaudio/best',
        'label': '🎵 MP3',
        'quality_label': 'MP3',
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


def download_video_sync(url: str, platform: str, quality: str, cancel_event: threading.Event) -> Optional[dict]:
    """Получает информацию И скачивает видео после выбора качества."""
    if cancel_event.is_set():
        return None
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    logger.info(f"⬇️ Скачиваю: {quality_config['description']}")
    
    if cancel_event.is_set():
        return None
    
    start_time = time.time()
    is_audio = (quality == 'mp3')
    format_str = quality_config['format_youtube'] if platform == 'youtube' else quality_config['format_tiktok']
    
    if platform == 'youtube':
        cookies_exists = os.path.exists(COOKIES_FILE)
        
        ydl_opts = {
            'quiet': True, 'no_warnings': True, 'socket_timeout': 30,
            'retries': 5, 'fragment_retries': 5, 'skip_unavailable_fragments': True,
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
                'concurrent_fragment_downloads': 16, 'buffersize': 2 * 1024 * 1024,
                'http_chunk_size': 20 * 1024 * 1024,
            })
        
        if is_audio:
            ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
            ydl_opts['merge_output_format'] = None
            ydl_opts['postprocessor_args'] = []
            ydl_opts['prefer_ffmpeg'] = True
        else:
            ydl_opts['merge_output_format'] = 'mp4'
            ydl_opts['postprocessor_args'] = ['-c', 'copy', '-movflags', '+faststart']
            ydl_opts['prefer_ffmpeg'] = True
            
    else:  # TikTok
        ydl_opts = {
            'quiet': True, 'no_warnings': True, 'socket_timeout': 30,
            'retries': 3, 'format': format_str,
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
            ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}]
    
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
                return None
            
            total_time = time.time() - start_time
            actual_format = info.get('format_id', '?')
            actual_height = info.get('height', 0)
            
            logger.info(f"⏱ Общее время: {total_time:.1f}с | 📊 {actual_format} (height={actual_height})")
            
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
                    possible = glob.glob(f"{DOWNLOAD_FOLDER}/*{info.get('id', '')}*")
                    if possible:
                        file_path = possible[0]
                        if is_audio:
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
                width = info.get('width') or quality_config['resolution'][0] or 640
                height = info.get('height') or quality_config['resolution'][1] or 360
            else:
                width, height = 0, 0
            
            thumb_path = None
            if not is_audio:
                if platform == 'youtube':
                    thumb_path = download_thumbnail_youtube(info.get('id', ''))
                elif platform == 'tiktok':
                    thumb_path = download_thumbnail_tiktok(info.get('thumbnail', ''), info.get('id', ''))
            
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
                'width': int(width),
                'height': int(height),
                'format_id': actual_format,
            }
            
            return {
                'title': full_info['title'],
                'fulltitle': full_info['fulltitle'],
                'uploader': full_info['uploader'],
                'channel': full_info['channel'],
                'duration': duration,
                'file_path': file_path,
                'file_size_mb': file_size_mb,
                'url': url, 'platform': platform,
                'quality': quality_config['description'],
                'quality_code': quality,
                'actual_height': actual_height,
                'is_audio': is_audio,
                'width': int(width), 'height': int(height),
                'thumb_path': thumb_path,
                'view_count': full_info['view_count'],
                'like_count': full_info['like_count'],
                'comment_count': full_info['comment_count'],
                'description': full_info['description'],
                'upload_date': full_info['upload_date'],
                'thumbnail_url': full_info['thumbnail'],
                'tags': full_info['tags'],
                'categories': full_info['categories'],
                'full_info': full_info,
            }
    
    except Exception as e:
        if str(e) == "DOWNLOAD_CANCELLED" or cancel_event.is_set():
            logger.info("🛑 Загрузка отменена")
            return None
        logger.error(f"Ошибка: {str(e)[:200]}")
        raise


def format_caption(video_info: dict, platform: str, from_cache: bool = False) -> str:
    """Форматирует подпись к видео"""
    duration = video_info.get('duration', 0)
    if duration > 0:
        minutes, secs = divmod(int(duration), 60)
        duration_str = f"{minutes}:{secs:02d}"
    else:
        duration_str = "Неизвестно"
    
    is_audio = video_info.get('is_audio', False)
    quality_str = video_info.get('quality', '')
    file_size_mb = video_info.get('file_size_mb', 0)
    
    if is_audio:
        caption = (
            f"🎵 **{video_info.get('fulltitle', video_info['title'])}**\n\n"
            f"👤 **{'Канал' if platform == 'youtube' else 'Автор'}:** {video_info.get('channel') or video_info.get('uploader', 'N/A')}\n"
            f"⏱ **Длительность:** {duration_str}\n"
            f"💾 **Размер:** {file_size_mb:.1f} MB\n"
            f"📊 **Формат:** MP3 (192 kbps)\n"
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
            f"📊 **Качество:** {quality_str}\n"
        )
    
    if from_cache:
        caption += "⚡ **Отправлено из кэша**\n"
    
    caption += f"🔗 {video_info.get('url', '')}"
    
    if len(caption) > 1000:
        caption = caption[:997] + '...'
    
    return caption


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
    
    cookies_status = "✅ Cookies" if os.path.exists(COOKIES_FILE) else "⚠️ Без cookies"
    aria_status = "🚀 aria2c" if ARIA2_AVAILABLE else "⚡ Встроенный"
    stats = get_stats()
    
    welcome = (
        f"🎬 **Привет, {user_name}!**\n\n"
        "Я - Media Download Bot! 🤖\n\n"
        "⚡ **Как я работаю:**\n"
        "1️⃣ Отправляешь ссылку\n"
        "2️⃣ Мгновенно появляются кнопки\n"
        "3️⃣ Выбираешь качество — я качаю\n"
        "4️⃣ Если уже скачано — пересылаю из хранилища!\n\n"
        f"📺 **YouTube:** 360p | 480p | 720p | 1080p | MP3\n"
        f"🎵 **TikTok:** 360p | 480p | 720p | 1080p | MP3\n"
        f"• {aria_status}\n"
        f"• {cookies_status}\n"
        f"• 🗄 Хранилище: {STORAGE_CHAT}\n"
        f"• ⚡ Кэш: {stats['total_videos']} видео ({stats['total_files']} файлов)\n\n"
        "⚠️ Макс. 2GB | /stats | /database | /cancel"
    )
    
    await event.reply(welcome)


@client.on(events.NewMessage(pattern='/stats'))
async def stats_handler(event):
    stats = get_stats()
    
    text = (
        f"📊 **Статистика кэша**\n\n"
        f"👤 Каналов: **{stats['total_channels']}**\n"
        f"🎬 Видео: **{stats['total_videos']}**\n"
        f"📁 Файлов: **{stats['total_files']}**\n"
        f"📤 Всего пересылок: **{stats['total_downloads']}**\n"
        f"💾 Общий размер: **{stats['total_size_mb']:.1f} MB**\n"
        f"🗄 Хранилище: {STORAGE_CHAT}\n\n"
    )
    
    if stats['top_downloads']:
        text += "🏆 **Топ-5 по пересылкам:**\n"
        for i, (title, quality, count, url) in enumerate(stats['top_downloads'][:5], 1):
            short_title = title[:40] + '...' if len(title) > 40 else title
            text += f"{i}. {short_title} [{quality}] - {count} раз\n"
    
    await event.reply(text)


@client.on(events.NewMessage(pattern='/database'))
async def database_handler(event):
    user_id = event.sender_id
    
    if not os.path.exists(DB_PATH):
        await event.reply("❌ **База данных не найдена**")
        return
    
    db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    stats = get_stats()
    
    caption = (
        f"🗄 **База данных бота**\n\n"
        f"📊 **Статистика:**\n"
        f"• Каналов: {stats['total_channels']}\n"
        f"• Видео: {stats['total_videos']}\n"
        f"• Файлов: {stats['total_files']}\n"
        f"• Пересылок: {stats['total_downloads']}\n"
        f"💾 **Размер:** {db_size_mb:.2f} MB\n"
        f"📅 **Обновлена:** {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )
    
    await client.send_file(
        entity=event.chat_id,
        file=DB_PATH,
        caption=caption,
        filename=f"video_cache_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
        part_size_kb=512,
    )


@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    user_id = event.sender_id
    if user_id in user_downloads:
        user_downloads[user_id].set()
        await event.reply("🛑 **Загрузка отменена**")
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
        await event.answer("❌ Сессия истекла", alert=True)
        return
    
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        await event.answer("⚠️ У вас уже есть активная загрузка!", alert=True)
        return
    
    selection = user_selections[user_id]
    url = selection['url']
    platform = selection['platform']
    video_id = selection['video_id']
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    logger.info(f"🌐 {platform.upper()} | 📊 {quality_config['description']} | ID: {video_id}")
    
    # ============================================================
    # ПРОВЕРЯЕМ КЭШ - ИЩЕМ СООБЩЕНИЕ В ЧАТЕ-ХРАНИЛИЩЕ
    # ============================================================
    cached = get_cached_file(video_id, quality)
    
    if cached and cached.get('storage_message_id') and cached.get('storage_chat_id'):
        # Пересылаем из чата-хранилища
        try:
            logger.info(f"⚡ Пересылаю из хранилища: msg_id={cached['storage_message_id']}")
            
            await event.edit(
                f"🎯 **Найдено в кэше!**\n📤 Пересылаю из хранилища...",
                buttons=None
            )
            
            await client.forward_messages(
                entity=event.chat_id,
                messages=cached['storage_message_id'],
                from_peer=cached['storage_chat_id'],
            )
            
            update_downloads_count(video_id, quality)
            await event.delete()
            
            logger.info(f"⚡ Переслано из хранилища: {cached['title'][:50]}... | {cached['quality_label']}")
            return
        except Exception as e:
            logger.warning(f"⚠️ Ошибка пересылки: {e}. Качаем заново...")
    
    # ============================================================
    # НЕТ В КЭШЕ ИЛИ ОШИБКА - КАЧАЕМ ЗАНОВО
    # ============================================================
    
    await event.edit(
        f"🔄 **Загружаю...**\n"
        f"🌐 {platform.upper()}\n"
        f"📊 {quality_config['label']}\n"
        f"⏳ Скачиваю...",
        buttons=None
    )
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    try:
        loop = asyncio.get_event_loop()
        
        download_task = loop.run_in_executor(
            None, download_video_sync, url, platform, quality, cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            await event.edit("⏰ **Таймаут загрузки**")
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
            await event.edit(f"❌ **Слишком большой файл:** {file_size_mb:.1f} MB")
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except:
                pass
            return
        
        await event.edit(f"✅ **Скачано!** ({file_size_mb:.1f} MB)\n📤 **Сохраняю в хранилище и отправляю...**")
        
        caption = format_caption(video_info, platform)
        
        # ============================================================
        # СОХРАНЯЕМ В ЧАТ-ХРАНИЛИЩЕ
        # ============================================================
        storage_message = None
        
        if storage_chat_id:
            try:
                if is_audio:
                    storage_message = await client.send_file(
                        entity=storage_chat_id,
                        file=file_path,
                        caption=f"[{quality_config['quality_label']}] {video_info['fulltitle'][:100]}",
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
                    storage_message = await client.send_file(
                        entity=storage_chat_id,
                        file=file_path,
                        caption=f"[{quality_config['quality_label']}] {video_info['fulltitle'][:100]}\n{video_info['url']}",
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
                
                logger.info(f"💾 Сохранено в хранилище: msg_id={storage_message.id}")
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения в хранилище: {e}")
        
        # ============================================================
        # ОТПРАВЛЯЕМ ПОЛЬЗОВАТЕЛЮ (пересылаем из хранилища или отправляем файл)
        # ============================================================
        if storage_message:
            # Пересылаем из хранилища пользователю
            await client.forward_messages(
                entity=event.chat_id,
                messages=storage_message.id,
                from_peer=storage_chat_id,
            )
            # Отправляем подпись отдельно
            await client.send_message(event.chat_id, caption)
        else:
            # Если хранилище недоступно - отправляем напрямую
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
        
        # ============================================================
        # СОХРАНЯЕМ В БД
        # ============================================================
        if storage_message:
            save_complete_info_with_storage(
                video_id=video_id,
                platform=platform,
                quality=quality,
                info=video_info.get('full_info', video_info),
                storage_chat_id=storage_chat_id,
                storage_message_id=storage_message.id,
                file_size_mb=file_size_mb,
            )
            logger.info(f"✅ Сохранено в БД: {video_info['title'][:50]}... [{quality}] | storage_msg={storage_message.id}")
        
        await event.delete()
        
        # Удаляем временные файлы
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
        except:
            pass
    
    except Exception as e:
        if not cancel_event.is_set():
            logger.error(f"Ошибка: {str(e)[:200]}")
            await event.edit(f"❌ **Ошибка:** {str(e)[:200]}")
    
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
    
    if text == '123455':
        await database_handler(event)
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
    
    logger.info(f"{platform_emoji} {platform_name}: {clean_url}")
    
    user_selections[user_id] = {
        'url': clean_url,
        'platform': platform,
        'video_id': video_id,
    }
    
    cached_qualities = get_available_qualities(video_id) if video_id else []
    
    quality_text = (
        f"{platform_emoji} **{platform_name}**\n\n"
        f"🔗 {clean_url}\n\n"
    )
    
    if cached_qualities:
        quality_text += "⚡ **В кэше:**\n"
        for q in cached_qualities:
            quality_text += f"• {q['label']}: {q['size_mb']:.1f} MB\n"
        quality_text += "\n"
    
    quality_text += "🎯 **Выберите качество:**"
    
    buttons = [
        [
            Button.inline("📺 360p", data="quality:360"),
            Button.inline("📺 480p", data="quality:480"),
        ],
        [
            Button.inline("📺 720p HD", data="quality:720"),
            Button.inline("📺 1080p Full HD", data="quality:1080"),
        ],
        [
            Button.inline("🎵 MP3 (аудио)", data="quality:mp3"),
        ],
    ]
    
    await event.reply(quality_text, buttons=buttons)


# ============================================================
# ЗАПУСК БОТА
# ============================================================

async def main():
    global storage_chat_id
    
    console_logger.separator("ЗАПУСК БОТА", char="=")
    
    stats = get_stats()
    
    logger.info(f"📺 YouTube: кнопки → точное качество → хранилище")
    logger.info(f"🎵 TikTok: кнопки → точное качество → хранилище")
    logger.info(f"📊 360p | 480p | 720p | 1080p | MP3")
    logger.info(f"💾 БД: {stats['total_videos']} видео | {stats['total_files']} файлов")
    logger.info(f"🍪 Cookies: {'✅' if os.path.exists(COOKIES_FILE) else '❌'}")
    logger.info(f"🚀 aria2c: {'✅ 16 потоков' if ARIA2_AVAILABLE else '⚡ Встроенный'}")
    
    # Запускаем клиент ДО получения entity
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    
    # Получаем ID чата-хранилища ПОСЛЕ запуска
    try:
        entity = await client.get_entity(STORAGE_CHAT)
        storage_chat_id = entity.id
        logger.info(f"🗄 Чат-хранилище: {STORAGE_CHAT} (id={storage_chat_id}) ✅")
    except Exception as e:
        logger.error(f"❌ Не удалось найти чат-хранилище {STORAGE_CHAT}: {e}")
        logger.warning("⚠️ Бот будет работать без хранилища (пересылка из кэша недоступна)")
    
    logger.info(f"✅ Бот запущен: @{me.username}")
    
    print()
    print("=" * 60)
    print(f"  🤖 БОТ: @{me.username}")
    print(f"  📺 YouTube + 🎵 TikTok")
    print(f"     360p | 480p | 720p | 1080p | MP3")
    print(f"  🗄 Хранилище: {STORAGE_CHAT}" + (" ✅" if storage_chat_id else " ❌"))
    print(f"  💾 БД: {stats['total_videos']} видео")
    print(f"  /stats | /database | 123455 | /cancel")
    print("=" * 60)
    print()
    
    await client.run_until_disconnected()
