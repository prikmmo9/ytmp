# youtube_tiktok_bot_mvp.py
import os
import asyncio
import re
import logging
import threading
import time
from datetime import datetime
from typing import Optional, Tuple

import yt_dlp
from telethon import TelegramClient, events
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

console_logger = create_logger(
    name='MediaBot',
    level=logging.DEBUG,
    detailed=True,
    show_separators=True
)
logger = console_logger.get_logger()

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

client = TelegramClient('bot_session', API_ID, API_HASH)
user_downloads = {}


def detect_platform(url: str) -> Tuple[Optional[str], Optional[str]]:
    youtube_patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in youtube_patterns:
        match = re.match(pattern, url)
        if match:
            return 'youtube', f"https://www.youtube.com/watch?v={match.group(1)}"
    
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        return 'youtube', f"https://www.youtube.com/watch?v={url.strip()}"
    
    tiktok_patterns = [
        r'(?:https?://)?(?:www\.)?tiktok\.com/@[\w.-]+/video/(\d+)',
        r'(?:https?://)?(?:www\.)?tiktok\.com/t/(\w+)',
        r'(?:https?://)?vm\.tiktok\.com/(\w+)',
    ]
    
    for pattern in tiktok_patterns:
        match = re.match(pattern, url)
        if match:
            return 'tiktok', url
    
    return None, None


