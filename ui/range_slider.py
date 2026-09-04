"""
ui/range_slider.py
動画の再生シークバー ＋ クリップの In点/Out点 トリミング用カスタムウィジェット
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush


class RangeSlider(QWidget):
    # シグナル
    positionChanged = Signal(int)     # ミリ秒 (シーク移動時)
    rangeChanged = Signal(int, int)   # in_ms, out_ms (トリミング範囲変更時)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(32)
        self.duration_ms = 10000  # デフォルト 10秒
        self.position_ms = 0
        self.in_ms = 0
        self.out_ms = 10000

        self.dragging = None  # "in", "out", "position", None
        self.setMouseTracking(True)

    def set_duration(self, ms: int):
        self.duration_ms = max(1, ms)
        self.in_ms = 0
        self.out_ms = self.duration_ms
        self.position_ms = 0
        self.update()

    def set_position(self, ms: int):
        self.position_ms = max(0, min(self.duration_ms, ms))
        self.update()

    def set_range(self, in_ms: int, out_ms: int):
        self.in_ms = max(0, min(self.duration_ms, in_ms))
        self.out_ms = max(self.in_ms + 100, min(self.duration_ms, out_ms))
        self.update()

    def _ms_to_x(self, ms: int) -> float:
        if self.duration_ms <= 0:
            return 10.0
        track_w = max(10, self.width() - 24)
        return 12.0 + (ms / self.duration_ms) * track_w

    def _x_to_ms(self, x: float) -> int:
        track_w = max(10, self.width() - 24)
        ratio = max(0.0, min(1.0, (x - 12.0) / track_w))
        return int(ratio * self.duration_ms)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        track_y = h / 2.0 - 4.0
        track_h = 8.0
        track_w = max(10, w - 24)

        # 1. トラック背景 (全体)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(60, 60, 60))
        painter.drawRoundedRect(QRectF(12, track_y, track_w, track_h), 4, 4)

        # 2. In〜Out 選択範囲ハイライト (アクセントブルー)
        x_in = self._ms_to_x(self.in_ms)
        x_out = self._ms_to_x(self.out_ms)
        painter.setBrush(QColor(0, 120, 212))
        painter.drawRoundedRect(QRectF(x_in, track_y, max(2.0, x_out - x_in), track_h), 4, 4)

        # 3. In点ハンドル (左側マーカー)
        painter.setPen(QPen(QColor(96, 205, 255), 2))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawRoundedRect(QRectF(x_in - 5, h / 2.0 - 10, 10, 20), 3, 3)

        # 4. Out点ハンドル (右側マーカー)
        painter.drawRoundedRect(QRectF(x_out - 5, h / 2.0 - 10, 10, 20), 3, 3)

        # 5. 再生ヘッド (赤ライン)
        x_pos = self._ms_to_x(self.position_ms)
        painter.setPen(QPen(QColor(255, 82, 82), 2))
        painter.drawLine(int(x_pos), int(h / 2.0 - 12), int(x_pos), int(h / 2.0 + 12))
        painter.setBrush(QColor(255, 82, 82))
        painter.drawEllipse(QRectF(x_pos - 4, h / 2.0 - 14, 8, 8))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        x = event.position().x()
        x_in = self._ms_to_x(self.in_ms)
        x_out = self._ms_to_x(self.out_ms)

        if abs(x - x_in) <= 8:
            self.dragging = "in"
        elif abs(x - x_out) <= 8:
            self.dragging = "out"
        else:
            self.dragging = "position"
            self.position_ms = self._x_to_ms(x)
            self.positionChanged.emit(self.position_ms)
            self.update()

    def mouseMoveEvent(self, event):
        x = event.position().x()
        if self.dragging == "in":
            new_in = min(self._x_to_ms(x), self.out_ms - 200)
            self.in_ms = max(0, new_in)
            self.rangeChanged.emit(self.in_ms, self.out_ms)
            self.update()
        elif self.dragging == "out":
            new_out = max(self._x_to_ms(x), self.in_ms + 200)
            self.out_ms = min(self.duration_ms, new_out)
            self.rangeChanged.emit(self.in_ms, self.out_ms)
            self.update()
        elif self.dragging == "position":
            self.position_ms = self._x_to_ms(x)
            self.positionChanged.emit(self.position_ms)
            self.update()
        else:
            # カーソル変更
            x_in = self._ms_to_x(self.in_ms)
            x_out = self._ms_to_x(self.out_ms)
            if abs(x - x_in) <= 8 or abs(x - x_out) <= 8:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        self.dragging = None
