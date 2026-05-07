# bot.py - Основной файл бота с прогресс-баром, многопоточностью и мониторингом
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
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления {sub['user_id']}: {e}")


# ============================================================
# ОБРАБОТЧИК ЗАГРУЗКИ С ПРОГРЕСС-БАРОМ
# ============================================================

async def process_download(event, user_id, url, platform, video_id, quality):
    """Обрабатывает загрузку с обновлением прогресс-бара в сообщении"""
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    logger.info(f"🌐 {platform.upper()} | 📊 {quality_config['description']} | ID: {video_id}")
    
    platform_emoji = "📺" if platform == "youtube" else "🎵"
    platform_name = "YouTube" if platform == "youtube" else "TikTok"
    start_time = datetime.now()
    
    def generate_progress_text(stage: int, stage_name: str, percent: float, 
                               extra_info: str = "", speed: str = "", eta: str = "",
                               title: str = "") -> str:
        bar_length = 20
        filled = int(bar_length * percent / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        elapsed_str = f"{int(elapsed // 60)}:{int(elapsed % 60):02d}"
        
        stage_emoji = {1: "🔍", 2: "⬇️", 3: "📤"}.get(stage, "✅")
        
        lines = [
            f"{platform_emoji} **{platform_name}** | 📊 {quality_config['description']}",
            f"",
            f"{bar} **{percent:.1f}%**",
            f"",
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
            lines.append(f"")
            lines.append(f"🎬 {title[:80]}")
        
        lines.append(f"")
        lines.append(f"🚫 /cancel для отмены")
        
        return "\n".join(lines)
    
    async def update_progress(stage: int, stage_name: str, percent: float, 
                              extra_info: str = "", speed: str = "", eta: str = "",
                              title: str = ""):
        try:
            text = generate_progress_text(stage, stage_name, percent, extra_info, speed, eta, title)
            await event.edit(text)
        except Exception as e:
            logger.error(f"Ошибка обновления прогресса: {e}")
    
    # ============================================================
    # ЭТАП 1: ПРОВЕРКА КЭША
    # ============================================================
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
            logger.warning(f"⚠️ Ошибка пересылки из кэша: {e}")
    
    # ============================================================
    # ЭТАП 1: ПОЛУЧЕНИЕ ИНФОРМАЦИИ (2% → 30% за ~80 секунд)
    # ============================================================
    stage1_total_steps = 16
    stage1_delay = 5.0
    
    stage1_messages = [
        (3, "Подключаюсь к серверу..."),
        (6, "Загружаю страницу видео..."),
        (9, "Извлекаю метаданные..."),
        (12, "Проверяю доступные форматы..."),
        (15, "Анализирую видеопотоки..."),
        (18, "Получаю информацию о разрешении..."),
        (20, "Проверяю аудиодорожки..."),
        (22, "Определяю оптимальный формат..."),
        (24, "Расшифровываю сигнатуры..."),
        (26, "Подготавливаю ссылки для скачивания..."),
        (28, "Формирую запрос к CDN..."),
        (29, "Информация получена! Перехожу к загрузке..."),
    ]
    
    current_msg_index = 0
    
    for step in range(1, stage1_total_steps + 1):
        if user_id in user_downloads and user_downloads[user_id].is_set():
            await update_progress(1, "Отменено", (step / stage1_total_steps) * 30, "🛑 Загрузка отменена")
            return
        
        percent = 2 + (step / stage1_total_steps) * 28
        
        while current_msg_index < len(stage1_messages) and percent >= stage1_messages[current_msg_index][0]:
            current_msg_index += 1
        
        if current_msg_index > 0:
            current_msg = stage1_messages[current_msg_index - 1][1]
        else:
            current_msg = "Инициализация загрузки..."
        
        await update_progress(1, "Получение информации", percent, current_msg)
        await asyncio.sleep(stage1_delay)
    
    await update_progress(1, "Информация получена", 30, "Запускаю скачивание...")
    await asyncio.sleep(0.5)
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    try:
        loop = asyncio.get_event_loop()
        
        # ============================================================
        # ЭТАП 2: СКАЧИВАНИЕ (30% → 70%)
        # ============================================================
        await update_progress(2, "Скачивание видео", 30, "Устанавливаю соединение с сервером...")
        
        def download_progress_callback(percent, speed, eta):
            mapped_percent = 30 + (percent * 40 / 100)
            
            extra = ""
            if percent < 10:
                extra = "Устанавливаю соединение с CDN..."
            elif percent < 30:
                extra = "Загружаю видеопоток..."
            elif percent < 60:
                extra = "Загружаю аудиопоток..."
            elif percent < 90:
                extra = "Объединяю видео и аудио..."
            else:
                extra = "Завершаю загрузку файла..."
            
            asyncio.create_task(
                update_progress(2, "Скачивание", mapped_percent, extra, speed, eta)
            )
        
        download_task = loop.run_in_executor(
            None, download_video, url, platform, quality, download_progress_callback, cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            await update_progress(2, "Таймаут", 30, "⏰ Загрузка заняла слишком много времени")
            await asyncio.sleep(2)
            await event.edit("⏰ **Таймаут загрузки**\nПопробуйте другое качество")
            return
        
        if cancel_event.is_set() or video_info is None:
            await event.edit("🛑 **Загрузка отменена**")
            return
        
        if video_info.get('too_short'):
            duration = video_info.get('duration', 0)
            minutes, secs = divmod(duration, 60)
            await update_progress(2, "Видео пропущено", 70, f"⏱ Слишком короткое ({minutes}:{secs:02d})")
            await asyncio.sleep(2)
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
        video_title = video_info.get('fulltitle', video_info['title'])
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            await update_progress(2, "Ошибка", 70, f"❌ Файл {file_size_mb:.1f} MB превышает лимит")
            await asyncio.sleep(2)
            await event.edit(f"❌ **Слишком большой файл:** {file_size_mb:.1f} MB\nМаксимум: {MAX_FILE_SIZE_MB} MB")
            try:
                if os.path.exists(file_path): os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
            except: pass
            return
        
        # ============================================================
        # ЭТАП 3: ОТПРАВКА (70% → 100%)
        # ============================================================
        await update_progress(3, "Отправка в Telegram", 70, "Сохраняю в хранилище...", 
                            title=video_title)
        
        caption = format_caption(video_info, platform)
        
        # Сохраняем в хранилище
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
                        # TikTok: без превью, вертикальное видео
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
                        # YouTube: с превью
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
        
        # Отправляем пользователю
        if storage_message:
            await client.forward_messages(
                event.chat_id, 
                storage_message.id, 
                from_peer=storage_chat_id,
            )
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
                    # TikTok: без превью
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
                    # YouTube: с превью
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
        
        await update_progress(3, "Завершение", 95, "Сохраняю в базу данных...", title=video_title)
        
        # Сохраняем в БД (только если есть video_id)
        if storage_message and video_id:
            save_complete_info_with_storage(
                video_id=video_id, platform=platform, quality=quality,
                info=video_info.get('full_info', video_info),
                storage_chat_id=storage_chat_id,
                storage_message_id=storage_message.id,
                file_size_mb=file_size_mb,
            )
            logger.info(f"✅ Сохранено в БД: {video_title[:50]}... [{quality}]")
        elif storage_message and not video_id:
            logger.warning(f"⚠️ Не сохранено в БД: video_id=None для {platform}")
        
        total_time = (datetime.now() - start_time).total_seconds()
        await update_progress(3, "Готово! ✅", 100, 
                            f"{file_size_mb:.1f} MB за {total_time:.0f}с | {quality_config['description']}",
                            title=video_title)
        await asyncio.sleep(2)
        await event.delete()
        
        try:
            if os.path.exists(file_path): os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        except: pass
        
        try:
            full_info = video_info.get('full_info', {})
            channel_id_db = full_info.get('channel_id', '')
            if channel_id_db:
                await notify_subscribers(channel_id_db, video_title, url)
        except:
            pass
        
        logger.info(f"✅ УСПЕШНО: {video_title[:50]}... | {file_size_mb:.1f}MB | {quality} | {total_time:.1f}s")
    
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
        "3️⃣ Выбираешь — я качаю\n"
        "4️⃣ Сохраняю в хранилище и кэширую\n\n"
        f"📺 **YouTube:** 360p | 480p | 720p | 1080p | MP3\n"
        f"🎵 **TikTok:** Видео со звуком | MP3\n"
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
    new_videos = await check_all_channels(
        bot_client=client,
        send_notifications=True,
        notify_callback=notify_subscribers
    )
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
    channel_id = None
    channel_name = "Неизвестный канал"
    
    if 'youtube.com/' in channel_input or 'youtu.be/' in channel_input:
        try:
            info = get_video_info(channel_input, 'youtube')
            if info:
                channel_id = info.get('channel_id', '')
                channel_name = info.get('channel', '') or info.get('uploader', 'Неизвестный')
        except:
            pass
    
    if not channel_id and channel_input.startswith('UC'):
        channel_id = channel_input
    
    if not channel_id:
        await event.reply("❌ **Не удалось определить ID канала**\nОтправьте ссылку на видео с этого канала сначала.")
        return
    
    subscribe_to_channel(user_id, channel_id, channel_name)
    
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
    user_id = event.sender_id
    subs = get_user_subscriptions(user_id)
    
    if not subs:
        await event.reply("📭 **У вас нет подписок**\n\nПодписаться: `/subscribe <channel_id>`")
        return
    
    text = f"📋 **Ваши подписки ({len(subs)}):**\n\n"
    for sub in subs:
        name = sub.get('channel_name_full') or sub.get('channel_name', 'Неизвестный')
        text += f"• **{name}**\n"
        text += f"  ID: `{sub['channel_id']}`\n"
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
    
    # Разные кнопки для разных платформ
    if platform == 'tiktok':
        quality_text += "🎯 **Выберите формат:**"
        buttons = [
            [Button.inline("🎵 Видео (со звуком)", data="quality:360")],
            [Button.inline("🎵 MP3 (аудио)", data="quality:mp3")],
        ]
    else:
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
        asyncio.create_task(monitor_loop(
            bot_client=client,
            interval_minutes=MONITOR_INTERVAL_MINUTES,
            notify_callback=notify_subscribers
        ))
        logger.info(f"🔍 Мониторинг запущен: {len(channels)} каналов, интервал {MONITOR_INTERVAL_MINUTES} мин")
    
    logger.info(f"✅ Бот запущен: @{me.username}")
    
    print()
    print("=" * 60)
    print(f"  🤖 БОТ: @{me.username}")
    print(f"  📺 YouTube: 360p | 480p | 720p | 1080p | MP3")
    print(f"  🎵 TikTok: Видео со звуком | MP3")
    print(f"  🔄 Многопоточность: до {MAX_CONCURRENT_DOWNLOADS} загрузок")
    print(f"  ⏱ Мин. длительность: {MIN_DURATION_SECONDS}с (1.3 мин)")
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
    
    client.loop.run_until_complete(main())    name='MediaBot',
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
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки уведомления {sub['user_id']}: {e}")


# ============================================================
# ОБРАБОТЧИК ЗАГРУЗКИ С ПРОГРЕСС-БАРОМ
# ============================================================

async def process_download(event, user_id, url, platform, video_id, quality):
    """Обрабатывает загрузку с обновлением прогресс-бара в сообщении"""
    
    quality_config = QUALITY_OPTIONS.get(quality, QUALITY_OPTIONS['360'])
    
    logger.info(f"🌐 {platform.upper()} | 📊 {quality_config['description']} | ID: {video_id}")
    
    platform_emoji = "📺" if platform == "youtube" else "🎵"
    platform_name = "YouTube" if platform == "youtube" else "TikTok"
    start_time = datetime.now()
    
    def generate_progress_text(stage: int, stage_name: str, percent: float, 
                               extra_info: str = "", speed: str = "", eta: str = "",
                               title: str = "") -> str:
        bar_length = 20
        filled = int(bar_length * percent / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        elapsed = (datetime.now() - start_time).total_seconds()
        elapsed_str = f"{int(elapsed // 60)}:{int(elapsed % 60):02d}"
        
        stage_emoji = {1: "🔍", 2: "⬇️", 3: "📤"}.get(stage, "✅")
        
        lines = [
            f"{platform_emoji} **{platform_name}** | 📊 {quality_config['description']}",
            f"",
            f"{bar} **{percent:.1f}%**",
            f"",
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
            lines.append(f"")
            lines.append(f"🎬 {title[:80]}")
        
        lines.append(f"")
        lines.append(f"🚫 /cancel для отмены")
        
        return "\n".join(lines)
    
    async def update_progress(stage: int, stage_name: str, percent: float, 
                              extra_info: str = "", speed: str = "", eta: str = "",
                              title: str = ""):
        try:
            text = generate_progress_text(stage, stage_name, percent, extra_info, speed, eta, title)
            await event.edit(text)
        except Exception as e:
            logger.error(f"Ошибка обновления прогресса: {e}")
    
    # ============================================================
    # ЭТАП 1: ПРОВЕРКА КЭША
    # ============================================================
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
            logger.warning(f"⚠️ Ошибка пересылки из кэша: {e}")
    
    # ============================================================
    # ЭТАП 1: ПОЛУЧЕНИЕ ИНФОРМАЦИИ (2% → 30% за ~80 секунд)
    # ============================================================
    stage1_total_steps = 16
    stage1_delay = 5.0
    
    stage1_messages = [
        (3, "Подключаюсь к серверу..."),
        (6, "Загружаю страницу видео..."),
        (9, "Извлекаю метаданные..."),
        (12, "Проверяю доступные форматы..."),
        (15, "Анализирую видеопотоки..."),
        (18, "Получаю информацию о разрешении..."),
        (20, "Проверяю аудиодорожки..."),
        (22, "Определяю оптимальный формат..."),
        (24, "Расшифровываю сигнатуры..."),
        (26, "Подготавливаю ссылки для скачивания..."),
        (28, "Формирую запрос к CDN..."),
        (29, "Информация получена! Перехожу к загрузке..."),
    ]
    
    current_msg_index = 0
    
    for step in range(1, stage1_total_steps + 1):
        if user_id in user_downloads and user_downloads[user_id].is_set():
            await update_progress(1, "Отменено", (step / stage1_total_steps) * 30, "🛑 Загрузка отменена")
            return
        
        percent = 2 + (step / stage1_total_steps) * 28
        
        while current_msg_index < len(stage1_messages) and percent >= stage1_messages[current_msg_index][0]:
            current_msg_index += 1
        
        if current_msg_index > 0:
            current_msg = stage1_messages[current_msg_index - 1][1]
        else:
            current_msg = "Инициализация загрузки..."
        
        await update_progress(1, "Получение информации", percent, current_msg)
        await asyncio.sleep(stage1_delay)
    
    await update_progress(1, "Информация получена", 30, "Запускаю скачивание...")
    await asyncio.sleep(0.5)
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    try:
        loop = asyncio.get_event_loop()
        
        # ============================================================
        # ЭТАП 2: СКАЧИВАНИЕ (30% → 70%)
        # ============================================================
        await update_progress(2, "Скачивание видео", 30, "Устанавливаю соединение с сервером...")
        
        def download_progress_callback(percent, speed, eta):
            mapped_percent = 30 + (percent * 40 / 100)
            
            extra = ""
            if percent < 10:
                extra = "Устанавливаю соединение с CDN..."
            elif percent < 30:
                extra = "Загружаю видеопоток..."
            elif percent < 60:
                extra = "Загружаю аудиопоток..."
            elif percent < 90:
                extra = "Объединяю видео и аудио..."
            else:
                extra = "Завершаю загрузку файла..."
            
            asyncio.create_task(
                update_progress(2, "Скачивание", mapped_percent, extra, speed, eta)
            )
        
        download_task = loop.run_in_executor(
            None, download_video, url, platform, quality, download_progress_callback, cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(download_task, timeout=DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError:
            await update_progress(2, "Таймаут", 30, "⏰ Загрузка заняла слишком много времени")
            await asyncio.sleep(2)
            await event.edit("⏰ **Таймаут загрузки**\nПопробуйте другое качество")
            return
        
        if cancel_event.is_set() or video_info is None:
            await event.edit("🛑 **Загрузка отменена**")
            return
        
        if video_info.get('too_short'):
            duration = video_info.get('duration', 0)
            minutes, secs = divmod(duration, 60)
            await update_progress(2, "Видео пропущено", 70, f"⏱ Слишком короткое ({minutes}:{secs:02d})")
            await asyncio.sleep(2)
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
        video_title = video_info.get('fulltitle', video_info['title'])
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            await update_progress(2, "Ошибка", 70, f"❌ Файл {file_size_mb:.1f} MB превышает лимит")
            await asyncio.sleep(2)
            await event.edit(f"❌ **Слишком большой файл:** {file_size_mb:.1f} MB\nМаксимум: {MAX_FILE_SIZE_MB} MB")
            try:
                if os.path.exists(file_path): os.remove(file_path)
                if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
            except: pass
            return
        
        # ============================================================
        # ЭТАП 3: ОТПРАВКА (70% → 100%)
        # ============================================================
        await update_progress(3, "Отправка в Telegram", 70, "Сохраняю в хранилище...", 
                            title=video_title)
        
        caption = format_caption(video_info, platform)
        
        # Сохраняем в хранилище
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
                        # TikTok: без превью, правильные размеры
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
                        # YouTube: с превью
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
        
        # Отправляем пользователю
        if storage_message:
            await client.forward_messages(
                event.chat_id, 
                storage_message.id, 
                from_peer=storage_chat_id,
            )
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
                    # TikTok: без превью
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
                    # YouTube: с превью
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
        
        await update_progress(3, "Завершение", 95, "Сохраняю в базу данных...", title=video_title)
        
        if storage_message:
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
            if os.path.exists(file_path): os.remove(file_path)
            if thumb_path and os.path.exists(thumb_path): os.remove(thumb_path)
        except: pass
        
        try:
            full_info = video_info.get('full_info', {})
            channel_id_db = full_info.get('channel_id', '')
            if channel_id_db:
                await notify_subscribers(channel_id_db, video_title, url)
        except:
            pass
        
        logger.info(f"✅ УСПЕШНО: {video_title[:50]}... | {file_size_mb:.1f}MB | {quality} | {total_time:.1f}s")
    
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
        "3️⃣ Выбираешь — я качаю\n"
        "4️⃣ Сохраняю в хранилище и кэширую\n\n"
        f"📺 **YouTube:** 360p | 480p | 720p | 1080p | MP3\n"
        f"🎵 **TikTok:** Видео со звуком | MP3\n"
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
    new_videos = await check_all_channels(
        bot_client=client,
        send_notifications=True,
        notify_callback=notify_subscribers
    )
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
    channel_id = None
    channel_name = "Неизвестный канал"
    
    if 'youtube.com/' in channel_input or 'youtu.be/' in channel_input:
        try:
            info = get_video_info(channel_input, 'youtube')
            if info:
                channel_id = info.get('channel_id', '')
                channel_name = info.get('channel', '') or info.get('uploader', 'Неизвестный')
        except:
            pass
    
    if not channel_id and channel_input.startswith('UC'):
        channel_id = channel_input
    
    if not channel_id:
        await event.reply("❌ **Не удалось определить ID канала**\nОтправьте ссылку на видео с этого канала сначала.")
        return
    
    subscribe_to_channel(user_id, channel_id, channel_name)
    
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
    user_id = event.sender_id
    subs = get_user_subscriptions(user_id)
    
    if not subs:
        await event.reply("📭 **У вас нет подписок**\n\nПодписаться: `/subscribe <channel_id>`")
        return
    
    text = f"📋 **Ваши подписки ({len(subs)}):**\n\n"
    for sub in subs:
        name = sub.get('channel_name_full') or sub.get('channel_name', 'Неизвестный')
        text += f"• **{name}**\n"
        text += f"  ID: `{sub['channel_id']}`\n"
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
    
    # Разные кнопки для разных платформ
    if platform == 'tiktok':
        quality_text += "🎯 **Выберите формат:**"
        buttons = [
            [Button.inline("🎵 Видео (со звуком)", data="quality:360")],
            [Button.inline("🎵 MP3 (аудио)", data="quality:mp3")],
        ]
    else:
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
        asyncio.create_task(monitor_loop(
            bot_client=client,
            interval_minutes=MONITOR_INTERVAL_MINUTES,
            notify_callback=notify_subscribers
        ))
        logger.info(f"🔍 Мониторинг запущен: {len(channels)} каналов, интервал {MONITOR_INTERVAL_MINUTES} мин")
    
    logger.info(f"✅ Бот запущен: @{me.username}")
    
    print()
    print("=" * 60)
    print(f"  🤖 БОТ: @{me.username}")
    print(f"  📺 YouTube: 360p | 480p | 720p | 1080p | MP3")
    print(f"  🎵 TikTok: Видео со звуком | MP3")
    print(f"  🔄 Многопоточность: до {MAX_CONCURRENT_DOWNLOADS} загрузок")
    print(f"  ⏱ Мин. длительность: {MIN_DURATION_SECONDS}с (1.3 мин)")
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