def download_video_sync(url: str, platform: str, cancel_event: threading.Event) -> Optional[dict]:
    if cancel_event.is_set():
        logger.info("🛑 Загрузка отменена до начала")
        return None
    
    console_logger.step(f"Скачивание ({platform})...", current=1, total=2)
    
    start_time = time.time()
    
    if platform == 'youtube':
        # ============================================================
        # ПРОБУЕМ РАЗНЫЕ ФОРМАТЫ ДЛЯ МАКСИМАЛЬНОЙ СКОРОСТИ
        # ============================================================
        
        # Пробуем НАТИВНЫЙ загрузчик (не aria2c)
        # Иногда встроенный загрузчик быстрее!
        logger.info("⚡ Встроенный загрузчик (нативный)")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'fragment_retries': 3,
            'skip_unavailable_fragments': True,
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
            'merge_output_format': 'mp4',
            # ВАЖНО: отключаем aria2c ВООБЩЕ
            'external_downloader': None,
            # Настройки для нативного загрузчика
            'concurrent_fragment_downloads': 16,
            'buffersize': 10 * 1024 * 1024,  # 10MB буфер
            'http_chunk_size': 50 * 1024 * 1024,  # 50MB чанки
            # Пробуем формат 22 (720p готовый mp4) - иногда он быстрее!
            'format': '22/18/134+140/best[height<=480][ext=mp4]/best',
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            }
        }
        
    elif platform == 'tiktok':
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'retries': 3,
            'format': 'best[ext=mp4]/best',
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(uploader)s_%(title).100s_%(id)s.%(ext)s',
            'postprocessors': [],
            'prefer_ffmpeg': False,
            'merge_output_format': None,
            'concurrent_fragment_downloads': 8,
            'buffersize': 10 * 1024 * 1024,
            'http_chunk_size': 50 * 1024 * 1024,
            'extractor_args': {
                'tiktok': {'api_hostname': 'api16-normal-c-useast1a.tiktokv.com'}
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
        }
    else:
        logger.error(f"Неизвестная платформа: {platform}")
        return None
    
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
                logger.info(f"✅ Готово: {os.path.basename(d.get('filename', 'unknown'))}")
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        # Пробуем сначала БЫСТРО получить инфо (без скачивания)
        info_opts = {**ydl_opts, 'skip_download': True}
        
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                logger.error("Не удалось получить информацию")
                return None
            
            # Проверяем, какой формат БУДЕТ выбран
            selected_format = info.get('format_id', '?')
            selected_ext = info.get('ext', '?')
            logger.info(f"🎯 Выбран формат: {selected_format} ({selected_ext})")
            
            # Проверяем размер
            if info.get('filesize'):
                size_mb = info['filesize'] / (1024 * 1024)
                logger.info(f"💾 Ожидаемый размер: {size_mb:.1f} MB")
            elif info.get('filesize_approx'):
                size_mb = info['filesize_approx'] / (1024 * 1024)
                logger.info(f"💾 Примерный размер: {size_mb:.1f} MB")
        
        if cancel_event.is_set():
            logger.info("🛑 Отменено")
            return None
        
        # ТЕПЕРЬ КАЧАЕМ
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            
            total_time = time.time() - start_time
            
            logger.info(f"⏱ Общее время: {total_time:.1f}с")
            logger.info(f"🎬 {info.get('title', 'N/A')[:80]}")
            logger.info(f"👤 {info.get('uploader', 'N/A')}")
            logger.info(f"📊 Формат: {info.get('format_id', '?')}")
            
            file_path = ydl.prepare_filename(info)
            
            if not os.path.exists(file_path):
                base = os.path.splitext(file_path)[0]
                for ext in ['.mp4', '.webm', '.mkv', '.mov', '.flv']:
                    alt_path = base + ext
                    if os.path.exists(alt_path):
                        file_path = alt_path
                        break
                else:
                    import glob
                    pattern = f"{DOWNLOAD_FOLDER}/*{info.get('id', '')}*"
                    possible = glob.glob(pattern)
                    if possible:
                        file_path = possible[0]
                    else:
                        logger.error("Файл не найден!")
                        return None
            
            file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
            download_speed = file_size_mb / total_time if total_time > 0 else 0
            
            logger.info(f"📁 {os.path.basename(file_path)}")
            logger.info(f"💾 {file_size_mb:.1f} MB")
            logger.info(f"⚡ Скорость: {download_speed:.2f} MB/s")
            
            duration = info.get('duration', 0)
            if duration:
                duration = int(duration)
            
            return {
                'title': info.get('title', 'Видео'),
                'fulltitle': info.get('fulltitle', info.get('title', 'Видео')),
                'uploader': info.get('uploader') or 'Неизвестный автор',
                'channel': info.get('channel', ''),
                'channel_url': info.get('channel_url', ''),
                'uploader_url': info.get('uploader_url', ''),
                'duration': duration,
                'file_path': file_path,
                'file_size_mb': file_size_mb,
                'url': url,
                'platform': platform,
                'description': info.get('description', ''),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'comment_count': info.get('comment_count', 0),
                'categories': info.get('categories', []),
                'tags': info.get('tags', []),
                'upload_date': info.get('upload_date', ''),
                'age_limit': info.get('age_limit', 0),
            }
            
    except Exception as e:
        if str(e) == "DOWNLOAD_CANCELLED" or cancel_event.is_set():
            logger.info("🛑 Загрузка отменена")
            return None
        logger.error(f"Ошибка: {str(e)[:200]}")
        raise


@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        user_name = sender.first_name or f"User {user_id}"
    except:
        user_name = f"User {user_id}"
    
    logger.info(f"📱 /start от {user_name}")
    
    await event.reply(
        f"🎬 **Привет, {user_name}!**\n\n"
        "Я - Media Download Bot! 🤖\n\n"
        "⚡ **Быстрая загрузка видео!**\n"
        "• 📺 YouTube: 720p/480p/360p mp4\n"
        "• 🎵 TikTok: лучшее качество\n"
        "• ⚡ Нативный загрузчик (макс. скорость)\n\n"
        "Просто отправь мне ссылку!\n\n"
        "⚠️ Макс. размер: 2GB | Отмена: /cancel"
    )


@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    await event.reply(
        "📖 **Справка**\n\n"
        "1️⃣ Отправьте ссылку\n"
        "2️⃣ Бот скачает видео\n"
        "3️⃣ Видео отправится вам\n\n"
        "Команды: /start /help /cancel"
    )


@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    user_id = event.sender_id
    if user_id in user_downloads:
        user_downloads[user_id].set()
        await event.reply("🛑 **Отмена загрузки...**")
        logger.info(f"🛑 Пользователь {user_id} отменил загрузку")
    else:
        await event.reply("ℹ️ Нет активных загрузок")


