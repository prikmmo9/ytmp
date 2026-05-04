# youtube_bot_mvp.py (исправленная версия с быстрым получением информации)
import os
import asyncio
import re
import logging
import threading
import time
from datetime import datetime
from typing import Optional

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
    name='YouTubeBot',
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

def clean_youtube_url(url: str) -> Optional[str]:
    """Очищает YouTube URL от лишних параметров."""
    patterns = [
        r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
        r'(?:https?://)?(?:www\.)?youtube\.com/shorts/([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.match(pattern, url)
        if match:
            return f"https://www.youtube.com/watch?v={match.group(1)}"
    
    if re.match(r'^[a-zA-Z0-9_-]{11}$', url.strip()):
        return f"https://www.youtube.com/watch?v={url.strip()}"
    
    return None


def download_video_sync(youtube_url: str, cancel_event: threading.Event) -> Optional[dict]:
    """
    Синхронная функция скачивания видео (запускается в отдельном потоке).
    """
    if cancel_event.is_set():
        logger.info("🛑 Загрузка отменена до начала")
        return None
    
    # Базовые опции для всех запросов
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
    
    # Опции для БЫСТРОГО получения информации
    info_opts = {
        **base_opts,
        'extract_flat': False,      # НЕ ПЛОСКИЙ, нам нужна инфа о видео
        'skip_download': True,      # Только информация, без скачивания
        'no_color': True,
        
        # КРИТИЧНО: Минимальный парсинг
        'playlistend': 1,           # Не загружать весь плейлист
        'no_check_formats': True,   # Не проверять доступность всех форматов
        'compat_opts': ['no-attach-info-json'],  # Не сохранять info.json
    }
    
    # Шаг 1: Получение информации (ИСПРАВЛЕНО)
    console_logger.step("Получение информации о видео...", current=1, total=3)
    
    if cancel_event.is_set():
        logger.info("🛑 Загрузка отменена (шаг 1)")
        return None
    
    try:
        # Шаг 1: Быстрое получение информации
        info_start = time.time()
        
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            
            if not info:
                logger.error("Не удалось получить информацию о видео")
                return None
            
            info_time = time.time() - info_start
            logger.info(f"⏱ Получение информации заняло: {info_time:.1f}с")
        
        if cancel_event.is_set():
            logger.info("🛑 Загрузка отменена (после получения информации)")
            return None
        
        # Показываем информацию
        console_logger.video_info({
            'title': info.get('title', 'N/A'),
            'uploader': info.get('uploader', 'N/A'),
            'duration': info.get('duration', 0),
            'view_count': info.get('view_count', 0),
            'url': youtube_url
        })
        
        # Шаг 2: Скачивание
        console_logger.step("Скачивание видео...", current=2, total=3)
        
        if cancel_event.is_set():
            logger.info("🛑 Загрузка отменена (перед скачиванием)")
            return None
        
        # Опции для БЫСТРОГО скачивания
        download_opts = {
            **base_opts,
            # Используем готовый mp4 формат 18 (360p с аудио)
            'format': '18/best[height<=360][ext=mp4]/best[height<=480][ext=mp4]/best[ext=mp4]/best',
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
            
            # Отключаем постобработку для скорости
            'postprocessors': [],
            'prefer_ffmpeg': False,
            'merge_output_format': None,
            
            # Не извлекаем ничего лишнего
            'writesubtitles': False,
            'writeautomaticsub': False,
            'writeinfojson': False,
            'writethumbnail': False,
        }
        
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
        
        download_opts['progress_hooks'] = [progress_hook]
        
        try:
            with yt_dlp.YoutubeDL(download_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=True)
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
        
        # Используем тот же ydl для prepare_filename
        file_path = download_opts['outtmpl'].replace('%(title).100s', info.get('title', 'video')[:100])
        file_path = file_path.replace('%(id)s', info.get('id', 'unknown'))
        file_path = file_path.replace('%(ext)s', info.get('ext', 'mp4'))
        
        # Если файл не найден по точному пути, ищем
        if not os.path.exists(file_path):
            import glob
            # Нормализуем имя для поиска
            safe_title = re.sub(r'[^\w\s-]', '', info.get('title', 'video'))[:100]
            pattern = f"{DOWNLOAD_FOLDER}/*{info.get('id', '')}*"
            possible = glob.glob(pattern)
            if possible:
                file_path = possible[0]
            else:
                logger.error(f"Файл не найден: {file_path}")
                return None
        
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        logger.info(f"📁 Файл: {os.path.basename(file_path)} | Размер: {file_size_mb:.1f} MB")
        
        return {
            'title': info.get('title', 'Видео'),
            'uploader': info.get('uploader') or 'Неизвестный канал',
            'duration': info.get('duration', 0),
            'file_path': file_path,
            'file_size_mb': file_size_mb,
            'url': youtube_url
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
        "Я - YouTube Download Bot! 🤖\n\n"
        "**Что я умею:**\n"
        "• Скачиваю видео с YouTube в качестве 360p\n"
        "• Принимаю разные форматы ссылок\n\n"
        "**Как использовать:**\n"
        "Просто отправь мне ссылку на YouTube видео!\n\n"
        "**Поддерживаемые форматы:**\n"
        "• `https://youtube.com/watch?v=VIDEO_ID`\n"
        "• `https://youtu.be/VIDEO_ID`\n"
        "• `https://youtube.com/shorts/VIDEO_ID`\n"
        "• Просто `VIDEO_ID` (11 символов)\n\n"
        "**Ограничения:**\n"
        "• Макс. размер: 2GB\n"
        "• Только открытые видео\n"
        "• По одной загрузке за раз\n\n"
        "📊 Качество: 360p | 📦 Макс. размер: 2GB"
    )
    
    await event.reply(welcome)


@client.on(events.NewMessage(pattern='/help'))
async def help_handler(event):
    """Обработчик команды /help"""
    logger.info(f"📖 /help от пользователя {event.sender_id}")
    
    help_text = (
        "📖 **Справка по использованию**\n\n"
        "1️⃣ Отправьте ссылку на YouTube видео\n"
        "2️⃣ Бот проверит видео и покажет информацию\n"
        "3️⃣ Начнется загрузка в качестве 360p\n"
        "4️⃣ После загрузки видео отправится вам\n\n"
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
    
    youtube_url = clean_youtube_url(text)
    
    if not youtube_url:
        return
    
    if user_id in user_downloads and not user_downloads[user_id].is_set():
        await event.reply(
            "⚠️ **У вас уже есть активная загрузка!**\n\n"
            "Дождитесь её завершения или отмените командой /cancel"
        )
        logger.warning(f"⚠️ Пользователь {user_id} пытается начать новую загрузку")
        return
    
    console_logger.separator(f"НОВЫЙ ЗАПРОС от {user_id}")
    logger.info(f"🔗 YouTube URL: {youtube_url}")
    
    cancel_event = threading.Event()
    user_downloads[user_id] = cancel_event
    
    status_msg = await event.reply(
        "⏬ **Начинаю загрузку видео...**\n"
        "🔍 Проверяю доступность видео...\n"
        "⏳ Пожалуйста, подождите... (отмена: /cancel)"
    )
    
    start_time = datetime.now()
    
    try:
        loop = asyncio.get_event_loop()
        
        download_task = loop.run_in_executor(
            None, 
            download_video_sync, 
            youtube_url, 
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
            logger.error(f"⏰ Таймаут загрузки для {youtube_url}")
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
                f"🚫 Лимит: **{MAX_FILE_SIZE_MB} MB**\n\n"
                f"💡 Попробуйте найти это видео в более низком качестве"
            )
            if os.path.exists(file_path):
                os.remove(file_path)
            return
        
        await status_msg.edit(
            f"✅ **Видео скачано!** ({file_size_mb:.1f} MB)\n"
            f"📤 Отправляю вам файл..."
        )
        
        console_logger.start_operation(
            "Отправка видео",
            size=f"{file_size_mb:.1f} MB",
            chat=chat_id
        )
        
        minutes, secs = divmod(int(video_info['duration']), 60)
        duration_str = f"{minutes}:{secs:02d}" if video_info['duration'] > 0 else "Неизвестно"
        
        caption = (
            f"🎬 **{video_info['title']}**\n\n"
            f"👤 **Канал:** {video_info['uploader']}\n"
            f"⏱ **Длительность:** {duration_str}\n"
            f"💾 **Размер:** {file_size_mb:.1f} MB\n"
            f"📊 **Качество:** 360p\n"
            f"🔗 {video_info['url']}"
        )
        
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
        
        if 'Video unavailable' in error_msg:
            error_text = "❌ **Видео недоступно**\n\n📌 Возможно, оно удалено или является приватным"
        elif 'Private video' in error_msg:
            error_text = "❌ **Приватное видео**\n\n🔒 Доступно только по приглашению"
        elif 'Copyright' in error_msg or 'blocked' in error_msg.lower():
            error_text = "❌ **Видео заблокировано**\n\n©️ Заблокировано правообладателем"
        elif 'age' in error_msg.lower():
            error_text = "❌ **Возрастное ограничение**\n\n🔞 Требуется подтверждение возраста"
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
    logger.info(f"📊 Качество видео: 360p (готовый mp4)")
    logger.info(f"📦 Макс. размер: {MAX_FILE_SIZE_MB} MB")
    logger.info(f"⏱ Таймаут загрузки: {DOWNLOAD_TIMEOUT}s")
    logger.info(f"🔧 yt-dlp версия: {yt_dlp.version.__version__}")
    logger.info(f"⚡ Оптимизация: быстрая проверка + быстрая загрузка")
    
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
        print(f"  📝 Отправьте ссылку на YouTube видео")
        print(f"  📊 Качество: 360p | ⏱ Таймаут: 10 мин")
        print(f"  🚫 Отмена: /cancel в любой момент")
        print(f"  ⚡ Быстрая проверка + быстрая загрузка")
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
