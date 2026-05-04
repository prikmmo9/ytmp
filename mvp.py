# youtube_bot_mvp.py (только консольное логирование)
import os
import asyncio
import re
import logging
from datetime import datetime

import yt_dlp
from telethon import TelegramClient, events

from logger_config import create_logger

# ============================================================
# КОНФИГУРАЦИЯ
# ============================================================
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'your_bot_token_here')
API_ID = int(os.getenv('API_ID', '1234567'))
API_HASH = os.getenv('API_HASH', 'your_api_hash_here')

DOWNLOAD_FOLDER = 'downloads'
MAX_FILE_SIZE_MB = 2000

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


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================

def clean_youtube_url(url: str) -> str | None:
    """Очищает YouTube URL"""
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


async def download_youtube_video(youtube_url: str, user_id: int) -> dict | None:
    """
    Скачивает YouTube видео в качестве 360p.
    """
    
    # Начало операции
    console_logger.start_operation(
        "Скачивание видео",
        url=youtube_url.split('=')[-1],
        user_id=user_id
    )
    
    ydl_opts = {
        'format': 'best[height<=360]/best[height<=480]/best',
        'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).100s_%(id)s.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 120,
        'retries': 10,
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        }
    }
    
    try:
        def sync_download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Шаг 1: Получаем информацию
                console_logger.step("Получение информации о видео...", current=1, total=4)
                
                info = ydl.extract_info(youtube_url, download=False)
                
                # Выводим информацию о видео
                console_logger.video_info({
                    'title': info.get('title', 'N/A'),
                    'uploader': info.get('uploader', 'N/A'),
                    'duration': info.get('duration', 0),
                    'view_count': info.get('view_count', 0),
                    'url': youtube_url
                })
                
                # Шаг 2: Проверяем размер
                console_logger.step("Проверка размера видео...", current=2, total=4)
                
                formats = info.get('formats', [])
                for fmt in formats:
                    height = fmt.get('height', 0) or 0
                    if height <= 360 and fmt.get('filesize'):
                        size_mb = fmt.get('filesize', 0) / (1024 * 1024)
                        console_logger.step(f"Ожидаемый размер: {size_mb:.1f} MB")
                        
                        if size_mb > MAX_FILE_SIZE_MB:
                            logger.warning(f"⚠️ Видео слишком большое: {size_mb:.1f} MB")
                        break
                
                # Шаг 3: Скачиваем
                console_logger.step("Начало загрузки...", current=3, total=4)
                
                # Прогресс-хук
                def progress_hook(d):
                    if d['status'] == 'downloading':
                        try:
                            percent_str = d.get('_percent_str', '0%').strip().replace('%', '')
                            percent = float(percent_str)
                            speed = d.get('_speed_str', 'N/A')
                            eta = d.get('_eta_str', 'N/A')
                            console_logger.download_progress(
                                percent,
                                speed=speed,
                                eta=eta
                            )
                        except:
                            pass
                
                ydl_opts['progress_hooks'] = [progress_hook]
                
                info = ydl.extract_info(youtube_url, download=True)
                file_path = ydl.prepare_filename(info)
                
                # Шаг 4: Проверяем файл
                console_logger.step("Проверка скачанного файла...", current=4, total=4)
                
                if not os.path.exists(file_path):
                    base = os.path.splitext(file_path)[0]
                    for ext in ['.mp4', '.webm', '.mkv', '.flv']:
                        alt_path = base + ext
                        if os.path.exists(alt_path):
                            file_path = alt_path
                            break
                
                return info, file_path
        
        loop = asyncio.get_event_loop()
        info, file_path = await loop.run_in_executor(None, sync_download)
        
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        console_logger.end_operation(
            "Скачивание видео",
            success=True,
            size=f"{file_size_mb:.1f} MB",
            file=os.path.basename(file_path)
        )
        
        return {
            'title': info.get('title', 'Видео'),
            'uploader': info.get('uploader', 'Неизвестный'),
            'duration': info.get('duration', 0),
            'file_path': file_path,
            'file_size_mb': file_size_mb,
            'url': youtube_url
        }
        
    except Exception as e:
        console_logger.end_operation(
            "Скачивание видео",
            success=False,
            error=str(e)[:100]
        )
        console_logger.error_details(e, "при скачивании видео")
        raise


