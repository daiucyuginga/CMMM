"""
ui/player_window.py
CMMM 連携用動画プレイヤー (CMMM Player)
定番動画プレイヤー機能（全画面、音量/ミュート、コマ送り、速度調整）
＋ 視聴しながらの In/Out マーキング ＆ CMMMレシピ相互連携
"""

import os
import sys
import json
import tempfile
import subprocess
import bisect
import logging
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QFileDialog, QMessageBox, QSlider,
    QListWidget, QListWidgetItem, QStyle, QApplication, QComboBox,
    QMenu
)
from PySide6.QtCore import Qt, QUrl, Signal, QRectF, QPointF, QPoint, QSize, QTimer, QEvent, QThread
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QKeySequence, QShortcut, QPolygonF, QCursor
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from core.ffmpeg_tools import FFmpegRunner
from core.gop_analyzer import VideoAnalyzer

SPEED_PRESETS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]
SKIP_PRESETS = [
    (5, "5秒"),
    (10, "10秒"),
    (30, "30秒"),
    (60, "1分"),
    (300, "5分"),
]


class KeyframeLoaderThread(QThread):
    """動画のキーフレーム（Iフレーム）一覧を非同期解析するワーカースレッド"""
    keyframesLoaded = Signal(list)  # List[float] (秒)

    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self._is_cancelled = False
        self.runner = FFmpegRunner()

    def cancel(self):
        self._is_cancelled = True
        self.runner.cancel()

    def run(self):
        try:
            analyzer = VideoAnalyzer(self.runner)
            kfs = analyzer.get_keyframes(self.video_path)
            if not self._is_cancelled:
                self.keyframesLoaded.emit(kfs)
        except Exception as e:
            logger.error(f"キーフレーム解析エラー: {e}")
            if not self._is_cancelled:
                self.keyframesLoaded.emit([])


class PlayerVideoWidget(QVideoWidget):
    """クリック再生、ダブルクリック全画面、ドロップ対応のプレビュー画面"""
    clicked = Signal()
    doubleClicked = Signal()
    fileDropped = Signal(str)
    wheelDelta = Signal(int)
    mouseMoved = Signal()
    resized = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000; border-radius: 4px;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        self.mouseMoved.emit()
        super().mouseMoveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    def wheelEvent(self, event):
        self.wheelDelta.emit(event.angleDelta().y())
        event.accept()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        event.accept()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path:
                    self.fileDropped.emit(path)
                    event.acceptProposedAction()
                    return
        event.ignore()


class PlayerSlider(QWidget):
    """マーク済み区間の可視化ハイライトを備えたシークバー（再生位置シーク完全独立）"""
    positionChanged = Signal(int)
    sliderReleased = Signal(int)           # ms (ドラッグ完了時の確定シーク用)
    markerSelected = Signal(int)           # marker_ms
    markerDeleteRequested = Signal(int)    # marker_ms

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.duration_ms = 1000
        self.position_ms = 0
        self.dragging = False
        self.hover_x: Optional[float] = None
        self.hover_y: Optional[float] = None

        # 分割マーカー情報 (ミリ秒単位のリスト)
        self.markers: List[int] = []
        self.mark_in_ms: Optional[int] = None
        self.clips: List[Tuple[int, int]] = []

        # マーカー表示・選択状態
        self.hovered_marker_ms: Optional[int] = None
        self.selected_marker_ms: Optional[int] = None

        self.setMouseTracking(True)

    def set_duration(self, ms: int):
        self.duration_ms = max(1, ms)
        self.position_ms = min(self.position_ms, self.duration_ms)
        self.update()

    def set_position(self, ms: int):
        if not self.dragging:
            self.position_ms = max(0, min(self.duration_ms, ms))
            self.update()

    def set_markers(self, markers: List[int]):
        self.markers = sorted(markers)
        self.update()

    def set_mark_in(self, ms: Optional[int]):
        self.mark_in_ms = ms
        self.update()

    def set_clips(self, clips: List[Tuple[int, int]]):
        self.clips = clips
        self.update()

    def _pos_to_x(self, ms: int) -> float:
        w = max(10, self.width() - 20)
        ratio = max(0.0, min(1.0, ms / self.duration_ms))
        return 10.0 + ratio * w

    def _x_to_pos(self, x: float) -> int:
        w = max(10, self.width() - 20)
        ratio = max(0.0, min(1.0, (x - 10.0) / w))
        return int(ratio * self.duration_ms)

    def _hit_test_marker(self, x: float, y: float) -> Optional[int]:
        """指定座標 (x, y) にあるマーカー（ミリ秒）を判定。なければ None"""
        if not self.markers:
            return None
        h = self.height()
        track_y = h / 2.0 - 5.0
        track_h = 10.0

        # 上部フラグ(track_y - 10) 〜 トラック下部(track_y + track_h + 10) の縦範囲
        if not (track_y - 10 <= y <= track_y + track_h + 10):
            return None

        best_m = None
        best_dist = 8.0  # 許容水平距離 8px
        for m in self.markers:
            mx = self._pos_to_x(m)
            dist = abs(x - mx)
            if dist < best_dist:
                best_dist = dist
                best_m = m
        return best_m

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        track_y = h / 2.0 - 5.0
        track_h = 10.0
        track_w = max(10.0, w - 20.0)
        track_rect = QRectF(10.0, track_y, track_w, track_h)

        # 1. トラック背景
        painter.setPen(QPen(QColor(55, 55, 60), 1))
        painter.setBrush(QColor(25, 25, 30))
        painter.drawRoundedRect(track_rect, 4, 4)

        # 2. 分割マーカーピンの描画（縦ライン ＆ 三角フラグ）
        for m in self.markers:
            mx = self._pos_to_x(m)
            is_hovered = (self.hovered_marker_ms == m)
            is_selected = (self.selected_marker_ms == m)

            # 色とスタイルの決定
            if is_hovered or is_selected:
                line_color = QColor(255, 235, 80)
                flag_color = QColor(255, 220, 40)
                flag_border = QColor(255, 255, 255)
                flag_w = 6.0
                flag_h = 7.0
            else:
                line_color = QColor(255, 185, 0)
                flag_color = QColor(255, 185, 0)
                flag_border = QColor(200, 140, 0)
                flag_w = 4.0
                flag_h = 5.0

            # トラックを貫くマーカー縦ライン
            painter.setPen(QPen(line_color, 2))
            painter.drawLine(QPointF(mx, track_y - 4), QPointF(mx, track_y + track_h + 4))

            # 上部の三角マーカーフラグ
            flag = QPolygonF([
                QPointF(mx - flag_w, track_y - flag_h - 1),
                QPointF(mx + flag_w, track_y - flag_h - 1),
                QPointF(mx, track_y - 1)
            ])
            painter.setPen(QPen(flag_border, 1))
            painter.setBrush(flag_color)
            painter.drawPolygon(flag)

        # 4. 再生済みプログレス（半透明白）
        cur_x = self._pos_to_x(self.position_ms)
        prog_w = max(0.0, cur_x - 10.0)
        if prog_w > 0:
            prog_rect = QRectF(10.0, track_y, prog_w, track_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 40))
            painter.drawRect(prog_rect)

        # 5. ホバー位置ガイド線
        if self.hover_x is not None and 10.0 <= self.hover_x <= 10.0 + track_w and not self.dragging:
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(self.hover_x, track_y - 2), QPointF(self.hover_x, track_y + track_h + 2))

        # 6. 再生ヘッド（白丸ノブ）
        painter.setPen(QPen(QColor(0, 120, 212), 2))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QPointF(cur_x, track_y + track_h / 2.0), 7, 7)

    def mousePressEvent(self, event):
        pos_x = event.position().x()
        pos_y = event.position().y()

        if event.button() == Qt.MouseButton.LeftButton:
            hit_m = self._hit_test_marker(pos_x, pos_y)
            if hit_m is not None:
                # マーカーピンをクリックした場合は選択＆その位置へシーク
                self.selected_marker_ms = hit_m
                self.position_ms = hit_m
                self.positionChanged.emit(hit_m)
                self.markerSelected.emit(hit_m)
            else:
                # 通常のシーク位置へジャンプ
                self.position_ms = self._x_to_pos(pos_x)
                self.positionChanged.emit(self.position_ms)

            self.dragging = True
            self.update()

        elif event.button() == Qt.MouseButton.RightButton:
            hit_m = self._hit_test_marker(pos_x, pos_y)
            if hit_m is not None:
                self.selected_marker_ms = hit_m
                self.markerSelected.emit(hit_m)
                self.update()

                # コンテキストメニュー
                menu = QMenu(self)
                act_del = menu.addAction("🗑 このマークを削除")
                act_jump = menu.addAction("⏮ このマークへジャンプ")
                chosen = menu.exec(event.globalPosition().toPoint())

                if chosen == act_del:
                    self.markerDeleteRequested.emit(hit_m)
                elif chosen == act_jump:
                    self.position_ms = hit_m
                    self.positionChanged.emit(hit_m)

    def mouseMoveEvent(self, event):
        self.hover_x = event.position().x()
        self.hover_y = event.position().y()

        # 通常の再生ヘッドドラッグ
        if self.dragging:
            self.position_ms = self._x_to_pos(self.hover_x)
            self.positionChanged.emit(self.position_ms)
            self.update()
            return

        # ホバー判定
        hit_m = self._hit_test_marker(self.hover_x, self.hover_y)
        if hit_m is not None:
            self.hovered_marker_ms = hit_m
            sec = hit_m / 1000.0
            self.setToolTip(f"分割マーク: {sec:.2f}秒 (クリックでジャンプ / 右クリックでメニュー)")
        else:
            self.hovered_marker_ms = None
            self.setToolTip("")

        self.update()

    def leaveEvent(self, event):
        self.hover_x = None
        self.hover_y = None
        self.hovered_marker_ms = None
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                self.dragging = False
                self.position_ms = self._x_to_pos(event.position().x())
                self.positionChanged.emit(self.position_ms)
                self.sliderReleased.emit(self.position_ms)
                self.update()


