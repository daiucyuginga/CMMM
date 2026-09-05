"""
ui/range_slider.py
Windows ムービーメーカー風の直感的な再生シークバー
音声波形背景表示、クリック即ジャンプ、ドラッグシーク、マウスホイールコマ送りに対応
"""

from typing import Optional
from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QPixmap, QPolygonF, QPainterPath


class VideoSlider(QWidget):
    positionChanged = Signal(int)     # シーク位置 (ミリ秒)
    wheelStep = Signal(int)           # コマ送り (+1 or -1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(56)
        self.duration_ms = 1000
        self.position_ms = 0
        self.dragging = False
        self.hover_x: Optional[float] = None

        # クリップ情報 & 音声波形
        self.file_path: Optional[str] = None
        self.waveform_pixmap: Optional[QPixmap] = None
        self.clip_start_sec: float = 0.0
        self.clip_end_sec: float = 0.0
        self.total_sec: float = 0.0

        self.setMouseTracking(True)

    def set_duration(self, ms: int):
        self.duration_ms = max(1, ms)
        self.position_ms = min(self.position_ms, self.duration_ms)
        self.update()

    def set_position(self, ms: int):
        if not self.dragging:
            self.position_ms = max(0, min(self.duration_ms, ms))
            self.update()

    def set_clip_info(self, file_path: str, start_sec: float, end_sec: float, total_sec: float):
        """アクティブなクリップ情報を設定"""
        self.file_path = file_path
        self.clip_start_sec = start_sec
        self.clip_end_sec = end_sec
        self.total_sec = total_sec

    def set_waveform(self, pixmap: Optional[QPixmap], start_sec: float = 0.0, end_sec: float = 0.0, total_sec: float = 0.0):
        """音声波形画像とクリップの範囲を設定"""
        self.waveform_pixmap = pixmap
        self.clip_start_sec = start_sec
        self.clip_end_sec = end_sec
        self.total_sec = total_sec
        self.update()

    def clear_waveform(self):
        self.waveform_pixmap = None
        self.file_path = None
        self.update()

    def _pos_to_x(self, ms: int) -> float:
        w = max(10, self.width() - 20)
        ratio = max(0.0, min(1.0, ms / self.duration_ms))
        return 10.0 + ratio * w

    def _x_to_pos(self, x: float) -> int:
        w = max(10, self.width() - 20)
        ratio = max(0.0, min(1.0, (x - 10.0) / w))
        return int(ratio * self.duration_ms)

    def _format_time(self, sec: float) -> str:
        s = max(0.0, sec)
        m, s = divmod(s, 60.0)
        h, m = divmod(m, 60.0)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        track_y = 6.0
        track_h = h - 14.0
        track_w = max(10.0, w - 20.0)
        track_rect = QRectF(10.0, track_y, track_w, track_h)

        # 1. トラック背景 (ダークグレー + 枠線)
        painter.setPen(QPen(QColor(55, 55, 60), 1))
        painter.setBrush(QColor(20, 20, 24))
        painter.drawRoundedRect(track_rect, 4, 4)

        # 2. 中央のゼロレベル基準線
        painter.setPen(QPen(QColor(40, 40, 48), 1, Qt.PenStyle.DashLine))
        center_y = track_y + track_h / 2.0
        painter.drawLine(QPointF(10.0, center_y), QPointF(10.0 + track_w, center_y))

        # 3. 音声波形の描画 (クリップ区間の拡大表示)
        if self.waveform_pixmap and not self.waveform_pixmap.isNull():
            img_w = self.waveform_pixmap.width()
            img_h = self.waveform_pixmap.height()

            if self.total_sec > 0 and self.clip_end_sec > self.clip_start_sec:
                src_x = img_w * max(0.0, min(1.0, self.clip_start_sec / self.total_sec))
                src_w = img_w * max(0.001, min(1.0, (self.clip_end_sec - self.clip_start_sec) / self.total_sec))
            else:
                src_x = 0.0
                src_w = float(img_w)

            src_rect = QRectF(src_x, 0.0, src_w, float(img_h))

            painter.save()
            path = QPainterPath()
            path.addRoundedRect(track_rect, 4, 4)
            painter.setClipPath(path)
            painter.drawPixmap(track_rect, self.waveform_pixmap, src_rect)
            painter.restore()

        # 4. 再生済みプログレス (半透明オーバーレイ & 下部ライン)
        cur_x = self._pos_to_x(self.position_ms)
        prog_w = max(0.0, cur_x - 10.0)
        if prog_w > 0:
            painter.save()
            path = QPainterPath()
            path.addRoundedRect(track_rect, 4, 4)
            painter.setClipPath(path)

            prog_rect = QRectF(10.0, track_y, prog_w, track_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 120, 212, 45))
            painter.drawRect(prog_rect)

            bar_h = 3.0
            bar_rect = QRectF(10.0, track_y + track_h - bar_h, prog_w, bar_h)
            painter.setBrush(QColor(0, 120, 212))
            painter.drawRect(bar_rect)
            painter.restore()

        # 5. マウスホバー位置ガイド線
        if self.hover_x is not None and 10.0 <= self.hover_x <= 10.0 + track_w and not self.dragging:
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(self.hover_x, track_y), QPointF(self.hover_x, track_y + track_h))

        # 6. 再生ヘッド (縦ライン + 上部ピン)
        painter.setPen(QPen(QColor(255, 255, 255, 240), 2))
        painter.drawLine(QPointF(cur_x, track_y - 2), QPointF(cur_x, track_y + track_h + 2))

        pin_w = 12.0
        pin_h = 8.0
        poly = QPolygonF([
            QPointF(cur_x - pin_w / 2.0, track_y - 3),
            QPointF(cur_x + pin_w / 2.0, track_y - 3),
            QPointF(cur_x, track_y + pin_h - 3)
        ])
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(QColor(0, 120, 212))
        painter.drawPolygon(poly)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.position_ms = self._x_to_pos(event.position().x())
            self.positionChanged.emit(self.position_ms)
            self.update()

    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        if self.dragging:
            self.position_ms = self._x_to_pos(self.hover_x)
            self.positionChanged.emit(self.position_ms)
        self.update()

    def leaveEvent(self, event):
        self.hover_x = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False
            self.position_ms = self._x_to_pos(event.position().x())
            self.positionChanged.emit(self.position_ms)
            self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta > 0:
            self.wheelStep.emit(1)
        elif delta < 0:
            self.wheelStep.emit(-1)
        event.accept()
