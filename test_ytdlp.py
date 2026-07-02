#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы yt-dlp с YouTube
Запуск: 
    python test_ytdlp.py                        # тестовое видео по умолчанию
    python test_ytdlp.py "https://youtube.com/..." # своя ссылка
    python test_ytdlp.py VIDEO_ID               # только ID видео
"""

import os
import sys
import time
import random
from datetime import datetime

def print_separator(title=""):
    print("\n" + "=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)
    else:
        print("=" * 70)

def get_video_url(user_input=None):
    """Получает URL видео из аргументов командной строки"""
    if user_input:
        # Если передан ID (11 символов), преобразуем в URL
        if len(user_input) == 11 and (user_input.isalnum() or '_' in user_input or '-' in user_input):
            return f"https://www.youtube.com/watch?v={user_input}"
        # Если уже ссылка
        return user_input
    # Видео по умолчанию
    return "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

def test_ytdlp_version():
    """Проверяет версию yt-dlp"""
    print_separator("ПРОВЕРКА ВЕРСИИ YT-DLP")
    try:
        import yt_dlp
        print(f"✅ yt-dlp версия: {yt_dlp.version.__version__}")
        return True
    except ImportError:
        print("❌ yt-dlp не установлен!")
        print("   Установите: pip install --upgrade yt-dlp")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def test_cookies_file():
    """Проверяет наличие файла cookies.txt"""
    print_separator("ПРОВЕРКА COOKIES")
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    
    if os.path.exists(cookies_path):
        size = os.path.getsize(cookies_path)
        print(f"✅ Файл cookies.txt найден (размер: {size} байт)")
        
        with open(cookies_path, 'r') as f:
            content = f.read()
            if '.youtube.com' in content:
                print("✅ В cookies есть YouTube записи")
            else:
                print("⚠️ В cookies нет записей для YouTube")
        return cookies_path
    else:
        print("❌ Файл cookies.txt НЕ НАЙДЕН!")
        print("   Без cookies YouTube может блокировать запросы")
        return None

def test_video_info(video_url, use_cookies=True):
    """Тестирует получение информации о видео"""
    print_separator(f"ТЕСТ 1: ПОЛУЧЕНИЕ ИНФОРМАЦИИ")
    print(f"📹 URL: {video_url}")
    print(f"🍪 Использовать cookies: {'Да' if use_cookies else 'Нет'}")
    
    import yt_dlp
    
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'skip_download': True,
    }
    
    if use_cookies and os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path
        print("🍪 Cookies загружены")
    
    try:
        start_time = time.time()
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("⏳ Получение информации...")
            info = ydl.extract_info(video_url, download=False)
            
            elapsed = time.time() - start_time
            
            if info:
                print(f"\n📊 ИНФОРМАЦИЯ О ВИДЕО (за {elapsed:.1f}с):")
                print(f"   Название: {info.get('title', 'N/A')[:80]}")
                print(f"   Канал: {info.get('uploader', 'N/A')}")
                
                duration = info.get('duration', 0)
                if duration:
                    minutes, seconds = divmod(int(duration), 60)
                    print(f"   Длительность: {minutes}:{seconds:02d} ({duration} сек)")
                else:
                    print(f"   Длительность: Неизвестно")
                
                view_count = info.get('view_count', 0)
                if view_count:
                    print(f"   Просмотры: {view_count:,}")
                
                like_count = info.get('like_count', 0)
                if like_count:
                    print(f"   Лайки: {like_count:,}")
                
                print(f"   Видео ID: {info.get('id', 'N/A')}")
                
                # Безопасно получаем доступные качества
                formats = info.get('formats', [])
                available_qualities = set()
                for f in formats:
                    height = f.get('height')
                    if height and isinstance(height, int) and height > 0:
                        available_qualities.add(f"{height}p")
                
                if available_qualities:
                    print(f"   Доступные качества: {', '.join(sorted(available_qualities))}")
                
                print("\n✅ Информация получена успешно!")
                return info
            else:
                print("❌ Не удалось получить информацию")
                return None
                
    except Exception as e:
        error_msg = str(e)
        print(f"❌ ОШИБКА: {error_msg[:200]}")
        
        if "Sign in to confirm" in error_msg:
            print("\n🔧 РЕКОМЕНДАЦИИ:")
            print("   1. Обновите cookies (экспортируйте заново из браузера в режиме инкогнито)")
            print("   2. Подождите 10-15 минут (YouTube блокирует частые запросы)")
            print("   3. Используйте VPN/Proxy")
        elif "Failed to extract any player response" in error_msg:
            print("\n🔧 РЕКОМЕНДАЦИИ:")
            print("   1. Обновите yt-dlp: pip install --upgrade yt-dlp")
            print("   2. Проверьте cookies")
            print("   3. Подождите 10-15 минут")
        
        return None

def test_video_download(video_url, quality='360', use_cookies=True):
    """Тестирует скачивание видео"""
    print_separator(f"ТЕСТ 2: СКАЧИВАНИЕ ВИДЕО ({quality})")
    print(f"📹 URL: {video_url}")
    
    import yt_dlp
    
    DOWNLOAD_FOLDER = 'test_downloads'
    os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
    
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    
    quality_formats = {
        '360': '18',
        '480': '18',
        '720': '22',
        'mp3': '18',
    }
    
    format_id = quality_formats.get(quality, '18')
    is_audio = (quality == 'mp3')
    
    # Добавляем задержку перед скачиванием
    delay = random.uniform(1, 2)
    print(f"⏳ Пауза {delay:.1f} сек перед скачиванием...")
    time.sleep(delay)
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 10,
        'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).50s_%(id)s.%(ext)s',
        'format': format_id,
    }
    
    if use_cookies and os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path
        print("🍪 Cookies загружены")
    
    if is_audio:
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
        ydl_opts['keepvideo'] = False
    
    try:
        start_time = time.time()
        print(f"⏳ Начало скачивания...")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            
            if info:
                elapsed = time.time() - start_time
                
                if is_audio:
                    base_path = ydl.prepare_filename(info)
                    file_path = os.path.splitext(base_path)[0] + '.mp3'
                else:
                    file_path = ydl.prepare_filename(info)
                
                if not os.path.exists(file_path):
                    base = os.path.splitext(file_path)[0]
                    for ext in ['.mp3'] if is_audio else ['.mp4', '.webm', '.mkv']:
                        alt_path = base + ext
                        if os.path.exists(alt_path):
                            file_path = alt_path
                            break
                
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path) / (1024 * 1024)
                    print(f"\n✅ {'MP3' if is_audio else 'ВИДЕО'} СКАЧАНО УСПЕШНО!")
                    print(f"   Файл: {os.path.basename(file_path)}")
                    print(f"   Размер: {file_size:.2f} MB")
                    print(f"   Время: {elapsed:.1f} сек")
                    
                    # Удаляем тестовый файл
                    os.remove(file_path)
                    print(f"   🗑 Тестовый файл удален")
                    return True
                else:
                    print(f"❌ Файл не найден")
                    return False
            else:
                print("❌ Не удалось скачать видео")
                return False
                
    except Exception as e:
        error_msg = str(e)
        print(f"❌ ОШИБКА: {error_msg[:200]}")
        
        if "Sign in to confirm" in error_msg:
            print("\n🔧 Нужно обновить cookies!")
        elif "403" in error_msg:
            print("\n🔧 YouTube блокирует запросы. Попробуйте позже или смените IP.")
        
        return False

def run_full_test(video_url=None):
    """Запускает полное тестирование"""
    print_separator("ТЕСТИРОВАНИЕ YT-DLP")
    print(f"Время начала: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Получаем URL видео
    video_url = get_video_url(video_url)
    print(f"\n📹 Тестируемое видео: {video_url}")
    
    # 1. Проверка версии
    if not test_ytdlp_version():
        return False
    
    # 2. Проверка cookies
    cookies_path = test_cookies_file()
    use_cookies = cookies_path is not None
    
    # 3. Тест получения информации
    info = test_video_info(video_url, use_cookies=use_cookies)
    
    if not info:
        print("\n❌ НЕ УДАЛОСЬ ПОЛУЧИТЬ ИНФОРМАЦИЮ О ВИДЕО")
        print("\n💡 Возможные решения:")
        print("   1. Обновите yt-dlp: pip install --upgrade yt-dlp")
        print("   2. Экспортируйте свежие cookies из браузера в режиме инкогнито")
        print("   3. Попробуйте другое видео")
        return False
    
    # 4. Тест скачивания
    print("\n" + "-" * 70)
    response = input("Хотите проверить скачивание видео? (y/n): ").lower().strip()
    
    download_success = None
    if response == 'y':
        print("\nВыберите качество:")
        print("  1 - 360p")
        print("  2 - 720p")
        print("  3 - MP3")
        choice = input("Ваш выбор (1/2/3): ").strip()
        
        quality_map = {'1': '360', '2': '720', '3': 'mp3'}
        quality = quality_map.get(choice, '360')
        
        download_success = test_video_download(video_url, quality=quality, use_cookies=use_cookies)
    
    # Итоги
    print_separator("ИТОГИ ТЕСТИРОВАНИЯ")
    print(f"✅ yt-dlp версия: OK")
    print(f"{'✅' if cookies_path else '⚠️'} Cookies: {'найдены' if cookies_path else 'не найдены'}")
    print(f"{'✅' if info else '❌'} Получение информации: {'успешно' if info else 'не удалось'}")
    
    if download_success is not None:
        print(f"{'✅' if download_success else '⚠️'} Скачивание: {'успешно' if download_success else 'не удалось'}")
    
    print(f"\n🏁 Тестирование завершено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    return True

if __name__ == '__main__':
    # Получаем URL из аргументов командной строки
    video_url = None
    if len(sys.argv) > 1:
        video_url = sys.argv[1]
        print(f"📹 Использую указанную ссылку: {video_url}")
    
    try:
        success = run_full_test(video_url)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n🛑 Тестирование прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)
