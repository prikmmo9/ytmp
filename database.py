# database.py - Модуль для работы с базой данных
import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

# Путь к БД
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'video_cache.db')


def init_database():
    """Создаёт все таблицы в БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # ============================================================
    # Таблица 1: Каналы (авторы)
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            platform TEXT NOT NULL,
            channel_url TEXT,
            subscriber_count INTEGER DEFAULT 0,
            avatar_url TEXT,
            description TEXT,
            verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # ============================================================
    # Таблица 2: Видео
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT UNIQUE NOT NULL,
            platform TEXT NOT NULL,
            channel_id TEXT,
            title TEXT NOT NULL,
            full_title TEXT,
            description TEXT,
            url TEXT NOT NULL,
            duration INTEGER DEFAULT 0,
            view_count INTEGER DEFAULT 0,
            like_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            thumbnail_url TEXT,
            thumbnail_path TEXT,
            upload_date TEXT,
            age_limit INTEGER DEFAULT 0,
            tags TEXT,
            categories TEXT,
            is_short INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
        )
    ''')
    
    # ============================================================
    # Таблица 3: Качества и Telegram file_id
    # ============================================================
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS video_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            quality TEXT NOT NULL,
            quality_label TEXT,
            file_size_mb REAL,
            width INTEGER,
            height INTEGER,
            format_id TEXT,
            telegram_file_id TEXT NOT NULL,
            telegram_file_unique_id TEXT,
            storage_chat_id INTEGER,
            storage_message_id INTEGER,
            downloads_count INTEGER DEFAULT 1,
            first_downloaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_downloaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(video_id, quality),
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        )
    ''')
    
    # Добавляем столбцы, если их нет (для старых БД)
    try:
        cursor.execute('ALTER TABLE video_files ADD COLUMN storage_chat_id INTEGER')
    except:
        pass
    
    try:
        cursor.execute('ALTER TABLE video_files ADD COLUMN storage_message_id INTEGER')
    except:
        pass
    
    # ============================================================
    # Индексы
    # ============================================================
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_video_files_lookup 
        ON video_files(video_id, quality)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_videos_channel 
        ON videos(channel_id)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_videos_platform 
        ON videos(platform)
    ''')
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_channels_platform 
        ON channels(platform)
    ''')
    
    conn.commit()
    conn.close()
    print("✅ База данных создана/обновлена")


# ============================================================
# Операции с каналами
# ============================================================

def add_or_update_channel(channel_id: str, name: str, platform: str, 
                          channel_url: str = None, subscriber_count: int = 0,
                          avatar_url: str = None, description: str = None,
                          verified: int = 0) -> int:
    """Добавляет или обновляет канал"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO channels (
            channel_id, name, platform, channel_url, subscriber_count,
            avatar_url, description, verified, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (channel_id, name, platform, channel_url, subscriber_count,
          avatar_url, description, verified))
    
    conn.commit()
    channel_pk = cursor.lastrowid
    conn.close()
    
    return channel_pk


