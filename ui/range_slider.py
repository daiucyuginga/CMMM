"""
ui/range_slider.py
Windows ムービーメーカー風の直感的な再生シークバー
クリック即ジャンプ、ドラッグシーク、マウスホイールコマ送りに対応
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QPen


class VideoSlider(QWidget):
    positionChanged = Signal(int)     # シーク位置 (ミリ秒)
    wheelStep = Signal(int)           # コマ送り (+1 or -1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.duration_ms = 1000
        self.position_ms = 0
        self.dragging = False
        self.setMouseTracking(True)

    def set_duration(self, ms: int):
        self.duration_ms = max(1, ms)
        self.position_ms = min(self.position_ms, self.duration_ms)
        self.update()

    def set_position(self, ms: int):
        if not self.dragging:
            self.position_ms = max(0, min(self.duration_ms, ms))
            self.update()

    def _pos_to_x(self, ms: int) -> float:
        w = max(10, self.width() - 20)
        ratio = max(0.0, min(1.0, ms / self.duration_ms))
        return 10.0 + ratio * w

    def _x_to_pos(self, x: float) -> int:
        w = max(10, self.width() - 20)
        ratio = max(0.0, min(1.0, (x - 10.0) / w))
        return int(ratio * self.duration_ms)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        track_y = h / 2.0 - 3.0
        track_h = 6.0
        track_w = max(10, w - 20)

        # トラック背景
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(50, 50, 50))
        painter.drawRoundedRect(QRectF(10, track_y, track_w, track_h), 3, 3)

        # 再生済みプログレス (青)
        cur_x = self._pos_to_x(self.position_ms)
        prog_w = max(0.0, cur_x - 10.0)
        painter.setBrush(QColor(0, 120, 212))
        painter.drawRoundedRect(QRectF(10, track_y, prog_w, track_h), 3, 3)

        # 再生ヘッド (丸ピン)
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QColor(0, 120, 212))
        painter.drawEllipse(QRectF(cur_x - 7, h / 2.0 - 7, 14, 14))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.position_ms = self._x_to_pos(event.position().x())
            self.positionChanged.emit(self.position_ms)
            self.update()

    def mouseMoveEvent(self, event):
        if self.dragging:
            self.position_ms = self._x_to_pos(event.position().x())
            self.positionChanged.emit(self.position_ms)
            self.update()

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
