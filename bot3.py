def save_complete_info_with_storage(video_id: str, platform: str, quality: str,
                                     info: Dict, storage_chat_id: int,
                                     storage_message_id: int, file_size_mb: float = 0):
    """
    Сохраняет полную информацию с привязкой к чату-хранилищу.
    """
    # 1. Канал
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
    
    # 2. Видео
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
    
    # 3. Файл с привязкой к хранилищу
    quality_config = {
        '360': '360p', '480': '480p', '720': '720p HD', 
        '1080': '1080p Full HD', 'mp3': 'MP3',
    }
    
    save_video_file_with_storage(
        video_id=video_id,
        quality=quality,
        telegram_file_id=str(storage_message_id),  # Используем как идентификатор
        quality_label=quality_config.get(quality, quality),
        file_size_mb=file_size_mb,
        width=info.get('width', 0),
        height=info.get('height', 0),
        format_id=info.get('format_id', ''),
        storage_chat_id=storage_chat_id,
        storage_message_id=storage_message_id,
    )


def save_video_file_with_storage(video_id: str, quality: str, telegram_file_id: str,
                                  quality_label: str = None, file_size_mb: float = 0,
                                  width: int = 0, height: int = 0, format_id: str = None,
                                  telegram_file_unique_id: str = None,
                                  storage_chat_id: int = None,
                                  storage_message_id: int = None) -> int:
    """Сохраняет с информацией о хранилище"""
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
    conn.close()
    
    return cursor.lastrowid


def get_cached_file(video_id: str, quality: str) -> Optional[Dict]:
    """
    Ищет видеофайл в кэше. Возвращает информацию с storage_chat_id и storage_message_id.
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
