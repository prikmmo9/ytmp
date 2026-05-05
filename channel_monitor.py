# channel_monitor.py - Мониторинг новых видео на каналах из БД
import os
import sqlite3
import asyncio
from typing import List, Dict, Optional
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

from logger_config import create_logger

logger = create_logger(
    name='ChannelMonitor',
    level=20,
    detailed=False,
    show_separators=False
).get_logger()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_cache.db')

# Пул потоков для проверки RSS (чтобы не блокировать event loop)
rss_executor = ThreadPoolExecutor(max_workers=3)


def get_monitored_channels() -> List[Dict]:
    """Получает список YouTube-каналов из БД для мониторинга"""
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


def check_channel_rss_sync(channel_id: str) -> List[Dict]:
    """Проверяет RSS-ленту канала на новые видео (синхронная)"""
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


async def check_channel_rss(channel_id: str) -> List[Dict]:
    """Асинхронная обёртка для проверки RSS"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(rss_executor, check_channel_rss_sync, channel_id)


def is_video_in_db(video_id: str) -> bool:
    """Проверяет, есть ли видео в БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM videos WHERE video_id = ?', (video_id,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def add_new_video_to_db(video_info: Dict):
    """Добавляет новое видео в БД"""
    from database import add_or_update_video
    add_or_update_video(
        video_id=video_info['video_id'],
        platform='youtube',
        title=video_info['title'],
        url=video_info['url'],
        channel_id=video_info['channel_id'],
        upload_date=video_info.get('published', ''),
    )


async def check_all_channels(bot_client=None, send_notifications: bool = True, 
                             notify_callback=None) -> List[Dict]:
    """Проверяет все каналы на новые видео (асинхронно, не блокирует бота)"""
    channels = get_monitored_channels()
    
    if not channels:
        return []
    
    logger.info(f"🔍 Проверяю {len(channels)} каналов...")
    
    all_new_videos = []
    
    for i, channel in enumerate(channels[:20]):
        channel_id = channel['channel_id']
        channel_name = channel['name']
        
        # Даём возможность обработать другие события каждые 2 канала
        if i % 2 == 0:
            await asyncio.sleep(0.1)
        
        try:
            new_videos = await check_channel_rss(channel_id)
            
            if new_videos:
                logger.info(f"📺 {channel_name}: +{len(new_videos)} новых видео")
                
                for video in new_videos:
                    add_new_video_to_db(video)
                    
                    # Отправка уведомлений в хранилище
                    if bot_client and send_notifications:
                        try:
                            message = (
                                f"🆕 **Новое видео!**\n\n"
                                f"📺 **{video['title'][:100]}**\n"
                                f"👤 **Канал:** {channel_name}\n"
                                f"🔗 {video['url']}\n"
                                f"📅 {video.get('published', '')[:10]}"
                            )
                            await bot_client.send_message(-1001776425232, message)
                            await asyncio.sleep(0.1)  # Небольшая пауза между сообщениями
                        except:
                            pass
                    
                    # Уведомление подписчикам
                    if notify_callback:
                        try:
                            await notify_callback(channel_id, video['title'], video['url'])
                            await asyncio.sleep(0.1)
                        except:
                            pass
                
                all_new_videos.extend(new_videos)
        except Exception as e:
            logger.error(f"❌ Ошибка проверки канала {channel_name}: {str(e)[:100]}")
    
    if all_new_videos:
        logger.info(f"✅ Найдено {len(all_new_videos)} новых видео")
    
    return all_new_videos


async def monitor_loop(bot_client=None, interval_minutes: int = 30, 
                       notify_callback=None):
    """Бесконечный цикл мониторинга (не блокирует бота)"""
    logger.info(f"🔄 Мониторинг каналов запущен (интервал: {interval_minutes} мин)")
    
    # Первая проверка через 30 секунд после запуска
    await asyncio.sleep(30)
    
    while True:
        try:
            await check_all_channels(
                bot_client=bot_client,
                send_notifications=True,
                notify_callback=notify_callback
            )
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга: {str(e)[:200]}")
        
        # Ждём следующий интервал (с возможностью прерывания)
        await asyncio.sleep(interval_minutes * 60)
