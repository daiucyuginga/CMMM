"""
ui/range_slider.py
クリップ拡大タイムライン ＋ ミニマップ（元動画全体の位置）
マウスホイール、ドラッグシーク、視認性の高い再生ヘッド
"""

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, Signal, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont


class ClipTimelineSlider(QWidget):
    positionChanged = Signal(int)     # シーク位置 (ミリ秒, 元動画絶対時間)
    wheelStep = Signal(int)           # マウスホイールによるコマ送り (+1 or -1)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(54)
        self.total_ms = 10000        # 元動画全体の長さ (ms)
        self.clip_in_ms = 0          # 現在クリップの開始 (ms)
        self.clip_out_ms = 10000     # 現在クリップの終了 (ms)
        self.current_pos_ms = 0      # 現在の再生位置 (ms)

        self.dragging = False
        self.setMouseTracking(True)

    def set_clip_info(self, total_ms: int, in_ms: int, out_ms: int, current_ms: int):
        self.total_ms = max(1, total_ms)
        self.clip_in_ms = max(0, min(self.total_ms, in_ms))
        self.clip_out_ms = max(self.clip_in_ms + 100, min(self.total_ms, out_ms))
        self.current_pos_ms = max(0, min(self.total_ms, current_ms))
        self.update()

    def set_position(self, current_ms: int):
        self.current_pos_ms = max(0, min(self.total_ms, current_ms))
        self.update()

    def _clip_dur_ms(self) -> int:
        return max(100, self.clip_out_ms - self.clip_in_ms)

    def _pos_to_clip_x(self, pos_ms: int) -> float:
        """クリップ拡大バー上の X座標 (pos_ms がクリップ範囲外なら端にクランプ)"""
        w = max(10, self.width() - 24)
        ratio = (pos_ms - self.clip_in_ms) / self._clip_dur_ms()
        ratio = max(0.0, min(1.0, ratio))
        return 12.0 + ratio * w

    def _clip_x_to_pos(self, x: float) -> int:
        """クリップ拡大バーの X座標から元動画の絶対ミリ秒を逆算"""
        w = max(10, self.width() - 24)
        ratio = max(0.0, min(1.0, (x - 12.0) / w))
        return int(self.clip_in_ms + ratio * self._clip_dur_ms())

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        track_w = max(10, w - 24)

        # ==========================================
        # 上段：クリップ拡大タイムライン (メイン)
        # ==========================================
        main_y = 6.0
        main_h = 24.0

        # 背景 (クリップ全幅)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(38, 38, 38))
        painter.drawRoundedRect(QRectF(12, main_y, track_w, main_h), 5, 5)

        # クリップ再生済みのプログレス (青)
        cur_x = self._pos_to_clip_x(self.current_pos_ms)
        prog_w = max(0.0, cur_x - 12.0)
        painter.setBrush(QColor(0, 120, 212))
        painter.drawRoundedRect(QRectF(12, main_y, prog_w, main_h), 5, 5)

        # 枠線
        painter.setPen(QPen(QColor(70, 70, 70), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(12, main_y, track_w, main_h), 5, 5)

        # 目盛り（10等分）
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        for i in range(1, 10):
            tick_x = 12.0 + (i / 10.0) * track_w
            painter.drawLine(int(tick_x), int(main_y + main_h - 6), int(tick_x), int(main_y + main_h))

        # 再生ヘッド (赤ライン ＋ 上部ポインタ)
        painter.setPen(QPen(QColor(255, 82, 82), 2))
        painter.drawLine(int(cur_x), int(main_y), int(cur_x), int(main_y + main_h))
        painter.setBrush(QColor(255, 82, 82))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(cur_x - 5, main_y - 3, 10, 10))

        # ==========================================
        # 下段：元動画全体ミニマップ (全体の中での位置)
        # ==========================================
        mini_y = main_y + main_h + 6.0
        mini_h = 8.0

        # 全体バー背景
        painter.setBrush(QColor(25, 25, 25))
        painter.drawRoundedRect(QRectF(12, mini_y, track_w, mini_h), 3, 3)

        # このクリップの区間を黄色/アクセントでハイライト
        clip_start_ratio = self.clip_in_ms / self.total_ms
        clip_end_ratio = self.clip_out_ms / self.total_ms
        mini_in_x = 12.0 + clip_start_ratio * track_w
        mini_out_x = 12.0 + clip_end_ratio * track_w
        mini_clip_w = max(3.0, mini_out_x - mini_in_x)

        painter.setBrush(QColor(96, 205, 255, 180))
        painter.drawRoundedRect(QRectF(mini_in_x, mini_y, mini_clip_w, mini_h), 3, 3)

        # 全体バー上の現在再生ヘッド位置
        cur_total_ratio = self.current_pos_ms / self.total_ms
        mini_head_x = 12.0 + cur_total_ratio * track_w
        painter.setPen(QPen(QColor(255, 82, 82), 2))
        painter.drawLine(int(mini_head_x), int(mini_y - 1), int(mini_head_x), int(mini_y + mini_h + 1))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            x = event.position().x()
            y = event.position().y()
            if y > 36:
                # ミニマップ（下段）をクリックした場合は全体シーク
                w = max(10, self.width() - 24)
                ratio = max(0.0, min(1.0, (x - 12.0) / w))
                self.current_pos_ms = int(ratio * self.total_ms)
            else:
                # メイン拡大バー（上段）をクリックした場合はクリップ内シーク
                self.current_pos_ms = self._clip_x_to_pos(x)

            self.positionChanged.emit(self.current_pos_ms)
            self.update()

    def mouseMoveEvent(self, event):
        if self.dragging:
            x = event.position().x()
            self.current_pos_ms = self._clip_x_to_pos(x)
            self.positionChanged.emit(self.current_pos_ms)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging = False

    def wheelEvent(self, event):
        # マウスホイールでコマ送り / コマ戻し
        delta = event.angleDelta().y()
        if delta > 0:
            self.wheelStep.emit(1)   # 進む
        elif delta < 0:
            self.wheelStep.emit(-1)  # 戻る
        event.accept()
