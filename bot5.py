# bot.py - Основной файл бота с многопоточностью, БД и мониторингом
import os
import asyncio
import logging
import threading
from datetime import datetime
from asyncio import Semaphore

import telethon
from telethon import TelegramClient, events, Button

from logger_config import create_logger
from database import *
from downloader import *
from channel_monitor import *
from progress_bar import VideoProgressBar

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
API_ID = int(os.getenv('API_ID', '22268845'))
API_HASH = os.getenv('API_HASH', 'ffbeffdfb86784e12b39aea5f53857d2')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8566350925:AAEOwpPgXhmR3SE_7TapSbzMJnqImnMA-Js')

STORAGE_CHAT = -1001776425232  # @copirkaDva
MAX_FILE_SIZE_MB = 2000
DOWNLOAD_TIMEOUT = 600
MAX_CONCURRENT_DOWNLOADS = 3

# Настройки мониторинга
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
# УВЕДОМЛЕНИЯ ПОДПИСЧИКАМ
# ============================================================

async def notify_subscribers(channel_id: str, video_title: str, video_url: str):
    """Отправляет уведомления подписчикам канала о новом видео"""
    subscribers = get_channel_subscribers(channel_id)
    
    if not subscribers:
        return
    
    channel = get_channel(channel_id)
    channel_name = channel['name'] if channel else 'Неизвестный канал'
    
    message = (
        f"🔔 **Новое видео на канале!**\n\n"
        f"📺 **{video_title[:100]}**\n"
        f"👤 **Канал:** {channel_name}\n"
        f"🔗 {video_url}"
    )
    
    for sub in subscribers:
        try:
            await client.send_message(sub['user_id'], message)
            logger.info(f"📤 Уведомление отправлено пользователю {sub['user_id']}")
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления {sub['user_id']}: {e}")


# ============================================================
# ОБРАБОТЧИК ЗАГРУЗКИ (многопоточный)
# ============================================================