@client.on(events.NewMessage)
async def message_handler(event):
    text = event.text.strip() if event.text else ""
    user_id = event.sender_id
    chat_id = event.chat_id
    
    if text.startswith('/'):
        return
    
    platform, clean_url = detect_platform(text)
    
    if not platform:
        return
    
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        await event.reply("⚠️ **Уже есть активная загрузка!**\nДождитесь или /cancel")
        return
    
    platform_emoji = "📺" if platform == "youtube" else "🎵"
    platform_name = "YouTube" if platform == "youtube" else "TikTok"
    
    console_logger.separator(f"НОВЫЙ ЗАПРОС от {user_id}")
    logger.info(f"{platform_emoji} Платформа: {platform_name}")
    logger.info(f"🔗 URL: {clean_url}")
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    status_msg = await event.reply(
        f"{platform_emoji} **Загружаю видео...**\n"
        f"🌐 {platform_name}\n"
        f"⚡ Нативный загрузчик\n"
        f"🚫 /cancel для отмены"
    )
    
    start_time = datetime.now()
    
    try:
        loop = asyncio.get_event_loop()
        
        download_task = loop.run_in_executor(
            None, download_video_sync, clean_url, platform, cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            await status_msg.edit("⏰ **Таймаут загрузки**")
            return
        
        if cancel_event.is_set() or video_info is None:
            await status_msg.edit("🛑 **Загрузка отменена**")
            return
        
        file_path = video_info['file_path']
        file_size_mb = video_info['file_size_mb']
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            await status_msg.edit(f"❌ **Файл слишком большой:** {file_size_mb:.1f} MB")
            if os.path.exists(file_path):
                os.remove(file_path)
            return
        
        await status_msg.edit(f"✅ **Скачано!** ({file_size_mb:.1f} MB)\n📤 Отправляю...")
        
        duration = video_info.get('duration', 0)
        if duration > 0:
            minutes, secs = divmod(int(duration), 60)
            duration_str = f"{minutes}:{secs:02d}"
        else:
            duration_str = "Неизвестно"
        
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
            f"🔗 {video_info['url']}"
        )
        
        if len(caption) > 1000:
            caption = caption[:997] + '...'
        
        await client.send_file(
            entity=chat_id,
            file=file_path,
            caption=caption,
            supports_streaming=True
        )
        
        total_time = (datetime.now() - start_time).total_seconds()
        
        await status_msg.delete()
        
        logger.info(f"✅ УСПЕШНО: {video_info['title'][:50]}... | {file_size_mb:.1f}MB | {total_time:.1f}s")
        
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning(f"Не удалось удалить файл: {e}")
        
    except Exception as e:
        if not cancel_event.is_set():
            logger.error(f"Ошибка: {str(e)[:200]}")
            await status_msg.edit(f"❌ **Ошибка:** {str(e)[:200]}")
    
    finally:
        if user_id in user_downloads:
            del user_downloads[user_id]


async def main():
    console_logger.separator("ЗАПУСК БОТА", char="=")
    
    logger.info(f"📺 YouTube: 22 (720p mp4) / 18 (360p) / 134+140")
    logger.info(f"🎵 TikTok: лучшее качество")
    logger.info(f"⚡ НАТИВНЫЙ загрузчик (без aria2c)")
    logger.info(f"📦 Буфер: 10MB | Чанки: 50MB")
    
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    
    logger.info(f"✅ Бот запущен: @{me.username}")
    
    print()
    print("=" * 60)
    print(f"  🤖 БОТ: @{me.username}")
    print(f"  📺 YouTube: 720p/360p mp4")
    print(f"  ⚡ Нативный загрузчик")
    print(f"  🚫 /cancel для отмены")
    print("=" * 60)
    print()
    
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        import yt_dlp
        import telethon
    except ImportError as e:
        print(f"❌ Установите: pip install yt-dlp telethon")
        exit(1)
    
    client.loop.run_until_complete(main())
