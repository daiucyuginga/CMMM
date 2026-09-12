"""
ui/main_window.py
CMMM メインウィンドウ
Windows ムービーメーカーの直感的な切り貼り編集（プレビュー位置で分割 → 不要クリップをDeleteで削除）
＋ 再生ボタン長押しでの倍速再生 (2.0x) & 右クリック速度選択
＋ Undo 機能 (Ctrl+Z / 元に戻す)
"""

import os
import sys
import time
import json
import threading
import subprocess
from typing import Optional, List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QFileDialog, QMessageBox, QProgressDialog,
    QButtonGroup, QGroupBox, QToolBar, QSlider
)
from PySide6.QtCore import Qt, QUrl, Slot, Signal, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from core.ffmpeg_tools import FFmpegRunner
from core.gop_analyzer import VideoAnalyzer, VideoMetadata
from core.engine import execute_recipe
from .styles import MODERN_DARK_STYLE
from .storyboard import StoryboardWidget, StoryboardItem
from .range_slider import VideoSlider
from .async_thumbnail import ThumbnailManager
from .async_waveform import WaveformManager


def generate_default_output_path(source_file_path: str) -> str:
    """元ファイル名に (1), (2)... を付与した重複しない出力パスを生成"""
    dir_name = os.path.dirname(source_file_path)
    base_name, ext = os.path.splitext(os.path.basename(source_file_path))

    idx = 1
    while True:
        candidate = os.path.join(dir_name, f"{base_name}({idx}){ext}")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


SPEED_PRESETS = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0]


class ClickableVideoWidget(QVideoWidget):
    """クリックで再生/一時停止を切り替える動画プレビュー画面"""
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)



