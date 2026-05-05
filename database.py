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
            channel_id TEXT UNIQUE NOT NULL,       -- ID канала (YouTube channel ID / TikTok username)
            name TEXT NOT NULL,                     -- Название канала
            platform TEXT NOT NULL,                 -- 'youtube' или 'tiktok'
            channel_url TEXT,                       -- Ссылка на канал
            subscriber_count INTEGER DEFAULT 0,     -- Количество подписчиков
            avatar_url TEXT,                        -- Ссылка на аватар
            description TEXT,                       -- Описание канала
            verified INTEGER DEFAULT 0,             -- Верифицирован? (0/1)
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
            video_id TEXT UNIQUE NOT NULL,          -- ID видео (YouTube video ID / TikTok video ID)
            platform TEXT NOT NULL,                 -- 'youtube' или 'tiktok'
            channel_id TEXT,                        -- ID канала (ссылка на channels.channel_id)
            title TEXT NOT NULL,                    -- Название видео
            full_title TEXT,                        -- Полное название
            description TEXT,                       -- Описание видео
            url TEXT NOT NULL,                      -- Полная ссылка на видео
            duration INTEGER DEFAULT 0,             -- Длительность в секундах
            view_count INTEGER DEFAULT 0,           -- Просмотры
            like_count INTEGER DEFAULT 0,           -- Лайки
            comment_count INTEGER DEFAULT 0,        -- Комментарии
            thumbnail_url TEXT,                     -- Ссылка на превью (оригинал)
            thumbnail_path TEXT,                    -- Локальный путь к превью (если скачали)
            upload_date TEXT,                       -- Дата загрузки на платформу
            age_limit INTEGER DEFAULT 0,            -- Возрастное ограничение
            tags TEXT,                              -- Теги (через запятую)
            categories TEXT,                        -- Категории (через запятую)
            is_short INTEGER DEFAULT 0,             -- Shorts/Reels? (0/1)
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
            video_id TEXT NOT NULL,                 -- ID видео (ссылка на videos.video_id)
            quality TEXT NOT NULL,                  -- '360', '480', '720', '1080', 'mp3'
            quality_label TEXT,                     -- '360p', '480p', '720p HD', '1080p Full HD', 'MP3'
            file_size_mb REAL,                      -- Размер файла в MB
            width INTEGER,                          -- Ширина видео
            height INTEGER,                         -- Высота видео
            format_id TEXT,                         -- ID формата yt-dlp
            telegram_file_id TEXT NOT NULL,         -- Telegram file_id для пересылки
            telegram_file_unique_id TEXT,           -- Telegram file_unique_id
            downloads_count INTEGER DEFAULT 1,      -- Сколько раз скачали
            first_downloaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_downloaded TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(video_id, quality),
            FOREIGN KEY (video_id) REFERENCES videos(video_id)
        )
    ''')
    
    # ============================================================
    # Индексы для быстрого поиска
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
    print("✅ База данных создана: 3 таблицы (channels, videos, video_files)")


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
    """Обновляет статистику видео (просмотры, лайки, комментарии)"""
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

def get_cached_file(video_id: str, quality: str) -> Optional[Dict]:
    """
    Ищет видеофайл в кэше по video_id и качеству.
    Возвращает информацию для мгновенной пересылки.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT vf.*, v.title, v.url as video_url, v.platform,
               v.duration, v.view_count, v.like_count, v.comment_count,
               v.description, v.upload_date, v.thumbnail_url,
               c.name as channel_name, c.channel_url as channel_url_link
        FROM video_files vf
        JOIN videos v ON vf.video_id = v.video_id
        LEFT JOIN channels c ON v.channel_id = c.channel_id
        WHERE vf.video_id = ? AND vf.quality = ?
    ''', (video_id, quality))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        columns = [
            'file_id', 'video_id', 'quality', 'quality_label', 'file_size_mb',
            'width', 'height', 'format_id', 'telegram_file_id', 'telegram_file_unique_id',
            'downloads_count', 'first_downloaded', 'last_downloaded', 'created_at',
            'title', 'video_url', 'platform', 'duration', 'view_count', 'like_count',
            'comment_count', 'description', 'upload_date', 'thumbnail_url',
            'channel_name', 'channel_url_link'
        ]
        result = dict(zip(columns, row))
        result['from_cache'] = True
        return result
    
    return None


