# bot.py - Основной файл бота с многопоточностью, БД и мониторингом
import os
import asyncio
import logging
import threading
from datetime import datetime
from asyncio import Semaphore

import telethon
from telethon import TelegramClient, events, Button
import requests
import xml.etree.ElementTree as ET
import sqlite3

from logger_config import create_logger
from database import *
from downloader import *

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
API_ID = int(os.getenv('API_ID', '22268845'))
API_HASH = os.getenv('API_HASH', 'ffbeffdfb86784e12b39aea5f53857d2')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8566350925:AAEOwpPgXhmR3SE_7TapSbzMJnqImnMA-Js')

STORAGE_CHAT = -1001776425232  # @copirkaDva
MAX_FILE_SIZE_MB = 2000
DOWNLOAD_TIMEOUT = 600
MAX_CONCURRENT_DOWNLOADS = 3  # Максимум одновременных загрузок
MONITOR_INTERVAL_MINUTES = 30
ENABLE_MONITORING = True

# Семафор для ограничения параллельных загрузок
download_semaphore = Semaphore(MAX_CONCURRENT_DOWNLOADS)

console_logger = create_logger(
    name='MediaBot',
    level=logging.DEBUG,
    detailed=True,
    show_separators=True
)
logger = console_logger.get_logger()

init_database()
logger.info("🗄 БД инициализирована")

client = TelegramClient('bot_session', API_ID, API_HASH)

user_selections = {}
user_downloads = {}
storage_chat_id = None


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

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
        caption += "⚡ **Переслано из хранилища**\n"
    
    caption += f"🔗 {video_info.get('url', '')}"
    
    if len(caption) > 1000:
        caption = caption[:997] + '...'
    
    return caption


# ============================================================
# МОНИТОРИНГ КАНАЛОВ
# ============================================================

def get_monitored_channels() -> list:
    """Получает список YouTube-каналов из БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT channel_id, name, platform, channel_url
        FROM channels
        WHERE platform = 'youtube' AND channel_id IS NOT NULL AND channel_id != ''
        ORDER BY name
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {'channel_id': row[0], 'name': row[1], 'platform': row[2], 'channel_url': row[3]}
        for row in rows
    ]


def check_channel_rss(channel_id: str) -> list:
    """Проверяет RSS-ленту канала на новые видео"""
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    
    try:
        response = requests.get(rss_url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            return []
        
        root = ET.fromstring(response.content)
        
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015',
        }
        
        videos = []
        
        for entry in root.findall('atom:entry', ns):
            video_id_elem = entry.find('yt:videoId', ns)
            title_elem = entry.find('atom:title', ns)
            published_elem = entry.find('atom:published', ns)
            
            if video_id_elem is not None:
                video_id = video_id_elem.text
                title = title_elem.text if title_elem is not None else 'Без названия'
                published = published_elem.text if published_elem is not None else ''
                url = f"https://www.youtube.com/watch?v={video_id}"
                
                if not is_video_in_db(video_id):
                    videos.append({
                        'video_id': video_id,
                        'title': title,
                        'url': url,
                        'published': published,
                        'channel_id': channel_id,
                    })
        
        return videos
    
    except Exception as e:
        logger.error(f"❌ RSS ошибка для {channel_id}: {str(e)[:100]}")
        return []


def is_video_in_db(video_id: str) -> bool:
    """Проверяет, есть ли видео в БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM videos WHERE video_id = ?', (video_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def add_new_video_to_db(video_info: dict):
    """Добавляет новое видео в БД"""
    add_or_update_video(
        video_id=video_info['video_id'],
        platform='youtube',
        title=video_info['title'],
        url=video_info['url'],
        channel_id=video_info['channel_id'],
        upload_date=video_info.get('published', ''),
    )


