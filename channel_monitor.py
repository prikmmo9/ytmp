# channel_monitor.py - Мониторинг новых видео на каналах из БД с автоскачиванием
import os
import sqlite3
import asyncio
from typing import List, Dict, Optional
import requests
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import telethon

from logger_config import create_logger

logger = create_logger(
    name='ChannelMonitor',
    level=20,
    detailed=False,
    show_separators=False
).get_logger()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_cache.db')
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
            'media': 'http://search.yahoo.com/mrss/',
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
                
                # Проверяем, не является ли видео Shorts
                is_short = False
                
                # Проверка по длительности через media:group
                media_group = entry.find('media:group', ns)
                if media_group is not None:
                    for content in media_group.findall('media:content', ns):
                        duration = content.get('duration')
                        if duration and int(duration) <= 60:
                            is_short = True
                            break
                
                # Проверка по названию
                if not is_short and title and '#shorts' in title.lower():
                    is_short = True
                
                if is_short:
                    logger.info(f"⏩ Пропущен Shorts: {title[:50]}...")
                    continue
                
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


async def auto_download_video(bot_client, video_id: str, video_url: str, 
                              storage_chat_id: int, quality: str = '360') -> bool:
    """Автоматически скачивает видео в качестве 360p"""
    try:
        from downloader import download_video, MIN_DURATION_SECONDS
        
        loop = asyncio.get_event_loop()
        
        def download_sync():
            return download_video(video_url, quality)
        
        video_info = await loop.run_in_executor(None, download_sync)
        
        if not video_info:
            return False
        
        # Пропускаем короткие видео
        if video_info.get('too_short'):
            logger.info(f"⏩ Пропущено короткое: {video_info.get('title', '')[:50]}")
            return False
        
        duration = video_info.get('duration', 0)
        if duration < MIN_DURATION_SECONDS:
            logger.info(f"⏩ Пропущено ({duration}с): {video_info.get('title', '')[:50]}")
            return False
        
        file_path = video_info.get('file_path')
        if not file_path or not os.path.exists(file_path):
            return False
        
        file_size_mb = video_info['file_size_mb']
        video_title = video_info.get('fulltitle', video_info['title'])
        thumb_path = video_info.get('thumb_path')
        duration = video_info.get('duration', 0)
        
        if storage_chat_id and bot_client:
            try:
                caption = (
                    f"📺 **{video_title}**\n\n"
                    f"👤 **Канал:** {video_info.get('channel', 'Неизвестный')}\n"
                    f"📊 **Качество:** 360p\n"
                    f"🔗 {video_url}"
                )
                
                storage_message = await bot_client.send_file(
                    entity=storage_chat_id,
                    file=file_path,
                    caption=caption,
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
                
                from database import save_complete_info_with_storage
                save_complete_info_with_storage(
                    video_id=video_id,
                    platform='youtube',
                    quality=quality,
                    info=video_info.get('full_info', video_info),
                    storage_chat_id=storage_chat_id,
                    storage_message_id=storage_message.id,
                    file_size_mb=file_size_mb,
                )
                
                logger.info(f"✅ Автоскачано: {video_title[:50]}... [360p]")
                
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                    if thumb_path and os.path.exists(thumb_path):
                        os.remove(thumb_path)
                except:
                    pass
                
                return True
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения: {e}")
        
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка автоскачивания {video_id}: {str(e)[:200]}")
        return False


async def check_all_channels(bot_client=None, send_notifications: bool = True, 
                             notify_callback=None, storage_chat_id: int = None,
                             auto_download: bool = True) -> List[Dict]:
    """Проверяет все каналы на новые видео"""
    channels = get_monitored_channels()
    
    if not channels:
        return []
    
    logger.info(f"🔍 Проверяю {len(channels)} каналов...")
    
    all_new_videos = []
    
    for i, channel in enumerate(channels[:20]):
        channel_id = channel['channel_id']
        channel_name = channel['name']
        
        if i % 2 == 0:
            await asyncio.sleep(0.1)
        
        try:
            new_videos = await check_channel_rss(channel_id)
            
            if new_videos:
                logger.info(f"📺 {channel_name}: +{len(new_videos)} новых видео")
                
                for video in new_videos:
                    add_new_video_to_db(video)
                    
                    if auto_download and bot_client and storage_chat_id:
                        logger.info(f"⬇️ Автоскачиваю: {video['title'][:50]}...")
                        await auto_download_video(
                            bot_client=bot_client,
                            video_id=video['video_id'],
                            video_url=video['url'],
                            storage_chat_id=storage_chat_id,
                            quality='360'
                        )
                    
                    if bot_client and send_notifications and storage_chat_id:
                        try:
                            message = (
                                f"🆕 **Новое видео!**\n\n"
                                f"📺 **{video['title'][:100]}**\n"
                                f"👤 **Канал:** {channel_name}\n"
                                f"🔗 {video['url']}\n"
                                f"📅 {video.get('published', '')[:10]}"
                            )
                            await bot_client.send_message(storage_chat_id, message)
                            await asyncio.sleep(0.1)
                        except:
                            pass
                    
                    if notify_callback:
                        try:
                            await notify_callback(channel_id, video['title'], video['url'])
                            await asyncio.sleep(0.1)
                        except:
                            pass
                
                all_new_videos.extend(new_videos)
        except Exception as e:
            logger.error(f"❌ Ошибка проверки {channel_name}: {str(e)[:100]}")
    
    if all_new_videos:
        logger.info(f"✅ Найдено {len(all_new_videos)} новых видео")
    
    return all_new_videos


async def monitor_loop(bot_client=None, interval_minutes: int = 30, 
                       notify_callback=None):
    """Бесконечный цикл мониторинга"""
    logger.info(f"🔄 Мониторинг запущен (интервал: {interval_minutes} мин, без Shorts)")
    
    await asyncio.sleep(30)
    
    while True:
        try:
            await check_all_channels(
                bot_client=bot_client,
                send_notifications=True,
                notify_callback=notify_callback,
                storage_chat_id=-1001776425232,
                auto_download=True
            )
        except Exception as e:
            logger.error(f"❌ Ошибка мониторинга: {str(e)[:200]}")
        
        await asyncio.sleep(interval_minutes * 60)