def save_video_file(video_id: str, quality: str, telegram_file_id: str,
                    quality_label: str = None, file_size_mb: float = 0,
                    width: int = 0, height: int = 0, format_id: str = None,
                    telegram_file_unique_id: str = None) -> int:
    """Сохраняет информацию о скачанном и отправленном видеофайле"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT OR REPLACE INTO video_files (
            video_id, quality, quality_label, file_size_mb, width, height,
            format_id, telegram_file_id, telegram_file_unique_id,
            last_downloaded, downloads_count
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP,
            COALESCE(
                (SELECT downloads_count + 1 FROM video_files 
                 WHERE video_id = ? AND quality = ?), 1
            )
        )
    ''', (video_id, quality, quality_label, file_size_mb, width, height,
          format_id, telegram_file_id, telegram_file_unique_id,
          video_id, quality))
    
    conn.commit()
    file_pk = cursor.lastrowid
    conn.close()
    
    return file_pk


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
            'label': row[1],
            'size_mb': row[2],
            'downloads': row[3],
        }
        for row in rows
    ]


# ============================================================
# Статистика
# ============================================================

def get_stats() -> Dict:
    """Возвращает полную статистику БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Каналы
    cursor.execute('SELECT COUNT(*) FROM channels')
    total_channels = cursor.fetchone()[0]
    
    # Видео
    cursor.execute('SELECT COUNT(*) FROM videos')
    total_videos = cursor.fetchone()[0]
    
    # Файлы
    cursor.execute('SELECT COUNT(*) FROM video_files')
    total_files = cursor.fetchone()[0]
    
    # Всего пересылок
    cursor.execute('SELECT SUM(downloads_count) FROM video_files')
    total_downloads = cursor.fetchone()[0] or 0
    
    # Общий размер
    cursor.execute('SELECT SUM(file_size_mb) FROM video_files')
    total_size = cursor.fetchone()[0] or 0
    
    # Топ-10 популярных
    cursor.execute('''
        SELECT v.title, vf.quality_label, vf.downloads_count, v.url
        FROM video_files vf
        JOIN videos v ON vf.video_id = v.video_id
        ORDER BY vf.downloads_count DESC
        LIMIT 10
    ''')
    top_downloads = cursor.fetchall()
    
    # Топ-10 по просмотрам
    cursor.execute('''
        SELECT title, view_count, like_count, url
        FROM videos
        ORDER BY view_count DESC
        LIMIT 10
    ''')
    top_views = cursor.fetchall()
    
    # Распределение по платформам
    cursor.execute('''
        SELECT platform, COUNT(*) 
        FROM videos 
        GROUP BY platform
    ''')
    platform_stats = cursor.fetchall()
    
    # Распределение по качеству
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
        'total_size_mb': total_size,
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


def get_channel_videos(channel_id: str, limit: int = 50) -> List[Dict]:
    """Получает все видео канала"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT v.*, c.name as channel_name
        FROM videos v
        JOIN channels c ON v.channel_id = c.channel_id
        WHERE v.channel_id = ?
        ORDER BY v.view_count DESC
        LIMIT ?
    ''', (channel_id, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    return rows


# ============================================================
# Вспомогательные функции
# ============================================================

def extract_video_id(url: str, platform: str) -> str:
    """Извлекает ID видео из URL"""
    if platform == 'youtube':
        # youtube.com/watch?v=XXXXX
        if 'v=' in url:
            return url.split('v=')[1].split('&')[0]
        # youtu.be/XXXXX
        if 'youtu.be/' in url:
            parts = url.split('youtu.be/')[1]
            return parts.split('?')[0].split('/')[0]
        # youtube.com/shorts/XXXXX
        if '/shorts/' in url:
            parts = url.split('/shorts/')[1]
            return parts.split('?')[0].split('/')[0]
        return url.split('/')[-1]
    elif platform == 'tiktok':
        # tiktok.com/@user/video/XXXXX
        if '/video/' in url:
            parts = url.split('/video/')[1]
            return parts.split('?')[0].split('/')[0]
        # vm.tiktok.com/XXXXX или vt.tiktok.com/XXXXX
        return url.split('/')[-1].split('?')[0]
    return url


def save_complete_info(video_id: str, platform: str, quality: str,
                       info: Dict, telegram_file_id: str,
                       telegram_file_unique_id: str = None,
                       file_path: str = None, file_size_mb: float = 0):
    """
    Сохраняет полную информацию: канал + видео + файл.
    Основная функция для сохранения после скачивания.
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
    
    # 3. Сохраняем файл
    quality_config = {
        '360': '360p',
        '480': '480p',
        '720': '720p HD',
        '1080': '1080p Full HD',
        'mp3': 'MP3',
    }
    
    width = info.get('width', 0)
    height = info.get('height', 0)
    
    save_video_file(
        video_id=video_id,
        quality=quality,
        telegram_file_id=telegram_file_id,
        quality_label=quality_config.get(quality, quality),
        file_size_mb=file_size_mb,
        width=width,
        height=height,
        format_id=info.get('format_id', ''),
        telegram_file_unique_id=telegram_file_unique_id,
    )


# ============================================================
# ТЕСТИРОВАНИЕ
# ============================================================

if __name__ == '__main__':
    print("=" * 60)
    print("  Создание базы данных для Video Cache Bot")
    print("=" * 60)
    print()
    
    init_database()
    
    print()
    print("📊 Таблицы созданы:")
    print("  1. channels    - каналы (авторы)")
    print("  2. videos      - видео (информация)")
    print("  3. video_files - качества и Telegram file_id")
    print()
    
    # Показываем структуру
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
    
    print("✅ База данных готова к использованию!")
    print(f"📁 Файл: {DB_PATH}")