async def check_all_channels(send_notifications: bool = True) -> list:
    """Проверяет все каналы на новые видео"""
    channels = get_monitored_channels()
    
    if not channels:
        return []
    
    logger.info(f"🔍 Проверяю {len(channels)} каналов...")
    
    all_new_videos = []
    
    for channel in channels[:20]:
        channel_id = channel['channel_id']
        channel_name = channel['name']
        
        new_videos = check_channel_rss(channel_id)
        
        if new_videos:
            logger.info(f"📺 {channel_name}: +{len(new_videos)} новых видео")
            
            for video in new_videos:
                add_new_video_to_db(video)
                
                if send_notifications and storage_chat_id:
                    try:
                        message = (
                            f"🆕 **Новое видео!**\n\n"
                            f"📺 **{video['title'][:100]}**\n"
                            f"👤 **Канал:** {channel_name}\n"
                            f"🔗 {video['url']}\n"
                            f"📅 {video.get('published', '')[:10]}"
                        )
                        await client.send_message(storage_chat_id, message)
                    except:
                        pass
            
            all_new_videos.extend(new_videos)
    
    if all_new_videos:
        logger.info(f"✅ Найдено {len(all_new_videos)} новых видео")
    
    return all_new_videos


async def monitor_loop():
    """Бесконечный цикл мониторинга"""
    logger.info(f"🔄 Мониторинг каналов запущен (интервал: {MONITOR_INTERVAL_MINUTES} мин)")
    
    await asyncio.sleep(60)
    
    while True:
        try:
            await check_all_channels(send_notifications=True)
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга: {str(e)[:200]}")
        
        await asyncio.sleep(MONITOR_INTERVAL_MINUTES * 60)


# ============================================================
# ОБРАБОТЧИК ЗАГРУЗКИ (многопоточный)
# ============================================================

