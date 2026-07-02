# bot.py - Упрощённая версия без проверки форматов
import os
import asyncio
import logging
import threading
import random
import time
from datetime import datetime
from asyncio import Semaphore

import telethon
from telethon import TelegramClient, events, Button

from logger_config import create_logger
from database import *
from downloader import *
from tiktok_handler import *
from channel_monitor import *

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
API_ID = int(os.getenv('API_ID', '22268845'))
API_HASH = os.getenv('API_HASH', 'ffbeffdfb86784e12b39aea5f53857d2')
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '8566350925:AAEOwpPgXhmR3SE_7TapSbzMJnqImnMA-Js')

STORAGE_CHAT = -1001776425232
MAX_FILE_SIZE_MB = 2000
DOWNLOAD_TIMEOUT = 600
MAX_CONCURRENT_DOWNLOADS = 3
MONITOR_INTERVAL_MINUTES = 30
ENABLE_MONITORING = True

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


def format_caption(video_info: dict, platform: str, from_cache: bool = False) -> str:
    title = video_info.get('fulltitle', video_info.get('title', 'Без названия'))
    channel = video_info.get('channel') or video_info.get('uploader', 'Неизвестный')
    quality_str = video_info.get('quality', '')
    
    if platform == 'tiktok':
        caption = (
            f"🎵 **{title}**\n\n"
            f"👤 **Автор:** @{channel}\n"
            f"📊 **Формат:** {quality_str}\n"
        )
    else:
        caption = (
            f"📺 **{title}**\n\n"
            f"👤 **Канал:** {channel}\n"
            f"📊 **Качество:** {quality_str}\n"
        )
    
    if from_cache:
        caption += "⚡ **Переслано из хранилища**\n"
    
    caption += f"🔗 {video_info.get('url', '')}"
    
    if len(caption) > 1000:
        caption = caption[:997] + '...'
    
    return caption


async def notify_subscribers(channel_id: str, video_title: str, video_url: str):
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
            await asyncio.sleep(0.1)
        except:
            pass


