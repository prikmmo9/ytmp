# channel_monitor.py - Мониторинг новых видео на каналах из БД
import os
import sqlite3
import asyncio
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import requests
import xml.etree.ElementTree as ET

from logger_config import create_logger

logger = create_logger(
    name='ChannelMonitor',
    level=20,
    detailed=False,
    show_separators=False
).get_logger()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_cache.db')

# Как часто проверять (в минутах)
CHECK_INTERVAL_MINUTES = 30

# Максимальное количество каналов для проверки за раз
MAX_CHANNELS_PER_CHECK = 10


def get_monitored_channels() -> List[Dict]:
    """Получает список каналов из БД для мониторинга"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT DISTINCT channel_id, name, platform, channel_url
        FROM channels
        WHERE platform = 'youtube'
        ORDER BY name
    ''')
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            'channel_id': row[0],
            'name': row[1],
            'platform': row[2],
            'channel_url': row[3],
        }
        for row in rows
    ]


def check_channel_rss(channel_id: str) -> List[Dict]:
    """
    Проверяет новые видео через RSS ленту YouTube.
    Возвращает список новых видео.
    """
    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    
    try:
        response = requests.get(rss_url, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        if response.status_code != 200:
            logger.warning(f"⚠️ RSS недоступен для {channel_id}: {response.status_code}")
            return []
        
        # Парсим XML
        root = ET.fromstring(response.content)
        
        # Пространства имён
        ns = {
            'atom': 'http://www.w3.org/2005/Atom',
            'yt': 'http://www.youtube.com/xml/schemas/2015',
            'media': 'http://search.yahoo.com/mrss/',
        }
        
        videos = []
        
        for entry in root.findall('atom:entry', ns):
            video_id_elem = entry.find('yt:videoId', ns)
            title_elem = entry.find('atom:title', ns)
            published_elem = entry.find('atom:published', ns)
            link_elem = entry.find('atom:link', ns)
            
            if video_id_elem is not None:
                video_id = video_id_elem.text
                title = title_elem.text if title_elem is not None else 'Без названия'
                published = published_elem.text if published_elem is not None else ''
                url = f"https://www.youtube.com/watch?v={video_id}"
                
                # Проверяем, есть ли уже в БД
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
        logger.error(f"❌ Ошибка проверки RSS для {channel_id}: {str(e)[:100]}")
        return []


def is_video_in_db(video_id: str) -> bool:
    """Проверяет, есть ли видео уже в БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM videos WHERE video_id = ?', (video_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def add_new_video_to_db(video_info: Dict):
    """Добавляет информацию о новом видео в БД"""
    from database import add_or_update_video
    
    add_or_update_video(
        video_id=video_info['video_id'],
        platform='youtube',
        title=video_info['title'],
        url=video_info['url'],
        channel_id=video_info['channel_id'],
        upload_date=video_info.get('published', ''),
    )


async def check_all_channels(bot_client=None) -> List[Dict]:
    """
    Проверяет все каналы на новые видео.
    Если передан bot_client, отправляет уведомления.
    """
    channels = get_monitored_channels()
    
    if not channels:
        logger.info("📭 Нет каналов для мониторинга")
        return []
    
    logger.info(f"🔍 Проверяю {len(channels)} каналов...")
    
    all_new_videos = []
    
    for channel in channels[:MAX_CHANNELS_PER_CHECK]:
        channel_id = channel['channel_id']
        channel_name = channel['name']
        
        new_videos = check_channel_rss(channel_id)
        
        if new_videos:
            logger.info(f"📺 {channel_name}: найдено {len(new_videos)} новых видео")
            
            for video in new_videos:
                # Сохраняем в БД
                add_new_video_to_db(video)
                
                # Отправляем уведомление (если есть клиент)
                if bot_client:
                    try:
                        await send_notification(bot_client, video, channel_name)
                    except:
                        pass
            
            all_new_videos.extend(new_videos)
    
    logger.info(f"✅ Проверка завершена. Новых видео: {len(all_new_videos)}")
    return all_new_videos


async def send_notification(bot_client, video: Dict, channel_name: str):
    """Отправляет уведомление о новом видео"""
    # Можно отправить в чат-хранилище или конкретным пользователям
    storage_chat = -1001776425232  # ID чата для уведомлений
    
    message = (
        f"🆕 **Новое видео на канале!**\n\n"
        f"📺 **{video['title']}**\n"
        f"👤 **Канал:** {channel_name}\n"
        f"🔗 {video['url']}\n"
        f"📅 {video.get('published', '')[:10]}"
    )
    
    try:
        await bot_client.send_message(storage_chat, message)
    except:
        pass


async def monitor_loop(bot_client=None):
    """Бесконечный цикл мониторинга"""
    logger.info(f"🔄 Запуск мониторинга каналов (интервал: {CHECK_INTERVAL_MINUTES} мин)")
    
    while True:
        try:
            await check_all_channels(bot_client)
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга: {str(e)[:200]}")
        
        # Ждём следующий интервал
        await asyncio.sleep(CHECK_INTERVAL_MINUTES * 60)


# ============================================================
# Команды для бота
# ============================================================

def register_handlers(client):
    """Регистрирует обработчики команд мониторинга"""
    
    @client.on(events.NewMessage(pattern='/monitor'))
    async def monitor_command(event):
        """Ручная проверка каналов"""
        await event.reply("🔍 **Проверяю каналы на новые видео...**")
        
        new_videos = await check_all_channels(client)
        
        if new_videos:
            text = f"✅ **Найдено {len(new_videos)} новых видео:**\n\n"
            for v in new_videos[:10]:
                text += f"📺 {v['title'][:60]}...\n🔗 {v['url']}\n\n"
        else:
            text = "📭 Новых видео не найдено"
        
        await event.reply(text)
    
    @client.on(events.NewMessage(pattern='/channels'))
    async def channels_list(event):
        """Список отслеживаемых каналов"""
        channels = get_monitored_channels()
        
        if not channels:
            await event.reply("📭 Нет каналов в базе данных")
            return
        
        text = f"📺 **Отслеживаемые каналы ({len(channels)}):**\n\n"
        for ch in channels[:20]:
            text += f"• {ch['name']}\n"
        
        await event.reply(text)


# ============================================================
# ТЕСТ
# ============================================================

if __name__ == '__main__':
    import asyncio
    
    async def test():
        print("Тест мониторинга каналов...")
        videos = await check_all_channels()
        for v in videos:
            print(f"  📺 {v['title'][:60]}...")
    
    asyncio.run(test())
