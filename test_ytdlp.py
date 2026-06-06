#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы yt-dlp с YouTube
Запуск: python test_ytdlp.py
"""

import os
import sys
import json
from datetime import datetime

def print_separator(title=""):
    print("\n" + "=" * 70)
    if title:
        print(f"  {title}")
        print("=" * 70)
    else:
        print("=" * 70)

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
        
        # Проверяем содержимое
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
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    }
    
    if use_cookies and os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path
        print("🍪 Cookies загружены")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("⏳ Получение информации...")
            info = ydl.extract_info(video_url, download=False)
            
            if info:
                print("\n📊 ИНФОРМАЦИЯ О ВИДЕО:")
                print(f"   Название: {info.get('title', 'N/A')[:80]}")
                print(f"   Канал: {info.get('uploader', 'N/A')}")
                print(f"   Длительность: {info.get('duration', 0)} сек")
                print(f"   Просмотры: {info.get('view_count', 0):,}")
                print(f"   Лайки: {info.get('like_count', 0):,}")
                print(f"   Видео ID: {info.get('id', 'N/A')}")
                
                # Доступные форматы
                formats = info.get('formats', [])
                available_qualities = set()
                for f in formats:
                    height = f.get('height', 0)
                    if height > 0:
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
        
        if "Failed to extract any player response" in error_msg:
            print("\n🔧 РЕКОМЕНДАЦИИ:")
            print("   1. Обновите yt-dlp: pip install --upgrade yt-dlp")
            print("   2. Проверьте cookies (экспортируйте заново)")
            print("   3. Подождите 10-15 минут (YouTube может блокировать)")
            print("   4. Используйте VPN/Proxy")
        elif "HTTP Error 403" in error_msg:
            print("\n🔧 РЕКОМЕНДАЦИИ:")
            print("   1. Обновите cookies (экспортируйте заново из браузера)")
            print("   2. Проверьте, что вы вошли в YouTube в браузере")
        elif "timed out" in error_msg:
            print("\n🔧 РЕКОМЕНДАЦИИ:")
            print("   1. Проверьте интернет-соединение")
            print("   2. Отключите VPN (если используется)")
        
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
        '360': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360]/18',
        '480': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480]/18',
        '720': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]/136+140/18',
        'mp3': 'bestaudio[ext=m4a]/140',
    }
    
    format_str = quality_formats.get(quality, quality_formats['360'])
    is_audio = (quality == 'mp3')
    
    ydl_opts = {
        'quiet': False,
        'no_warnings': False,
        'socket_timeout': 30,
        'retries': 10,
        'fragment_retries': 10,
        'skip_unavailable_fragments': True,
        'outtmpl': f'{DOWNLOAD_FOLDER}/%(title).50s_%(id)s.%(ext)s',
        'format': format_str,
        'extractor_args': {
            'youtube': {
                'player_client': ['ios', 'android'],
                'player_skip': ['webpage', 'configs'],
            }
        },
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'external_downloader': None,
        'concurrent_fragment_downloads': 1,
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
        ydl_opts['merge_output_format'] = None
    else:
        ydl_opts['merge_output_format'] = 'mp4'
    
    try:
        start_time = datetime.now()
        print(f"⏳ Начало скачивания...")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            
            if info:
                elapsed = (datetime.now() - start_time).total_seconds()
                
                # Находим файл
                file_path = ydl.prepare_filename(info)
                if is_audio:
                    file_path = os.path.splitext(file_path)[0] + '.mp3'
                
                if os.path.exists(file_path):
                    file_size = os.path.getsize(file_path) / (1024 * 1024)
                    print(f"\n✅ ВИДЕО СКАЧАНО УСПЕШНО!")
                    print(f"   Файл: {os.path.basename(file_path)}")
                    print(f"   Размер: {file_size:.2f} MB")
                    print(f"   Время: {elapsed:.1f} сек")
                    
                    # Удаляем тестовый файл
                    os.remove(file_path)
                    print(f"   🗑 Тестовый файл удален")
                    return True
                else:
                    print(f"❌ Файл не найден: {file_path}")
                    return False
            else:
                print("❌ Не удалось скачать видео")
                return False
                
    except Exception as e:
        error_msg = str(e)
        print(f"❌ ОШИБКА: {error_msg[:200]}")
        return False

def test_multiple_clients(video_url):
    """Тестирует разные клиенты YouTube"""
    print_separator("ТЕСТ 3: РАЗНЫЕ КЛИЕНТЫ YOUTUBE")
    
    import yt_dlp
    
    clients = [
        ('ios', 'iOS клиент'),
        ('android', 'Android клиент'),
        ('web', 'Web клиент'),
        ('ios,android', 'iOS + Android'),
    ]
    
    cookies_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cookies.txt')
    
    results = []
    
    for client, client_name in clients:
        print(f"\n🔄 Тестируем {client_name}...")
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': 30,
            'skip_download': True,
            'cookiefile': cookies_path if os.path.exists(cookies_path) else None,
            'extractor_args': {
                'youtube': {
                    'player_client': client.split(',') if ',' in client else [client],
                    'player_skip': ['webpage', 'configs'],
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
            }
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                if info and info.get('title'):
                    print(f"   ✅ УСПЕШНО - {info.get('title', '')[:50]}")
                    results.append((client_name, True))
                else:
                    print(f"   ❌ НЕ УДАЛОСЬ")
                    results.append((client_name, False))
        except Exception as e:
            print(f"   ❌ ОШИБКА: {str(e)[:80]}")
            results.append((client_name, False))
    
    print("\n📊 ИТОГИ ТЕСТИРОВАНИЯ КЛИЕНТОВ:")
    for client_name, success in results:
        status = "✅" if success else "❌"
        print(f"   {status} {client_name}")
    
    return any(success for _, success in results)

def run_full_test(video_url=None):
    """Запускает полное тестирование"""
    print_separator("ТЕСТИРОВАНИЕ YT-DLP")
    print(f"Время начала: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Тест 0: Видео по умолчанию
    if not video_url:
        video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Тестовое видео
        print(f"\n📹 Используется тестовое видео: {video_url}")
        print("   (можно указать другой URL при запуске: python test_ytdlp.py https://...)\n")
    
    # 1. Проверка версии
    if not test_ytdlp_version():
        return False
    
    # 2. Проверка cookies
    cookies_path = test_cookies_file()
    
    # 3. Тест получения информации
    use_cookies = cookies_path is not None
    info = test_video_info(video_url, use_cookies=use_cookies)
    
    if not info:
        # Пробуем без cookies
        print("\n🔄 Пробуем без cookies...")
        info = test_video_info(video_url, use_cookies=False)
    
    if not info:
        print("\n❌ НЕ УДАЛОСЬ ПОЛУЧИТЬ ИНФОРМАЦИЮ О ВИДЕО")
        print("\n🔧 ПОЛНЫЕ РЕКОМЕНДАЦИИ:")
        print("   1. Обновите yt-dlp: pip install --upgrade yt-dlp")
        print("   2. Экспортируйте свежие cookies (войдите в YouTube в браузере)")
        print("   3. Подождите 15-30 минут (возможна временная блокировка)")
        print("   4. Используйте VPN/Proxy")
        print("   5. Проверьте интернет-соединение")
        return False
    
    # 4. Тест скачивания (только если информация получена)
    print("\n" + "-" * 70)
    response = input("Хотите проверить скачивание видео? (y/n): ").lower().strip()
    
    if response == 'y':
        download_success = test_video_download(video_url, quality='360', use_cookies=use_cookies)
        if not download_success:
            print("\n⚠️ Скачивание не удалось, но это может быть из-за размера видео или блокировки")
    else:
        download_success = None
    
    # 5. Тест разных клиентов
    print("\n" + "-" * 70)
    response = input("Хотите проверить разные клиенты YouTube? (y/n): ").lower().strip()
    
    if response == 'y':
        clients_success = test_multiple_clients(video_url)
    else:
        clients_success = None
    
    # Итоги
    print_separator("ИТОГИ ТЕСТИРОВАНИЯ")
    print(f"✅ yt-dlp версия: OK")
    print(f"{'✅' if cookies_path else '⚠️'} Cookies: {'найдены' if cookies_path else 'не найдены'}")
    print(f"{'✅' if info else '❌'} Получение информации: {'успешно' if info else 'не удалось'}")
    
    if download_success is not None:
        print(f"{'✅' if download_success else '⚠️'} Скачивание: {'успешно' if download_success else 'не удалось'}")
    
    if clients_success is not None:
        print(f"{'✅' if clients_success else '⚠️'} Клиенты: {'хотя бы один работает' if clients_success else 'ни один не работает'}")
    
    print(f"\n🏁 Тестирование завершено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    return True

if __name__ == '__main__':
    # Получаем URL из аргументов командной строки
    video_url = None
    if len(sys.argv) > 1:
        video_url = sys.argv[1]
    
    try:
        success = run_full_test(video_url)
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n🛑 Тестирование прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)