async def process_download(event, user_id, url, platform, video_id, quality, format_id='18'):
    if platform == 'tiktok':
        quality_config = TIKTOK_QUALITY_OPTIONS.get(quality, TIKTOK_QUALITY_OPTIONS['360'])
    else:
        quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
        quality_config['format'] = format_id
    
    logger.info(f"🌐 {platform.upper()} | 📊 {quality_config['description']} | ID: {video_id}")
    
    platform_emoji = "📺" if platform == "youtube" else "🎵"
    platform_name = "YouTube" if platform == "youtube" else "TikTok"
    start_time = datetime.now()
    
    def generate_progress_text(stage, stage_name, percent, extra_info="", speed="", eta="", title=""):
        bar_length = 20
        filled = int(bar_length * percent / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        elapsed = (datetime.now() - start_time).total_seconds()
        elapsed_str = f"{int(elapsed // 60)}:{int(elapsed % 60):02d}"
        stage_emoji = {1: "🔍", 2: "⬇️", 3: "📤"}.get(stage, "✅")
        
        lines = [
            f"{platform_emoji} **{platform_name}** | 📊 {quality_config['description']}",
            "",
            f"{bar} **{percent:.1f}%**",
            "",
            f"{stage_emoji} **Этап {stage}/3:** {stage_name}",
            f"⏱ Прошло: {elapsed_str}",
        ]
        if speed:
            lines.append(f"⚡ Скорость: {speed}")
        if eta:
            lines.append(f"⏳ Осталось: {eta}")
        if extra_info:
            lines.append(f"💡 {extra_info}")
        if title:
            lines.append("")
            lines.append(f"🎬 {title[:80]}")
        lines.append("")
        lines.append("🚫 /cancel для отмены")
        return "\n".join(lines)
    
    async def update_progress(stage, stage_name, percent, extra_info="", speed="", eta="", title=""):
        try:
            text = generate_progress_text(stage, stage_name, percent, extra_info, speed, eta, title)
            await event.edit(text)
        except:
            pass
    
    # Проверка кэша (только для YouTube)
    if platform == 'youtube':
        await update_progress(1, "Проверка кэша", 2, "Ищу в базе данных...")
        await asyncio.sleep(0.5)
        
        cached = get_cached_file(video_id, quality)
        if cached and cached.get('storage_message_id') and cached.get('storage_chat_id'):
            try:
                await update_progress(1, "Найдено в кэше!", 15, "Пересылаю из хранилища...")
                await asyncio.sleep(0.5)
                await client.forward_messages(
                    entity=event.chat_id,
                    messages=cached['storage_message_id'],
                    from_peer=cached['storage_chat_id'],
                )
                update_downloads_count(video_id, quality)
                await update_progress(1, "Готово!", 100, f"✅ {cached['file_size_mb']:.1f} MB из кэша")
                await asyncio.sleep(1.5)
                await event.delete()
                return
            except Exception as e:
                logger.warning(f"⚠️ Ошибка пересылки: {e}")
    
    # Этап 1: Получение информации
    for step in range(1, 17):
        if user_id in user_downloads and user_downloads[user_id].is_set():
            await update_progress(1, "Отменено", (step / 16) * 30, "🛑 Загрузка отменена")
            return
        
        percent = 2 + (step / 16) * 28
        if step == 1:
            extra = "Подключаюсь к серверу..."
        elif step == 5:
            extra = "Извлекаю метаданные..."
        elif step == 10:
            extra = "Анализирую видеопотоки..."
        elif step == 15:
            extra = "Подготавливаю ссылки для скачивания..."
        else:
            extra = "Получение информации..."
        
        await update_progress(1, "Получение информации", percent, extra)
        await asyncio.sleep(1.5)
    
    await update_progress(1, "Информация получена", 30, "Запускаю скачивание...")
    await asyncio.sleep(0.5)
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    try:
        loop = asyncio.get_event_loop()
        await update_progress(2, "Скачивание видео", 30, "Устанавливаю соединение с сервером...")
        
        def download_progress_callback(percent, speed, eta):
            mapped_percent = 30 + (percent * 40 / 100)
            extra = ""
            if percent < 10:
                extra = "Устанавливаю соединение..."
            elif percent < 30:
                extra = "Загружаю видеопоток..."
            elif percent < 60:
                extra = "Загружаю аудиопоток..."
            elif percent < 90:
                extra = "Объединяю видео и аудио..."
            else:
                extra = "Завершаю загрузку..."
            asyncio.create_task(update_progress(2, "Скачивание", mapped_percent, extra, speed, eta))
        
        if platform == 'tiktok':
            def tiktok_download_wrapper():
                return download_tiktok_video(url, quality, download_progress_callback, cancel_event)
            download_task = loop.run_in_executor(None, tiktok_download_wrapper)
        else:
            download_task = asyncio.create_task(
                download_video(url, quality, download_progress_callback, cancel_event)
            )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            await event.edit("⏰ **Таймаут загрузки**\nПопробуйте другое качество")
            return
        
        if cancel_event.is_set() or video_info is None:
            await event.edit("🛑 **Загрузка отменена**")
            return
        
        if video_info.get('too_short'):
            duration = video_info.get('duration', 0)
            minutes, secs = divmod(duration, 60)
            await event.edit(
                f"⏱ **Видео слишком короткое!**\n\n"
                f"📺 {video_info.get('fulltitle', video_info['title'])[:100]}\n"
                f"⏱ Длительность: **{minutes}:{secs:02d}**\n"
                f"⚠️ Минимум: **1:18** (1.3 минуты)"
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
        video_title = video_info.get('fulltitle', video_info['title'])
        
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
        
        await update_progress(3, "Отправка в Telegram", 70, "Сохраняю в хранилище...", title=video_title)
        caption = format_caption(video_info, platform)
        
        storage_message = None
        if storage_chat_id:
            try:
                if is_audio:
                    storage_message = await client.send_file(
                        entity=storage_chat_id, file=file_path, caption=caption,
                        attributes=[telethon.types.DocumentAttributeAudio(
                            duration=duration if duration > 0 else 0,
                            title=video_title[:100],
                            performer=video_info.get('uploader', 'Unknown'),
                        )],
                    )
                else:
                    if platform == 'tiktok':
                        storage_message = await client.send_file(
                            entity=storage_chat_id, file=file_path, caption=caption,
                            force_document=False,
                            attributes=[telethon.types.DocumentAttributeVideo(
                                duration=duration if duration > 0 else 0,
                                w=video_info.get('width', 576),
                                h=video_info.get('height', 1024),
                                supports_streaming=True, round_message=False
                            )],
                            supports_streaming=True,
                        )
                    else:
                        storage_message = await client.send_file(
                            entity=storage_chat_id, file=file_path, caption=caption,
                            force_document=False,
                            thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                            attributes=[telethon.types.DocumentAttributeVideo(
                                duration=duration if duration > 0 else 0,
                                w=video_info.get('width', 640),
                                h=video_info.get('height', 360),
                                supports_streaming=True, round_message=False
                            )],
                            supports_streaming=True,
                        )
                logger.info(f"💾 Сохранено в хранилище: msg_id={storage_message.id}")
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения в хранилище: {e}")
        
        await update_progress(3, "Отправка в Telegram", 80, "Отправляю вам...", title=video_title)
        
        if storage_message:
            await client.forward_messages(event.chat_id, storage_message.id, from_peer=storage_chat_id)
        else:
            if is_audio:
                await client.send_file(
                    event.chat_id, file_path, caption=caption,
                    attributes=[telethon.types.DocumentAttributeAudio(
                        duration=duration if duration > 0 else 0,
                        title=video_title,
                        performer=video_info.get('uploader', 'Unknown'),
                    )])
            else:
                if platform == 'tiktok':
                    await client.send_file(
                        event.chat_id, file_path, caption=caption,
                        force_document=False,
                        attributes=[telethon.types.DocumentAttributeVideo(
                            duration=duration if duration > 0 else 0,
                            w=video_info.get('width', 576),
                            h=video_info.get('height', 1024),
                            supports_streaming=True, round_message=False
                        )],
                        supports_streaming=True,
                    )
                else:
                    await client.send_file(
                        event.chat_id, file_path, caption=caption,
                        force_document=False,
                        thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
                        attributes=[telethon.types.DocumentAttributeVideo(
                            duration=duration if duration > 0 else 0,
                            w=video_info.get('width', 640),
                            h=video_info.get('height', 360),
                            supports_streaming=True, round_message=False
                        )],
                        supports_streaming=True)
        
        # Сохраняем в БД ТОЛЬКО YouTube видео
        if storage_message and video_id and platform == 'youtube':
            save_complete_info_with_storage(
                video_id=video_id, platform=platform, quality=quality,
                info=video_info.get('full_info', video_info),
                storage_chat_id=storage_chat_id,
                storage_message_id=storage_message.id,
                file_size_mb=file_size_mb,
            )
            logger.info(f"✅ Сохранено в БД: {video_title[:50]}... [{quality}]")
        
        total_time = (datetime.now() - start_time).total_seconds()
        await update_progress(3, "Готово! ✅", 100,
                            f"{file_size_mb:.1f} MB за {total_time:.0f}с | {quality_config['description']}",
                            title=video_title)
        await asyncio.sleep(2)
        await event.delete()
        
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
        except:
            pass
        
    except Exception as e:
        if not cancel_event.is_set():
            error_msg = str(e)
            if "Sign in to confirm" in error_msg:
                await event.edit(
                    f"❌ **Ошибка аутентификации YouTube**\n\n"
                    f"YouTube требует подтверждение, что вы не бот.\n"
                    f"Попробуйте:\n"
                    f"1️⃣ Обновить cookies (экспортируйте из браузера в режиме инкогнито)\n"
                    f"2️⃣ Подождать 10-15 минут\n"
                    f"3️⃣ Использовать VPN/Proxy\n\n"
                    f"Ошибка: {error_msg[:150]}"
                )
            elif "Requested format is not available" in error_msg:
                await event.edit(
                    f"❌ **Формат недоступен**\n\n"
                    f"Выбранное качество временно недоступно.\n"
                    f"Попробуйте другое качество (360p или MP3).\n\n"
                    f"Ошибка: {error_msg[:150]}"
                )
            else:
                await event.edit(f"❌ **Ошибка:** {error_msg[:200]}")
    finally:
        if user_id in user_downloads:
            del user_downloads[user_id]
        if user_id in user_selections:
            del user_selections[user_id]


@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    try:
        sender = await event.get_sender()
        user_name = sender.first_name or str(user_id)
        add_or_update_user(user_id, username=getattr(sender, 'username', None),
                          first_name=getattr(sender, 'first_name', None))
    except:
        user_name = str(user_id)
    
    stats = get_stats()
    
    welcome = (
        f"🎬 **Привет, {user_name}!**\n\n"
        "Я - Media Download Bot! 🤖\n\n"
        f"📺 **YouTube:** 360p | 720p | 1080p | MP3\n"
        f"🎵 **TikTok:** Видео со звуком | MP3\n"
        f"⏱ YouTube мин. длительность: 1.3 минуты\n"
        f"• Кэш: {stats['total_videos']} видео\n"
        f"• Пользователей: {stats['total_users']}\n\n"
        "✅ - качество уже в кэше\n"
        "⚠️ Макс. 2GB | /stats | /monitor | /channels | /subscribe | /mysubs | /cancel"
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
    await event.reply(text)


@client.on(events.NewMessage(pattern='/monitor'))
async def monitor_handler(event):
    await event.reply("🔍 **Проверяю каналы...**")
    new_videos = await check_all_channels(
        bot_client=client,
        send_notifications=True,
        notify_callback=notify_subscribers
    )
    if new_videos:
        text = f"✅ **Найдено {len(new_videos)} новых видео**"
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
    await event.reply(text)


@client.on(events.NewMessage(pattern='/subscribe'))
async def subscribe_handler(event):
    user_id = event.sender_id
    args = event.text.split()
    if len(args) < 2:
        await event.reply("📋 Использование: `/subscribe UC_channel_id`\n\nПолучить ID канала можно через /channels")
        return
    
    channel_id = args[1]
    subscribe_to_channel(user_id, channel_id, "Канал")
    await event.reply(f"✅ **Подписка оформлена!**\nОтписаться: `/unsubscribe {channel_id}`")


@client.on(events.NewMessage(pattern='/unsubscribe'))
async def unsubscribe_handler(event):
    user_id = event.sender_id
    args = event.text.split()
    if len(args) < 2:
        await event.reply("📋 Использование: `/unsubscribe <channel_id>`")
        return
    
    channel_id = args[1]
    if unsubscribe_from_channel(user_id, channel_id):
        await event.reply("✅ **Вы отписались от канала**")
    else:
        await event.reply("❌ **Подписка не найдена**")


@client.on(events.NewMessage(pattern='/mysubs'))
async def mysubs_handler(event):
    user_id = event.sender_id
    subs = get_user_subscriptions(user_id)
    if not subs:
        await event.reply("📭 **У вас нет подписок**\nИспользуйте /subscribe для подписки на канал")
        return
    
    text = f"📋 **Ваши подписки ({len(subs)}):**\n\n"
    for sub in subs:
        name = sub.get('channel_name_full') or sub.get('channel_name', 'Неизвестный')
        text += f"• **{name}**\n"
    await event.reply(text)


@client.on(events.NewMessage(pattern='/database'))
async def database_handler(event):
    if not os.path.exists(DB_PATH):
        await event.reply("❌ **База данных не найдена**")
        return
    await client.send_file(
        entity=event.chat_id, file=DB_PATH,
        caption=f"🗄 **База данных бота**\n📅 {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}",
    )


@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    user_id = event.sender_id
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        user_downloads[user_id].set()
        await event.reply("🛑 **Загрузка отменена**")
    else:
        await event.reply("ℹ️ Нет активных загрузок")


@client.on(events.CallbackQuery)
async def callback_handler(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    
    if not data.startswith('quality:'):
        return
    
    parts = data.split(':')
    quality = parts[1]
    format_id = parts[2] if len(parts) > 2 else '18'
    
    if user_id not in user_selections:
        await event.answer("❌ Сессия истекла. Отправьте ссылку заново", alert=True)
        return
    
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        await event.answer("⚠️ У вас уже есть активная загрузка! Дождитесь завершения", alert=True)
        return
    
    selection = user_selections[user_id]
    url = selection['url']
    platform = selection['platform']
    video_id = selection['video_id']
    
    if platform == 'tiktok':
        quality_config = TIKTOK_QUALITY_OPTIONS.get(quality, TIKTOK_QUALITY_OPTIONS['360'])
    else:
        quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    platform_emoji = "📺" if platform == "youtube" else "🎵"
    platform_name = "YouTube" if platform == "youtube" else "TikTok"
    
    progress_text = (
        f"{platform_emoji} **{platform_name}** | 📊 {quality_config['description']}\n\n"
        f"🔗 {url}\n\n"
        f"[░░░░░░░░░░░░░░░░░░░░] **0%**\n\n"
        f"🔍 **Этап 1/3:** Подготовка...\n"
        f"⏳ Запуск загрузки..."
    )
    
    await event.edit(progress_text, buttons=None)
    
    async with download_semaphore:
        await process_download(event, user_id, url, platform, video_id, quality, format_id)


@client.on(events.NewMessage)
async def message_handler(event):
    text = event.text.strip() if event.text else ""
    user_id = event.sender_id
    
    if text.startswith('/'):
        return
    
    if text == '123455':
        await database_handler(event)
        return
    
    try:
        sender = await event.get_sender()
        add_or_update_user(user_id, username=getattr(sender, 'username', None),
                          first_name=getattr(sender, 'first_name', None))
    except:
        pass
    
    # Проверяем YouTube
    platform, clean_url, video_id = detect_youtube(text)
    
    # Если не YouTube, проверяем TikTok
    if not platform:
        clean_url, video_id = detect_tiktok(text)
        if clean_url:
            platform = 'tiktok'
    
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
    
    # Показываем кэш только для YouTube
    cached_qualities = get_available_qualities(video_id) if video_id and platform == 'youtube' else []
    cached_map = {q['quality']: True for q in cached_qualities}
    
    # Формируем текст
    quality_text = f"{platform_emoji} **{platform_name}**\n\n🔗 {clean_url}\n\n"
    
    if platform == 'tiktok':
        quality_text += "🎯 **Выберите формат:**\n\n"
        
        has_video = any(q['quality'] == '360' for q in cached_qualities)
        has_mp3 = any(q['quality'] == 'mp3' for q in cached_qualities)
        
        video_label = "✅ 🎵 Видео (со звуком)" if has_video else "🎵 Видео (со звуком)"
        mp3_label = "✅ 🎵 MP3 (аудио)" if has_mp3 else "🎵 MP3 (аудио)"
        
        buttons = [
            [Button.inline(video_label, data="quality:360:18")],
            [Button.inline(mp3_label, data="quality:mp3:18")],
        ]
    else:
        quality_text += "🎯 **Выберите качество:**\n⏱ Мин. длительность: 1.3 мин.\n\n"
        
        # Показываем все качества, при скачивании будет fallback на 360p
        btn_360 = "✅ 📺 360p" if '360' in cached_map else "📺 360p"
        btn_720 = "✅ 📺 720p HD" if '720' in cached_map else "📺 720p HD"
        btn_1080 = "✅ 📺 1080p Full HD" if '1080' in cached_map else "📺 1080p Full HD"
        btn_mp3 = "✅ 🎵 MP3 (аудио)" if 'mp3' in cached_map else "🎵 MP3 (аудио)"
        
        buttons = [
            [Button.inline(btn_360, data="quality:360:18")],
            [Button.inline(btn_720, data="quality:720:22")],
            [Button.inline(btn_1080, data="quality:1080:22")],
            [Button.inline(btn_mp3, data="quality:mp3:18")],
        ]
    
    await event.reply(quality_text, buttons=buttons)


async def main():
    global storage_chat_id
    
    console_logger.separator("ЗАПУСК БОТА", char="=")
    stats = get_stats()
    
    logger.info(f"📺 YouTube + 🎵 TikTok | 🔄 Многопоточность: до {MAX_CONCURRENT_DOWNLOADS} загрузок")
    logger.info(f"💾 БД: {stats['total_videos']} видео | 👥 {stats['total_users']} пользователей")
    
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    
    try:
        entity = await client.get_entity(STORAGE_CHAT)
        storage_chat_id = entity.id
        logger.info(f"🗄 Хранилище: {STORAGE_CHAT} ✅")
    except Exception as e:
        logger.error(f"❌ Хранилище недоступно: {e}")
        logger.warning("⚠️ Бот будет работать без сохранения в хранилище")
    
    if ENABLE_MONITORING:
        channels = get_monitored_channels()
        if channels:
            logger.info(f"📡 Запуск мониторинга {len(channels)} каналов...")
            asyncio.create_task(monitor_loop(
                bot_client=client,
                interval_minutes=MONITOR_INTERVAL_MINUTES,
                notify_callback=notify_subscribers
            ))
        else:
            logger.warning("⚠️ Нет каналов для мониторинга")
    
    logger.info(f"✅ Бот запущен: @{me.username}")
    
    print()
    print("=" * 60)
    print(f"  🤖 БОТ: @{me.username}")
    print(f"  📺 YouTube: 360p | 720p | 1080p | MP3")
    print(f"  🎵 TikTok: Видео со звуком | MP3")
    print(f"  🔴 Поддержка YouTube Live")
    print(f"  ✅ - качество в кэше")
    print(f"  💾 БД: {stats['total_videos']} видео | 👥 {stats['total_users']} пользователей")
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
        print(f"❌ Установите зависимости: pip install yt-dlp telethon requests")
        exit(1)
    
    client.loop.run_until_complete(main())
