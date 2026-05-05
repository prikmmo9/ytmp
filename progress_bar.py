# progress_bar.py - Модуль прогресс-бара с этапами загрузки
import asyncio
import time
from datetime import datetime
from typing import Optional, Callable

from logger_config import create_logger

logger = create_logger(
    name='ProgressBar',
    level=20,
    detailed=False,
    show_separators=False
).get_logger()


class VideoProgressBar:
    """
    Прогресс-бар загрузки видео с тремя этапами:
    - Этап 1 (0-30%): Получение информации и начало загрузки
    - Этап 2 (30-70%): Загрузка файла с серверов YouTube/TikTok
    - Этап 3 (70-100%): Отправка файла в Telegram
    """
    
    def __init__(self, message, platform: str, quality: str, start_time=None):
        """
        Args:
            message: сообщение Telegram для обновления
            platform: 'youtube' или 'tiktok'
            quality: качество ('360', '480', '720', '1080', 'mp3')
        """
        self.message = message
        self.platform = platform
        self.quality = quality
        self.start_time = start_time or datetime.now()
        
        # Текущий этап
        self.stage = 1
        
        # Прогресс внутри этапов
        self.stage_progress = 0
        
        # Дополнительная информация
        self.speed = ""
        self.eta = ""
        self.file_size = ""
        self.title = ""
        
        # Флаги
        self.cancelled = False
        self.completed = False
        
        # Callback для отмены
        self.cancel_callback = None
        
        # Платформенные эмодзи
        self.platform_emoji = "📺" if platform == "youtube" else "🎵"
        self.platform_name = "YouTube" if platform == "youtube" else "TikTok"
    
    def set_cancel_callback(self, callback: Callable):
        """Устанавливает функцию для отмены загрузки"""
        self.cancel_callback = callback
    
    def set_title(self, title: str):
        """Устанавливает название видео"""
        self.title = title[:80] if title else ""
    
    def _generate_bar(self, percent: float, length: int = 20) -> str:
        """Генерирует текстовый прогресс-бар"""
        filled = int(length * percent / 100)
        bar = "█" * filled + "░" * (length - filled)
        return f"[{bar}]"
    
    def _get_stage_info(self) -> tuple:
        """
        Возвращает информацию о текущем этапе.
        Returns: (stage_name, stage_emoji, min_percent, max_percent)
        """
        if self.stage == 1:
            return "Получение информации", "🔍", 0, 30
        elif self.stage == 2:
            return "Скачивание", "⬇️", 30, 70
        elif self.stage == 3:
            return "Отправка в Telegram", "📤", 70, 100
        return "Завершено", "✅", 100, 100
    
    def _calculate_total_percent(self) -> float:
        """
        Вычисляет общий процент на основе этапа и прогресса внутри этапа.
        """
        stage_name, emoji, stage_min, stage_max = self._get_stage_info()
        stage_range = stage_max - stage_min
        
        # Прогресс внутри этапа (0-100%)
        if self.stage == 1:
            # Этап 1: имитация прогресса
            return stage_min + (self.stage_progress * stage_range / 100)
        elif self.stage == 2:
            # Этап 2: реальный прогресс загрузки
            return stage_min + (self.stage_progress * stage_range / 100)
        elif self.stage == 3:
            # Этап 3: прогресс отправки
            return stage_min + (self.stage_progress * stage_range / 100)
        else:
            return 100.0
    
    def update_stage1(self, progress: float = 0, status: str = ""):
        """
        Обновляет прогресс этапа 1 (получение информации).
        progress: 0-100 (процент внутри этапа)
        """
        self.stage = 1
        self.stage_progress = min(progress, 100)
        
        if status:
            logger.info(f"🔍 Этап 1: {status}")
    
    def update_download(self, percent: float, speed: str = "", eta: str = ""):
        """
        Обновляет прогресс этапа 2 (скачивание).
        percent: 0-100 (реальный процент загрузки)
        """
        self.stage = 2
        self.stage_progress = min(percent, 100)
        
        if speed:
            self.speed = speed
        if eta:
            self.eta = eta
    
    def update_upload(self, progress: float = 0, status: str = ""):
        """
        Обновляет прогресс этапа 3 (отправка в Telegram).
        progress: 0-100 (процент внутри этапа)
        """
        self.stage = 3
        self.stage_progress = min(progress, 100)
        
        if status:
            self.file_size = status
    
    def complete(self, file_size_mb: float = 0):
        """Завершает прогресс-бар"""
        self.stage = 4
        self.stage_progress = 100
        self.completed = True
        
        total_time = (datetime.now() - self.start_time).total_seconds()
        logger.info(f"✅ Завершено: {file_size_mb:.1f} MB | {total_time:.1f}с")
    
    def cancel(self):
        """Отменяет загрузку"""
        self.cancelled = True
        logger.info("🛑 Загрузка отменена")
    
    def _generate_text(self) -> str:
        """
        Генерирует текст для отображения в сообщении.
        """
        total_percent = self._calculate_total_percent()
        bar = self._generate_bar(total_percent)
        stage_name, stage_emoji, _, _ = self._get_stage_info()
        
        elapsed = (datetime.now() - self.start_time).total_seconds()
        elapsed_str = f"{int(elapsed // 60)}:{int(elapsed % 60):02d}"
        
        lines = [
            f"{self.platform_emoji} **{self.platform_name}** | 📊 {self.quality}",
            f"",
        ]
        
        if self.title:
            lines.append(f"🎬 {self.title}")
            lines.append(f"")
        
        lines.append(f"{bar} **{total_percent:.1f}%**")
        lines.append(f"")
        lines.append(f"{stage_emoji} **Этап {self.stage}/3:** {stage_name}")
        lines.append(f"⏱ Прошло: {elapsed_str}")
        
        if self.stage == 2 and self.speed:
            lines.append(f"⚡ Скорость: {self.speed}")
        if self.stage == 2 and self.eta:
            lines.append(f"⏳ Осталось: {self.eta}")
        if self.stage == 3 and self.file_size:
            lines.append(f"💾 {self.file_size}")
        
        if self.cancelled:
            lines.append(f"")
            lines.append(f"🛑 **Загрузка отменена**")
        elif self.completed:
            lines.append(f"")
            lines.append(f"✅ **Загрузка завершена!**")
        
        lines.append(f"")
        lines.append(f"🚫 /cancel для отмены")
        
        return "\n".join(lines)
    
    async def update_message(self):
        """Отправляет обновление прогресса в Telegram"""
        if self.message:
            try:
                text = self._generate_text()
                await self.message.edit(text)
            except Exception as e:
                logger.error(f"Ошибка обновления прогресса: {e}")
    
    async def start_stage1_simulation(self, duration: float = 3.0, steps: int = 10):
        """
        Имитирует прогресс этапа 1.
        Постепенно увеличивает прогресс от 0 до 100% в течение duration секунд.
        """
        step_delay = duration / steps
        
        for i in range(steps + 1):
            if self.cancelled:
                break
            
            progress = (i / steps) * 100
            status_text = f"Получение информации о видео... ({i}/{steps})"
            self.update_stage1(progress, status_text)
            await self.update_message()
            
            if i < steps:
                await asyncio.sleep(step_delay)
    
    async def start_upload_simulation(self, file_size_mb: float = 0, duration: float = 5.0, steps: int = 20):
        """
        Имитирует прогресс этапа 3 (отправка).
        """
        step_delay = duration / steps
        
        for i in range(steps + 1):
            if self.cancelled:
                break
            
            progress = (i / steps) * 100
            
            if file_size_mb > 0:
                uploaded = (file_size_mb * progress / 100)
                status = f"Отправлено {uploaded:.1f}/{file_size_mb:.1f} MB"
            else:
                status = f"Отправка... ({i}/{steps})"
            
            self.update_upload(progress, status)
            await self.update_message()
            
            if i < steps:
                await asyncio.sleep(step_delay)