class PlayerWindow(QMainWindow):
    """CMMM 連携用動画プレイヤーメインウィンドウ"""
    def __init__(self, initial_video_path: str = "", initial_recipe_path: str = ""):
        super().__init__()
        self.setWindowTitle("CMMM プレイヤー")
        self.resize(1060, 680)

        # 状態
        self.file_path: Optional[str] = None
        self.duration_sec: float = 0.0
        self.markers: List[float] = []  # 分割マーカー（秒単位の昇順リスト）
        self.keyframes: List[float] = []  # キーフレーム（秒単位の昇順リスト）
        self._kf_thread: Optional[KeyframeLoaderThread] = None
        self.playback_rate = 1.0
        self.speed_preset_index = 2
        self.saved_volume = 70

        # Undo / Redo 履歴スタック
        self.undo_stack: List[List[float]] = []
        self.redo_stack: List[List[float]] = []

        # スキップ設定 (初期値: 5秒, index=0, value=5)
        self.skip_seconds_value = 5
        self.skip_preset_index = 0

        # シークデバウンス（キー連打・スライダードラッグ時の引っ掛かり・音飛び防止）
        self._pending_seek_ms: Optional[int] = None
        self._seek_debounce_timer = QTimer(self)
        self._seek_debounce_timer.setSingleShot(True)
        self._seek_debounce_timer.setInterval(35)  # 35ms（約1フレーム分）
        self._seek_debounce_timer.timeout.connect(self._apply_pending_seek)

        # 全画面時のマウス位置監視タイマー (下部ホバーで即時表示、離脱で即時非表示)
        self.hover_check_timer = QTimer(self)
        self.hover_check_timer.setInterval(50)
        self.hover_check_timer.timeout.connect(self._update_fullscreen_controls_visibility)

        # 全画面時のマウスカーソル自動非表示タイマー (2秒間静止でカーソル & コントロールを非表示)
        self._cursor_hidden = False
        self._cursor_hide_timer = QTimer(self)
        self._cursor_hide_timer.setSingleShot(True)
        self._cursor_hide_timer.setInterval(2000)
        self._cursor_hide_timer.timeout.connect(self._hide_cursor_fullscreen)

        # メディアプレイヤー初期化
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        self._setup_ui()
        self._setup_shortcuts()
        self._setup_player_events()

        self.setAcceptDrops(True)
        if QApplication.instance():
            QApplication.instance().installEventFilter(self)

        # 初期読み込み (UIレンダラーバインド後に安全に実行)
        if initial_recipe_path and os.path.exists(initial_recipe_path):
            QTimer.singleShot(50, lambda: self.load_recipe(initial_recipe_path))
        elif initial_video_path and os.path.exists(initial_video_path):
            QTimer.singleShot(50, lambda: self.open_video(initial_video_path))

    def _setup_ui(self):
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #1a1a1e;
                color: #e0e0e0;
                font-family: "Segoe UI", "Yu Gothic UI", sans-serif;
                font-size: 12px;
            }
            QPushButton {
                background-color: #2b2b32;
                border: 1px solid #3e3e48;
                border-radius: 4px;
                padding: 5px 12px;
                color: #e0e0e0;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #383842;
                border-color: #60cdff;
            }
            QPushButton:pressed {
                background-color: #202026;
            }
            QPushButton:disabled {
                background-color: #222226;
                color: #666;
                border-color: #333;
            }
            QPushButton#accentButton {
                background-color: #0078d4;
                border: 1px solid #005a9e;
                color: #fff;
                font-weight: bold;
            }
            QPushButton#accentButton:hover {
                background-color: #1084d9;
            }
            QListWidget {
                background-color: #141416;
                border: 1px solid #33333a;
                border-radius: 4px;
                padding: 4px;
            }
            QListWidget::item {
                     QSlider::handle:horizontal {
                background: #fff;
                width: 12px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 6px;
            }
            QComboBox {
                background-color: #2b2b32;
                border: 1px solid #3e3e48;
                border-radius: 4px;
                padding: 3px 8px;
                color: #e0e0e0;
                font-weight: 500;
                min-width: 58px;
            }
            QComboBox:hover {
                background-color: #383842;
                border-color: #60cdff;
            }
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QComboBox QAbstractItemView {
                background-color: #222226;
                border: 1px solid #3e3e48;
                selection-background-color: #0078d4;
                color: #e0e0e0;
                padding: 2px;
            }
        """)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self.splitter)

        # ====================================================
        # 左側: プレイヤーセクション
        # ====================================================
        self.player_container = QWidget()
        self.player_layout = QVBoxLayout(self.player_container)
        self.player_layout.setContentsMargins(0, 0, 0, 0)
        self.player_layout.setSpacing(6)

        # 動画画面
        self.video_widget = PlayerVideoWidget(self)
        self.video_widget.clicked.connect(self.toggle_play)
        self.video_widget.doubleClicked.connect(self.toggle_fullscreen)
        self.video_widget.fileDropped.connect(self.handle_dropped_path)
        self.video_widget.wheelDelta.connect(self._on_wheel_volume)
        self.video_widget.mouseMoved.connect(self._update_fullscreen_controls_visibility)
        self.video_widget.installEventFilter(self)
        self.video_widget.setMinimumHeight(340)
        self.player.setVideoOutput(self.video_widget)
        self.player_layout.addWidget(self.video_widget, stretch=1)

        # 下部操作パネル（通常時は下に配置、全画面時は自動隠蔽オーバーレイ）
        self.bottom_controls = QWidget()
        self.bottom_controls.setObjectName("bottomControls")
        self.bottom_controls.installEventFilter(self)
        bottom_layout = QVBoxLayout(self.bottom_controls)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(6)

        # タイムコード行 (左: 時間, 右: 速度ステップボタン)
        tc_layout = QHBoxLayout()
        self.lbl_current_time = QLabel("00:00:00.000")
        self.lbl_current_time.setStyleSheet("color: #60cdff; font-weight: bold; font-family: monospace; font-size: 13px;")
        tc_sep = QLabel("/")
        tc_sep.setStyleSheet("color: #666;")
        self.lbl_total_time = QLabel("00:00:00.000")
        self.lbl_total_time.setStyleSheet("color: #888; font-family: monospace; font-size: 13px;")
        self.lbl_file_name = QLabel("")
        self.lbl_file_name.setStyleSheet("color: #aaa; margin-left: 12px;")

        tc_layout.addWidget(self.lbl_current_time)
        tc_layout.addWidget(tc_sep)
        tc_layout.addWidget(self.lbl_total_time)
        tc_layout.addWidget(self.lbl_file_name)
        tc_layout.addStretch()

        # 速度調整ステップボタン
        speed_box = QWidget()
        speed_h = QHBoxLayout(speed_box)
        speed_h.setContentsMargins(0, 0, 0, 0)
        speed_h.setSpacing(4)
        lbl_speed = QLabel("速度")
        lbl_speed.setStyleSheet("color: #888; font-size: 11px;")
        speed_h.addWidget(lbl_speed)

        self.btn_speed_down = QPushButton("－")
        self.btn_speed_down.setFixedSize(24, 24)
        self.btn_speed_down.setToolTip("速度ダウン")
        self.btn_speed_down.setStyleSheet("font-weight: bold; font-size: 13px; padding: 0;")
        self.btn_speed_down.clicked.connect(lambda: self.change_speed_step(-1))
        speed_h.addWidget(self.btn_speed_down)

        self.btn_speed_reset = QPushButton("1.0x")
        self.btn_speed_reset.setFixedWidth(52)
        self.btn_speed_reset.setFixedHeight(24)
        self.btn_speed_reset.setToolTip("クリックで等倍 (1.0x) にリセット")
        self.btn_speed_reset.setStyleSheet("color: #60cdff; font-weight: bold; font-size: 12px; border: 1px solid #484852; border-radius: 3px; background-color: #252528; padding: 0;")
        self.btn_speed_reset.clicked.connect(self.reset_speed)
        speed_h.addWidget(self.btn_speed_reset)

        self.btn_speed_up = QPushButton("＋")
        self.btn_speed_up.setFixedSize(24, 24)
        self.btn_speed_up.setToolTip("速度アップ")
        self.btn_speed_up.setStyleSheet("font-weight: bold; font-size: 13px; padding: 0;")
        self.btn_speed_up.clicked.connect(lambda: self.change_speed_step(1))
        speed_h.addWidget(self.btn_speed_up)

        tc_layout.addWidget(speed_box)
        bottom_layout.addLayout(tc_layout)

        # シークバー
        self.slider = PlayerSlider(self)
        self.slider.positionChanged.connect(self.on_slider_seek)
        self.slider.sliderReleased.connect(lambda pos: self.seek(pos, immediate=True))
        self.slider.markerSelected.connect(self._on_slider_marker_selected)
        self.slider.markerDeleteRequested.connect(self._on_slider_marker_delete_requested)
        bottom_layout.addWidget(self.slider)

        # コントロール行
        ctrl_layout = QHBoxLayout()
        ctrl_layout.setContentsMargins(0, 0, 0, 0)
        ctrl_layout.setSpacing(6)

        # ファイルを開くボタン
        btn_open = QPushButton("📂 開く")
        btn_open.setToolTip("動画ファイルを開く (Ctrl+O)")
        btn_open.clicked.connect(self.on_open_file_dialog)
        ctrl_layout.addWidget(btn_open)

        # スキップ (秒数) & コマ送り
        self.btn_skip_prev = QPushButton("◀ 5秒")
        self.btn_skip_prev.setToolTip("5秒 戻る (Left)")
        self.btn_skip_prev.clicked.connect(lambda: self.do_skip(-1))
        ctrl_layout.addWidget(self.btn_skip_prev)

        btn_step_m1 = QPushButton("◀ 1コマ")
        btn_step_m1.setAutoRepeat(True)
        btn_step_m1.setAutoRepeatDelay(300)
        btn_step_m1.setAutoRepeatInterval(60)
        btn_step_m1.setToolTip("1フレーム戻る ([)")
        btn_step_m1.clicked.connect(lambda: self.step_frames(-1))
        ctrl_layout.addWidget(btn_step_m1)

        # 再生ボタン
        self.btn_play = QPushButton("▶ 再生")
        self.btn_play.setStyleSheet("background-color: #0078d4; color: #fff; font-weight: bold; min-width: 76px; padding: 6px 12px;")
        self.btn_play.setToolTip("再生 / 一時停止 (Space)")
        self.btn_play.clicked.connect(self.toggle_play)
        ctrl_layout.addWidget(self.btn_play)

        btn_step_p1 = QPushButton("1コマ ▶")
        btn_step_p1.setAutoRepeat(True)
        btn_step_p1.setAutoRepeatDelay(300)
        btn_step_p1.setAutoRepeatInterval(60)
        btn_step_p1.setToolTip("1フレーム進む (])")
        btn_step_p1.clicked.connect(lambda: self.step_frames(1))
        ctrl_layout.addWidget(btn_step_p1)

        self.btn_skip_next = QPushButton("5秒 ▶")
        self.btn_skip_next.setToolTip("5秒 進む (Right)")
        self.btn_skip_next.clicked.connect(lambda: self.do_skip(1))
        ctrl_layout.addWidget(self.btn_skip_next)

        # スキップ設定セレクター
        self.combo_skip = QComboBox()
        self.combo_skip.setToolTip("スキップ秒数設定")
        for sec, label in SKIP_PRESETS:
            self.combo_skip.addItem(label, sec)
        self.combo_skip.setCurrentIndex(self.skip_preset_index)
        self.combo_skip.currentIndexChanged.connect(self._on_skip_preset_changed)
        ctrl_layout.addWidget(self.combo_skip)

        ctrl_layout.addSpacing(10)

        # 音量セクション
        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFixedSize(30, 30)
        self.btn_mute.setToolTip("ミュート切替 (Ctrl+M)")
        self.btn_mute.setStyleSheet("padding: 0; font-size: 14px;")
        self.btn_mute.clicked.connect(self.toggle_mute)
        ctrl_layout.addWidget(self.btn_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setToolTip("音量スライダー (上下キーまたはホイール)")
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        ctrl_layout.addWidget(self.volume_slider)

        self.lbl_volume = QLabel("70%")
        self.lbl_volume.setStyleSheet("color: #888; min-width: 32px; font-size: 11px;")
        ctrl_layout.addWidget(self.lbl_volume)

        ctrl_layout.addSpacing(10)

        # 分割マーキング & マーク移動ボタン
        self.btn_marker_prev = QPushButton("⏮")
        self.btn_marker_prev.setToolTip("前の分割マークへ移動 (J)")
        self.btn_marker_prev.clicked.connect(lambda: self.jump_to_marker(-1))
        ctrl_layout.addWidget(self.btn_marker_prev)

        self.btn_marker = QPushButton("📍 マーク (M)")
        self.btn_marker.setToolTip("現在位置に分割マークを設置 / 解除 (M)")
        self.btn_marker.setStyleSheet("font-weight: bold; color: #ffb900;")
        self.btn_marker.clicked.connect(self.toggle_marker)
        ctrl_layout.addWidget(self.btn_marker)

        self.btn_marker_next = QPushButton("⏭")
        self.btn_marker_next.setToolTip("次の分割マークへ移動 (L)")
        self.btn_marker_next.clicked.connect(lambda: self.jump_to_marker(1))
        ctrl_layout.addWidget(self.btn_marker_next)

        ctrl_layout.addStretch()

        # 全画面ボタン
        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.setFixedSize(30, 30)
        self.btn_fullscreen.setToolTip("全画面表示の切替 (F / ダブルクリック)")
        self.btn_fullscreen.setStyleSheet("font-size: 14px; padding: 0;")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        ctrl_layout.addWidget(self.btn_fullscreen)

        # フォーカスがボタン等に吸い取られて矢印キー等のショートカットが阻害されるのを防止
        for w in [
            btn_open, self.btn_skip_prev, btn_step_m1, self.btn_play, btn_step_p1, self.btn_skip_next,
            self.combo_skip, self.btn_mute, self.volume_slider, self.btn_marker_prev, self.btn_marker,
            self.btn_marker_next, self.btn_fullscreen, self.btn_speed_down, self.btn_speed_reset, self.btn_speed_up
        ]:
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        bottom_layout.addLayout(ctrl_layout)
        self.player_layout.addWidget(self.bottom_controls)
        self.splitter.addWidget(self.player_container)

        # ====================================================
        # 右側: 分割マーカーリストパネル
        # ====================================================
        self.right_panel = QWidget()
        self.right_panel.setMinimumWidth(260)
        self.right_panel.setMaximumWidth(340)
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(4, 0, 4, 0)
        right_layout.setSpacing(8)

        # パネルタイトル
        header_row = QHBoxLayout()
        lbl_list_title = QLabel("分割ポイント")
        lbl_list_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #ffb900;")
        self.lbl_marker_count = QLabel("0 カ所")
        self.lbl_marker_count.setStyleSheet("color: #888;")
        self.lbl_clip_count = self.lbl_marker_count  # 互換用参照
        header_row.addWidget(lbl_list_title)
        header_row.addStretch()
        header_row.addWidget(self.lbl_marker_count)
        right_layout.addLayout(header_row)

        # マーカーリスト
        self.marker_list_widget = QListWidget()
        self.marker_list_widget.itemDoubleClicked.connect(self.on_marker_item_clicked)
        self.marker_list_widget.currentRowChanged.connect(self._on_marker_list_row_changed)
        self.clip_list_widget = self.marker_list_widget  # 互換用参照
        right_layout.addWidget(self.marker_list_widget)

        # リスト操作ボタン
        list_btn_row = QHBoxLayout()
        self.btn_move_marker = QPushButton("🔄 移動")
        self.btn_move_marker.setToolTip("選択中（または最寄り）の分割マークを現在の再生位置へ移動 (Shift+M)")
        self.btn_move_marker.clicked.connect(self.move_selected_marker_to_current)
        list_btn_row.addWidget(self.btn_move_marker)

        btn_del_marker = QPushButton("🗑 削除")
        btn_del_marker.setToolTip("選択した分割マーク（または最寄りのマーク）を削除 (Delete)")
        btn_del_marker.clicked.connect(self.delete_selected_marker)
        list_btn_row.addWidget(btn_del_marker)

        btn_clear_markers = QPushButton("全クリア")
        btn_clear_markers.clicked.connect(self.clear_all_markers)
        list_btn_row.addWidget(btn_clear_markers)
        right_layout.addLayout(list_btn_row)

        # クリップ数 & 合計時間サマリー
        self.lbl_summary = QLabel("クリップ数: 1 / 合計: 00:00:00.000")
        self.lbl_summary.setStyleSheet("color: #aaa; font-size: 11px;")
        right_layout.addWidget(self.lbl_summary)

        right_layout.addSpacing(6)

        # 連携アクションボタン
        self.btn_send_cmmm = QPushButton("🎬 CMMMで開く")
        self.btn_send_cmmm.setObjectName("accentButton")
        self.btn_send_cmmm.setStyleSheet("padding: 8px 12px; font-size: 13px;")
        self.btn_send_cmmm.setToolTip("マークした分割ポイントでクリップに分割してCMMMへ即座に展開")
        self.btn_send_cmmm.clicked.connect(self.on_send_to_cmmm_clicked)
        right_layout.addWidget(self.btn_send_cmmm)

        self.btn_save_recipe = QPushButton("レシピ保存")
        self.btn_save_recipe.setToolTip("CMMM互換の recipe.json として保存 (Ctrl+S)")
        self.btn_save_recipe.clicked.connect(self.on_save_recipe_clicked)
        right_layout.addWidget(self.btn_save_recipe)

        self.splitter.addWidget(self.right_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 0)

    def _setup_shortcuts(self):
        # スペース: 再生/停止
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        # F / F11: 全画面
        QShortcut(QKeySequence(Qt.Key.Key_F), self, self.toggle_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self.toggle_fullscreen)
        # Esc: 全画面解除
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.exit_fullscreen)
        # Ctrl+M: ミュート
        QShortcut(QKeySequence("Ctrl+M"), self, self.toggle_mute)
        # ジャンプ / スキップ ショートカット
        # スキップ (Left / Right): デフォルトは設定された秒数（初期値5秒）
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.do_skip(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.do_skip(1))
        # キーフレームスキップ (Alt+Left / Alt+Right)
        QShortcut(QKeySequence("Alt+Left"), self, lambda: self.skip_keyframe(-1))
        QShortcut(QKeySequence("Alt+Right"), self, lambda: self.skip_keyframe(1))
        # 30秒 (Ctrl+Left / Ctrl+Right)
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.skip_seconds(-30))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.skip_seconds(30))
        # 1分 (Shift+Left / Shift+Right)
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.skip_seconds(-60))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.skip_seconds(60))
        # 5分 (Ctrl+Alt+Left / Ctrl+Alt+Right)
        QShortcut(QKeySequence("Ctrl+Alt+Left"), self, lambda: self.skip_seconds(-300))
        QShortcut(QKeySequence("Ctrl+Alt+Right"), self, lambda: self.skip_seconds(300))
        # 音量上下 (Up / Down)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.change_volume(5))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.change_volume(-5))
        # コマ送り [ / ]
        QShortcut(QKeySequence(Qt.Key.Key_BracketLeft), self, lambda: self.step_frames(-1))
        QShortcut(QKeySequence(Qt.Key.Key_BracketRight), self, lambda: self.step_frames(1))
        # 分割マーカーショートカット
        # M: マーク設置 / 解除
        QShortcut(QKeySequence(Qt.Key.Key_M), self, self.toggle_marker)
        # Shift+M: 選択中（または最寄り）マークを現在位置へ移動
        QShortcut(QKeySequence("Shift+M"), self, self.move_selected_marker_to_current)
        # J / L: 前 / 次のマークへ移動
        QShortcut(QKeySequence(Qt.Key.Key_J), self, lambda: self.jump_to_marker(-1))
        QShortcut(QKeySequence(Qt.Key.Key_L), self, lambda: self.jump_to_marker(1))
        # Delete: 選択中（または最寄り）マーカー削除
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.delete_selected_marker)
        # Undo / Redo (元に戻す / やり直す)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.redo)
        # Ctrl+O / Ctrl+S
        QShortcut(QKeySequence("Ctrl+O"), self, self.on_open_file_dialog)
        QShortcut(QKeySequence("Ctrl+S"), self, self.on_save_recipe_clicked)

    def _setup_player_events(self):
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    SUPPORTED_VIDEO_EXTS = (
        ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv",
        ".ts", ".wmv", ".m2ts", ".mts", ".mpg", ".mpeg"
    )

    def handle_dropped_path(self, path: str) -> bool:
        """ドラッグ＆ドロップされたファイル（動画またはレシピJSON）を判定して開く"""
        if not path or not os.path.exists(path):
            return False
        ext = os.path.splitext(path)[1].lower()
        if ext in self.SUPPORTED_VIDEO_EXTS:
            self.open_video(path)
            return True
        elif ext == ".json":
            self.load_recipe(path)
            return True
        return False

    # --- ドラッグ＆ドロップ ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if self.handle_dropped_path(path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    # --- メディア再生制御 ---
    def open_video(self, file_path: str):
        norm_path = os.path.abspath(file_path)
        if not os.path.exists(norm_path):
            return

        self.file_path = norm_path
        self.lbl_file_name.setText(os.path.basename(norm_path))
        self.markers.clear()
        self._refresh_marker_list()

        # ffprobe で即座に正確な再生時間を取得
        try:
            analyzer = VideoAnalyzer(FFmpegRunner())
            meta = analyzer.get_metadata(norm_path)
            self.duration_sec = meta.duration
            dur_ms = int(meta.duration * 1000)
            self.slider.set_duration(dur_ms)
            self.lbl_total_time.setText(self._format_time(self.duration_sec))
        except Exception:
            pass

        # キーフレーム解析を非同期で開始
        self.keyframes = []
        if self._kf_thread:
            self._stop_keyframe_thread()

        self._kf_thread = KeyframeLoaderThread(norm_path, self)
        self._kf_thread.keyframesLoaded.connect(self._on_keyframes_loaded)
        self._kf_thread.start()

        self.player.setSource(QUrl.fromLocalFile(norm_path))
        self.player.setPlaybackRate(self.playback_rate)
        self.player.setPosition(0)
        self.player.play()
        self.btn_play.setText("⏸ 一時停止")
        self.statusBar().showMessage(f"再生中: {os.path.basename(norm_path)}", 3000)

    def _stop_keyframe_thread(self):
        if not self._kf_thread:
            return
        try:
            self._kf_thread.keyframesLoaded.disconnect()
        except (TypeError, RuntimeError):
            pass
        self._kf_thread.cancel()
        self._kf_thread.wait(3000)
        self._kf_thread = None

    def _on_keyframes_loaded(self, kfs: List[float]):
        self.keyframes = sorted(kfs)
        if self.keyframes:
            self.statusBar().showMessage(f"キーフレーム解析完了: {len(self.keyframes)} 箇所 (Left/Right でスキップ)", 3000)

    def set_keyframes(self, kfs: List[float]):
        """キーフレーム一覧を直接設定（テスト・モック用）"""
        self.keyframes = sorted(kfs)

    def on_open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "動画ファイルを開く", "", "動画ファイル (*.mp4 *.mov *.mkv *.avi *.webm);;すべてのファイル (*.*)"
        )
        if path:
            self.open_video(path)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.setPlaybackRate(self.playback_rate)
            self.player.play()

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setText("⏸ 一時停止")
        else:
            self.btn_play.setText("▶ 再生")

    def _on_player_position_changed(self, pos_ms: int):
        # シーク待機中はUIをシーク先位置に維持（ラバーバンド現象防止）
        if self._pending_seek_ms is not None:
            return
        self.slider.set_position(pos_ms)
        sec = pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(sec))

    def _on_player_duration_changed(self, dur_ms: int):
        self.slider.set_duration(dur_ms)
        self.duration_sec = dur_ms / 1000.0
        self.lbl_total_time.setText(self._format_time(self.duration_sec))

    def _apply_pending_seek(self):
        """デバウンスタイマー完了時に実際のプレイヤーシークを発行"""
        if self._pending_seek_ms is not None:
            target_ms = self._pending_seek_ms
            self._pending_seek_ms = None
            self.player.setPosition(target_ms)

    def seek(self, pos_ms: int, immediate: bool = False):
        """再生位置のシーク（UI即時更新 ＋ プレイヤーへのスマートデバウンス適用）"""
        max_ms = int(self.duration_sec * 1000) if self.duration_sec > 0 else max(pos_ms, 0)
        pos_ms = max(0, min(max_ms, pos_ms))

        # UI（シークバー・時間表示）は即座に更新してレスポンスを高める
        self.slider.set_position(pos_ms)
        sec = pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(sec))

        if immediate:
            self._seek_debounce_timer.stop()
            self._pending_seek_ms = None
            self.player.setPosition(pos_ms)
        else:
            self._pending_seek_ms = pos_ms
            self._seek_debounce_timer.start(35)

    def on_slider_seek(self, pos_ms: int):
        self.seek(pos_ms, immediate=False)

    def skip_keyframe(self, direction: int):
        """
        キーフレーム単位での前後の移動 (direction: -1 = 前へ, +1 = 次へ)
        """
        if not self.keyframes:
            # キーフレーム未取得時のフォールバック (1秒スキップ)
            self.skip_seconds(-1.0 if direction < 0 else 1.0)
            self.statusBar().showMessage(f"キーフレーム解析中... (1秒{'戻る' if direction < 0 else '進む'})", 1000)
            return

        cur_sec = (self._pending_seek_ms if self._pending_seek_ms is not None else max(self.player.position(), self.slider.position_ms)) / 1000.0
        eps = 0.05  # 約1.5フレーム分の許容マージン

        if direction < 0:
            # 現在位置より前のキーフレーム（最大のもの）
            idx = bisect.bisect_left(self.keyframes, cur_sec - eps) - 1
            if idx >= 0:
                target_sec = self.keyframes[idx]
            else:
                target_sec = self.keyframes[0]
        else:
            # 現在位置より後のキーフレーム（最小のもの）
            idx = bisect.bisect_right(self.keyframes, cur_sec + eps)
            if idx < len(self.keyframes):
                target_sec = self.keyframes[idx]
            else:
                target_sec = self.keyframes[-1]

        target_ms = int(target_sec * 1000)
        self.seek(target_ms, immediate=True)
        kf_idx = self.keyframes.index(target_sec) + 1 if target_sec in self.keyframes else 0
        total_kf = len(self.keyframes)
        self.statusBar().showMessage(
            f"キーフレーム [{kf_idx}/{total_kf}]: {self._format_time(target_sec)} ({'◀ 前' if direction < 0 else '次 ▶'})",
            1500
        )

    def do_skip(self, direction: int):
        """
        現在のスキップ設定に応じたスキップを実行 (direction: -1 = 戻る, +1 = 進む)
        """
        self.skip_seconds(self.skip_seconds_value * direction)

    def skip_seconds(self, sec: float):
        """指定秒数のスキップ（連打時の累積加算に対応）"""
        if self._pending_seek_ms is not None:
            base_ms = self._pending_seek_ms
        else:
            base_ms = max(self.player.position(), self.slider.position_ms)

        max_ms = int(self.duration_sec * 1000) if self.duration_sec > 0 else int(base_ms + sec * 1000)
        new_ms = max(0, min(max_ms, base_ms + int(sec * 1000)))
        self.seek(new_ms, immediate=False)
        sign = "+" if sec > 0 else ""
        self.statusBar().showMessage(f"スキップ: {sign}{sec:.0f}秒 ({self._format_time(new_ms / 1000.0)})", 1000)

    def step_frames(self, frames: int):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        fps = 30.0  # 標準fps
        delta_ms = int((1000.0 / fps) * frames)
        cur = self._pending_seek_ms if self._pending_seek_ms is not None else max(self.player.position(), self.slider.position_ms)
        max_ms = int(self.duration_sec * 1000) if self.duration_sec > 0 else cur + delta_ms
        new_ms = max(0, min(max_ms, cur + delta_ms))
        self.seek(new_ms, immediate=True)

    # --- 速度制御 ---
    def change_speed_step(self, delta: int):
        new_idx = max(0, min(len(SPEED_PRESETS) - 1, self.speed_preset_index + delta))
        self.set_speed_index(new_idx)

    def reset_speed(self):
        idx_1x = SPEED_PRESETS.index(1.0) if 1.0 in SPEED_PRESETS else 2
        self.set_speed_index(idx_1x)

    def set_speed_index(self, index: int):
        self.speed_preset_index = max(0, min(len(SPEED_PRESETS) - 1, index))
        rate = SPEED_PRESETS[self.speed_preset_index]
        self.playback_rate = rate
        self.player.setPlaybackRate(rate)
        self.btn_speed_reset.setText(f"{rate}x")
        self.btn_speed_down.setEnabled(self.speed_preset_index > 0)
        self.btn_speed_up.setEnabled(self.speed_preset_index < len(SPEED_PRESETS) - 1)

    # --- 音量制御 ---
    def on_volume_changed(self, val: int):
        self.audio_output.setVolume(val / 100.0)
        self.lbl_volume.setText(f"{val}%")
        if val == 0:
            self.btn_mute.setText("🔇")
        else:
            self.btn_mute.setText("🔊")

    def toggle_mute(self):
        is_muted = self.audio_output.isMuted()
        if is_muted or self.volume_slider.value() == 0:
            self.audio_output.setMuted(False)
            self.volume_slider.setValue(self.saved_volume if self.saved_volume > 0 else 70)
            self.btn_mute.setText("🔊")
            self.statusBar().showMessage(f"🔊 ミュート解除 (音量: {self.volume_slider.value()}%)", 1500)
        else:
            self.saved_volume = self.volume_slider.value()
            self.audio_output.setMuted(True)
            self.volume_slider.setValue(0)
            self.btn_mute.setText("🔇")
            self.statusBar().showMessage("🔇 ミュート中", 1500)

    def change_volume(self, delta: int):
        cur = self.volume_slider.value()
        new_vol = max(0, min(100, cur + delta))
        if self.audio_output.isMuted() and new_vol > 0:
            self.audio_output.setMuted(False)
            self.btn_mute.setText("🔊")
        self.volume_slider.setValue(new_vol)
        icon = "🔇" if new_vol == 0 else "🔉" if new_vol < 50 else "🔊"
        self.statusBar().showMessage(f"{icon} 音量: {new_vol}%", 1500)

    def _on_wheel_volume(self, delta_y: int):
        step = 5 if delta_y > 0 else -5
        self.change_volume(step)

    # --- スキップ設定制御 ---
    def _on_skip_preset_changed(self, index: int):
        if 0 <= index < len(SKIP_PRESETS):
            sec, label = SKIP_PRESETS[index]
            self.skip_seconds_value = sec
            self.skip_preset_index = index
            self.btn_skip_prev.setText(f"◀ {label}")
            self.btn_skip_next.setText(f"{label} ▶")
            self.btn_skip_prev.setToolTip(f"{label} 戻る (Left)")
            self.btn_skip_next.setToolTip(f"{label} 進む (Right)")
            self.statusBar().showMessage(f"スキップ設定を「{label}」に変更しました", 2000)

    # --- 全画面制御 ＆ オートハイド ---
    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.exit_fullscreen()
        else:
            self.enter_fullscreen()

    def enter_fullscreen(self):
        self.right_panel.setVisible(False)
        self.showFullScreen()
        self.btn_fullscreen.setText("🗗")

        # マウス位置監視タイマーを開始し、現在位置で即時判定
        self.hover_check_timer.start(50)
        self._cursor_hidden = False
        self._cursor_hide_timer.start(2000)
        self._update_fullscreen_controls_visibility()

    def exit_fullscreen(self):
        if self.isFullScreen():
            self._cursor_hide_timer.stop()
            self.hover_check_timer.stop()
            if self._cursor_hidden:
                self._cursor_hidden = False
                QApplication.restoreOverrideCursor()
            self.bottom_controls.show()
            self.showNormal()
            self.right_panel.setVisible(True)
            self.btn_fullscreen.setText("⛶")

    def _hide_cursor_fullscreen(self):
        """全画面時にマウスが一定時間静止した場合にカーソルとコントロールを非表示化"""
        if not self.isFullScreen():
            return
        if not self._cursor_hidden:
            self._cursor_hidden = True
            QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)
        if self.bottom_controls.isVisible():
            self.bottom_controls.hide()

    def _show_cursor_fullscreen(self):
        """全画面時にマウスが動いた際にカーソルを再表示"""
        if self._cursor_hidden:
            self._cursor_hidden = False
            QApplication.restoreOverrideCursor()
        self._update_fullscreen_controls_visibility()

    def _on_fullscreen_mouse_activity(self):
        """全画面中のマウス移動または操作検知"""
        if not self.isFullScreen():
            return
        self._show_cursor_fullscreen()
        self._cursor_hide_timer.start(2000)

    def _update_fullscreen_controls_visibility(self):
        if not self.isFullScreen():
            return

        # カーソル非表示中（マウス静止中）はコントロールも非表示を維持
        if self._cursor_hidden:
            if self.bottom_controls.isVisible():
                self.bottom_controls.hide()
            return

        # マウスカーソルの位置（ウィンドウ内ローカル座標）
        local_pos = self.mapFromGlobal(QCursor.pos())
        in_window = self.rect().contains(local_pos)

        if in_window:
            # 画面下端（コントロールパネル領域、下から120px以内）にカーソルがあるか判定
            threshold_y = self.height() - 120
            is_bottom = (local_pos.y() >= threshold_y) or self.bottom_controls.underMouse()
            if is_bottom:
                if not self.bottom_controls.isVisible():
                    self.bottom_controls.show()
            else:
                if self.bottom_controls.isVisible():
                    self.bottom_controls.hide()
        else:
            # 画面外の場合は非表示
            if self.bottom_controls.isVisible():
                self.bottom_controls.hide()

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()

        # 音量上下 (Up / Down)
        if key == Qt.Key.Key_Up and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            self.change_volume(5)
            event.accept()
            return
        elif key == Qt.Key.Key_Down and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            self.change_volume(-5)
            event.accept()
            return
        # キーフレーム移動 (Alt+Left / Alt+Right)
        elif key == Qt.Key.Key_Left and (mods & Qt.KeyboardModifier.AltModifier) and not (mods & Qt.KeyboardModifier.ControlModifier):
            self.skip_keyframe(-1)
            event.accept()
            return
        elif key == Qt.Key.Key_Right and (mods & Qt.KeyboardModifier.AltModifier) and not (mods & Qt.KeyboardModifier.ControlModifier):
            self.skip_keyframe(1)
            event.accept()
            return
        # スキップ (Left / Right)
        elif key == Qt.Key.Key_Left and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)):
            self.do_skip(-1)
            event.accept()
            return
        elif key == Qt.Key.Key_Right and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)):
            self.do_skip(1)
            event.accept()
            return

        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        # 全画面時のマウス位置監視 & カーソル自動非表示タイマーリセット
        if self.isFullScreen():
            if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress):
                self._on_fullscreen_mouse_activity()
            elif event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
                self._update_fullscreen_controls_visibility()

        # フォーカスのあるウィジェットに吸い取られずに上下・左右キーを確実に捕捉
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            mods = event.modifiers()

            # 音量上下 (Up / Down)
            if key == Qt.Key.Key_Up and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                self.change_volume(5)
                return True
            elif key == Qt.Key.Key_Down and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
                self.change_volume(-5)
                return True
            # キーフレーム移動 (Alt+Left / Alt+Right)
            elif key == Qt.Key.Key_Left and (mods & Qt.KeyboardModifier.AltModifier) and not (mods & Qt.KeyboardModifier.ControlModifier):
                self.skip_keyframe(-1)
                return True
            elif key == Qt.Key.Key_Right and (mods & Qt.KeyboardModifier.AltModifier) and not (mods & Qt.KeyboardModifier.ControlModifier):
                self.skip_keyframe(1)
                return True
            # スキップ (Left / Right)
            elif key == Qt.Key.Key_Left and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)):
                self.do_skip(-1)
                return True
            elif key == Qt.Key.Key_Right and not (mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.ShiftModifier)):
                self.do_skip(1)
                return True

        # ドラッグ＆ドロップの確実な捕捉（再生画面上や全画面時でも確実に受け入れ）
        if event.type() in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
                return True
        elif event.type() == QEvent.Type.Drop:
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    path = url.toLocalFile()
                    if self.handle_dropped_path(path):
                        event.acceptProposedAction()
                        return True

        return super().eventFilter(watched, event)

    def closeEvent(self, event):
        if self._cursor_hidden:
            self._cursor_hidden = False
            QApplication.restoreOverrideCursor()
        self.player.stop()
        self.player.setSource(QUrl())
        self._stop_keyframe_thread()
        super().closeEvent(event)

    # --- Undo / Redo 履歴管理 ---
    def _save_undo_state(self):
        """現在のマーカー状態を Undo スタックに保存"""
        if self.undo_stack and self.undo_stack[-1] == self.markers:
            return
        self.undo_stack.append(list(self.markers))
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self):
        """直前のマーカー操作を取り消す (Ctrl+Z)"""
        if not self.undo_stack:
            self.statusBar().showMessage("これ以上元に戻せません (Undo履歴なし)", 2000)
            return

        self.redo_stack.append(list(self.markers))
        self.markers = self.undo_stack.pop()
        self.slider.selected_marker_ms = None
        self._refresh_marker_list()
        self.statusBar().showMessage(f"操作を元に戻しました (残りのUndo: {len(self.undo_stack)} 件)", 2500)

    def redo(self):
        """取り消したマーカー操作をやり直す (Ctrl+Y / Ctrl+Shift+Z)"""
        if not self.redo_stack:
            self.statusBar().showMessage("これ以上やり直せません (Redo履歴なし)", 2000)
            return

        self.undo_stack.append(list(self.markers))
        self.markers = self.redo_stack.pop()
        self.slider.selected_marker_ms = None
        self._refresh_marker_list()
        self.statusBar().showMessage(f"操作をやり直しました (残りのRedo: {len(self.redo_stack)} 件)", 2500)

    # --- 分割マーキング（Marker） ---
    def toggle_marker(self):
        """現在位置に分割マークを設置または解除（トグル）"""
        if not self.file_path or self.duration_sec <= 0:
            return

        cur_pos_ms = self.slider.position_ms
        cur_sec = round(cur_pos_ms / 1000.0, 3)

        # 先頭付近や末尾付近（0.05秒以内）は除外
        if cur_sec <= 0.05 or cur_sec >= self.duration_sec - 0.05:
            return

        self._save_undo_state()

        # 既存マーカー（±0.15秒以内）があれば解除
        existing = [m for m in self.markers if abs(m - cur_sec) <= 0.15]
        if existing:
            for em in existing:
                self.markers.remove(em)
            self.statusBar().showMessage(f"分割マークを解除しました: {self._format_time(cur_sec)}", 2500)
        else:
            self.markers.append(cur_sec)
            self.markers.sort()
            self.statusBar().showMessage(f"分割マークを設置: {self._format_time(cur_sec)}", 2500)

        self._refresh_marker_list()

    def jump_to_marker(self, direction: int):
        """前後の分割マークへジャンプ (direction: -1 = 前, 1 = 次)"""
        if not self.markers:
            self.statusBar().showMessage("分割マークがありません", 2000)
            return

        cur_sec = self.slider.position_ms / 1000.0
        target: Optional[float] = None

        if direction < 0:
            # 前のマーカー
            prev_markers = [m for m in self.markers if m < cur_sec - 0.08]
            if prev_markers:
                target = prev_markers[-1]
            else:
                target = 0.0  # 先頭
        else:
            # 次のマーカー
            next_markers = [m for m in self.markers if m > cur_sec + 0.08]
            if next_markers:
                target = next_markers[0]
            else:
                target = self.duration_sec  # 末尾

        if target is not None:
            self.seek(int(target * 1000), immediate=True)
            self.statusBar().showMessage(f"マークへ移動: {self._format_time(target)}", 2000)

    def _refresh_marker_list(self):
        cur_selected_sec = None
        current_row = self.marker_list_widget.currentRow()
        if 0 <= current_row < len(self.markers):
            cur_selected_sec = self.markers[current_row]

        self.marker_list_widget.blockSignals(True)
        self.marker_list_widget.clear()
        self.markers.sort()

        for idx, m_sec in enumerate(self.markers, start=1):
            text = f"📍 {idx}. {self._format_time(m_sec)}"
            item = QListWidgetItem(text)
            self.marker_list_widget.addItem(item)

        clip_count = len(self.markers) + 1 if self.duration_sec > 0 else 0
        self.lbl_marker_count.setText(f"{len(self.markers)} カ所")
        self.lbl_summary.setText(f"クリップ数: {clip_count} / 合計: {self._format_time(self.duration_sec)}")
        self.slider.set_markers([int(m * 1000) for m in self.markers])

        # 選択状態の復元
        if cur_selected_sec is not None and cur_selected_sec in self.markers:
            new_row = self.markers.index(cur_selected_sec)
            self.marker_list_widget.setCurrentRow(new_row)
            self.slider.selected_marker_ms = int(cur_selected_sec * 1000)
        elif self.slider.selected_marker_ms is not None:
            sec = self.slider.selected_marker_ms / 1000.0
            if sec in self.markers:
                self.marker_list_widget.setCurrentRow(self.markers.index(sec))
            else:
                self.slider.selected_marker_ms = None
        else:
            self.slider.selected_marker_ms = None
        self.marker_list_widget.blockSignals(False)
        self.slider.update()

    def on_marker_item_clicked(self, item: QListWidgetItem):
        row = self.marker_list_widget.row(item)
        if 0 <= row < len(self.markers):
            sec = self.markers[row]
            self.seek(int(sec * 1000), immediate=True)
            self.player.play()

    def move_selected_marker_to_current(self):
        """選択中、または現在位置の最寄りの分割マークを現在の再生位置へ移動"""
        if not self.file_path or not self.markers or self.duration_sec <= 0:
            return

        cur_sec = round(self.slider.position_ms / 1000.0, 3)
        if cur_sec <= 0.05 or cur_sec >= self.duration_sec - 0.05:
            self.statusBar().showMessage("動画の端付近には移動できません", 2000)
            return

        row = self.marker_list_widget.currentRow()
        if 0 <= row < len(self.markers):
            target_idx = row
        else:
            # 最寄りを探す
            closest_idx = 0
            min_diff = abs(self.markers[0] - cur_sec)
            for idx, m in enumerate(self.markers):
                diff = abs(m - cur_sec)
                if diff < min_diff:
                    min_diff = diff
                    closest_idx = idx
            target_idx = closest_idx

        old_sec = self.markers[target_idx]
        if abs(old_sec - cur_sec) < 0.01:
            return

        other_markers = [m for i, m in enumerate(self.markers) if i != target_idx]
        if any(abs(m - cur_sec) <= 0.15 for m in other_markers):
            self.statusBar().showMessage("移動先に別の分割マークがすでに存在します", 2000)
            return

        self._save_undo_state()
        self.markers[target_idx] = cur_sec
        self.slider.selected_marker_ms = int(cur_sec * 1000)
        self._refresh_marker_list()
        self.statusBar().showMessage(f"マークを {self._format_time(old_sec)} から {self._format_time(cur_sec)} へ移動しました", 3000)

    def delete_selected_marker(self):
        """選択中、または現在位置の最寄りの分割マークを削除"""
        if not self.markers:
            return

        row = self.marker_list_widget.currentRow()
        cur_sec = self.slider.position_ms / 1000.0

        if 0 <= row < len(self.markers):
            self._save_undo_state()
            del_sec = self.markers[row]
            del self.markers[row]
            self.slider.selected_marker_ms = None
            self._refresh_marker_list()
            self.statusBar().showMessage(f"分割マークを削除しました: {self._format_time(del_sec)}", 2500)
        else:
            # 未選択の場合、現在位置から最も近いマーカー（±0.5秒以内）を自動判定して削除
            candidates = [(idx, m, abs(m - cur_sec)) for idx, m in enumerate(self.markers) if abs(m - cur_sec) <= 0.5]
            if candidates:
                self._save_undo_state()
                candidates.sort(key=lambda x: x[2])
                idx_to_del, del_sec, _ = candidates[0]
                del self.markers[idx_to_del]
                self.slider.selected_marker_ms = None
                self._refresh_marker_list()
                self.statusBar().showMessage(f"最寄りの分割マークを削除しました: {self._format_time(del_sec)}", 2500)
            else:
                self.statusBar().showMessage("削除対象のマークが選択されていないか、近くにありません", 2000)

    def clear_all_markers(self):
        if not self.markers:
            return
        self._save_undo_state()
        self.markers.clear()
        self.slider.selected_marker_ms = None
        self._refresh_marker_list()

    def _on_marker_list_row_changed(self, row: int):
        if 0 <= row < len(self.markers):
            sec = self.markers[row]
            self.slider.selected_marker_ms = int(sec * 1000)
        else:
            self.slider.selected_marker_ms = None
        self.slider.update()

    def _on_slider_marker_selected(self, marker_ms: int):
        sec = marker_ms / 1000.0
        best_idx = -1
        best_diff = 0.08
        for idx, m in enumerate(self.markers):
            diff = abs(m - sec)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx
        if best_idx >= 0:
            self.marker_list_widget.setCurrentRow(best_idx)

    def _on_slider_marker_delete_requested(self, marker_ms: int):
        sec = marker_ms / 1000.0
        best_idx = -1
        best_diff = 0.1
        for idx, m in enumerate(self.markers):
            diff = abs(m - sec)
            if diff < best_diff:
                best_diff = diff
                best_idx = idx

        if best_idx >= 0:
            self._save_undo_state()
            del_sec = self.markers[best_idx]
            del self.markers[best_idx]
            self.slider.selected_marker_ms = None
            self._refresh_marker_list()
            self.statusBar().showMessage(f"分割マークを削除しました: {self._format_time(del_sec)}", 2500)

    # --- 互換用エイリアス ---
    def set_mark_in(self):
        self.toggle_marker()

    def set_mark_out(self):
        self.toggle_marker()

    def delete_selected_clip(self):
        self.delete_selected_marker()

    def clear_all_clips(self):
        self.clear_all_markers()

    def on_clip_item_clicked(self, item):
        self.on_marker_item_clicked(item)

    # --- クリップ生成 ＆ CMMMレシピ連携 ---
    def get_split_clips(self) -> List[Tuple[float, float]]:
        """分割マーカーを基にクリップ区間リスト [(start, end)] を生成"""
        if not self.file_path or self.duration_sec <= 0:
            return []
        if not self.markers:
            return [(0.0, round(self.duration_sec, 3))]

        points = [0.0] + sorted(self.markers) + [round(self.duration_sec, 3)]
        clips = []
        for i in range(len(points) - 1):
            st = points[i]
            en = points[i + 1]
            if en - st >= 0.05:
                clips.append((st, en))
        return clips

    @property
    def clips(self) -> List[Tuple[float, float]]:
        return self.get_split_clips()

    def build_recipe_dict(self, mode: str = "lossless") -> dict:
        if not self.file_path:
            return {}

        split_clips = self.get_split_clips()
        clip_data = []
        for start, end in split_clips:
            clip_data.append({
                "source": self.file_path,
                "start": round(start, 3),
                "end": round(end, 3)
            })

        return {
            "mode": mode,
            "output_file": "",
            "clips": clip_data
        }

    def on_save_recipe_clicked(self):
        if not self.file_path:
            QMessageBox.warning(self, "警告", "動画ファイルが開かれていません。")
            return

        base, _ = os.path.splitext(self.file_path)
        default_name = f"{base}_recipe.json"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "レシピ保存", default_name, "JSON (*.json)"
        )
        if not save_path:
            return

        recipe = self.build_recipe_dict()
        recipe["output_file"] = f"{base}_cut.mp4"
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(recipe, f, indent=2, ensure_ascii=False)

        QMessageBox.information(self, "保存完了", f"レシピを保存しました:\n{save_path}")

    def on_send_to_cmmm_clicked(self):
        """マークした分割ポイントでクリップに分割して CMMM 本体に送信・展開"""
        if not self.file_path:
            QMessageBox.warning(self, "警告", "動画ファイルが開かれていません。")
            return

        tmp_dir = tempfile.gettempdir()
        tmp_recipe = os.path.join(tmp_dir, "cmmm_player_recipe.json")

        recipe = self.build_recipe_dict()
        base, _ = os.path.splitext(self.file_path)
        recipe["output_file"] = f"{base}_cut.mp4"

        with open(tmp_recipe, "w", encoding="utf-8") as f:
            json.dump(recipe, f, indent=2, ensure_ascii=False)

        # CMMM 本体の探索と起動
        launched = self._launch_cmmm_with_recipe(tmp_recipe)
        if launched:
            clip_count = len(recipe.get("clips", []))
            self.statusBar().showMessage(f"CMMM に {clip_count} 個のクリップを展開して起動しました！", 4000)
        else:
            QMessageBox.critical(
                self,
                "エラー",
                f"CMMM 本体の起動ファイル（CMMM.exe または CMMM.pyw）が見つかりませんでした。\n"
                f"レシピファイルは一時保存されています:\n{tmp_recipe}"
            )

    def _find_python_interpreter(self) -> Optional[str]:
        """Python インタプリタ (pythonw.exe / pyw.exe / python.exe) を安全かつ確実に探索"""
        import shutil

        # 1. 非frozen時なら sys.executable がそのまま安全に使える
        if not getattr(sys, "frozen", False):
            if sys.executable and os.path.exists(sys.executable):
                return sys.executable

        # 2. frozen(exe)時は sys.executable は自分自身(CMMM-Player.exe)なので絶対に使わない！
        # 2-1. システム PATH からの探索
        for name in ["pythonw", "pyw", "python", "py"]:
            found = shutil.which(name)
            if found and os.path.exists(found):
                return found

        # 2-2. Windows レジストリ (Python.NoConFile / Python.File 等) からの探索
        if os.name == "nt":
            try:
                import winreg
                reg_paths = [
                    r"Python.NoConFile\shell\open\command",
                    r"Python.File\shell\open\command",
                    r"Applications\pythonw.exe\shell\open\command",
                    r"Applications\python.exe\shell\open\command",
                ]
                for rp in reg_paths:
                    try:
                        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rp) as key:
                            val, _ = winreg.QueryValueEx(key, "")
                            if val:
                                if val.startswith('"'):
                                    end_idx = val.find('"', 1)
                                    if end_idx != -1:
                                        p = val[1:end_idx]
                                        if os.path.exists(p):
                                            return p
                                else:
                                    p = val.split()[0]
                                    if os.path.exists(p):
                                        return p
                    except Exception:
                        pass
            except Exception:
                pass

        # 2-3. Windows の一般的なインストール場所
        candidates = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Launcher\pyw.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python314\pythonw.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python314\python.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
            r"C:\Windows\pyw.exe",
            r"C:\Windows\py.exe",
        ]
        for p in candidates:
            if os.path.exists(p):
                return p

        return None

    def _launch_cmmm_with_recipe(self, recipe_path: str) -> bool:
        """CMMM 本体（CMMM.exe または CMMM.pyw）を探してレシピを展開起動"""
        search_dirs = []

        # 1. カレントディレクトリ
        search_dirs.append(os.path.abspath("."))

        # 2. 実行ファイル（sys.executable）の場所および親ディレクトリ
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        search_dirs.append(exe_dir)
        search_dirs.append(os.path.dirname(exe_dir))

        # 3. ソーススクリプトディレクトリ（非frozen時）
        try:
            script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            search_dirs.append(script_dir)
        except Exception:
            pass

        # 4. 既知のプロジェクトインストール場所
        search_dirs.append(r"C:\Projects_Apps\my_Projects\CMMM")

        clean_dirs = []
        for d in search_dirs:
            if d and os.path.isdir(d) and d not in clean_dirs:
                clean_dirs.append(d)

        cmmm_exe = None
        cmmm_pyw = None

        for d in clean_dirs:
            p_exe = os.path.join(d, "CMMM.exe")
            if os.path.exists(p_exe) and not cmmm_exe:
                cmmm_exe = p_exe
            p_pyw = os.path.join(d, "CMMM.pyw")
            if os.path.exists(p_pyw) and not cmmm_pyw:
                cmmm_pyw = p_pyw

        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        # 1. CMMM.exe が見つかった場合は直接起動
        if cmmm_exe:
            try:
                subprocess.Popen([cmmm_exe, recipe_path], cwd=os.path.dirname(cmmm_exe), **kwargs)
                return True
            except Exception as e:
                logger.error(f"CMMM.exe 起動失敗: {e}")

        # 2. CMMM.pyw が見つかった場合
        if cmmm_pyw:
            cmmm_dir = os.path.dirname(os.path.abspath(cmmm_pyw))
            python_bin = self._find_python_interpreter()

            if python_bin and os.path.exists(python_bin):
                try:
                    subprocess.Popen([python_bin, cmmm_pyw, recipe_path], cwd=cmmm_dir, **kwargs)
                    return True
                except Exception as e:
                    logger.error(f"Python経由の CMMM.pyw 起動失敗: {e}")

            # Python バイナリが見つからない/直接起動に失敗した場合は Windows シェルで起動
            if os.name == "nt":
                try:
                    subprocess.Popen(f'start "" "{cmmm_pyw}" "{recipe_path}"', shell=True, cwd=cmmm_dir)
                    return True
                except Exception as e:
                    logger.error(f"シェル経由の CMMM.pyw 起動失敗: {e}")

        return False

    def load_recipe(self, recipe_path: str):
        if not os.path.exists(recipe_path):
            return
        try:
            with open(recipe_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            clips = data.get("clips", [])
            if clips:
                first_src = clips[0].get("source")
                if first_src and os.path.exists(first_src):
                    self.open_video(first_src)
                self.markers.clear()
                # クリップの境界点をマーカーとして復元
                for i in range(len(clips) - 1):
                    end_pt = float(clips[i].get("end", 0))
                    if end_pt > 0:
                        self.markers.append(round(end_pt, 3))
                self._refresh_marker_list()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"レシピ読み込み失敗:\n{e}")

    # --- ユーティリティ ---
    def _format_time(self, sec: float) -> str:
        s = max(0.0, sec)
        m, s = divmod(s, 60.0)
        h, m = divmod(m, 60.0)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
