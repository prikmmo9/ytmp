# logger_config.py
import logging
import sys
from datetime import datetime
from typing import Optional


class ColoredFormatter(logging.Formatter):
    """
    Форматтер с поддержкой ANSI цветов для разных уровней логирования.
    """
    
    # ANSI цвета
    COLORS = {
        'DEBUG': '\033[36m',      # Голубой
        'INFO': '\033[32m',       # Зеленый
        'WARNING': '\033[33m',    # Желтый
        'ERROR': '\033[31m',      # Красный
        'CRITICAL': '\033[35m',   # Фиолетовый
        'RESET': '\033[0m',       # Сброс
        'BOLD': '\033[1m',        # Жирный
        'DIM': '\033[2m',         # Тусклый
    }
    
    # Иконки для уровней логирования
    ICONS = {
        'DEBUG': '🔍',
        'INFO': 'ℹ️',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '💥',
    }
    
    def __init__(self, fmt: str, datefmt: str, detailed: bool = True):
        super().__init__(fmt, datefmt)
        self.detailed = detailed
    
    def format(self, record):
        # Сохраняем оригинальные значения
        original_levelname = record.levelname
        original_msg = record.msg
        
        # Добавляем цвет
        color = self.COLORS.get(record.levelname, '')
        reset = self.COLORS['RESET']
        bold = self.COLORS['BOLD']
        dim = self.COLORS['DIM']
        
        # Добавляем иконку
        icon = self.ICONS.get(record.levelname, '')
        
        # Форматируем уровень
        record.levelname = f"{color}{bold}{record.levelname:<8}{reset}"
        
        # Форматируем имя логгера
        if self.detailed:
            record.name = f"{record.name}"
        
        # Форматируем сообщение с цветом в зависимости от уровня
        if record.levelno >= logging.ERROR:
            record.msg = f"{color}{bold}{record.msg}{reset}"
        elif record.levelno >= logging.WARNING:
            record.msg = f"{color}{record.msg}{reset}"
        
        # Добавляем иконку к сообщению (только для не-DEBUG)
        if icon and record.levelno > logging.DEBUG:
            record.msg = f"{icon} {record.msg}"
        
        # Форматируем время
        formatted = super().format(record)
        
        # Восстанавливаем оригинальные значения
        record.levelname = original_levelname
        record.msg = original_msg
        
        return formatted


