# youtube_tiktok_bot_mvp.py (с поддержкой YouTube и TikTok)
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
DOWNLOAD_TIMEOUT = 600  # 10 минут на скачивание

# Создаем консольный логгер
console_logger = create_logger(
    name='MediaBot',
    level=logging.DEBUG,
    detailed=True,
    show_separators=True
)
logger = console_logger.get_logger()

# Создаем папку для загрузок
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# Клиент Telegram
client = TelegramClient('bot_session', API_ID, API_HASH)

# Отслеживание активных загрузок: user_id -> threading.Event()
user_downloads = {}


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def detect_platform(url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Определяет платформу и очищает URL.
    
    Returns:
        Tuple[platform, clean_url] где platform: 'youtube', 'tiktok', или None
    """
    # Проверяем YouTube
    youtube_patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in youtube_patterns:
        match = re.match(pattern, url)
        if match:
            return 'youtube', f"https://www.youtube.com/watch?v={match.group(1)}"
    
    # Проверяем YouTube ID (просто 11 символов)
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        return 'youtube', f"https://www.youtube.com/watch?v={url.strip()}"
    
    # Проверяем TikTok
    tiktok_patterns = [
        r'(?:https?://)?(?:www\.)?tiktok\.com/@[\w.-]+/video/(\d+)',
        r'(?:https?://)?(?:www\.)?tiktok\.com/t/(\w+)',
        r'(?:https?://)?vm\.tiktok\.com/(\w+)',
        r'(?:https?://)?vt\.tiktok\.com/(\w+)',
    ]
    
    for pattern in tiktok_patterns:
        match = re.match(pattern, url)
        if match:
            # Возвращаем оригинальный URL, yt-dlp сам разберется
            return 'tiktok', url
    
    return None, None


def download_video_sync(url: str, platform: str, cancel_event: threading.Event) -> Optional[dict]:
    """
    Синхронная функция скачивания видео (запускается в отдельном потоке).
    
    Args:
        url: URL видео
        platform: 'youtube' или 'tiktok'
        cancel_event: Событие для отмены загрузки
    
    Returns:
        dict с информацией о видео или None при ошибке/отмене
    """
    if cancel_event.is_set():
        logger.info("🛑 Загрузка отменена до начала")
        return None
    
    # Общие базовые опции
    base_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
    }
    
    # Опции зависят от платформы
    if platform == 'youtube':
        ydl_opts = {
            **base_opts,
            # Для YouTube: готовый mp4 формат 18 (360p с аудио)
            'format': '18/best[height<=360][ext=mp4]/best[height<=480][ext=mp4]/best[ext=mp4]/best',
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
            # Скачиваем превью как отдельный файл
            'writethumbnail': True,
            'postprocessors': [],
            'prefer_ffmpeg': False,
            'merge_output_format': None,
        }
    elif platform == 'tiktok':
        ydl_opts = {
            **base_opts,
            # Для TikTok: лучшее качество, обычно mp4
            'format': 'best[ext=mp4]/best',
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(uploader)s_%(title).100s_%(id)s.%(ext)s',
            # Скачиваем превью как отдельный файл
            'writethumbnail': True,
            'postprocessors': [],
            'prefer_ffmpeg': False,
            'merge_output_format': None,
            # Специфичные для TikTok
            'extractor_args': {
                'tiktok': {
                    'api_hostname': 'api16-normal-c-useast1a.tiktokv.com',
                }
            },
        }
    else:
        logger.error(f"Неизвестная платформа: {platform}")
        return None
    
    # Шаг 1: Получение информации
    console_logger.step(f"Получение информации о видео ({platform})...", current=1, total=3)
    
    if cancel_event.is_set():
        logger.info("🛑 Загрузка отменена (шаг 1)")
        return None
    
    try:
        # Опции для быстрого получения информации
        info_opts = {
            **ydl_opts,
            'skip_download': True,
            'no_check_formats': True,
            'playlistend': 1,
            'writethumbnail': False,
        }
        
        info_start = time.time()
        
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            if not info:
                logger.error("Не удалось получить информацию о видео")
                return None
            
            info_time = time.time() - info_start
            logger.info(f"⏱ Получение информации заняло: {info_time:.1f}с")
        
        if cancel_event.is_set():
            logger.info("🛑 Загрузка отменена (после получения информации)")
            return None
        
        # Показываем информацию
        if platform == 'youtube':
            console_logger.video_info({
                'title': info.get('title', 'N/A'),
                'uploader': info.get('uploader', 'N/A'),
                'duration': info.get('duration', 0),
                'view_count': info.get('view_count', 0),
                'url': url
            })
        elif platform == 'tiktok':
            logger.info(f"🎵 Платформа: TikTok")
            logger.info(f"🎬 Название: {info.get('title', 'N/A')[:60]}")
            logger.info(f"👤 Автор: {info.get('uploader', 'N/A')}")
            logger.info(f"⏱ Длительность: {info.get('duration', 0)}с")
            logger.info(f"❤️ Лайков: {info.get('like_count', 'N/A')}")
            logger.info(f"💬 Комментариев: {info.get('comment_count', 'N/A')}")
        
        # Шаг 2: Скачивание видео и превью
        console_logger.step("Скачивание видео и превью...", current=2, total=3)
        
        if cancel_event.is_set():
            logger.info("🛑 Загрузка отменена (перед скачиванием)")
            return None
        
        download_start = time.time()
        
        def progress_hook(d):
            if d['status'] == 'downloading':
                try:
                    percent_str = d.get('_percent_str', '0%').strip().replace('%', '')
                    percent = float(percent_str) if percent_str else 0
                    speed = d.get('_speed_str', '')
                    eta = d.get('_eta_str', '')
                    
                    if cancel_event.is_set():
                        logger.info("🛑 Отмена загрузки во время скачивания")
                        raise Exception("DOWNLOAD_CANCELLED")
                    
                    console_logger.download_progress(percent, speed=speed, eta=eta)
                except Exception as e:
                    if str(e) == "DOWNLOAD_CANCELLED":
                        raise
                    pass
        
        ydl_opts['progress_hooks'] = [progress_hook]
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                download_time = time.time() - download_start
                logger.info(f"⏱ Скачивание заняло: {download_time:.1f}с")
        except Exception as e:
            if str(e) == "DOWNLOAD_CANCELLED" or cancel_event.is_set():
                logger.info("🛑 Скачивание прервано пользователем")
                return None
            raise
        
        if cancel_event.is_set():
            logger.info("🛑 Загрузка отменена (после скачивания)")
            file_path = ydl.prepare_filename(info)
            if os.path.exists(file_path):
                os.remove(file_path)
            # Удаляем и превью если есть
            base_path = os.path.splitext(file_path)[0]
            for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                thumb = base_path + ext
                if os.path.exists(thumb):
                    os.remove(thumb)
            return None
        
        # Шаг 3: Проверка файлов
        console_logger.step("Проверка файлов...", current=3, total=3)
        
        file_path = ydl.prepare_filename(info)
        
        # Ищем видео файл если расширение не совпало
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
                    # Ищем видео файл (не превью)
                    video_files = [f for f in possible if not f.endswith(('.jpg', '.jpeg', '.png', '.webp'))]
                    if video_files:
                        file_path = video_files[0]
                    else:
                        file_path = possible[0]
                else:
                    logger.error(f"Файл не найден: {file_path}")
                    return None
        
        # Ищем файл превью
        thumbnail_path = None
        # yt-dlp сохраняет превью с тем же именем но с расширением jpg/webp
        base_path = os.path.splitext(file_path)[0]
        for ext in ['.jpg', '.jpeg', '.png', '.webp']:
            potential_thumb = base_path + ext
            if os.path.exists(potential_thumb):
                thumbnail_path = potential_thumb
                break
        
        # Если превью не найдено, ищем в папке по ID
        if not thumbnail_path:
            import glob
            thumb_pattern = f"{DOWNLOAD_FOLDER}/*{info.get('id', '')}*.jpg"
            possible_thumbs = glob.glob(thumb_pattern)
            if possible_thumbs:
                thumbnail_path = possible_thumbs[0]
        
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        logger.info(f"📁 Видео: {os.path.basename(file_path)} | Размер: {file_size_mb:.1f} MB")
        if thumbnail_path:
            thumb_size = os.path.getsize(thumbnail_path) / 1024
            logger.info(f"🖼 Превью: {os.path.basename(thumbnail_path)} ({thumb_size:.1f} KB)")
        else:
            logger.warning("⚠️ Превью не найдено")
        
        # Для TikTok duration может быть float
        duration = info.get('duration', 0)
        if duration:
            duration = int(duration)
        
        return {
            'title': info.get('title', 'Видео'),
            'uploader': info.get('uploader') or 'Неизвестный автор',
            'duration': duration,
            'file_path': file_path,
            'thumbnail_path': thumbnail_path,
            'file_size_mb': file_size_mb,
            'url': url,
            'platform': platform,
            'description': info.get('description', ''),
        }
            
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        logger.error(f"yt-dlp error: {error_msg[:200]}")
        raise
    except Exception as e:
        if str(e) == "DOWNLOAD_CANCELLED":
            logger.info("🛑 Загрузка отменена пользователем")
            return None
        logger.error(f"Ошибка при скачивании: {str(e)[:200]}")
        raise


# ============================================================
# ОБРАБОТЧИКИ КОМАНД
# ============================================================

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Обработчик команды /start"""
    user_id = event.sender_id
    
    try:
        sender = await event.get_sender()
        user_name = sender.first_name or f"User {user_id}"
    except:
        user_name = f"User {user_id}"
    
    logger.info(f"📱 /start от {user_name} (ID: {user_id})")
    
    welcome = (
        f"🎬 **Привет, {user_name}!**\n\n"
        "Я - Media Download Bot! 🤖\n\n"
        "**Что я умею:**\n"
        "• Скачиваю видео с **YouTube** в качестве 360p\n"
        "• Скачиваю видео с **TikTok** в лучшем качестве\n"
        "• 🖼 **Добавляю превью к видео в Telegram**\n\n"
        "**Как использовать:**\n"
        "Просто отправь мне ссылку на видео!\n\n"
        "**Поддерживаемые платформы:**\n"
        "📺 **YouTube:**\n"
        "• `https://youtube.com/watch?v=VIDEO_ID`\n"
        "• `https://youtu.be/VIDEO_ID`\n"
        "• `https://youtube.com/shorts/VIDEO_ID`\n"
        "• Просто `VIDEO_ID` (11 символов)\n\n"
        "🎵 **TikTok:**\n"
        "• `https://tiktok.com/@user/video/123456`\n"
        "• `https://vm.tiktok.com/XXXXX`\n"
        "• `https://vt.tiktok.com/XXXXX`\n\n"
        "**Ограничения:**\n"
        "• Макс. размер: 2GB\n"
        "• Только открытые видео\n"
        "• По одной загрузке за раз\n\n"
        "📊 YouTube: 360p | 🎵 TikTok: лучшее качество | 🖼 С превью"
    )
    
    await event.reply(welcome)


@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    """Обработчик команды /help"""
    logger.info(f"📖 /help от пользователя {event.sender_id}")
    
    help_text = (
        "📖 **Справка по использованию**\n\n"
        "1️⃣ Отправьте ссылку на видео\n"
        "2️⃣ Бот определит платформу автоматически\n"
        "3️⃣ Проверит видео и покажет информацию\n"
        "4️⃣ Скачает видео и превью\n"
        "5️⃣ Отправит видео с превью в Telegram\n\n"
        "⚠️ **Важно:**\n"
        "• Загружается только одно видео за раз\n"
        "• Дождитесь окончания текущей загрузки\n"
        "• Не отправляйте новую ссылку пока идет загрузка\n"
        "• Превью добавляется без изменения видео\n\n"
        "**Команды:**\n"
        "/start - Главное меню\n"
        "/help - Эта справка\n"
        "/cancel - Отменить текущую загрузку"
    )
    
    await event.reply(help_text)


@client.on(events.NewMessage(pattern='/cancel'))
async def cancel_handler(event):
    """Отмена текущей загрузки"""
    user_id = event.sender_id
    
    if user_id in user_downloads:
        user_downloads[user_id].set()
        await event.reply(
            "🛑 **Отмена загрузки...**\n\n"
            "⏳ Завершаю текущие операции и очищаю ресурсы.\n"
            "Пожалуйста, подождите несколько секунд."
        )
        logger.info(f"🛑 Пользователь {user_id} запросил отмену загрузки")
    else:
        await event.reply("ℹ️ У вас нет активных загрузок для отмены")


# ============================================================
# ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ
# ============================================================

@client.on(events.NewMessage)
async def message_handler(event):
    """Обрабатывает все входящие сообщения"""
    text = event.text.strip() if event.text else ""
    user_id = event.sender_id
    chat_id = event.chat_id
    
    if text.startswith('/'):
        return
    
    # Определяем платформу и очищаем URL
    platform, clean_url = detect_platform(text)
    
    if not platform:
        return  # Просто игнорируем сообщения без ссылок
    
    # Проверяем, нет ли уже активной загрузки
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        await event.reply(
            "⚠️ **У вас уже есть активная загрузка!**\n\n"
            "Дождитесь её завершения или отмените командой /cancel"
        )
        logger.warning(f"⚠️ Пользователь {user_id} пытается начать новую загрузку")
        return
    
    platform_emoji = "📺" if platform == "youtube" else "🎵"
    platform_name = "YouTube" if platform == "youtube" else "TikTok"
    
    console_logger.separator(f"НОВЫЙ ЗАПРОС от {user_id}")
    logger.info(f"{platform_emoji} Платформа: {platform_name}")
    logger.info(f"🔗 URL: {clean_url}")
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    status_msg = await event.reply(
        f"{platform_emoji} **Начинаю загрузку видео...**\n"
        f"🌐 Платформа: **{platform_name}**\n"
        "🔍 Проверяю доступность...\n"
        "🖼 Скачаю превью для видео\n"
        "⏳ Пожалуйста, подождите... (отмена: /cancel)"
    )
    
    start_time = datetime.now()
    
    try:
        loop = asyncio.get_event_loop()
        
        download_task = loop.run_in_executor(
            None, 
            download_video_sync, 
            clean_url, 
            platform,
            cancel_event
        )
        
        try:
            video_info = await asyncio.wait_for(
                download_task,
                timeout=DOWNLOAD_TIMEOUT
            )
        except asyncio.TimeoutError:
            await status_msg.edit(
                "⏰ **Превышено время ожидания**\n\n"
                "Загрузка заняла более 10 минут и была отменена.\n"
                "Попробуйте другое видео или проверьте скорость интернета."
            )
            logger.error(f"⏰ Таймаут загрузки для {clean_url}")
            return
        
        if cancel_event.is_set() or video_info is None:
            await status_msg.edit(
                "🛑 **Загрузка отменена**\n\n"
                "Все временные файлы удалены."
            )
            logger.info(f"🛑 Загрузка отменена для {user_id}")
            return
        
        file_path = video_info['file_path']
        thumbnail_path = video_info.get('thumbnail_path')
        file_size_mb = video_info['file_size_mb']
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            await status_msg.edit(
                f"❌ **Файл слишком большой для Telegram**\n\n"
                f"📊 Размер: **{file_size_mb:.1f} MB**\n"
                f"🚫 Лимит: **{MAX_FILE_SIZE_MB} MB**"
            )
            if os.path.exists(file_path):
                os.remove(file_path)
            if thumbnail_path and os.path.exists(thumbnail_path):
                os.remove(thumbnail_path)
            return
        
        await status_msg.edit(
            f"✅ **Видео скачано!** ({file_size_mb:.1f} MB)\n"
            f"{'🖼 Превью готово' if thumbnail_path else '⚠️ Без превью'}\n"
            f"📤 Отправляю вам файл..."
        )
        
        console_logger.start_operation(
            "Отправка видео",
            size=f"{file_size_mb:.1f} MB",
            chat=chat_id
        )
        
        # Формируем подпись в зависимости от платформы
        duration = video_info.get('duration', 0)
        if platform == 'youtube' and duration > 0:
            minutes, secs = divmod(int(duration), 60)
            duration_str = f"{minutes}:{secs:02d}"
        elif duration > 0:
            duration_str = f"{int(duration)}с"
        else:
            duration_str = "Неизвестно"
        
        if platform == 'youtube':
            caption = (
                f"📺 **{video_info['title']}**\n\n"
                f"👤 **Канал:** {video_info['uploader']}\n"
                f"⏱ **Длительность:** {duration_str}\n"
                f"💾 **Размер:** {file_size_mb:.1f} MB\n"
                f"📊 **Качество:** 360p\n"
                f"🖼 **Превью:** {'есть' if thumbnail_path else 'нет'}\n"
                f"🔗 {video_info['url']}"
            )
        else:  # TikTok
            caption = (
                f"🎵 **{video_info['title']}**\n\n"
                f"👤 **Автор:** @{video_info['uploader']}\n"
                f"⏱ **Длительность:** {duration_str}\n"
                f"💾 **Размер:** {file_size_mb:.1f} MB\n"
                f"🌐 **Платформа:** TikTok\n"
                f"🖼 **Превью:** {'есть' if thumbnail_path else 'нет'}\n"
                f"🔗 {video_info['url']}"
            )
        
        # Отправляем файл с превью (отдельный файл, не вшитый в видео)
        await client.send_file(
            entity=chat_id,
            file=file_path,
            caption=caption,
            supports_streaming=True,
            thumb=thumbnail_path,  # Превью как отдельный файл
            video_note=False
        )
        
        upload_time = (datetime.now() - start_time).total_seconds()
        
        console_logger.end_operation("Отправка видео", success=True)
        
        await status_msg.delete()
        
        logger.info(
            f"✅ УСПЕШНО: {video_info['title'][:50]}... | "
            f"Платформа: {platform_name} | "
            f"Размер: {file_size_mb:.1f}MB | "
            f"Время: {upload_time:.1f}s | "
            f"Превью: {'есть' if thumbnail_path else 'нет'}"
        )
        
        # Очистка временных файлов
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug(f"Удален файл: {file_path}")
            if thumbnail_path and os.path.exists(thumbnail_path):
                os.remove(thumbnail_path)
                logger.debug(f"Удалено превью: {thumbnail_path}")
        except Exception as e:
            logger.warning(f"Не удалось удалить файлы: {e}")
        
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        
        # Обработка ошибок для разных платформ
        if 'Video unavailable' in error_msg:
            error_text = "❌ **Видео недоступно**\n\n📌 Возможно, оно удалено или является приватным"
        elif 'Private video' in error_msg:
            error_text = "❌ **Приватное видео**\n\n🔒 Доступно только по приглашению"
        elif 'Copyright' in error_msg or 'blocked' in error_msg.lower():
            error_text = "❌ **Видео заблокировано**\n\n©️ Заблокировано правообладателем"
        elif 'age' in error_msg.lower():
            error_text = "❌ **Возрастное ограничение**\n\n🔞 Требуется подтверждение возраста"
        elif 'TikTok' in error_msg or 'tiktok' in error_msg.lower():
            error_text = f"❌ **Ошибка TikTok**\n\n```{error_msg[:200]}```"
        else:
            error_text = f"❌ **Ошибка при скачивании**\n\n```{error_msg[:200]}```"
        
        if cancel_event.is_set():
            error_text = "🛑 **Загрузка отменена**\n\nВсе временные файлы удалены."
        else:
            await status_msg.edit(error_text)
            logger.error(f"❌ ОШИБКА: {error_msg[:100]}...")
        
    except Exception as e:
        if not cancel_event.is_set():
            logger.error(f"💥 НЕОЖИДАННАЯ ОШИБКА: {str(e)[:200]}")
            await status_msg.edit(f"❌ **Произошла ошибка**\n\n```{str(e)[:200]}```")
    
    finally:
        if user_id in user_downloads:
            del user_downloads[user_id]
        
        try:
            if cancel_event.is_set():
                await status_msg.edit("🛑 **Загрузка отменена**\n\nВсе временные файлы успешно удалены.")
        except:
            pass


# ============================================================
# ЗАПУСК БОТА
# ============================================================

async def main():
    """Главная функция"""
    
    console_logger.separator("ЗАПУСК БОТА", char="=")
    
    logger.info(f"📁 Папка загрузок: {os.path.abspath(DOWNLOAD_FOLDER)}")
    logger.info(f"📺 YouTube: 360p (готовый mp4)")
    logger.info(f"🎵 TikTok: лучшее качество")
    logger.info(f"🖼 Превью: скачивается отдельно для Telegram")
    logger.info(f"📦 Макс. размер: {MAX_FILE_SIZE_MB} MB")
    logger.info(f"⏱ Таймаут загрузки: {DOWNLOAD_TIMEOUT}s")
    logger.info(f"🔧 yt-dlp версия: {yt_dlp.version.__version__}")
    logger.info(f"⚡ Без постобработки (быстрая загрузка)")
    
    try:
        test_file = os.path.join(DOWNLOAD_FOLDER, '.write_test')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        logger.info("✅ Права на запись: OK")
    except Exception as e:
        logger.error(f"❌ Ошибка прав доступа: {e}")
    
    console_logger.separator()
    
    try:
        await client.start(bot_token=BOT_TOKEN)
        me = await client.get_me()
        
        logger.info(f"✅ Бот запущен: @{me.username} (ID: {me.id})")
        
        print()
        print("=" * 60)
        print(f"  🤖 БОТ ЗАПУЩЕН: @{me.username}")
        print("=" * 60)
        print(f"  📝 Отправьте ссылку на видео")
        print(f"  📺 YouTube: 360p | 🎵 TikTok: лучшее")
        print(f"  🖼 Превью: скачивается отдельно")
        print(f"  ⏱ Таймаут: 10 мин")
        print(f"  🚫 Отмена: /cancel в любой момент")
        print(f"  ⚡ Без перекодирования")
        print("=" * 60)
        print()
        
        await client.run_until_disconnected()
        
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
        print("\n👋 До свидания!")
    except Exception as e:
        logger.critical(f"💥 Критическая ошибка: {e}", exc_info=True)
        raise
    finally:
        if client.is_connected():
            await client.disconnect()
        logger.info("🛑 Бот завершил работу")


if __name__ == '__main__':
    try:
        import yt_dlp
        import telethon
    except ImportError as e:
        print(f"❌ Отсутствуют зависимости: {e}")
        print("📦 Установите: pip install yt-dlp telethon")
        exit(1)
    
    client.loop.run_until_complete(main())
