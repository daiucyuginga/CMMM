"""
ui/async_waveform.py
QThreadPool を使用したバックグラウンド非同期音声波形生成
動画の音声波形を高速抽出し、シークバーの背景に提供する
"""

import os
import logging
import subprocess
from typing import Dict, Optional, Set
from PySide6.QtCore import QRunnable, QObject, Signal, QThreadPool
from PySide6.QtGui import QImage, QPixmap
from core.ffmpeg_tools import FFmpegRunner

logger = logging.getLogger(__name__)


class WaveformSignals(QObject):
    finished = Signal(str, QImage)  # file_path, QImage
    error = Signal(str, str)        # file_path, error_message


class WaveformTask(QRunnable):
    def __init__(self, file_path: str):
        super().__init__()
        self.file_path = file_path
        self.signals = WaveformSignals()
        self.runner = FFmpegRunner()

    def run(self):
        try:
            # 高速音声波形画像生成
            # - threads 0: マルチスレッド
            # - aresample=8000: ダウンサンプリングで包絡線計算負荷を大幅削減
            # - mono, sqrtスケールで無音・微小音を鮮明化, 2400x88, 透明背景, シアン/ブルー
            # - pipe:1 直接出力でディスクI/Oゼロ
            args = [
                self.runner.ffmpeg_path,
                "-hide_banner", "-y",
                "-threads", "0",
                "-i", self.file_path,
                "-vn",
                "-filter_complex", "aresample=8000,aformat=channel_layouts=mono,showwavespic=s=2400x88:colors=#388bfd:scale=sqrt",
                "-frames:v", "1",
                "-c:v", "png",
                "-f", "image2",
                "pipe:1"
            ]

            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            res = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs
            )

            if res.returncode == 0 and res.stdout:
                img = QImage.fromData(res.stdout)
                if not img.isNull():
                    self.signals.finished.emit(self.file_path, img)
                else:
                    self.signals.error.emit(self.file_path, "Image is null")
            else:
                self.signals.error.emit(self.file_path, "FFmpeg failed or no audio stream")
        except Exception as e:
            logger.warning(f"波形生成失敗 ({self.file_path}): {e}")
            self.signals.error.emit(self.file_path, str(e))


class WaveformManager(QObject):
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = WaveformManager()
        return cls._instance

    def __init__(self):
        super().__init__()
        self.pool = QThreadPool.globalInstance()
        self.cache: Dict[str, QPixmap] = {}
        self._in_progress: Set[str] = set()
        self._active_tasks: Set[WaveformTask] = set()

    def request_waveform(self, file_path: str, callback):
        norm_path = os.path.abspath(file_path)
        if norm_path in self.cache:
            callback(norm_path, self.cache[norm_path])
            return

        if norm_path in self._in_progress:
            return

        self._in_progress.add(norm_path)
        task = WaveformTask(norm_path)
        self._active_tasks.add(task)

        def _on_finished(path, img: QImage):
            self._in_progress.discard(path)
            self._active_tasks.discard(task)
            pixmap = QPixmap.fromImage(img)
            self.cache[path] = pixmap
            callback(path, pixmap)

        def _on_error(path, err):
            self._in_progress.discard(path)
            self._active_tasks.discard(task)

        task.signals.finished.connect(_on_finished)
        task.signals.error.connect(_on_error)
        self.pool.start(task)