def get_channel(channel_id: str) -> Optional[Dict]:
    """Получает информацию о канале"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM channels WHERE channel_id = ?', (channel_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        columns = ['id', 'channel_id', 'name', 'platform', 'channel_url',
                   'subscriber_count', 'avatar_url', 'description', 'verified',
                   'created_at', 'updated_at']
        return dict(zip(columns, row))
    
    return None


# ============================================================
# Операции с видео
# ============================================================

def add_or_update_video(video_id: str, platform: str, title: str, url: str,
                        channel_id: str = None, full_title: str = None,
                        description: str = None, duration: int = 0,
                        view_count: int = 0, like_count: int = 0,
                        comment_count: int = 0, thumbnail_url: str = None,
                        thumbnail_path: str = None, upload_date: str = None,
                        age_limit: int = 0, tags: str = None,
                        categories: str = None, is_short: int = 0) -> int:
    """Добавляет или обновляет информацию о видео"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO videos (
            video_id, platform, channel_id, title, full_title, description,
            url, duration, view_count, like_count, comment_count,
            thumbnail_url, thumbnail_path, upload_date, age_limit,
            tags, categories, is_short, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ''', (video_id, platform, channel_id, title, full_title, description,
          url, duration, view_count, like_count, comment_count,
          thumbnail_url, thumbnail_path, upload_date, age_limit,
          tags, categories, is_short))
    
    conn.commit()
    video_pk = cursor.lastrowid
    conn.close()
    
    return video_pk


def get_video(video_id: str) -> Optional[Dict]:
    """Получает информацию о видео"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT v.*, c.name as channel_name, c.channel_url as channel_url_link
        FROM videos v
        LEFT JOIN channels c ON v.channel_id = c.channel_id
        WHERE v.video_id = ?
    ''', (video_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        columns = ['id', 'video_id', 'platform', 'channel_id', 'title',
                   'full_title', 'description', 'url', 'duration', 'view_count',
                   'like_count', 'comment_count', 'thumbnail_url', 'thumbnail_path',
                   'upload_date', 'age_limit', 'tags', 'categories', 'is_short',
                   'created_at', 'updated_at', 'channel_name', 'channel_url_link']
        return dict(zip(columns, row))
    
    return None


def update_video_stats(video_id: str, view_count: int = None, 
                       like_count: int = None, comment_count: int = None):
    """Обновляет статистику видео"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    updates = []
    values = []
    
    if view_count is not None:
        updates.append("view_count = ?")
        values.append(view_count)
    if like_count is not None:
        updates.append("like_count = ?")
        values.append(like_count)
    if comment_count is not None:
        updates.append("comment_count = ?")
        values.append(comment_count)
    
    if updates:
        updates.append("updated_at = CURRENT_TIMESTAMP")
        values.append(video_id)
        cursor.execute(f'''
            UPDATE videos SET {', '.join(updates)} WHERE video_id = ?
        ''', values)
    
    conn.commit()
    conn.close()


# ============================================================
# Операции с файлами (качествами)
# ============================================================

def save_video_file(video_id: str, quality: str, telegram_file_id: str,
                    quality_label: str = None, file_size_mb: float = 0,
                    width: int = 0, height: int = 0, format_id: str = None,
                    telegram_file_unique_id: str = None,
                    storage_chat_id: int = None,
                    storage_message_id: int = None) -> int:
    """Сохраняет информацию о скачанном и отправленном видеофайле"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO video_files (
            video_id, quality, quality_label, file_size_mb, width, height,
            format_id, telegram_file_id, telegram_file_unique_id,
            storage_chat_id, storage_message_id,
            last_downloaded, downloads_count
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
            COALESCE(
                (SELECT downloads_count + 1 FROM video_files 
                 WHERE video_id = ? AND quality = ?), 1
            )
        )
    ''', (video_id, quality, quality_label, file_size_mb, width, height,
          format_id, telegram_file_id, telegram_file_unique_id,
          storage_chat_id, storage_message_id,
          video_id, quality))
    
    conn.commit()
    file_pk = cursor.lastrowid
    conn.close()
    
    return file_pk


def get_cached_file(video_id: str, quality: str) -> Optional[Dict]:
    """
    Ищет видеофайл в кэше по video_id и качеству.
    Возвращает информацию с storage_chat_id и storage_message_id.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT vf.*, v.title, v.url as video_url, v.platform,
               v.duration, v.view_count, v.like_count, v.comment_count,
               c.name as channel_name
        FROM video_files vf
        JOIN videos v ON vf.video_id = v.video_id
        LEFT JOIN channels c ON v.channel_id = c.channel_id
        WHERE vf.video_id = ? AND vf.quality = ?
    ''', (video_id, quality))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        columns = [
            'id', 'video_id', 'quality', 'quality_label', 'file_size_mb',
            'width', 'height', 'format_id', 'telegram_file_id', 'telegram_file_unique_id',
            'downloads_count', 'first_downloaded', 'last_downloaded', 'created_at',
            'storage_chat_id', 'storage_message_id',
            'title', 'video_url', 'platform', 'duration', 'view_count', 'like_count',
            'comment_count', 'channel_name'
        ]
        result = dict(zip(columns, row))
        result['from_cache'] = True
        return result
    
    return None


