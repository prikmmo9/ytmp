# bot.py - Основной файл бота
import os
import asyncio
import threading
from datetime import datetime

import telethon
from telethon import TelegramClient, events, Button

from logger_config import create_logger
from database import *
from downloader import *  # Импортируем всё из модуля скачивания

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
API_ID = int(os.getenv('API_ID', '22268845'))
API_HASH = os.getenv('API_HASH', 'ffbeffdfb86784e12b39aea5f53857d2')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8566350925:AAEOwpPgXhmR3SE_7TapSbzMJnqImnMA-Js')

STORAGE_CHAT = -1001776425232  # @copirkaDva
MAX_FILE_SIZE_MB = 2000
DOWNLOAD_TIMEOUT = 600

# Логгер
console_logger = create_logger(
    name='MediaBot',
    level=logging.DEBUG,
    detailed=True,
    show_separators=True
)
logger = console_logger.get_logger()

# Инициализация
init_database()
logger.info("🗄 БД инициализирована")

client = TelegramClient('bot_session', API_ID, API_HASH)

user_selections = {}
user_downloads = {}
storage_chat_id = None


# ============================================================
# ФУНКЦИИ
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
# ОБРАБОТЧИКИ КОМАНД
# ============================================================

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_name = event.sender_id
    try:
        sender = await event.get_sender()
        user_name = sender.first_name or user_name
    except:
        pass
    
    stats = get_stats()
    
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
        f"• 🚀 aria2c: {'✅' if ARIA2_AVAILABLE else '❌'}\n"
        f"• 🍪 Cookies: {'✅' if os.path.exists(COOKIES_FILE) else '❌'}\n"
        f"• 🗄 Хранилище: {'✅' if storage_chat_id else '❌'}\n"
        f"• ⚡ Кэш: {stats['total_videos']} видео\n\n"
        "⚠️ Макс. 2GB | /stats | /database | /cancel"
    )
    
    await event.reply(welcome)


@client.on(events.NewMessage(pattern='/stats'))
async def stats_handler(event):
    stats = get_stats()
    text = (
        f"📊 **Статистика**\n\n"
        f"👤 Каналов: **{stats['total_channels']}**\n"
        f"🎬 Видео: **{stats['total_videos']}**\n"
        f"📁 Файлов: **{stats['total_files']}**\n"
        f"📤 Пересылок: **{stats['total_downloads']}**\n"
        f"💾 Размер: **{stats['total_size_mb']} MB**\n"
    )
    if stats['top_downloads']:
        text += "\n🏆 **Топ-5:**\n"
        for i, (title, quality, count, url) in enumerate(stats['top_downloads'][:5], 1):
            text += f"{i}. {title[:40]}... [{quality}] - {count} раз\n"
    await event.reply(text)


@client.on(events.NewMessage(pattern='/database'))
async def database_handler(event):
    if not os.path.exists(DB_PATH):
        await event.reply("❌ БД не найдена")
        return
    stats = get_stats()
    db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    caption = f"🗄 **БД**\n📊 Видео: {stats['total_videos']}\n💾 {db_size_mb:.2f} MB"
    await client.send_file(event.chat_id, DB_PATH, caption=caption)


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
    
    selection = user_selections[user_id]
    url = selection['url']
    platform = selection['platform']
    video_id = selection['video_id']
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    logger.info(f"🌐 {platform.upper()} | 📊 {quality_config['description']} | ID: {video_id}")
    
    # Проверяем кэш
    cached = get_cached_file(video_id, quality)
    
    if cached and cached.get('storage_message_id') and cached.get('storage_chat_id'):
        try:
            await event.edit("🎯 **Найдено в кэше!**\n📤 Пересылаю из хранилища...", buttons=None)
            
            await client.forward_messages(
                entity=event.chat_id,
                messages=cached['storage_message_id'],
                from_peer=cached['storage_chat_id'],
            )
            
            caption = format_caption({
                'title': cached['title'],
                'fulltitle': cached['title'],
                'uploader': cached.get('channel_name', ''),
                'channel': cached.get('channel_name', ''),
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
    await event.edit(f"🔄 **Загружаю...**\n📊 {quality_config['label']}", buttons=None)
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    try:
        loop = asyncio.get_event_loop()
        
        # Используем downloader.download_video с колбэком прогресса
        def progress_callback(percent, speed, eta):
            console_logger.download_progress(percent, speed=speed, eta=eta)
        
        download_task = loop.run_in_executor(
            None, download_video, url, platform, quality, progress_callback, cancel_event
        )
        
        video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        
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
            if os.path.exists(file_path):
                os.remove(file_path)
            return
        
        await event.edit(f"✅ **Скачано!** ({file_size_mb:.1f} MB)\n📤 Отправляю...")
        
        # Сохраняем в хранилище
        storage_message = None
        if storage_chat_id:
            try:
                storage_caption = f"[{quality_config['quality_label']}] {video_info['fulltitle'][:200]}\n{video_info['url']}"
                
                if is_audio:
                    storage_message = await client.send_file(
                        entity=storage_chat_id,
                        file=file_path,
                        caption=storage_caption,
                        attributes=[telethon.types.DocumentAttributeAudio(
                            duration=duration, title=video_info.get('fulltitle', '')[:100],
                            performer=video_info.get('uploader', 'Unknown'),
                        )],
                    )
                else:
                    storage_message = await client.send_file(
                        entity=storage_chat_id,
                        file=file_path,
                        caption=storage_caption,
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
                        h=video_info.get('height',
