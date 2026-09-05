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
from typing import Optional, List, Tuple

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QFileDialog, QMessageBox, QSlider,
    QListWidget, QListWidgetItem, QStyle, QApplication, QComboBox
)
from PySide6.QtCore import Qt, QUrl, Signal, QRectF, QPointF, QPoint, QSize, QTimer, QEvent
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

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            if ext in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv"):
                self.fileDropped.emit(path)
                break


class PlayerSlider(QWidget):
    """マーク済み区間の可視化ハイライトを備えたシークバー"""
    positionChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(28)
        self.duration_ms = 1000
        self.position_ms = 0
        self.dragging = False
        self.hover_x: Optional[float] = None

        # マーク情報
        self.mark_in_ms: Optional[int] = None
        self.clips: List[Tuple[int, int]] = []  # (start_ms, end_ms)

        self.setMouseTracking(True)

    def set_duration(self, ms: int):
        self.duration_ms = max(1, ms)
        self.position_ms = min(self.position_ms, self.duration_ms)
        self.update()

    def set_position(self, ms: int):
        if not self.dragging:
            self.position_ms = max(0, min(self.duration_ms, ms))
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

        # 2. 登録済みマーク区間（シアン・ブルー帯）
        for c_start, c_end in self.clips:
            x1 = self._pos_to_x(c_start)
            x2 = self._pos_to_x(c_end)
            if x2 > x1:
                clip_rect = QRectF(x1, track_y, x2 - x1, track_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(0, 120, 212, 160))
                painter.drawRect(clip_rect)

        # 3. 現在マーク中のプレビュー区間（In点から現在位置、黄色）
        if self.mark_in_ms is not None:
            x1 = self._pos_to_x(self.mark_in_ms)
            x2 = self._pos_to_x(self.position_ms)
            if x1 != x2:
                start_x = min(x1, x2)
                span_w = abs(x2 - x1)
                mark_rect = QRectF(start_x, track_y, span_w, track_h)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(255, 185, 0, 150))
                painter.drawRect(mark_rect)

            # In点位置の縦マーカーピン
            painter.setPen(QPen(QColor(255, 185, 0), 2))
            painter.drawLine(QPointF(x1, track_y - 3), QPointF(x1, track_y + track_h + 3))

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


