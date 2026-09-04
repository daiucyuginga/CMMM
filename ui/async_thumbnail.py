"""
ui/async_thumbnail.py
QThreadPool を使用したバックグラウンド非同期サムネイル生成
UI を一切フリーズさせずに高速にアイコンを生成する
"""

import os
import tempfile
import logging
from PySide6.QtCore import QRunnable, QObject, Signal, QThreadPool
from PySide6.QtGui import QPixmap, QImage
from core.ffmpeg_tools import FFmpegRunner

logger = logging.getLogger(__name__)


class ThumbnailSignals(QObject):
    finished = Signal(str, QPixmap)  # clip_id, pixmap
    error = Signal(str, str)         # clip_id, error_message


class ThumbnailTask(QRunnable):
    def __init__(self, clip_id: str, video_path: str, time_sec: float = 0.5):
        super().__init__()
        self.clip_id = clip_id
        self.video_path = video_path
        self.time_sec = time_sec
        self.signals = ThumbnailSignals()
        self.runner = FFmpegRunner()

    def run(self):
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name

            # キーフレーム直近から超高速に1フレーム生成
            args = [
                "-ss", f"{self.time_sec:.2f}",
                "-skip_frame", "nokey",
                "-i", self.video_path,
                "-frames:v", "1",
                "-q:v", "3",
                "-s", "160x90",
                "-y",
                tmp_path
            ]
            self.runner.run_ffmpeg(args)

            pixmap = QPixmap(tmp_path)
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            if not pixmap.isNull():
                self.signals.finished.emit(self.clip_id, pixmap)
            else:
                self.signals.error.emit(self.clip_id, "Pixmap is null")
        except Exception as e:
            logger.warning(f"サムネイル生成失敗 ({self.clip_id}): {e}")
            self.signals.error.emit(self.clip_id, str(e))


class ThumbnailManager:
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = ThumbnailManager()
        return cls._instance

    def __init__(self):
        self.pool = QThreadPool.globalInstance()

    def request_thumbnail(self, clip_id: str, video_path: str, time_sec: float, callback):
        task = ThumbnailTask(clip_id, video_path, time_sec)
        task.signals.finished.connect(callback)
        self.pool.start(task)
