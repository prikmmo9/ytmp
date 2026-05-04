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


def format_value(value, key='', max_length=500):
    """Форматирует значение для красивого вывода"""
    if value is None or value == '':
        return None
    
    if isinstance(value, (list, tuple)):
        if len(value) == 0:
            return None
        return ', '.join(str(item) for item in value[:10])  # Первые 10 элементов
    
    if isinstance(value, dict):
        if len(value) == 0:
            return None
        items = []
        for k, v in list(value.items())[:10]:
            items.append(f"{k}: {v}")
        return '\n'.join(items)
    
    value_str = str(value)
    if len(value_str) > max_length:
        value_str = value_str[:max_length] + '...'
    
    return value_str


def print_all_video_info(info: dict, platform: str):
    """Выводит ВСЮ информацию о видео в консоль без сокращений"""
    console_logger.separator(f"ПОЛНАЯ ИНФОРМАЦИЯ О ВИДЕО ({platform.upper()})")
    
    # Сначала выводим основные поля в красивом формате
    important_fields = {
        'id': '🆔 ID',
        'title': '🎬 Название',
        'fulltitle': '📝 Полное название',
        'description': '📄 Описание',
        'uploader': '👤 Автор/Канал',
        'uploader_id': '🔢 ID автора',
        'uploader_url': '🔗 URL автора',
        'channel': '📺 Канал',
        'channel_id': '🔢 ID канала',
        'channel_url': '🔗 URL канала',
        'duration': '⏱ Длительность',
        'duration_string': '⏱ Длительность (строка)',
        'view_count': '👁 Просмотры',
        'like_count': '❤️ Лайки',
        'comment_count': '💬 Комментарии',
        'share_count': '🔄 Репосты',
        'average_rating': '⭐ Рейтинг',
        'age_limit': '🔞 Возрастное ограничение',
        'upload_date': '📅 Дата загрузки',
        'release_date': '📅 Дата релиза',
        'categories': '🏷 Категории',
        'tags': '🔖 Теги',
        'webpage_url': '🌐 URL страницы',
        'original_url': '🔗 Оригинальный URL',
        'extractor': '🔧 Экстрактор',
        'extractor_key': '🔑 Ключ экстрактора',
        'format': '📊 Формат',
        'format_id': '🆔 Формата',
        'ext': '📎 Расширение',
        'width': '📐 Ширина',
        'height': '📏 Высота',
        'resolution': '🖥 Разрешение',
        'fps': '🎞 FPS',
        'vcodec': '🎥 Видеокодек',
        'acodec': '🔊 Аудиокодек',
        'abr': '📻 Битрейт аудио',
        'vbr': '🎬 Битрейт видео',
        'tbr': '📡 Общий битрейт',
        'filesize': '💾 Размер файла',
        'filesize_approx': '💾 Примерный размер',
        'live_status': '🔴 Статус трансляции',
        'is_live': '🔴 Прямой эфир',
        'was_live': '🟤 Был в эфире',
        'playlist': '📋 Плейлист',
        'playlist_index': '🔢 Индекс в плейлисте',
        'playlist_count': '📊 Всего в плейлисте',
        'thumbnail': '🖼 Превью',
        'thumbnails': '🖼 Превью (все)',
    }
    
    # Выводим важные поля в красивом формате
    for field, label in important_fields.items():
        if field in info and info[field] is not None:
            value = info[field]
            
            if field == 'duration' and isinstance(value, (int, float)) and value > 0:
                minutes, secs = divmod(int(value), 60)
                hours, minutes = divmod(minutes, 60)
                if hours > 0:
                    logger.info(f"{label}: {hours}:{minutes:02d}:{secs:02d} ({value} сек)")
                else:
                    logger.info(f"{label}: {minutes}:{secs:02d} ({value} сек)")
            
            elif field in ['view_count', 'like_count', 'comment_count', 'share_count']:
                if isinstance(value, (int, float)) and value > 0:
                    logger.info(f"{label}: {value:,}")
                elif value is not None:
                    logger.info(f"{label}: {value}")
            
            elif field in ['filesize', 'filesize_approx']:
                if isinstance(value, (int, float)) and value > 0:
                    size_mb = value / (1024 * 1024)
                    size_gb = size_mb / 1024
                    if size_gb >= 1:
                        logger.info(f"{label}: {size_gb:.2f} GB ({size_mb:.2f} MB, {value:,} байт)")
                    else:
                        logger.info(f"{label}: {size_mb:.2f} MB ({value:,} байт)")
                elif value is not None:
                    logger.info(f"{label}: {value}")
            
            elif field == 'description':
                desc = str(value)
                logger.info(f"{label}:")
                # Выводим описание построчно для читаемости
                for line in desc.split('\n')[:50]:  # Первые 50 строк
                    if line.strip():
                        logger.info(f"  {line[:200]}")
                if len(desc.split('\n')) > 50:
                    logger.info(f"  ... (еще {len(desc.split('\n')) - 50} строк)")
            
            elif field == 'categories' and isinstance(value, list):
                logger.info(f"{label}: {', '.join(str(c) for c in value)}")
            
            elif field == 'tags' and isinstance(value, list):
                tags_str = ', '.join(str(t) for t in value)
                if len(tags_str) > 500:
                    tags_str = tags_str[:500] + '...'
                logger.info(f"{label}: {tags_str}")
            
            elif field == 'thumbnails' and isinstance(value, list):
                logger.info(f"{label}: {len(value)} шт.")
                for i, thumb in enumerate(value[:5]):
                    if isinstance(thumb, dict):
                        logger.info(f"  [{i}] {thumb.get('url', '')[:150]}")
            
            elif field == 'thumbnail' and isinstance(value, str):
                logger.info(f"{label}: {value[:200]}")
            
            else:
                formatted = format_value(value, field, 500)
                if formatted:
                    logger.info(f"{label}: {formatted}")
    
    # Выводим ВСЕ остальные поля, которые не попали в important_fields
    console_logger.separator("ДОПОЛНИТЕЛЬНЫЕ ПОЛЯ")
    printed_fields = set(important_fields.keys())
    
    for key, value in info.items():
        if key not in printed_fields and value is not None:
            formatted = format_value(value, key)
            if formatted:
                logger.info(f"\033[90m{key}:\033[0m {formatted}")
    
    # Выводим форматы если есть
    if 'formats' in info and info['formats']:
        console_logger.separator("ДОСТУПНЫЕ ФОРМАТЫ")
        formats = info['formats']
        logger.info(f"Всего форматов: {len(formats)}")
        
        for i, fmt in enumerate(formats):
            if isinstance(fmt, dict):
                fmt_id = fmt.get('format_id', 'N/A')
                ext = fmt.get('ext', 'N/A')
                resolution = fmt.get('resolution', 'N/A')
                fps = fmt.get('fps', 'N/A')
                vcodec = fmt.get('vcodec', 'N/A')
                acodec = fmt.get('acodec', 'N/A')
                filesize = fmt.get('filesize', 0)
                tbr = fmt.get('tbr', 0)
                format_note = fmt.get('format_note', '')
                
                size_str = ''
                if filesize and filesize > 0:
                    size_mb = filesize / (1024 * 1024)
                    size_str = f" | {size_mb:.1f}MB"
                
                tbr_str = ''
                if tbr and tbr > 0:
                    tbr_str = f" | {tbr:.0f}kbps"
                
                note_str = f" [{format_note}]" if format_note else ""
                
                logger.info(
                    f"  [{i}] \033[36m{fmt_id}\033[0m | "
                    f"\033[33m{ext}\033[0m | "
                    f"{resolution}@{fps}fps{note_str} | "
                    f"v:{vcodec or 'none'} a:{acodec or 'none'}"
                    f"{size_str}{tbr_str}"
                )
    
    # Выводим субтитры если есть
    if 'subtitles' in info and info['subtitles']:
        console_logger.separator("СУБТИТРЫ")
        for lang, subs in info['subtitles'].items():
            logger.info(f"  {lang}: {len(subs)} дорожек")
    
    # Выводим automatic_captions если есть
    if 'automatic_captions' in info and info['automatic_captions']:
        console_logger.separator("АВТОМАТИЧЕСКИЕ СУБТИТРЫ")
        for lang, subs in info['automatic_captions'].items():
            logger.info(f"  {lang}: {len(subs)} дорожек")
    
    console_logger.separator(f"КОНЕЦ ИНФОРМАЦИИ О ВИДЕО ({platform.upper()})")


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
            # Отключаем постобработку для скорости
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
            # TikTok обычно отдает готовые mp4 файлы
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
            'no_check_formats': True,  # КРИТИЧНО: ускоряет проверку
            'playlistend': 1,
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
        
        # Показываем ВСЮ информацию о видео
        print_all_video_info(info, platform)
        
        # Шаг 2: Скачивание
        console_logger.step("Скачивание видео...", current=2, total=3)
        
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
            return None
        
        # Шаг 3: Проверка файла
        console_logger.step("Проверка файла...", current=3, total=3)
        
        file_path = ydl.prepare_filename(info)
        
        # Ищем файл если расширение не совпало
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
                    logger.error(f"Файл не найден: {file_path}")
                    return None
        
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        logger.info(f"📁 Файл: {os.path.basename(file_path)} | Размер: {file_size_mb:.1f} MB")
        
        # Для TikTok duration может быть float
        duration = info.get('duration', 0)
        if duration:
            duration = int(duration)
        
        return {
            'title': info.get('title', 'Видео'),
            'fulltitle': info.get('fulltitle', info.get('title', 'Видео')),
            'uploader': info.get('uploader') or 'Неизвестный автор',
            'uploader_id': info.get('uploader_id', ''),
            'uploader_url': info.get('uploader_url', ''),
            'channel': info.get('channel', ''),
            'channel_id': info.get('channel_id', ''),
            'channel_url': info.get('channel_url', ''),
            'duration': duration,
            'file_path': file_path,
            'file_size_mb': file_size_mb,
            'url': url,
            'platform': platform,
            'description': info.get('description', ''),
            'view_count': info.get('view_count', 0),
            'like_count': info.get('like_count', 0),
            'comment_count': info.get('comment_count', 0),
            'share_count': info.get('share_count', 0),
            'categories': info.get('categories', []),
            'tags': info.get('tags', []),
            'upload_date': info.get('upload_date', ''),
            'release_date': info.get('release_date', ''),
            'age_limit': info.get('age_limit', 0),
            'average_rating': info.get('average_rating', 0),
            'live_status': info.get('live_status', ''),
            'is_live': info.get('is_live', False),
            'was_live': info.get('was_live', False),
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
        "• Показываю ВСЮ информацию о видео!\n\n"
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
        "📊 YouTube: 360p | 🎵 TikTok: лучшее качество"
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
        "3️⃣ Проверит видео и покажет ВСЮ информацию\n"
        "4️⃣ Начнется загрузка\n"
        "5️⃣ Видео отправится вам с подробным описанием\n\n"
        "⚠️ **Важно:**\n"
        "• Загружается только одно видео за раз\n"
        "• Дождитесь окончания текущей загрузки\n"
        "• Не отправляйте новую ссылку пока идет загрузка\n\n"
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
        "🔍 Получаю полную информацию...\n"
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
        file_size_mb = video_info['file_size_mb']
        
        if file_size_mb > MAX_FILE_SIZE_MB:
            await status_msg.edit(
                f"❌ **Файл слишком большой для Telegram**\n\n"
                f"📊 Размер: **{file_size_mb:.1f} MB**\n"
                f"🚫 Лимит: **{MAX_FILE_SIZE_MB} MB**"
            )
            if os.path.exists(file_path):
                os.remove(file_path)
            return
        
        await status_msg.edit(
            f"✅ **Видео скачано!** ({file_size_mb:.1f} MB)\n"
            f"📤 Отправляю вам файл с подробной информацией..."
        )
        
        console_logger.start_operation(
            "Отправка видео",
            size=f"{file_size_mb:.1f} MB",
            chat=chat_id
        )
        
        # Формируем ПОДРОБНУЮ подпись со всей информацией
        duration = video_info.get('duration', 0)
        if duration > 0:
            minutes, secs = divmod(int(duration), 60)
            hours, minutes = divmod(minutes, 60)
            if hours > 0:
                duration_str = f"{hours}:{minutes:02d}:{secs:02d}"
            else:
                duration_str = f"{minutes}:{secs:02d}"
        else:
            duration_str = "Неизвестно"
        
        if platform == 'youtube':
            caption_parts = [f"📺 **{video_info.get('fulltitle', video_info['title'])}**\n"]
            
            if video_info.get('channel'):
                caption_parts.append(f"📺 **Канал:** {video_info['channel']}")
            elif video_info.get('uploader'):
                caption_parts.append(f"👤 **Автор:** {video_info['uploader']}")
            
            if video_info.get('channel_url'):
                caption_parts.append(f"🔗 **URL канала:** {video_info['channel_url']}")
            elif video_info.get('uploader_url'):
                caption_parts.append(f"🔗 **URL автора:** {video_info['uploader_url']}")
            
            caption_parts.append(f"⏱ **Длительность:** {duration_str}")
            
            if video_info.get('view_count'):
                caption_parts.append(f"👁 **Просмотров:** {video_info['view_count']:,}")
            
            if video_info.get('like_count'):
                caption_parts.append(f"👍 **Лайков:** {video_info['like_count']:,}")
            
            if video_info.get('comment_count'):
                caption_parts.append(f"💬 **Комментариев:** {video_info['comment_count']:,}")
            
            if video_info.get('categories'):
                caption_parts.append(f"🏷 **Категории:** {', '.join(video_info['categories'])}")
            
            if video_info.get('tags'):
                tags = video_info['tags'][:5]  # Первые 5 тегов
                caption_parts.append(f"🔖 **Теги:** {', '.join(tags)}")
            
            if video_info.get('upload_date'):
                date = video_info['upload_date']
                formatted_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
                caption_parts.append(f"📅 **Дата загрузки:** {formatted_date}")
            
            if video_info.get('age_limit', 0) > 0:
                caption_parts.append(f"🔞 **Возрастное ограничение:** {video_info['age_limit']}+")
            
            if video_info.get('average_rating'):
                caption_parts.append(f"⭐ **Рейтинг:** {video_info['average_rating']}/5")
            
            if video_info.get('live_status'):
                status_map = {
                    'is_live': '🔴 В эфире',
                    'is_upcoming': '🟡 Предстоит',
                    'was_live': '🟤 Завершен',
                    'not_live': '⚪ Не трансляция'
                }
                caption_parts.append(f"🔴 **Статус:** {status_map.get(video_info['live_status'], video_info['live_status'])}")
            
            caption_parts.extend([
                f"💾 **Размер:** {file_size_mb:.1f} MB",
                f"📊 **Качество:** 360p (YouTube)",
                f"🔗 {video_info['url']}"
            ])
            
        else:  # TikTok
            caption_parts = [f"🎵 **{video_info.get('fulltitle', video_info['title'])}**\n"]
            
            if video_info.get('uploader'):
                caption_parts.append(f"👤 **Автор:** @{video_info['uploader']}")
            
            if video_info.get('uploader_url'):
                caption_parts.append(f"🔗 **Профиль:** {video_info['uploader_url']}")
            
            caption_parts.append(f"⏱ **Длительность:** {duration_str}")
            
            if video_info.get('view_count'):
                caption_parts.append(f"👁 **Просмотров:** {video_info['view_count']:,}")
            
            if video_info.get('like_count'):
                caption_parts.append(f"❤️ **Лайков:** {video_info['like_count']:,}")
            
            if video_info.get('comment_count'):
                caption_parts.append(f"💬 **Комментариев:** {video_info['comment_count']:,}")
            
            if video_info.get('share_count'):
                caption_parts.append(f"🔄 **Репостов:** {video_info['share_count']:,}")
            
            if video_info.get('upload_date'):
                date = video_info['upload_date']
                formatted_date = f"{date[:4]}-{date[4:6]}-{date[6:8]}"
                caption_parts.append(f"📅 **Дата загрузки:** {formatted_date}")
            
            caption_parts.extend([
                f"💾 **Размер:** {file_size_mb:.1f} MB",
                f"🌐 **Платформа:** TikTok",
                f"🔗 {video_info['url']}"
            ])
        
        # Добавляем описание если есть (обрезаем до 500 символов для Telegram)
        description = video_info.get('description', '')
        if description:
            desc_short = description[:500]
            if len(description) > 500:
                desc_short += '...'
            caption_parts.append(f"\n📝 **Описание:**\n{desc_short}")
        
        caption = '\n'.join(caption_parts)
        
        # Обрезаем caption если он слишком длинный для Telegram (лимит 1024 символа)
        if len(caption) > 1000:
            caption = caption[:997] + '...'
        
        # Отправляем файл
        await client.send_file(
            entity=chat_id,
            file=file_path,
            caption=caption,
            supports_streaming=True
        )
        
        upload_time = (datetime.now() - start_time).total_seconds()
        
        console_logger.end_operation("Отправка видео", success=True)
        
        await status_msg.delete()
        
        logger.info(
            f"✅ УСПЕШНО: {video_info['title'][:50]}... | "
            f"Платформа: {platform_name} | "
            f"Размер: {file_size_mb:.1f}MB | "
            f"Время: {upload_time:.1f}s"
        )
        
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            logger.warning(f"Не удалось удалить файл {file_path}: {e}")
        
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
    logger.info(f"📦 Макс. размер: {MAX_FILE_SIZE_MB} MB")
    logger.info(f"⏱ Таймаут загрузки: {DOWNLOAD_TIMEOUT}s")
    logger.info(f"🔧 yt-dlp версия: {yt_dlp.version.__version__}")
    logger.info(f"⚡ Быстрая загрузка (без постобработки)")
    logger.info(f"📋 Вывод ВСЕЙ информации о видео")
    
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
        print(f"  ⏱ Таймаут: 10 мин")
        print(f"  🚫 Отмена: /cancel в любой момент")
        print(f"  ⚡ Быстрая загрузка")
        print(f"  📋 Показывает ВСЮ информацию")
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