# ============================================================
# ОБРАБОТЧИКИ
# ============================================================

@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    """Приветствие"""
    logger.info(f"📱 /start от пользователя {event.sender_id}")
    
    await event.reply(
        "🎬 **YouTube Download Bot**\n\n"
        "Отправь мне ссылку на YouTube видео, "
        "и я скачаю его в качестве 360p!\n\n"
        "**Поддерживаемые форматы:**\n"
        "• `youtube.com/watch?v=...`\n"
        "• `youtu.be/...`\n"
        "• `youtube.com/shorts/...`\n"
        "• Просто ID видео (11 символов)"
    )


@client.on(events.NewMessage)
async def message_handler(event):
    """Обработка сообщений"""
    text = event.text.strip() if event.text else ""
    user_id = event.sender_id
    chat_id = event.chat_id
    
    if text.startswith('/'):
        return
    
    youtube_url = clean_youtube_url(text)
    
    if not youtube_url:
        return
    
    console_logger.separator(f"НОВЫЙ ЗАПРОС от {user_id}")
    logger.info(f"🔗 YouTube URL: {youtube_url}")
    
    status_msg = await event.reply("⏬ Начинаю загрузку...")
    
    try:
        video_info = await download_youtube_video(youtube_url, user_id)
        
        if not video_info:
            await status_msg.edit("❌ Не удалось скачать видео")
            return
        
        # Отправка
        console_logger.start_operation(
            "Отправка видео",
            size=f"{video_info['file_size_mb']:.1f} MB",
            chat_id=chat_id
        )
        
        if video_info['file_size_mb'] > MAX_FILE_SIZE_MB:
            await status_msg.edit(
                f"❌ Файл слишком большой: {video_info['file_size_mb']:.1f} MB\n"
                f"Лимит Telegram: {MAX_FILE_SIZE_MB} MB"
            )
            os.remove(video_info['file_path'])
            return
        
        caption = (
            f"🎬 **{video_info['title']}**\n"
            f"👤 {video_info['uploader']} | "
            f"💾 {video_info['file_size_mb']:.1f} MB | "
            f"📊 360p\n"
            f"🔗 {video_info['url']}"
        )
        
        await status_msg.edit("📤 Отправляю файл...")
        
        await client.send_file(
            entity=chat_id,
            file=video_info['file_path'],
            caption=caption,
            supports_streaming=True
        )
        
        console_logger.end_operation("Отправка видео", success=True)
        
        await status_msg.delete()
        
        # Удаляем файл
        try:
            os.remove(video_info['file_path'])
            logger.debug(f"🗑️ Файл удален: {video_info['file_path']}")
        except:
            pass
        
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e)
        
        error_text = "❌ **Ошибка при скачивании**\n\n"
        if 'Video unavailable' in error_msg:
            error_text += "📌 Видео недоступно"
        elif 'Private video' in error_msg:
            error_text += "🔒 Приватное видео"
        elif 'age' in error_msg.lower():
            error_text += "🔞 Возрастное ограничение"
        else:
            error_text += f"```{error_msg[:200]}```"
        
        await status_msg.edit(error_text)
        
    except Exception as e:
        logger.error(f"💥 Неожиданная ошибка: {str(e)[:200]}")
        await status_msg.edit(f"❌ Ошибка: {str(e)[:200]}")


# ============================================================
# ЗАПУСК
# ============================================================

async def main():
    console_logger.separator("ЗАПУСК БОТА")
    logger.info(f"📁 Папка загрузок: {DOWNLOAD_FOLDER}")
    logger.info(f"📊 Качество: 360p")
    logger.info(f"📦 Макс. размер: {MAX_FILE_SIZE_MB} MB")
    console_logger.separator()
    
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    
    print(f"\n✅ Бот @{me.username} запущен!")
    print("📝 Отправь ссылку на YouTube видео\n")
    
    await client.run_until_disconnected()


if __name__ == '__main__':
    client.loop.run_until_complete(main())