class MainWindow(QMainWindow):
    exportProgressSignal = Signal(int, int, str)
    exportFinishedSignal = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CMMM")
        self.resize(1200, 820)
        self.setMinimumSize(960, 620)
        self.setStyleSheet(MODERN_DARK_STYLE)

        self.runner = FFmpegRunner()
        self.analyzer = VideoAnalyzer(self.runner)

        # 状態
        self.base_meta: Optional[VideoMetadata] = None
        self.mode = "lossless"
        self.active_clip_item: Optional[StoryboardItem] = None
        self.playback_rate = 1.0
        self.speed_preset_index = 2
        self.saved_volume = 70
        self.source_metadata: dict[str, dict] = {}

        # Undo 履歴スタック & サムネイルキャッシュ
        self.undo_stack: List[dict] = []
        self.thumb_cache: dict[str, QPixmap] = {}

        # メディアプレイヤー初期化
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(0.7)
        self.video_widget = ClickableVideoWidget(self)
        self.video_widget.clicked.connect(self.toggle_play)
        self.player.setVideoOutput(self.video_widget)

        self._setup_ui()
        self._setup_shortcuts()
        self._setup_player_events()

        self.setAcceptDrops(True)

    def _setup_ui(self):
        # 1. ツールバー
        toolbar = QToolBar("MainToolbar", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        btn_add = QPushButton("➕ 動画を追加")
        btn_add.clicked.connect(self.on_add_video_clicked)
        toolbar.addWidget(btn_add)

        toolbar.addSeparator()

        # Undo ボタン
        self.btn_undo = QPushButton("↩ 元に戻す")
        self.btn_undo.setToolTip("元に戻す (Ctrl+Z)")
        self.btn_undo.clicked.connect(self.undo)
        toolbar.addWidget(self.btn_undo)

        toolbar.addSeparator()

        lbl_mode = QLabel(" モード: ")
        lbl_mode.setStyleSheet("color: #aaa;")
        toolbar.addWidget(lbl_mode)

        self.btn_lossless = QPushButton("完全無劣化")
        self.btn_lossless.setCheckable(True)
        self.btn_lossless.setChecked(True)
        self.btn_lossless.setProperty("class", "modeButton")
        self.btn_lossless.clicked.connect(lambda: self.set_mode("lossless"))
        toolbar.addWidget(self.btn_lossless)

        self.btn_smart = QPushButton("スマートカット")
        self.btn_smart.setCheckable(True)
        self.btn_smart.setProperty("class", "modeButton")
        self.btn_smart.clicked.connect(lambda: self.set_mode("smart"))
        toolbar.addWidget(self.btn_smart)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.btn_lossless)
        self.mode_group.addButton(self.btn_smart)

        toolbar.addSeparator()

        self.btn_load_recipe = QPushButton("レシピ読込")
        self.btn_load_recipe.clicked.connect(self.on_load_recipe_clicked)
        toolbar.addWidget(self.btn_load_recipe)

        self.btn_save_recipe = QPushButton("レシピ保存")
        self.btn_save_recipe.clicked.connect(self.on_save_recipe_clicked)
        toolbar.addWidget(self.btn_save_recipe)

        toolbar.addSeparator()

        self.btn_player = QPushButton("プレイヤー")
        self.btn_player.setToolTip("視聴・マーク用プレイヤーを起動")
        self.btn_player.clicked.connect(self.on_open_player_clicked)
        toolbar.addWidget(self.btn_player)

        toolbar.addSeparator()

        btn_export = QPushButton("エクスポート")
        btn_export.setObjectName("accentButton")
        btn_export.clicked.connect(self.on_export_clicked)
        toolbar.addWidget(btn_export)

        self.btn_ffmpeg_log = QPushButton("FFmpegログ")
        self.btn_ffmpeg_log.setToolTip("最後に実行した FFmpeg コマンドとエラー出力を表示")
        self.btn_ffmpeg_log.clicked.connect(self.show_ffmpeg_log)
        toolbar.addWidget(self.btn_ffmpeg_log)

        # 2. メインレイアウト
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        v_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(v_splitter)

        # 上部エリア: プレビュー + 右側情報パネル
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        v_splitter.addWidget(h_splitter)

        # --- プレビューセクション ---
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)

        # 動画画面
        self.video_widget.setStyleSheet("background-color: #000; border-radius: 6px;")
        self.video_widget.setMinimumHeight(320)
        preview_layout.addWidget(self.video_widget, stretch=1)

        # タイムコード行
        tc_layout = QHBoxLayout()
        self.lbl_current_time = QLabel("00:00:00.000")
        self.lbl_current_time.setProperty("class", "timeCode")
        tc_sep = QLabel("/")
        tc_sep.setStyleSheet("color: #666;")
        self.lbl_total_time = QLabel("00:00:00.000")
        self.lbl_total_time.setProperty("class", "timeCode")

        self.lbl_clip_name_banner = QLabel("")
        self.lbl_clip_name_banner.setStyleSheet("color: #aaa; margin-left: 12px;")

        tc_layout.addWidget(self.lbl_current_time)
        tc_layout.addWidget(tc_sep)
        tc_layout.addWidget(self.lbl_total_time)
        tc_layout.addWidget(self.lbl_clip_name_banner)
        tc_layout.addStretch()

        # 速度調整セクション (ステップボタン式: [-] 1.0x [+])
        speed_box = QWidget()
        speed_h = QHBoxLayout(speed_box)
        speed_h.setContentsMargins(0, 0, 0, 0)
        speed_h.setSpacing(4)

        lbl_speed_title = QLabel("速度")
        lbl_speed_title.setStyleSheet("color: #888; font-size: 11px;")
        speed_h.addWidget(lbl_speed_title)

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
        preview_layout.addLayout(tc_layout)

        # WMM風の再生シークバー
        self.video_slider = VideoSlider(self)
        self.video_slider.positionChanged.connect(self.on_slider_position_changed)
        self.video_slider.wheelStep.connect(self.on_wheel_step)
        preview_layout.addWidget(self.video_slider)

        # コントロール行
        ctrl_layout = QHBoxLayout()

        btn_skip_m15 = QPushButton("◀ 15秒")
        btn_skip_m15.clicked.connect(lambda: self.skip_seconds(-15))

        btn_skip_m1 = QPushButton("◀ 1秒")
        btn_skip_m1.setAutoRepeat(True)
        btn_skip_m1.setAutoRepeatDelay(350)
        btn_skip_m1.setAutoRepeatInterval(120)
        btn_skip_m1.clicked.connect(lambda: self.skip_seconds(-1))

        btn_step_m1f = QPushButton("◀ 1コマ")
        btn_step_m1f.setAutoRepeat(True)
        btn_step_m1f.setAutoRepeatDelay(300)
        btn_step_m1f.setAutoRepeatInterval(60)
        btn_step_m1f.clicked.connect(lambda: self.step_frames(-1))

        # 再生ボタン (シンプルなQPushButton)
        self.btn_play = QPushButton("▶ 再生")
        self.btn_play.setStyleSheet("background-color: #0078d4; color: #fff; font-weight: bold; min-width: 76px; padding: 6px 12px;")
        self.btn_play.setToolTip("再生 / 一時停止 (Space)")
        self.btn_play.clicked.connect(self.toggle_play)

        btn_step_p1f = QPushButton("1コマ ▶")
        btn_step_p1f.setAutoRepeat(True)
        btn_step_p1f.setAutoRepeatDelay(300)
        btn_step_p1f.setAutoRepeatInterval(60)
        btn_step_p1f.clicked.connect(lambda: self.step_frames(1))

        btn_skip_p1 = QPushButton("1秒 ▶")
        btn_skip_p1.setAutoRepeat(True)
        btn_skip_p1.setAutoRepeatDelay(350)
        btn_skip_p1.setAutoRepeatInterval(120)
        btn_skip_p1.clicked.connect(lambda: self.skip_seconds(1))

        btn_skip_p15 = QPushButton("15秒 ▶")
        btn_skip_p15.clicked.connect(lambda: self.skip_seconds(15))

        ctrl_layout.addWidget(btn_skip_m15)
        ctrl_layout.addWidget(btn_skip_m1)
        ctrl_layout.addWidget(btn_step_m1f)
        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addWidget(btn_step_p1f)
        ctrl_layout.addWidget(btn_skip_p1)
        ctrl_layout.addWidget(btn_skip_p15)

        ctrl_layout.addSpacing(20)

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
        self.volume_slider.setFixedWidth(90)
        self.volume_slider.setToolTip("音量スライダー (上下キーまたはホイール)")
        self.volume_slider.valueChanged.connect(self.on_volume_changed)
        ctrl_layout.addWidget(self.volume_slider)

        self.lbl_volume = QLabel("70%")
        self.lbl_volume.setStyleSheet("color: #888; min-width: 32px; font-size: 11px;")
        ctrl_layout.addWidget(self.lbl_volume)

        ctrl_layout.addSpacing(10)

        # 分割ボタン
        self.btn_split = QPushButton("✂ 分割")
        self.btn_split.setObjectName("splitButton")
        self.btn_split.setToolTip("ここで分割 (S)")
        self.btn_split.setStyleSheet("font-size: 14px; font-weight: bold; padding: 6px 16px; background-color: rgba(96, 205, 255, 0.18); border: 2px solid #60cdff; color: #60cdff;")
        self.btn_split.clicked.connect(lambda: self.split_current_clip())
        ctrl_layout.addWidget(self.btn_split)

        ctrl_layout.addStretch()

        # 削除ボタン
        btn_delete_clip = QPushButton("🗑️ 削除")
        btn_delete_clip.setToolTip("選択クリップを削除 (Delete)")
        btn_delete_clip.clicked.connect(lambda: self.storyboard.delete_selected())
        ctrl_layout.addWidget(btn_delete_clip)

        preview_layout.addLayout(ctrl_layout)
        h_splitter.addWidget(preview_container)

        # --- 右側情報パネル ---
        side_panel = QWidget()
        side_panel.setMaximumWidth(260)
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(4, 0, 4, 0)
        side_layout.setSpacing(10)

        gb_base = QGroupBox("プロジェクト")
        gb_base_layout = QVBoxLayout(gb_base)
        self.lbl_base_file = QLabel("ファイル: 未設定")
        self.lbl_base_res = QLabel("解像度: -")
        self.lbl_base_codec = QLabel("コーデック: -")
        self.lbl_base_clips = QLabel("クリップ数: 0 本")
        self.lbl_base_dur = QLabel("合計時間: 00:00:00")
        gb_base_layout.addWidget(self.lbl_base_file)
        gb_base_layout.addWidget(self.lbl_base_res)
        gb_base_layout.addWidget(self.lbl_base_codec)
        gb_base_layout.addWidget(self.lbl_base_clips)
        gb_base_layout.addWidget(self.lbl_base_dur)
        side_layout.addWidget(gb_base)

        gb_clip = QGroupBox("クリップ")
        gb_clip_layout = QVBoxLayout(gb_clip)
        self.lbl_clip_name = QLabel("名前: -")
        self.lbl_clip_in = QLabel("開始: -")
        self.lbl_clip_out = QLabel("終了: -")
        self.lbl_clip_len = QLabel("長さ: -")
        gb_clip_layout.addWidget(self.lbl_clip_name)
        gb_clip_layout.addWidget(self.lbl_clip_in)
        gb_clip_layout.addWidget(self.lbl_clip_out)
        gb_clip_layout.addWidget(self.lbl_clip_len)
        side_layout.addWidget(gb_clip)

        gb_tips = QGroupBox("ショートカット")
        gb_tips_layout = QVBoxLayout(gb_tips)
        gb_tips_layout.addWidget(QLabel("Space : 再生 / 停止"))
        gb_tips_layout.addWidget(QLabel("Ctrl + Z : 元に戻す"))
        gb_tips_layout.addWidget(QLabel("S : 分割"))
        gb_tips_layout.addWidget(QLabel("Delete : 削除"))
        gb_tips_layout.addWidget(QLabel("← / → : 1コマ移動"))
        gb_tips_layout.addWidget(QLabel("Ctrl + ←/→ : 1秒スキップ"))
        gb_tips_layout.addWidget(QLabel("↑ / ↓ : 15秒スキップ"))
        side_layout.addWidget(gb_tips)

        side_layout.addStretch()
        h_splitter.addWidget(side_panel)
        h_splitter.setStretchFactor(0, 1)

        # --- 下部：ストーリーボード ---
        sb_container = QWidget()
        sb_container.setMinimumHeight(220)
        sb_layout = QVBoxLayout(sb_container)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(4)

        sb_header = QHBoxLayout()
        sb_title = QLabel("ストーリーボード")
        sb_title.setStyleSheet("font-weight: bold; color: #ddd;")
        sb_header.addWidget(sb_title)
        sb_header.addStretch()
        sb_layout.addLayout(sb_header)

        self.storyboard = StoryboardWidget(self)
        self.storyboard.filesDropped.connect(self.add_videos)
        self.storyboard.itemClicked.connect(self.on_clip_selected)
        self.storyboard.clipDeleted.connect(self.on_clip_deleted)
        self.storyboard.orderChanged.connect(self.update_stats)
        self.storyboard.beforeChange.connect(self._save_undo_state)
        sb_layout.addWidget(self.storyboard)

        v_splitter.addWidget(sb_container)
        v_splitter.setStretchFactor(0, 5)
        v_splitter.setStretchFactor(1, 2)
        v_splitter.setSizes([520, 240])

        self.statusBar().showMessage("準備完了")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_S), self, self.split_current_clip)
        QShortcut(QKeySequence(Qt.Key.Key_M), self, self.split_current_clip)
        QShortcut(QKeySequence("Ctrl+M"), self, self.toggle_mute)
        QShortcut(QKeySequence("Ctrl+Z"), self, self.undo)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.step_frames(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.step_frames(1))
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.skip_seconds(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.skip_seconds(1))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.change_volume(5))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.change_volume(-5))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.storyboard.delete_selected)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self, self.storyboard.delete_selected)

    def _setup_player_events(self):
        self.player.positionChanged.connect(self.on_player_position_changed)
        self.player.playbackStateChanged.connect(self._on_playback_state_changed)
        self.exportProgressSignal.connect(self._update_export_progress)
        self.exportFinishedSignal.connect(self._finish_export)

    def _on_playback_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play.setText("⏸ 一時停止")
        else:
            self.btn_play.setText("▶ 再生")

    def set_mode(self, mode: str):
        self.mode = mode
        msg = "完全無劣化モード" if mode == "lossless" else "スマートカットモード"
        self.statusBar().showMessage(msg, 3000)

    # ========================================================
    # 【Undo機能】直前の操作を取り消して1つ前の状態に戻す
    # ========================================================
    def _save_undo_state(self):
        """現在のストーリーボードと選択状態をスタックに退避"""
        snapshot = []
        for i in range(self.storyboard.count()):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem):
                snapshot.append({
                    "clip_id": item.clip_id,
                    "file_path": item.file_path,
                    "start_sec": item.start_sec,
                    "end_sec": item.end_sec,
                    "total_sec": item.total_sec
                })

        cur_row = self.storyboard.currentRow()
        self.undo_stack.append({
            "clips": snapshot,
            "cur_row": cur_row,
            "base_meta": self.base_meta
        })
        if len(self.undo_stack) > 40:
            self.undo_stack.pop(0)

    def undo(self):
        """Ctrl+Z: 1つ前の編集状態を復元"""
        if not self.undo_stack:
            self.statusBar().showMessage("これ以上元に戻せません", 2000)
            return

        state = self.undo_stack.pop()
        clips = state.get("clips", [])
        cur_row = state.get("cur_row", 0)
        self.base_meta = state.get("base_meta")

        # ストーリーボードをクリアして再構築
        self.storyboard.blockSignals(True)
        self.storyboard.clear()

        for c in clips:
            item = StoryboardItem(
                clip_id=c["clip_id"],
                file_path=c["file_path"],
                start_sec=c["start_sec"],
                end_sec=c["end_sec"],
                total_sec=c["total_sec"]
            )
            cid = c["clip_id"]
            if cid in self.thumb_cache:
                scaled = self.thumb_cache[cid].scaled(160, 90, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(scaled))
            else:
                ThumbnailManager.instance().request_thumbnail(
                    clip_id=cid,
                    video_path=c["file_path"],
                    time_sec=c["start_sec"] + 0.5,
                    callback=self._on_thumbnail_ready
                )
            self.storyboard.addItem(item)

        self.storyboard.blockSignals(False)

        if self.storyboard.count() > 0:
            new_row = max(0, min(cur_row, self.storyboard.count() - 1))
            self.storyboard.setCurrentRow(new_row)
            item = self.storyboard.item(new_row)
            if item:
                self.on_clip_selected(item)
        else:
            self.active_clip_item = None
            self.player.stop()
            self.player.setSource(QUrl())
            self._update_base_info()
            self._update_clip_info()

        self._update_base_info()
        self.update_stats()
        self.statusBar().showMessage("元に戻しました (Undo)", 2000)

    # --- 動画追加 ---
    def add_videos(self, file_paths: List[str]):
        self._save_undo_state()
        for path in file_paths:
            try:
                meta = self.analyzer.get_metadata(path)

                if self.base_meta is None:
                    self.base_meta = meta
                    self._update_base_info()
                else:
                    is_compat, reason = VideoAnalyzer.check_compatibility(self.base_meta, meta)
                    if not is_compat:
                        QMessageBox.warning(
                            self,
                            "形式不一致",
                            f"{meta.file_name} は基準動画と形式が異なるため追加できません。\n\n理由: {reason}"
                        )
                        continue

                clip_id = f"clip_{os.urandom(4).hex()}"
                item = StoryboardItem(
                    clip_id=clip_id,
                    file_path=path,
                    start_sec=0.0,
                    end_sec=meta.duration,
                    total_sec=meta.duration
                )
                self.storyboard.addItem(item)

                ThumbnailManager.instance().request_thumbnail(
                    clip_id=clip_id,
                    video_path=path,
                    time_sec=0.5,
                    callback=self._on_thumbnail_ready
                )

                # 音声波形のバックグラウンド先行生成
                WaveformManager.instance().request_waveform(
                    file_path=path,
                    callback=self._on_waveform_ready
                )

                if self.storyboard.count() == 1:
                    self.storyboard.setCurrentItem(item)
                    self.on_clip_selected(item)

                self.statusBar().showMessage(f"追加: {meta.file_name}", 2500)

            except Exception as e:
                QMessageBox.critical(self, "エラー", f"動画の読み込みに失敗しました:\n{e}")

        self.update_stats()

    def _on_thumbnail_ready(self, clip_id: str, pixmap):
        self.thumb_cache[clip_id] = pixmap
        for i in range(self.storyboard.count()):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem) and item.clip_id == clip_id:
                scaled = pixmap.scaled(160, 90, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(scaled))
                break

    def _on_waveform_ready(self, file_path: str, pixmap: QPixmap):
        if self.active_clip_item and os.path.abspath(self.active_clip_item.file_path) == os.path.abspath(file_path):
            item = self.active_clip_item
            self.video_slider.set_waveform(pixmap, item.start_sec, item.end_sec, item.total_sec)

    # --- クリップ選択 ---
    def on_clip_selected(self, item: StoryboardItem):
        self.active_clip_item = item
        file_path = item.file_path

        current_source = self.player.source().toLocalFile()
        if os.path.abspath(current_source) != os.path.abspath(file_path):
            self.player.setSource(QUrl.fromLocalFile(file_path))

        dur_ms = int((item.end_sec - item.start_sec) * 1000)
        self.video_slider.set_duration(dur_ms)
        self.video_slider.set_clip_info(file_path, item.start_sec, item.end_sec, item.total_sec)
        self.player.setPosition(int(item.start_sec * 1000))

        # 音声波形の要求 & 反映 (キャッシュがあれば即座に描画)
        WaveformManager.instance().request_waveform(file_path, self._on_waveform_ready)

        self._update_clip_info()

    def on_clip_deleted(self, clip_id: str):
        if self.storyboard.count() == 0:
            self.base_meta = None
            self.active_clip_item = None
            self.player.stop()
            self.player.setSource(QUrl())
            self.video_slider.clear_waveform()
            self._update_base_info()
            self._update_clip_info()
        else:
            cur = self.storyboard.currentItem()
            if cur and isinstance(cur, StoryboardItem):
                self.on_clip_selected(cur)
        self.update_stats()

    # --- プレイヤー再生制御 ---
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState or self.btn_play.text() == "⏸ 一時停止":
            self.player.pause()
            self.btn_play.setText("▶ 再生")
        else:
            self.player.setPlaybackRate(self.playback_rate)
            self.player.play()
            self.btn_play.setText("⏸ 一時停止")

    def change_speed_step(self, delta: int):
        """速度を段階的に変更 (-1: ダウン, +1: アップ)"""
        new_idx = max(0, min(len(SPEED_PRESETS) - 1, self.speed_preset_index + delta))
        self.set_speed_index(new_idx)

    def reset_speed(self):
        """速度を 1.0x にリセット"""
        idx_1x = SPEED_PRESETS.index(1.0) if 1.0 in SPEED_PRESETS else 2
        self.set_speed_index(idx_1x)

    def set_speed_index(self, index: int):
        """指定インデックスの速度を適用"""
        self.speed_preset_index = max(0, min(len(SPEED_PRESETS) - 1, index))
        rate = SPEED_PRESETS[self.speed_preset_index]
        self.playback_rate = rate
        self.player.setPlaybackRate(rate)
        self.btn_speed_reset.setText(f"{rate}x")
        self.btn_speed_down.setEnabled(self.speed_preset_index > 0)
        self.btn_speed_up.setEnabled(self.speed_preset_index < len(SPEED_PRESETS) - 1)

    def set_playback_speed(self, rate: float):
        """再生速度の直接指定"""
        if rate in SPEED_PRESETS:
            self.set_speed_index(SPEED_PRESETS.index(rate))
        else:
            self.playback_rate = rate
            self.player.setPlaybackRate(rate)
            self.btn_speed_reset.setText(f"{rate}x")

    # --- 音量制御 ---
    def on_volume_changed(self, val: int):
        self.audio_output.setVolume(val / 100.0)
        self.lbl_volume.setText(f"{val}%")
        self.btn_mute.setText("🔇" if val == 0 else "🔊")

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

    def skip_seconds(self, sec: float):
        if not self.active_clip_item:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 再生")

        cur_abs_ms = self.player.position()
        target_abs_ms = cur_abs_ms + int(sec * 1000)
        min_abs_ms = int(self.active_clip_item.start_sec * 1000)
        max_abs_ms = int(self.active_clip_item.end_sec * 1000)
        new_abs_ms = max(min_abs_ms, min(max_abs_ms, target_abs_ms))
        self.player.setPosition(new_abs_ms)

    def step_frames(self, frames: int):
        fps = self.base_meta.fps if self.base_meta and self.base_meta.fps else 30.0
        frame_dur_sec = 1.0 / fps
        self.skip_seconds(frames * frame_dur_sec)

    def on_wheel_step(self, step: int):
        self.step_frames(step)

    def on_player_position_changed(self, abs_pos_ms: int):
        if not self.active_clip_item:
            return

        item = self.active_clip_item
        start_ms = int(item.start_sec * 1000)
        end_ms = int(item.end_sec * 1000)

        # クリップ末尾に達したら一時停止
        if abs_pos_ms >= end_ms and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.player.setPosition(start_ms)
            self.btn_play.setText("▶ 再生")
            abs_pos_ms = start_ms

        rel_pos_ms = max(0, abs_pos_ms - start_ms)
        self.video_slider.set_position(rel_pos_ms)

        cur_sec = rel_pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(cur_sec))

    def on_slider_position_changed(self, rel_pos_ms: int):
        if not self.active_clip_item:
            return
        start_ms = int(self.active_clip_item.start_sec * 1000)
        abs_pos_ms = start_ms + rel_pos_ms
        self.player.setPosition(abs_pos_ms)

    # --- 分割 (Split) ---
    def split_current_clip(self, at_sec: Optional[float] = None):
        """プレビューしている位置でクリップを左右2つに分割する"""
        if not self.active_clip_item:
            return

        item = self.active_clip_item
        if isinstance(at_sec, bool) or at_sec is None:
            rel_sec = self.video_slider.position_ms / 1000.0
            cur_abs_sec = item.start_sec + rel_sec
        else:
            cur_abs_sec = float(at_sec)

        # 端すぎないかチェック
        if cur_abs_sec <= item.start_sec + 0.05 or cur_abs_sec >= item.end_sec - 0.05:
            self.statusBar().showMessage("分割位置がクリップの端に近すぎます", 2000)
            return

        # Undo 状態を保存！
        self._save_undo_state()

        orig_end = item.end_sec

        # 1. 前半クリップ
        item.end_sec = cur_abs_sec
        item.update_text()

        # 2. 後半クリップを挿入
        cur_row = self.storyboard.row(item)
        new_id = f"clip_{os.urandom(4).hex()}"
        new_item = StoryboardItem(
            clip_id=new_id,
            file_path=item.file_path,
            start_sec=cur_abs_sec,
            end_sec=orig_end,
            total_sec=item.total_sec
        )
        self.storyboard.insertItem(cur_row + 1, new_item)

        ThumbnailManager.instance().request_thumbnail(
            clip_id=new_id,
            video_path=item.file_path,
            time_sec=cur_abs_sec,
            callback=self._on_thumbnail_ready
        )

        self.storyboard.setCurrentItem(new_item)
        self.on_clip_selected(new_item)
        self.update_stats()
        self.statusBar().showMessage(f"分割しました", 2000)

    # --- 表示更新 ---
    def _update_base_info(self):
        if not self.base_meta:
            self.lbl_base_file.setText("ファイル: 未設定")
            self.lbl_base_res.setText("解像度: -")
            self.lbl_base_codec.setText("コーデック: -")
            return
        self.lbl_base_file.setText(f"{self.base_meta.file_name}")
        self.lbl_base_res.setText(f"{self.base_meta.width}x{self.base_meta.height} ({self.base_meta.fps}fps)")
        self.lbl_base_codec.setText(f"{self.base_meta.video_codec} / {self.base_meta.audio_codec or '無音'}")

    def _update_clip_info(self):
        if not self.active_clip_item:
            self.lbl_clip_name.setText("名前: -")
            self.lbl_clip_in.setText("開始: -")
            self.lbl_clip_out.setText("終了: -")
            self.lbl_clip_len.setText("長さ: -")
            self.lbl_total_time.setText("00:00:00.000")
            self.lbl_clip_name_banner.setText("")
            return

        item = self.active_clip_item
        dur = item.end_sec - item.start_sec
        self.lbl_clip_name.setText(f"{item.file_name}")
        self.lbl_clip_in.setText(f"{self._format_time(item.start_sec)}")
        self.lbl_clip_out.setText(f"{self._format_time(item.end_sec)}")
        self.lbl_clip_len.setText(f"{self._format_time(dur)}")
        self.lbl_total_time.setText(self._format_time(dur))
        self.lbl_clip_name_banner.setText(f"[{item.file_name}]")

    def update_stats(self):
        count = self.storyboard.count()
        total_sec = 0.0
        for i in range(count):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem):
                total_sec += (item.end_sec - item.start_sec)

        self.lbl_base_clips.setText(f"クリップ数: {count} 本")
        m, s = divmod(int(total_sec), 60)
        h, m = divmod(m, 60)
        self.lbl_base_dur.setText(f"合計時間: {h:02d}:{m:02d}:{s:02d}")

    # --- ドラッグ＆ドロップ ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            paths = [u.toLocalFile() for u in urls if u.isLocalFile()]
            if paths:
                event.acceptProposedAction()
                self.add_videos(paths)

    def on_add_video_clicked(self):
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "動画ファイルを選択",
            "",
            "動画ファイル (*.mp4 *.mkv *.mov *.avi *.webm *.ts *.flv *.wmv *.m4v);;すべてのファイル (*.*)"
        )
        if files:
            self.add_videos(files)

    # --- レシピ保存 & エクスポート ---
    def _build_recipe(self, output_file: str) -> dict:
        clips = []
        for i in range(self.storyboard.count()):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem):
                clips.append({
                    "source": item.file_path,
                    "start": item.start_sec,
                    "end": item.end_sec
                })
        recipe = {
            "mode": self.mode,
            "output_file": output_file,
            "clips": clips
        }
        if self.source_metadata:
            recipe["source_metadata"] = self.source_metadata
        return recipe

    def on_load_recipe_clicked(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "レシピ読込", "", "JSON (*.json)"
        )
        if file_path:
            self.load_recipe(file_path)

    def load_recipe(self, recipe_path: str) -> bool:
        """レシピ JSON ファイルを読み込み、ストーリーボードに展開する"""
        norm_path = os.path.abspath(recipe_path)
        if not os.path.exists(norm_path):
            QMessageBox.warning(self, "エラー", f"ファイルが見つかりません:\n{norm_path}")
            return False

        try:
            with open(norm_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"レシピの読み込みに失敗しました:\n{e}")
            return False

        clips = data.get("clips", [])
        if not clips:
            QMessageBox.warning(self, "警告", "レシピ内にクリップが含まれていません。")
            return False

        # モードの反映
        mode = data.get("mode", "lossless")
        if mode in ("lossless", "smart"):
            self.set_mode(mode)

        # キーフレーム等メタデータの取得・キャッシュ注入
        self.source_metadata = data.get("source_metadata", {})
        if isinstance(self.source_metadata, dict):
            for src_path, meta_dict in self.source_metadata.items():
                if isinstance(meta_dict, dict) and "keyframes" in meta_dict:
                    self.analyzer.set_cached_keyframes(src_path, meta_dict["keyframes"])

        # 既存クリップをリセット
        self.storyboard.clear()
        self.base_meta = None
        self.active_clip_item = None
        self.player.stop()

        # 各クリップの追加
        for clip in clips:
            source = clip.get("source")
            if not source or not os.path.exists(source):
                continue
            start = float(clip.get("start", 0.0))
            end = float(clip.get("end", 0.0))

            # 基準動画メタデータの読み込み
            if self.base_meta is None:
                try:
                    self.base_meta = self.analyzer.get_metadata(source)
                    self._update_base_info()
                except Exception as e:
                    continue

            total_sec = self.base_meta.duration if (self.base_meta and source == self.base_meta.file_path) else 0.0
            if total_sec <= 0:
                try:
                    meta = self.analyzer.get_metadata(source)
                    total_sec = meta.duration
                except Exception:
                    total_sec = end

            if end <= start:
                end = total_sec

            clip_id = f"clip_{self.storyboard.count()}_{int(time.time() * 1000)}"
            item = StoryboardItem(
                clip_id=clip_id,
                file_path=source,
                start_sec=start,
                end_sec=end,
                total_sec=total_sec
            )
            self.storyboard.addItem(item)
            ThumbnailManager.instance().request_thumbnail(
                item.clip_id,
                item.file_path,
                time_sec=item.start_sec + 0.5,
                callback=self._on_thumbnail_ready
            )

        if self.storyboard.count() > 0:
            first_item = self.storyboard.item(0)
            self.storyboard.setCurrentItem(first_item)
            self.on_clip_selected(first_item)
            self.update_stats()
            return True
        else:
            QMessageBox.warning(self, "警告", "有効な動画ファイルが見つかりませんでした。")
            return False

    def on_open_player_clicked(self):
        """視聴・マーク用プレイヤーを起動"""
        current_file = ""
        if self.active_clip_item and os.path.exists(self.active_clip_item.file_path):
            current_file = self.active_clip_item.file_path
        elif self.storyboard.count() > 0:
            item = self.storyboard.item(0)
            if isinstance(item, StoryboardItem) and os.path.exists(item.file_path):
                current_file = item.file_path
        elif self.base_meta and os.path.exists(self.base_meta.file_path):
            current_file = self.base_meta.file_path

        player_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cmmm_player.pyw")
        if not os.path.exists(player_script):
            player_script = os.path.abspath("cmmm_player.pyw")

        cmd = [sys.executable, player_script]
        if current_file:
            cmd.append(current_file)

        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        try:
            subprocess.Popen(cmd, **kwargs)
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"プレイヤーの起動に失敗しました:\n{e}")

    def on_save_recipe_clicked(self):
        if self.storyboard.count() == 0:
            QMessageBox.warning(self, "警告", "保存するクリップがありません。")
            return

        first_item = self.storyboard.item(0)
        default_name = "recipe.json"
        if isinstance(first_item, StoryboardItem):
            base, _ = os.path.splitext(first_item.file_path)
            default_name = f"{base}.json"

        save_path, _ = QFileDialog.getSaveFileName(
            self, "レシピ保存", default_name, "JSON (*.json)"
        )
        if not save_path:
            return

        recipe = self._build_recipe("output.mp4")
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(recipe, f, indent=2, ensure_ascii=False)

        QMessageBox.information(self, "保存完了", f"レシピを保存しました:\n{save_path}")

    def show_ffmpeg_log(self):
        if not self.runner.last_command:
            self.statusBar().showMessage("FFmpeg 実行ログはまだありません", 2500)
            return

        cmd = " ".join(self.runner.last_command)
        tail = self.runner.last_stderr.strip()
        msg = f"最後のコマンド:\n{cmd}\n\nstderr:\n{tail[:4000] if tail else 'なし'}"
        QMessageBox.information(self, "FFmpegログ", msg)

    def on_export_clicked(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 再生")
        if self.storyboard.count() == 0:
            QMessageBox.warning(self, "警告", "エクスポートするクリップがありません。")
            return

        first_item = self.storyboard.item(0)
        default_out = "output(1).mp4"
        if isinstance(first_item, StoryboardItem):
            default_out = generate_default_output_path(first_item.file_path)

        save_path, _ = QFileDialog.getSaveFileName(
            self, "エクスポート", default_out, "動画 (*.mp4 *.mkv);;すべて (*.*)"
        )
        if not save_path:
            return

        recipe = self._build_recipe(save_path)

        self.progress_dialog = QProgressDialog("書き出し中...", "キャンセル", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(0)

        def run_task():
            def progress_cb(cur, total, msg):
                self.exportProgressSignal.emit(cur, total, msg)

            try:
                execute_recipe(recipe, progress_callback=progress_cb, runner=self.runner)
                self.exportFinishedSignal.emit(True, f"書き出しが完了しました:\n{save_path}")
            except Exception as e:
                self.exportFinishedSignal.emit(False, str(e))

        threading.Thread(target=run_task, daemon=True).start()

    def closeEvent(self, event):
        """終了時に実行中の FFmpeg を停止してからアプリを閉じる。"""
        self.player.stop()
        self.runner.cancel()
        super().closeEvent(event)

    def _update_export_progress(self, cur, total, msg):
        percent = int((cur / total) * 100) if total > 0 else 0
        self.progress_dialog.setValue(percent)
        self.progress_dialog.setLabelText(f"[{percent}%] {msg}")

    def _finish_export(self, success: bool, msg: str):
        self.progress_dialog.close()
        if success:
            QMessageBox.information(self, "完了", msg)
        else:
            QMessageBox.critical(self, "エラー", f"失敗しました:\n{msg}")

    @staticmethod
    def _format_time(sec: float) -> str:
        if sec < 0: sec = 0
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