class PlayerWindow(QMainWindow):
    """CMMM 連携用動画プレイヤーメインウィンドウ"""
    def __init__(self, initial_video_path: str = "", initial_recipe_path: str = ""):
        super().__init__()
        self.setWindowTitle("CMMM プレイヤー")
        self.resize(1060, 680)

        # 状態
        self.file_path: Optional[str] = None
        self.duration_sec: float = 0.0
        self.mark_in_sec: Optional[float] = None
        self.clips: List[Tuple[float, float]] = []  # [(start_sec, end_sec)]
        self.playback_rate = 1.0
        self.speed_preset_index = 2
        self.saved_volume = 70

        # スキップ設定 (初期値: 5秒, index=0)
        self.skip_seconds_value = 5
        self.skip_preset_index = 0

        # 全画面時のマウス位置監視タイマー (下部ホバーで即時表示、離脱で即時非表示)
        self.hover_check_timer = QTimer(self)
        self.hover_check_timer.setInterval(50)
        self.hover_check_timer.timeout.connect(self._update_fullscreen_controls_visibility)

        # メディアプレイヤー初期化
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)

        self._setup_ui()
        self._setup_shortcuts()
        self._setup_player_events()

        self.setAcceptDrops(True)

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
        self.video_widget.fileDropped.connect(self.open_video)
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
        bottom_layout.addWidget(self.slider)

        # コントロール行
        ctrl_layout = QHBoxLayout()

        # ファイルを開くボタン
        btn_open = QPushButton("開く")
        btn_open.setToolTip("動画ファイルを開く (Ctrl+O)")
        btn_open.clicked.connect(self.on_open_file_dialog)
        ctrl_layout.addWidget(btn_open)

        # スキップ & コマ送り
        init_skip_label = SKIP_PRESETS[self.skip_preset_index][1]
        self.btn_skip_prev = QPushButton(f"◀ {init_skip_label}")
        self.btn_skip_prev.setToolTip(f"{init_skip_label} 戻る (Left)")
        self.btn_skip_prev.clicked.connect(lambda: self.skip_seconds(-self.skip_seconds_value))
        ctrl_layout.addWidget(self.btn_skip_prev)

        btn_step_m1 = QPushButton("◀ 1コマ")
        btn_step_m1.setAutoRepeat(True)
        btn_step_m1.setAutoRepeatDelay(300)
        btn_step_m1.setAutoRepeatInterval(60)
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
        btn_step_p1.clicked.connect(lambda: self.step_frames(1))
        ctrl_layout.addWidget(btn_step_p1)

        self.btn_skip_next = QPushButton(f"{init_skip_label} ▶")
        self.btn_skip_next.setToolTip(f"{init_skip_label} 進む (Right)")
        self.btn_skip_next.clicked.connect(lambda: self.skip_seconds(self.skip_seconds_value))
        ctrl_layout.addWidget(self.btn_skip_next)

        # スキップ秒数セレクター
        self.combo_skip = QComboBox()
        self.combo_skip.setToolTip("スキップ時間設定 (左右キーにも連動)")
        for sec, label in SKIP_PRESETS:
            self.combo_skip.addItem(label, sec)
        self.combo_skip.setCurrentIndex(self.skip_preset_index)
        self.combo_skip.currentIndexChanged.connect(self._on_skip_preset_changed)
        ctrl_layout.addWidget(self.combo_skip)

        ctrl_layout.addSpacing(10)

        # 音量セクション
        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFixedSize(30, 30)
        self.btn_mute.setToolTip("ミュート切替 (M)")
        self.btn_mute.setStyleSheet("padding: 0; font-size: 14px;")
        self.btn_mute.clicked.connect(self.toggle_mute)
        ctrl_layout.addWidget(self.btn_mute)

        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(70)
        self.volume_slider.setFixedWidth(80)
        self.volume_slider.setToolTip("音量スライダー (ホイールまたは上下キー)")
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        ctrl_layout.addWidget(self.volume_slider)

        self.lbl_volume = QLabel("70%")
        self.lbl_volume.setStyleSheet("color: #888; min-width: 32px; font-size: 11px;")
        ctrl_layout.addWidget(self.lbl_volume)

        ctrl_layout.addSpacing(10)

        # In / Out マーキングボタン
        self.btn_in = QPushButton("［ In (I)")
        self.btn_in.setToolTip("現在の位置を開始点にセット (I)")
        self.btn_in.setStyleSheet("font-weight: bold; color: #ffb900;")
        self.btn_in.clicked.connect(self.set_mark_in)
        ctrl_layout.addWidget(self.btn_in)

        self.btn_out = QPushButton("Out (O) ］")
        self.btn_out.setToolTip("現在の位置を終了点にして区間登録 (O)")
        self.btn_out.setStyleSheet("font-weight: bold; color: #60cdff;")
        self.btn_out.clicked.connect(self.set_mark_out)
        ctrl_layout.addWidget(self.btn_out)

        ctrl_layout.addStretch()

        # 全画面ボタン
        self.btn_fullscreen = QPushButton("⛶")
        self.btn_fullscreen.setFixedSize(30, 30)
        self.btn_fullscreen.setToolTip("全画面表示の切替 (F / ダブルクリック)")
        self.btn_fullscreen.setStyleSheet("font-size: 14px; padding: 0;")
        self.btn_fullscreen.clicked.connect(self.toggle_fullscreen)
        ctrl_layout.addWidget(self.btn_fullscreen)

        bottom_layout.addLayout(ctrl_layout)
        self.player_layout.addWidget(self.bottom_controls)
        self.splitter.addWidget(self.player_container)

        # ====================================================
        # 右側: マーク区間リストパネル
        # ====================================================
        self.right_panel = QWidget()
        self.right_panel.setMinimumWidth(260)
        self.right_panel.setMaximumWidth(340)
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(4, 0, 4, 0)
        right_layout.setSpacing(8)

        # パネルタイトル
        header_row = QHBoxLayout()
        lbl_list_title = QLabel("マーク区間")
        lbl_list_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #60cdff;")
        self.lbl_clip_count = QLabel("0 件")
        self.lbl_clip_count.setStyleSheet("color: #888;")
        header_row.addWidget(lbl_list_title)
        header_row.addStretch()
        header_row.addWidget(self.lbl_clip_count)
        right_layout.addLayout(header_row)

        # 区間リスト
        self.clip_list_widget = QListWidget()
        self.clip_list_widget.itemDoubleClicked.connect(self.on_clip_item_clicked)
        right_layout.addWidget(self.clip_list_widget)

        # リスト操作ボタン
        list_btn_row = QHBoxLayout()
        btn_del_clip = QPushButton("削除")
        btn_del_clip.setToolTip("選択した区間を削除 (Delete)")
        btn_del_clip.clicked.connect(self.delete_selected_clip)
        list_btn_row.addWidget(btn_del_clip)

        btn_clear_clips = QPushButton("全クリア")
        btn_clear_clips.clicked.connect(self.clear_all_clips)
        list_btn_row.addWidget(btn_clear_clips)
        right_layout.addLayout(list_btn_row)

        # 合計時間サマリー
        self.lbl_summary = QLabel("合計時間: 00:00:00.000")
        self.lbl_summary.setStyleSheet("color: #aaa; font-size: 11px;")
        right_layout.addWidget(self.lbl_summary)

        right_layout.addSpacing(6)

        # 連携アクションボタン
        self.btn_send_cmmm = QPushButton("🎬 CMMMで開く")
        self.btn_send_cmmm.setObjectName("accentButton")
        self.btn_send_cmmm.setStyleSheet("padding: 8px 12px; font-size: 13px;")
        self.btn_send_cmmm.setToolTip("マークした区間をCMMMのストーリーボードへ即座に展開")
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
        # I / O: マーキング
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self.set_mark_in)
        QShortcut(QKeySequence(Qt.Key.Key_O), self, self.set_mark_out)
        # M: ミュート
        QShortcut(QKeySequence(Qt.Key.Key_M), self, self.toggle_mute)
        # ジャンプ / スキップ ショートカット
        # 5秒 (Left / Right)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.skip_seconds(-self.skip_seconds_value))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.skip_seconds(self.skip_seconds_value))
        # 30秒 (Ctrl+Left / Ctrl+Right)
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.skip_seconds(-30))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.skip_seconds(30))
        # 1分 (Shift+Left / Shift+Right)
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.skip_seconds(-60))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.skip_seconds(60))
        # 5分 (Ctrl+Alt+Left / Ctrl+Alt+Right)
        QShortcut(QKeySequence("Ctrl+Alt+Left"), self, lambda: self.skip_seconds(-300))
        QShortcut(QKeySequence("Ctrl+Alt+Right"), self, lambda: self.skip_seconds(300))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.change_volume(5))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.change_volume(-5))
        # コマ送り [ / ]
        QShortcut(QKeySequence(Qt.Key.Key_BracketLeft), self, lambda: self.step_frames(-1))
        QShortcut(QKeySequence(Qt.Key.Key_BracketRight), self, lambda: self.step_frames(1))
        # Delete: 選択区間削除
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.delete_selected_clip)
        # Ctrl+O / Ctrl+S
        QShortcut(QKeySequence("Ctrl+O"), self, self.on_open_file_dialog)
        QShortcut(QKeySequence("Ctrl+S"), self, self.on_save_recipe_clicked)

    def _setup_player_events(self):
        self.player.positionChanged.connect(self._on_player_position_changed)
        self.player.durationChanged.connect(self._on_player_duration_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    # --- ドラッグ＆ドロップ ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            ext = os.path.splitext(path)[1].lower()
            if ext in (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".ts", ".wmv"):
                self.open_video(path)
                break
            elif ext == ".json":
                self.load_recipe(path)
                break

    # --- メディア再生制御 ---
    def open_video(self, file_path: str):
        norm_path = os.path.abspath(file_path)
        if not os.path.exists(norm_path):
            return

        self.file_path = norm_path
        self.lbl_file_name.setText(os.path.basename(norm_path))
        self.mark_in_sec = None
        self.btn_in.setStyleSheet("font-weight: bold; color: #ffb900;")

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

        self.player.setSource(QUrl.fromLocalFile(norm_path))
        self.player.setPlaybackRate(self.playback_rate)
        self.player.setPosition(0)
        self.player.play()
        self.btn_play.setText("⏸ 一時停止")
        self.statusBar().showMessage(f"再生中: {os.path.basename(norm_path)}", 3000)

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
        self.slider.set_position(pos_ms)
        sec = pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(sec))

    def _on_player_duration_changed(self, dur_ms: int):
        self.slider.set_duration(dur_ms)
        self.duration_sec = dur_ms / 1000.0
        self.lbl_total_time.setText(self._format_time(self.duration_sec))

    def seek(self, pos_ms: int):
        """再生位置のシーク（スライダーとプレイヤーの即時同期）"""
        self.slider.set_position(pos_ms)
        self.player.setPosition(pos_ms)
        sec = pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(sec))

    def on_slider_seek(self, pos_ms: int):
        self.seek(pos_ms)

    def skip_seconds(self, sec: float):
        cur = max(self.player.position(), self.slider.position_ms)
        new_ms = max(0, min(int(self.duration_sec * 1000), cur + int(sec * 1000)))
        self.seek(new_ms)

    def step_frames(self, frames: int):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        fps = 30.0  # 標準fps
        delta_ms = int((1000.0 / fps) * frames)
        cur = max(self.player.position(), self.slider.position_ms)
        new_ms = max(0, min(int(self.duration_sec * 1000), cur + delta_ms))
        self.seek(new_ms)

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
        else:
            self.saved_volume = self.volume_slider.value()
            self.audio_output.setMuted(True)
            self.volume_slider.setValue(0)
            self.btn_mute.setText("🔇")

    def change_volume(self, delta: int):
        cur = self.volume_slider.value()
        new_vol = max(0, min(100, cur + delta))
        self.volume_slider.setValue(new_vol)

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
            self.statusBar().showMessage(f"スキップ時間を {label} に変更しました", 2000)

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
        self._update_fullscreen_controls_visibility()

    def exit_fullscreen(self):
        if self.isFullScreen():
            self.hover_check_timer.stop()
            self.bottom_controls.show()
            self.showNormal()
            self.right_panel.setVisible(True)
            self.btn_fullscreen.setText("⛶")

    def _update_fullscreen_controls_visibility(self):
        if not self.isFullScreen():
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

    def eventFilter(self, watched, event):
        if self.isFullScreen():
            if event.type() in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.Enter, QEvent.Type.Leave):
                self._update_fullscreen_controls_visibility()
        return super().eventFilter(watched, event)

    # --- マーキング（In / Out） ---
    def set_mark_in(self):
        if not self.file_path:
            return
        cur_pos_ms = max(self.player.position(), self.slider.position_ms)
        cur_sec = cur_pos_ms / 1000.0
        self.mark_in_sec = cur_sec
        self.slider.set_mark_in(int(cur_sec * 1000))
        self.btn_in.setStyleSheet("background-color: rgba(255, 185, 0, 0.3); border: 2px solid #ffb900; color: #ffb900; font-weight: bold;")
        self.statusBar().showMessage(f"In点をセット: {self._format_time(cur_sec)}", 3000)

    def set_mark_out(self):
        if not self.file_path:
            return
        cur_pos_ms = max(self.player.position(), self.slider.position_ms)
        cur_sec = cur_pos_ms / 1000.0
        start = self.mark_in_sec if self.mark_in_sec is not None else 0.0
        end = max(start + 0.1, cur_sec)

        # 区間登録
        self.clips.append((start, end))
        self.mark_in_sec = None
        self.slider.set_mark_in(None)
        self.btn_in.setStyleSheet("font-weight: bold; color: #ffb900;")

        self._refresh_clip_list()
        self.statusBar().showMessage(f"区間を登録: {self._format_time(start)} ～ {self._format_time(end)}", 3000)

    def _refresh_clip_list(self):
        self.clip_list_widget.clear()
        total_dur = 0.0
        slider_clips = []

        for idx, (start, end) in enumerate(self.clips, start=1):
            dur = end - start
            total_dur += dur
            text = f"{idx}. {self._format_time(start)} ～ {self._format_time(end)} ({dur:.1f}秒)"
            item = QListWidgetItem(text)
            self.clip_list_widget.addItem(item)
            slider_clips.append((int(start * 1000), int(end * 1000)))

        self.lbl_clip_count.setText(f"{len(self.clips)} 件")
        self.lbl_summary.setText(f"合計時間: {self._format_time(total_dur)}")
        self.slider.set_clips(slider_clips)

    def on_clip_item_clicked(self, item: QListWidgetItem):
        row = self.clip_list_widget.row(item)
        if 0 <= row < len(self.clips):
            start, end = self.clips[row]
            self.player.setPosition(int(start * 1000))
            self.player.play()

    def delete_selected_clip(self):
        row = self.clip_list_widget.currentRow()
        if 0 <= row < len(self.clips):
            del self.clips[row]
            self._refresh_clip_list()

    def clear_all_clips(self):
        if not self.clips:
            return
        self.clips.clear()
        self.mark_in_sec = None
        self.slider.set_mark_in(None)
        self.btn_in.setStyleSheet("font-weight: bold; color: #ffb900;")
        self._refresh_clip_list()

    # --- レシピ生成 & 連携 ---
    def build_recipe_dict(self, mode: str = "lossless") -> dict:
        if not self.file_path:
            return {}

        clip_data = []
        for start, end in self.clips:
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
        if not self.file_path or not self.clips:
            QMessageBox.warning(self, "警告", "保存するマーク区間がありません。")
            return

        base, _ = os.path.splitext(self.file_path)
        default_name = f"{base}_recipe.json"
        save_path, _ = QFileDialog.getSaveFileName(
            self, "レシピ保存", default_name, "JSON (*.json)"
        )
        if not save_path:
            return

        recipe = self.build_recipe_dict()
        recipe["output_file"] = f"{base}_output.mp4"
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(recipe, f, indent=2, ensure_ascii=False)

        QMessageBox.information(self, "保存完了", f"レシピを保存しました:\n{save_path}")

    def on_send_to_cmmm_clicked(self):
        """マークした区間を CMMM 本体に送信して起動・展開"""
        if not self.file_path or not self.clips:
            QMessageBox.warning(self, "警告", "CMMMに送るマーク区間がありません。\n先にIn/Outで区間をマークしてください。")
            return

        # 一時レシピファイルを作成
        tmp_dir = tempfile.gettempdir()
        tmp_recipe = os.path.join(tmp_dir, "cmmm_player_recipe.json")

        recipe = self.build_recipe_dict()
        base, _ = os.path.splitext(self.file_path)
        recipe["output_file"] = f"{base}_cut.mp4"

        with open(tmp_recipe, "w", encoding="utf-8") as f:
            json.dump(recipe, f, indent=2, ensure_ascii=False)

        # CMMM.pyw を起動
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmmm_script = os.path.join(root_dir, "CMMM.pyw")

        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            subprocess.Popen([sys.executable, cmmm_script, tmp_recipe], **kwargs)
            self.statusBar().showMessage("CMMM にクリップを展開して起動しました！", 4000)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"CMMM の起動に失敗しました:\n{e}")

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
                self.clips.clear()
                for c in clips:
                    self.clips.append((float(c.get("start", 0)), float(c.get("end", 0))))
                self._refresh_clip_list()
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"レシピ読み込み失敗:\n{e}")

    # --- ユーティリティ ---
    def _format_time(self, sec: float) -> str:
        s = max(0.0, sec)
        m, s = divmod(s, 60.0)
        h, m = divmod(m, 60.0)
        return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"