class ConsoleLogger:
    """
    Кастомный логгер с красивым консольным выводом.
    
    Features:
    - Цветной вывод для разных уровней
    - Иконки для быстрого визуального восприятия
    - Разделители для важных операций
    - Отображение времени выполнения
    """
    
    def __init__(
        self,
        name: str = 'YouTubeBot',
        level: int = logging.DEBUG,
        detailed: bool = True,
        show_separators: bool = True
    ):
        """
        Args:
            name: Имя логгера
            level: Уровень логирования
            detailed: Показывать детальную информацию (файл, функцию, строку)
            show_separators: Показывать разделители для важных операций
        """
        self.name = name
        self.detailed = detailed
        self.show_separators = show_separators
        self._start_times = {}
        
        # Создаем логгер
        self.logger = logging.getLogger(name)
        self.logger.setLevel(level)
        self.logger.propagate = False
        
        # Удаляем все существующие обработчики
        for handler in self.logger.handlers[:]:
            self.logger.removeHandler(handler)
        
        # Добавляем консольный обработчик
        self._add_console_handler(level)
    
    def _add_console_handler(self, level: int):
        """Добавляет обработчик для вывода в консоль"""
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        
        # Создаем форматтер
        if self.detailed:
            fmt = (
                '%(asctime)s | %(levelname)s | '
                '%(name)s:%(funcName)s:%(lineno)d | '
                '%(message)s'
            )
        else:
            fmt = '%(asctime)s | %(levelname)s | %(message)s'
        
        datefmt = '%H:%M:%S'
        
        formatter = ColoredFormatter(fmt, datefmt, self.detailed)
        console_handler.setFormatter(formatter)
        
        self.logger.addHandler(console_handler)
    
    def get_logger(self) -> logging.Logger:
        """Возвращает настроенный логгер"""
        return self.logger
    
    # ============================================================
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ ДЛЯ КРАСИВОГО ВЫВОДА
    # ============================================================
    
    def separator(self, title: str = "", char: str = "=", length: int = 70):
        """Выводит разделитель"""
        if not self.show_separators:
            return
        
        if title:
            line = f"{char * 3} {title} {char * (length - len(title) - 5)}"
        else:
            line = char * length
        
        self.logger.info(f"\n\033[1;34m{line}\033[0m")
    
    def start_operation(self, operation: str, **context):
        """
        Логирует начало операции.
        
        Usage:
            logger.start_operation("Downloading video", url="youtube.com/...")
        """
        self._start_times[operation] = datetime.now()
        
        context_str = " | ".join(f"\033[90m{k}\033[0m=\033[33m{v}\033[0m" for k, v in context.items())
        
        self.logger.info(f"\033[1m▶️  {operation}\033[0m {context_str}")
    
    def end_operation(self, operation: str, success: bool = True, **context):
        """
        Логирует завершение операции.
        
        Usage:
            logger.end_operation("Downloading video", success=True, size="50MB")
        """
        start_time = self._start_times.pop(operation, datetime.now())
        elapsed = (datetime.now() - start_time).total_seconds()
        
        context_str = " | ".join(f"{k}={v}" for k, v in context.items())
        
        if success:
            icon = "\033[32m✅\033[0m"
            time_str = f"\033[32m{elapsed:.1f}s\033[0m"
        else:
            icon = "\033[31m❌\033[0m"
            time_str = f"\033[31m{elapsed:.1f}s\033[0m"
        
        self.logger.info(f"{icon} \033[1m{operation}\033[0m | ⏱ {time_str} | {context_str}")
    
    def step(self, step: str, current: int = None, total: int = None):
        """Логирует шаг выполнения"""
        if current and total:
            progress = f"[{current}/{total}]"
            self.logger.info(f"  \033[36m{progress}\033[0m {step}")
        else:
            self.logger.info(f"  \033[36m→\033[0m {step}")
    
    def video_info(self, info: dict):
        """Красиво выводит информацию о видео"""
        self.separator("Информация о видео")
        
        title = info.get('title', 'N/A')
        if len(title) > 60:
            title = title[:57] + '...'
        
        self.logger.info(f"\033[1;33m🎬 Название:\033[0m {title}")
        self.logger.info(f"\033[1;33m👤 Канал:\033[0m    {info.get('uploader', 'N/A')}")
        
        duration = info.get('duration', 0)
        if duration:
            minutes, secs = divmod(int(duration), 60)
            self.logger.info(f"\033[1;33m⏱ Длительность:\033[0m {minutes}:{secs:02d}")
        
        views = info.get('view_count', 0)
        if views:
            self.logger.info(f"\033[1;33m👁 Просмотров:\033[0m {views:,}")
        
        self.logger.info(f"\033[1;33m🔗 URL:\033[0m        {info.get('url', 'N/A')}")
    
    def download_progress(self, percent: float, speed: str = "", eta: str = "", 
                          downloaded: str = "", total: str = ""):
        """Выводит прогресс загрузки с баром"""
        bar_length = 30
        filled = int(bar_length * percent / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        
        parts = [f"\r\033[K  📥 [{bar}] {percent:.1f}%"]
        if speed:
            parts.append(f"🚀 {speed}")
        if eta:
            parts.append(f"⏱ {eta}")
        
        print(" | ".join(parts), end="", flush=True)
        
        if percent >= 100:
            print()  # Новая строка после завершения
    
    def error_details(self, error: Exception, context: str = ""):
        """Подробно выводит ошибку"""
        self.logger.error(f"\033[1;31m{'='*70}\033[0m")
        self.logger.error(f"\033[1;31mОШИБКА\033[0m {context}")
        self.logger.error(f"\033[31mТип:\033[0m    {type(error).__name__}")
        self.logger.error(f"\033[31mТекст:\033[0m  {str(error)[:300]}")
        
        import traceback
        tb = traceback.format_exc()
        if len(tb) > 500:
            tb = tb[:497] + '...'
        self.logger.debug(f"\033[31mTraceback:\033[0m\n{tb}")
        self.logger.error(f"\033[1;31m{'='*70}\033[0m")


# ============================================================
# ФУНКЦИЯ ДЛЯ БЫСТРОГО СОЗДАНИЯ
# ============================================================

def create_logger(
    name: str = 'YouTubeBot',
    level: int = logging.DEBUG,
    detailed: bool = True,
    show_separators: bool = True
) -> ConsoleLogger:
    """
    Быстрое создание настроенного логгера.
    
    Args:
        name: Имя логгера
        level: Уровень логирования (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        detailed: Показывать название функции и строку кода
        show_separators: Показывать разделители
    
    Returns:
        ConsoleLogger instance
    """
    return ConsoleLogger(
        name=name,
        level=level,
        detailed=detailed,
        show_separators=show_separators
    )


# ============================================================
# ТЕСТИРОВАНИЕ
# ============================================================

if __name__ == "__main__":
    # Создаем логгер
    console_logger = create_logger('TestLogger', level=logging.DEBUG)
    logger = console_logger.get_logger()
    
    # Тестируем разные уровни
    console_logger.separator("ТЕСТИРОВАНИЕ ЛОГГЕРА")
    
    logger.debug("Это DEBUG сообщение - детальная отладка")
    logger.info("Это INFO сообщение - общая информация")
    logger.warning("Это WARNING сообщение - предупреждение")
    logger.error("Это ERROR сообщение - ошибка")
    logger.critical("Это CRITICAL сообщение - критическая ошибка")
    
    # Тестируем операции
    console_logger.separator("ТЕСТИРОВАНИЕ ОПЕРАЦИЙ")
    
    console_logger.start_operation(
        "Downloading video",
        url="https://youtube.com/watch?v=abc123",
        quality="360p"
    )
    
    console_logger.step("Извлечение информации о видео...")
    console_logger.step("Получение форматов...", current=1, total=3)
    console_logger.step("Выбор качества 360p", current=2, total=3)
    console_logger.step("Начало загрузки", current=3, total=3)
    
    # Симуляция прогресса
    import time
    for i in range(0, 101, 10):
        console_logger.download_progress(
            i,
            speed=f"{2.5:.1f} MB/s",
            eta=f"{10-i//10} сек",
            downloaded=f"{i*5} MB",
            total="500 MB"
        )
        time.sleep(0.1)
    print()  # Завершаем строку прогресса
    
    console_logger.end_operation(
        "Downloading video",
        success=True,
        size="50.5 MB",
        file="video.mp4"
    )
    
    # Тестируем информацию о видео
    console_logger.video_info({
        'title': 'Как создать Telegram бота на Python за 10 минут',
        'uploader': 'Python Hub',
        'duration': 600,
        'view_count': 150000,
        'url': 'https://youtube.com/watch?v=abc123'
    })
    
    # Тестируем ошибку
    try:
        raise ValueError("Что-то пошло не так при обработке видео")
    except Exception as e:
        console_logger.error_details(e, "При скачивании видео")
    
    console_logger.separator("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
    
    print("\n✅ Все тесты пройдены!")