def update_downloads_count(video_id: str, quality: str):
    """Увеличивает счётчик скачиваний"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE video_files 
        SET downloads_count = downloads_count + 1,
            last_downloaded = CURRENT_TIMESTAMP
        WHERE video_id = ? AND quality = ?
    ''', (video_id, quality))
    
    conn.commit()
    conn.close()


def get_available_qualities(video_id: str) -> List[str]:
    """Возвращает список качеств, доступных в кэше для видео"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT quality, quality_label, file_size_mb, downloads_count
        FROM video_files 
        WHERE video_id = ?
        ORDER BY downloads_count DESC
    ''', (video_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    return [
        {
            'quality': row[0],
            'label': row[1] or row[0],
            'size_mb': row[2] or 0,
            'downloads': row[3] or 0,
        }
        for row in rows
    ]


# ============================================================
# Полное сохранение с хранилищем
# ============================================================

def save_complete_info_with_storage(video_id: str, platform: str, quality: str,
                                     info: Dict, storage_chat_id: int,
                                     storage_message_id: int, file_size_mb: float = 0):
    """
    Сохраняет полную информацию: канал + видео + файл с привязкой к хранилищу.
    """
    # 1. Сохраняем канал
    channel_id = info.get('channel_id', '') or info.get('uploader_id', '')
    channel_name = info.get('channel', '') or info.get('uploader', 'Неизвестный')
    channel_url = info.get('channel_url', '') or info.get('uploader_url', '')
    
    if channel_id and channel_name:
        add_or_update_channel(
            channel_id=channel_id,
            name=channel_name,
            platform=platform,
            channel_url=channel_url,
        )
    
    # 2. Сохраняем видео
    tags_str = ','.join(info.get('tags', [])) if info.get('tags') else None
    categories_str = ','.join(info.get('categories', [])) if info.get('categories') else None
    
    add_or_update_video(
        video_id=video_id,
        platform=platform,
        title=info.get('title', 'Без названия'),
        url=info.get('url', ''),
        channel_id=channel_id if channel_id else None,
        full_title=info.get('fulltitle', ''),
        description=info.get('description', ''),
        duration=info.get('duration', 0),
        view_count=info.get('view_count', 0),
        like_count=info.get('like_count', 0),
        comment_count=info.get('comment_count', 0),
        thumbnail_url=info.get('thumbnail', ''),
        upload_date=info.get('upload_date', ''),
        age_limit=info.get('age_limit', 0),
        tags=tags_str,
        categories=categories_str,
        is_short=1 if info.get('duration', 0) <= 60 else 0,
    )
    
    # 3. Сохраняем файл с привязкой к хранилищу
    quality_config = {
        '360': '360p', '480': '480p', '720': '720p HD',
        '1080': '1080p Full HD', 'mp3': 'MP3',
    }
    
    save_video_file(
        video_id=video_id,
        quality=quality,
        telegram_file_id=str(storage_message_id),
        quality_label=quality_config.get(quality, quality),
        file_size_mb=file_size_mb,
        width=info.get('width', 0),
        height=info.get('height', 0),
        format_id=info.get('format_id', ''),
        storage_chat_id=storage_chat_id,
        storage_message_id=storage_message_id,
    )


# ============================================================
# Статистика
# ============================================================

def get_stats() -> Dict:
    """Возвращает полную статистику БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM channels')
    total_channels = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM videos')
    total_videos = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM video_files')
    total_files = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(downloads_count) FROM video_files')
    total_downloads = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT SUM(file_size_mb) FROM video_files')
    total_size = cursor.fetchone()[0] or 0
    
    cursor.execute('''
        SELECT v.title, vf.quality_label, vf.downloads_count, v.url
        FROM video_files vf
        JOIN videos v ON vf.video_id = v.video_id
        ORDER BY vf.downloads_count DESC
        LIMIT 10
    ''')
    top_downloads = cursor.fetchall()
    
    cursor.execute('''
        SELECT title, view_count, like_count, url
        FROM videos
        ORDER BY view_count DESC
        LIMIT 10
    ''')
    top_views = cursor.fetchall()
    
    cursor.execute('''
        SELECT platform, COUNT(*) 
        FROM videos 
        GROUP BY platform
    ''')
    platform_stats = cursor.fetchall()
    
    cursor.execute('''
        SELECT quality_label, COUNT(*), SUM(file_size_mb)
        FROM video_files
        GROUP BY quality
    ''')
    quality_stats = cursor.fetchall()
    
    conn.close()
    
    return {
        'total_channels': total_channels,
        'total_videos': total_videos,
        'total_files': total_files,
        'total_downloads': total_downloads,
        'total_size_mb': round(total_size, 1) if total_size else 0,
        'top_downloads': top_downloads,
        'top_views': top_views,
        'platform_stats': platform_stats,
        'quality_stats': quality_stats,
    }


def search_videos(query: str, limit: int = 20) -> List[Dict]:
    """Поиск видео по названию или описанию"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT v.*, c.name as channel_name,
               GROUP_CONCAT(vf.quality_label, ', ') as available_qualities
        FROM videos v
        LEFT JOIN channels c ON v.channel_id = c.channel_id
        LEFT JOIN video_files vf ON v.video_id = vf.video_id
        WHERE v.title LIKE ? OR v.description LIKE ?
        GROUP BY v.video_id
        ORDER BY v.view_count DESC
        LIMIT ?
    ''', (f'%{query}%', f'%{query}%', limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    return rows


# ============================================================
# ТЕСТИРОВАНИЕ
# ============================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  Обновление базы данных")
    print("=" * 60)
    print()
    
    init_database()
    
    print()
    print("✅ Таблицы обновлены (добавлены поля storage_chat_id, storage_message_id)")
    print()
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = cursor.fetchall()
    
    for table in tables:
        table_name = table[0]
        print(f"📋 Таблица: {table_name}")
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        for col in columns:
            print(f"   - {col[1]} ({col[2]})")
        print()
    
    conn.close()
    
    print("✅ Готово!")