async def process_download(event, user_id, url, platform, video_id, quality):
    """Обрабатывает загрузку с семафором"""
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    logger.info(f"🌐 {platform.upper()} | 📊 {quality_config['description']} | ID: {video_id}")
    logger.info(f"🔗 {url}")
    
    # Проверяем кэш
    cached = get_cached_file(video_id, quality)
    
    if cached and cached.get('storage_message_id') and cached.get('storage_chat_id'):
        try:
            logger.info(f"⚡ Пересылаю из хранилища: msg_id={cached['storage_message_id']}")
            
            await event.edit(
                f"🎯 **Найдено в кэше!**\n"
                f"📤 Пересылаю из хранилища...\n"
                f"📊 {cached['quality_label']} | 💾 {cached['file_size_mb']:.1f} MB",
                buttons=None
            )
            
            await client.forward_messages(
                entity=event.chat_id,
                messages=cached['storage_message_id'],
                from_peer=cached['storage_chat_id'],
            )
            
            caption = format_caption({
                'title': cached['title'],
                'fulltitle': cached['title'],
                'uploader': cached.get('channel_name', 'Неизвестный'),
                'channel': cached.get('channel_name', 'Неизвестный'),
                'duration': cached['duration'],
                'view_count': cached['view_count'],
                'like_count': cached['like_count'],
                'file_size_mb': cached['file_size_mb'],
                'quality': cached['quality_label'],
                'url': cached['video_url'],
                'is_audio': (quality == 'mp3'),
            }, platform, from_cache=True)
            
            await client.send_message(event.chat_id, caption)
            update_downloads_count(video_id, quality)
            await event.delete()
            return
        except Exception as e:
            logger.warning(f"⚠️ Ошибка пересылки: {e}")
    
    # Качаем заново
    await event.edit(
        f"🔄 **Загружаю...**\n"
        f"🌐 {platform.upper()}\n"
        f"📊 {quality_config['label']}\n"
        f"⏳ Получаю информацию и скачиваю...",
        buttons=None
    )
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    try:
        loop = asyncio.get_event_loop()
        
        def progress_callback(percent, speed, eta):
            console_logger.download_progress(percent, speed=speed, eta=eta)
        
        download_task = loop.run_in_executor(
            None, download_video, url, platform, quality, progress_callback, cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            await event.edit("⏰ **Таймаут загрузки**\nПопробуйте другое качество")
            return
        
        if cancel_event.is_set() or video_info is None:
            await event.edit("🛑 **Загрузка отменена**")
            return
        
        # Проверка на слишком короткое видео
        if video_info.get('too_short'):
            duration = video_info.get('duration', 0)
            minutes, secs = divmod(duration, 60)
            await event.edit(
                f"⏱ **Видео слишком короткое!**\n\n"
                f"📺 {video_info.get('fulltitle', video_info['title'])[:100]}\n"
                f"👤 {video_info.get('channel') or video_info.get('uploader', 'N/A')}\n"
                f"⏱ Длительность: **{minutes}:{secs:02d}**\n"
                f"⚠️ Минимум: **1:18** (1.3 минуты)\n\n"
                f"🔗 {video_info.get('url', '')}\n\n"
                f"Попробуйте другое видео или выберите MP3."
            )
            return
        
        file_path = video_info.get('file_path')
        if not file_path or not os.path.exists(file_path):
            await event.edit("❌ **Ошибка: файл не найден**")
            return
        
        file_size_mb = video_info['file_size_mb']
        is_audio = video_info['is_audio']
        thumb_path = video_info.get('thumb_path')
        duration = video_info.get('duration', 0)
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            await event.edit(f"❌ **Слишком большой файл:** {file_size_mb:.1f} MB")
            try:
                if os.path.exists(file_path): os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
            except: pass
            return
        
        await event.edit(f"✅ **Скачано!** ({file_size_mb:.1f} MB)\n📤 **Отправляю...**")
        
        # Сохраняем в хранилище
        storage_message = None
        if storage_chat_id:
            try:
                storage_caption = f"[{quality_config['quality_label']}] {video_info['fulltitle'][:200]}\n{video_info['url']}"
                
                if is_audio:
                    storage_message = await client.send_file(
                        entity=storage_chat_id, file=file_path, caption=storage_caption,
                        attributes=[telethon.types.DocumentAttributeAudio(
                            duration=duration, title=video_info.get('fulltitle', '')[:100],
                            performer=video_info.get('uploader', 'Unknown'),
                        )],
                    )
                else:
                    storage_message = await client.send_file(
                        entity=storage_chat_id, file=file_path, caption=storage_caption,
                        force_document=False,
                        thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                        attributes=[telethon.types.DocumentAttributeVideo(
                            duration=duration, w=video_info.get('width', 640),
                            h=video_info.get('height', 360),
                            supports_streaming=True, round_message=False
                        )],
                        supports_streaming=True,
                    )
                logger.info(f"💾 Сохранено в хранилище: msg_id={storage_message.id}")
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения в хранилище: {e}")
        
        # Отправляем пользователю
        caption = format_caption(video_info, platform)
        
        if storage_message:
            await client.forward_messages(event.chat_id, storage_message.id, from_peer=storage_chat_id)
            await client.send_message(event.chat_id, caption)
        else:
            if is_audio:
                await client.send_file(event.chat_id, file_path, caption=caption,
                    attributes=[telethon.types.DocumentAttributeAudio(
                        duration=duration, title=video_info.get('fulltitle', video_info['title']),
                        performer=video_info.get('uploader', 'Unknown'),
                    )])
            else:
                await client.send_file(event.chat_id, file_path, caption=caption,
                    force_document=False,
                    thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                    attributes=[telethon.types.DocumentAttributeVideo(
                        duration=duration, w=video_info.get('width', 640),
                        h=video_info.get('height', 360),
                        supports_streaming=True, round_message=False
                    )],
                    supports_streaming=True)
        
        # Сохраняем в БД
        if storage_message:
            save_complete_info_with_storage(
                video_id=video_id, platform=platform, quality=quality,
                info=video_info.get('full_info', video_info),
                storage_chat_id=storage_chat_id,
                storage_message_id=storage_message.id,
                file_size_mb=file_size_mb,
            )
        
        await event.delete()
        
        # Чистим
        try:
            if os.path.exists(file_path): os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        except: pass
    
    except Exception as e:
        if not cancel_event.is_set():
            logger.error(f"Ошибка: {str(e)[:200]}")
            await event.edit(f"❌ **Ошибка:** {str(e)[:200]}")
    finally:
        if user_id in user_downloads: del user_downloads[user_id]
        if user_id in user_selections: del user_selections[user_id]


# ============================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        user_name = sender.first_name or user_id
    except:
        user_name = user_id
    
    # Регистрируем пользователя
    try:
        add_or_update_user(user_id, username=getattr(sender, 'username', None), 
                          first_name=getattr(sender, 'first_name', None))
    except:
        pass
    
    stats = get_stats()
    channels_count = len(get_monitored_channels())
    
    welcome = (
        f"🎬 **Привет, {user_name}!**\n\n"
        "Я - Media Download Bot! 🤖\n\n"
        "⚡ **Как я работаю:**\n"
        "1️⃣ Отправляешь ссылку\n"
        "2️⃣ Мгновенно появляются кнопки\n"
        "3️⃣ Выбираешь качество — я качаю\n"
        "4️⃣ Сохраняю в хранилище и кэширую\n\n"
        f"📺 **YouTube:** 360p | 480p | 720p | 1080p | MP3\n"
        f"🎵 **TikTok:** 360p | 480p | 720p | 1080p | MP3\n"
        f"⏱ Мин. длительность: 1.3 минуты\n"
        f"• 🚀 aria2c: {'✅' if ARIA2_AVAILABLE else '❌'}\n"
        f"• 🍪 Cookies: {'✅' if os.path.exists(COOKIES_FILE) else '❌'}\n"
        f"• 🗄 Хранилище: {'✅' if storage_chat_id else '❌'}\n"
        f"• ⚡ Кэш: {stats['total_videos']} видео\n"
        f"• 🔍 Мониторинг: {channels_count} каналов\n\n"
        "⚠️ Макс. 2GB | /stats | /monitor | /channels | /database | /cancel"
    )
    
    await event.reply(welcome)


@client.on(events.NewMessage(pattern='/stats'))
async def stats_handler(event):
    stats = get_stats()
    text = (
        f"📊 **Статистика**\n\n"
        f"👥 Пользователей: **{stats['total_users']}**\n"
        f"📋 Подписок: **{stats['total_subscriptions']}**\n"
        f"👤 Каналов: **{stats['total_channels']}**\n"
        f"🎬 Видео: **{stats['total_videos']}**\n"
        f"📁 Файлов: **{stats['total_files']}**\n"
        f"📤 Пересылок: **{stats['total_downloads']}**\n"
        f"💾 Размер: **{stats['total_size_mb']} MB**\n"
    )
    if stats['top_downloads']:
        text += "\n🏆 **Топ-5 по пересылкам:**\n"
        for i, (title, quality, count, url) in enumerate(stats['top_downloads'][:5], 1):
            short_title = title[:40] + '...' if len(title) > 40 else title
            text += f"{i}. {short_title} [{quality}] - {count} раз\n"
    await event.reply(text)


@client.on(events.NewMessage(pattern='/monitor'))
async def monitor_handler(event):
    await event.reply("🔍 **Проверяю каналы на новые видео...**")
    new_videos = await check_all_channels(send_notifications=False)
    if new_videos:
        text = f"✅ **Найдено {len(new_videos)} новых видео:**\n\n"
        for v in new_videos[:15]:
            text += f"📺 {v['title'][:60]}...\n🔗 {v['url']}\n\n"
    else:
        text = "📭 **Новых видео не найдено**"
    await event.reply(text)


@client.on(events.NewMessage(pattern='/channels'))
async def channels_handler(event):
    channels = get_monitored_channels()
    if not channels:
        await event.reply("📭 **Нет каналов в базе данных**")
        return
    text = f"📺 **Отслеживаемые каналы ({len(channels)}):**\n\n"
    for ch in channels[:30]:
        text += f"• {ch['name']}\n"
    if len(channels) > 30:
        text += f"\n... и ещё {len(channels) - 30}"
    await event.reply(text)


@client.on(events.NewMessage(pattern='/database'))
async def database_handler(event):
    if not os.path.exists(DB_PATH):
        await event.reply("❌ **База данных не найдена**")
        return
    db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    stats = get_stats()
    caption = (
        f"🗄 **База данных бота**\n\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"📋 Подписок: {stats['total_subscriptions']}\n"
        f"👤 Каналов: {stats['total_channels']}\n"
        f"🎬 Видео: {stats['total_videos']}\n"
        f"📁 Файлов: {stats['total_files']}\n"
        f"💾 Размер: {db_size_mb:.2f} MB\n"
        f"📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )
    await client.send_file(
        entity=event.chat_id, file=DB_PATH, caption=caption,
        filename=f"video_cache_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
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
    
    async with download_semaphore:
        await process_download(event, user_id, url, platform, video_id, quality)


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
    
    # Кодовое слово для БД
    if text == '123455':
        await database_handler(event)
        return
    
    # Регистрируем пользователя при любом сообщении
    try:
        sender = await event.get_sender()
        add_or_update_user(user_id, username=getattr(sender, 'username', None),
                          first_name=getattr(sender, 'first_name', None))
    except:
        pass
    
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
            quality_text += f"• {q['label']}: {q['size_mb']:.1f} MB (скачано {q['downloads']} раз)\n"
        quality_text += "\n"
    
    quality_text += "🎯 **Выберите качество:**\n⏱ Мин. длительность: 1.3 мин."
    
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
    channels = get_monitored_channels()
    
    logger.info(f"📺 YouTube + 🎵 TikTok | 🔄 Многопоточность: до {MAX_CONCURRENT_DOWNLOADS} загрузок")
    logger.info(f"⏱ Мин. длительность: {MIN_DURATION_SECONDS}с (1.3 мин)")
    logger.info(f"💾 БД: {stats['total_videos']} видео | 👥 {stats['total_users']} пользователей")
    logger.info(f"👤 Каналов: {stats['total_channels']} | 🔍 Мониторинг: {len(channels)}")
    logger.info(f"🍪 Cookies: {'✅' if os.path.exists(COOKIES_FILE) else '❌'} | 🚀 aria2c: {'✅' if ARIA2_AVAILABLE else '❌'}")
    
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    
    try:
        entity = await client.get_entity(STORAGE_CHAT)
        storage_chat_id = entity.id
        logger.info(f"🗄 Хранилище: @copirkaDva ✅")
    except Exception as e:
        logger.error(f"❌ Хранилище недоступно: {e}")
    
    if ENABLE_MONITORING and channels:
        asyncio.create_task(monitor_loop())
        logger.info(f"🔍 Мониторинг запущен: {len(channels)} каналов, интервал {MONITOR_INTERVAL_MINUTES} мин")
    
    logger.info(f"✅ Бот запущен: @{me.username}")
    
    print()
    print("=" * 60)
    print(f"  🤖 БОТ: @{me.username}")
    print(f"  📺 YouTube + 🎵 TikTok")
    print(f"  🔄 Многопоточность: до {MAX_CONCURRENT_DOWNLOADS} загрузок")
    print(f"  ⏱ Мин. длительность: 1.3 минуты")
    print(f"  🗄 Хранилище: {'✅' if storage_chat_id else '❌'}")
    print(f"  💾 БД: {stats['total_videos']} видео | 👥 {stats['total_users']} пользователей")
    print(f"  🔍 Мониторинг: {len(channels)} каналов")
    print("=" * 60)
    print()
    
    await client.run_until_disconnected()


if __name__ == '__main__':
    try:
        import yt_dlp
        import telethon
        import requests
        import sqlite3
    except ImportError as e:
        print(f"❌ Установите: pip install yt-dlp telethon requests")
        exit(1)
    
    client.loop.run_until_complete(main())