# ============================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ BOT.PY
# ============================================================

def create_progress_bar(message, platform: str, quality: str) -> VideoProgressBar:
    """
    Создаёт прогресс-бар для использования в боте.
    
    Использование:
        progress = create_progress_bar(status_msg, platform, quality)
        
        # Этап 1: получение информации
        await progress.start_stage1_simulation(duration=3.0)
        
        # Этап 2: скачивание (обновляется через callback)
        def download_progress(percent, speed, eta):
            progress.update_download(percent, speed, eta)
            await progress.update_message()
        
        # Этап 3: отправка
        await progress.start_upload_simulation(file_size_mb=50.0, duration=5.0)
        
        # Завершение
        progress.complete(file_size_mb=50.0)
        await progress.update_message()
    """
    quality_labels = {
        '360': '360p',
        '480': '480p', 
        '720': '720p HD',
        '1080': '1080p Full HD',
        'mp3': 'MP3',
    }
    
    return VideoProgressBar(
        message=message,
        platform=platform,
        quality=quality_labels.get(quality, quality),
    )


# ============================================================
# ТЕСТИРОВАНИЕ
# ============================================================

async def test_progress_bar():
    """Тест прогресс-бара (запускается без Telegram)"""
    print("=" * 60)
    print("  Тест ProgressBar")
    print("=" * 60)
    print()
    
    # Создаём прогресс-бар без сообщения (только логи)
    progress = VideoProgressBar(
        message=None,
        platform='youtube',
        quality='720p HD',
    )
    progress.set_title("Тестовое видео для проверки прогресс-бара загрузки")
    
    print("Этап 1: Получение информации")
    for i in range(11):
        progress.update_stage1(i * 10, f"Шаг {i}/10")
        bar = progress._generate_bar(progress._calculate_total_percent())
        print(f"  {bar} {progress._calculate_total_percent():.1f}% - {progress._get_stage_info()[0]}")
        await asyncio.sleep(0.2)
    
    print("\nЭтап 2: Скачивание")
    for i in range(11):
        progress.update_download(i * 10, speed=f"{2.5:.1f} MB/s", eta=f"{10-i} сек")
        bar = progress._generate_bar(progress._calculate_total_percent())
        print(f"  {bar} {progress._calculate_total_percent():.1f}% - {progress._get_stage_info()[0]} | ⚡ 2.5 MB/s")
        await asyncio.sleep(0.3)
    
    print("\nЭтап 3: Отправка в Telegram")
    for i in range(11):
        progress.update_upload(i * 10, f"Отправлено {i*5}/{50} MB")
        bar = progress._generate_bar(progress._calculate_total_percent())
        print(f"  {bar} {progress._calculate_total_percent():.1f}% - {progress._get_stage_info()[0]}")
        await asyncio.sleep(0.2)
    
    progress.complete(file_size_mb=50.0)
    print(f"\n✅ Завершено! 100%")
    print(f"   {progress._generate_bar(100)}")


if __name__ == '__main__':
    asyncio.run(test_progress_bar())