async def process_download(event, user_id, url, platform, video_id, quality):
    """Обрабатывает загрузку с прогресс-баром"""
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    logger.info(f"🌐 {platform.upper()} | 📊 {quality_config['description']} | ID: {video_id}")
    
    # Создаём прогресс-бар
    progress = VideoProgressBar(
        message=event._message if hasattr(event, '_message') else None,
        platform=platform,
        quality=quality_config['description'],
    )
    
    # Этап 1: Проверка кэша и получение информации
    progress.update_stage1(10, "Проверяю кэш...")
    await progress.update_message()
    await asyncio.sleep(0.3)
    
    # Проверяем кэш
    cached = get_cached_file(video_id, quality)
    
    if cached and cached.get('storage_message_id') and cached.get('storage_chat_id'):
        try:
            progress.update_stage1(80, "Найдено в кэше! Пересылаю...")
            await progress.update_message()
            
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
            
            progress.complete(file_size_mb=cached['file_size_mb'])
            await progress.update_message()
            await asyncio.sleep(1)
            await event.delete()
            return
        except Exception as e:
            logger.warning(f"⚠️ Ошибка пересылки: {e}")
    
    # Качаем заново
    progress.update_stage1(30, "Начинаю загрузку...")
    await progress.update_message()
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    try:
        loop = asyncio.get_event_loop()
        
        def download_progress_callback(percent, speed, eta):
            progress.update_download(percent, speed, eta)
            asyncio.create_task(progress.update_message())
        
        # Завершаем этап 1
        progress.update_stage1(100, "Информация получена")
        await progress.update_message()
        
        # Этап 2: Скачивание
        progress.stage = 2
        progress.stage_progress = 0
        await progress.update_message()
        
        download_task = loop.run_in_executor(
            None, download_video, url, platform, quality, download_progress_callback, cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            progress.cancel()
            await progress.update_message()
            await event.edit("⏰ **Таймаут загрузки**\nПопробуйте другое качество")
            return
        
        if cancel_event.is_set() or video_info is None:
            progress.cancel()
            await progress.update_message()
            return
        
        # Проверка на слишком короткое видео
        if video_info.get('too_short'):
            duration = video_info.get('duration', 0)
            minutes, secs = divmod(duration, 60)
            progress.cancel()
            await progress.update_message()
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
            progress.cancel()
            await progress.update_message()
            await event.edit(f"❌ **Слишком большой файл:** {file_size_mb:.1f} MB")
            try:
                if os.path.exists(file_path): os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
            except: pass
            return
        
        # Этап 3: Отправка
        progress.stage = 3
        progress.stage_progress = 0
        progress.update_upload(0, f"Подготовка к отправке...")
        await progress.update_message()
        
        progress.set_title(video_info['title'])
        
        # Сохраняем в хранилище
        storage_message = None
        if storage_chat_id:
            try:
                progress.update_upload(20, "Сохраняю в хранилище...")
                await progress.update_message()
                
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
        progress.update_upload(50, "Отправляю вам...")
        await progress.update_message()
        
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
        
        progress.update_upload(90, "Почти готово...")
        await progress.update_message()
        
        # Сохраняем в БД
        if storage_message:
            save_complete_info_with_storage(
                video_id=video_id, platform=platform, quality=quality,
                info=video_info.get('full_info', video_info),
                storage_chat_id=storage_chat_id,
                storage_message_id=storage_message.id,
                file_size_mb=file_size_mb,
            )
        
        progress.complete(file_size_mb=file_size_mb)
        await progress.update_message()
        await asyncio.sleep(1.5)
        await event.delete()
        
        # Чистим
        try:
            if os.path.exists(file_path): os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        except: pass
        
        # Уведомляем подписчиков
        try:
            full_info = video_info.get('full_info', {})
            channel_id_db = full_info.get('channel_id', '')
            if channel_id_db:
                await notify_subscribers(channel_id_db, video_info['title'], url)
        except:
            pass
    
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
        add_or_update_user(user_id, username=getattr(sender, 'username', None),
                          first_name=getattr(sender, 'first_name', None))
    except:
        user_name = user_id
    
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
        f"• 👥 Пользователей: {stats['total_users']}\n\n"
        "⚠️ Макс. 2GB | /stats | /monitor | /channels | /subscribe | /mysubs | /database | /cancel"
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
    new_videos = await check_all_channels(client, send_notifications=True)
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
        await event.reply("📭 **Нет каналов в базе данных**\n\nКаналы добавляются при скачивании видео.")
        return
    text = f"📺 **Отслеживаемые каналы ({len(channels)}):**\n\n"
    for ch in channels[:30]:
        text += f"• {ch['name']}\n"
    if len(channels) > 30:
        text += f"\n... и ещё {len(channels) - 30}"
    text += f"\n🔍 Проверка каждые {MONITOR_INTERVAL_MINUTES} мин."
    await event.reply(text)


@client.on(events.NewMessage(pattern='/subscribe'))
async def subscribe_handler(event):
    """Подписка на канал: /subscribe <channel_id или ссылка>"""
    user_id = event.sender_id
    args = event.text.split()
    
    if len(args) < 2:
        await event.reply(
            "📋 **Подписка на канал**\n\n"
            "Использование:\n"
            "`/subscribe UC_channel_id`\n"
            "`/subscribe https://youtube.com/@channel`\n\n"
            "После подписки вы будете получать уведомления о новых видео."
        )
        return
    
    channel_input = args[1]
    
    # Пробуем получить channel_id
    channel_id = None
    channel_name = "Неизвестный канал"
    
    # Если это ссылка YouTube
    if 'youtube.com/' in channel_input or 'youtu.be/' in channel_input:
        try:
            from downloader import get_video_info
            info = get_video_info(channel_input, 'youtube')
            if info:
                channel_id = info.get('channel_id', '')
                channel_name = info.get('channel', '') or info.get('uploader', 'Неизвестный')
        except:
            pass
    
    # Если это ID канала (начинается с UC)
    if not channel_id and channel_input.startswith('UC'):
        channel_id = channel_input
    
    if not channel_id:
        await event.reply("❌ **Не удалось определить ID канала**\nОтправьте ссылку на видео с этого канала сначала.")
        return
    
    # Подписываем
    subscribe_to_channel(user_id, channel_id, channel_name)
    
    # Добавляем в таблицу каналов если нет
    existing = get_channel(channel_id)
    if not existing:
        add_or_update_channel(channel_id, channel_name, 'youtube', 
                            channel_url=f"https://youtube.com/channel/{channel_id}")
    
    await event.reply(
        f"✅ **Подписка оформлена!**\n\n"
        f"👤 Канал: **{channel_name}**\n"
        f"🔔 Вы будете получать уведомления о новых видео.\n\n"
        f"Отписаться: `/unsubscribe {channel_id}`\n"
        f"Мои подписки: /mysubs"
    )
    
    logger.info(f"🔔 Пользователь {user_id} подписался на {channel_name}")


@client.on(events.NewMessage(pattern='/unsubscribe'))
async def unsubscribe_handler(event):
    """Отписка от канала: /unsubscribe <channel_id>"""
    user_id = event.sender_id
    args = event.text.split()
    
    if len(args) < 2:
        await event.reply("📋 Использование: `/unsubscribe <channel_id>`\nСписок подписок: /mysubs")
        return
    
    channel_id = args[1]
    
    if unsubscribe_from_channel(user_id, channel_id):
        await event.reply("✅ **Вы отписались от канала**")
    else:
        await event.reply("❌ **Подписка не найдена**")


@client.on(events.NewMessage(pattern='/mysubs'))
async def mysubs_handler(event):
    """Показывает подписки пользователя"""
    user_id = event.sender_id
    subs = get_user_subscriptions(user_id)
    
    if not subs:
        await event.reply(
            "📭 **У вас нет подписок**\n\n"
            "Подписаться: `/subscribe <channel_id>`"
        )
        return
    
    text = f"📋 **Ваши подписки ({len(subs)}):**\n\n"
    for sub in subs:
        name = sub.get('channel_name_full') or sub.get('channel_name', 'Неизвестный')
        quality = sub.get('quality', '720')
        text += f"• **{name}**\n"
        text += f"  Качество: {quality}p | ID: `{sub['channel_id']}`\n"
        text += f"  Отписаться: `/unsubscribe {sub['channel_id']}`\n\n"
    
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
        asyncio.create_task(monitor_loop(client, MONITOR_INTERVAL_MINUTES, notify_subscribers))
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
    print(f"  /start | /stats | /monitor | /channels | /subscribe | /mysubs | /database")
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
