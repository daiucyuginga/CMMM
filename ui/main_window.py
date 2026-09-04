"""
ui/main_window.py
CMMM メインウィンドウ
Windows ムービーメーカーの直感的な切り貼り編集（プレビュー位置で分割 → 不要クリップをDeleteで削除）を忠実に再現
"""

import os
import sys
import json
import threading
from typing import Optional, List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QFileDialog, QMessageBox, QProgressDialog,
    QButtonGroup, QGroupBox, QToolBar
)
from PySide6.QtCore import Qt, QUrl, Slot, Signal
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from core.ffmpeg_tools import FFmpegRunner
from core.gop_analyzer import VideoAnalyzer, VideoMetadata
from core.engine import execute_recipe
from .styles import MODERN_DARK_STYLE
from .storyboard import StoryboardWidget, StoryboardItem
from .range_slider import VideoSlider
from .async_thumbnail import ThumbnailManager


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


class MainWindow(QMainWindow):
    exportProgressSignal = Signal(int, int, str)
    exportFinishedSignal = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CMMM")
        self.resize(1200, 800)
        self.setMinimumSize(960, 600)
        self.setStyleSheet(MODERN_DARK_STYLE)

        self.runner = FFmpegRunner()
        self.analyzer = VideoAnalyzer(self.runner)

        # 状態
        self.base_meta: Optional[VideoMetadata] = None
        self.mode = "lossless"  # "lossless" または "smart"
        self.active_clip_item: Optional[StoryboardItem] = None

        # メディアプレイヤー初期化
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.video_widget = QVideoWidget(self)
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

        btn_recipe = QPushButton("レシピ保存")
        btn_recipe.clicked.connect(self.on_save_recipe_clicked)
        toolbar.addWidget(btn_recipe)

        toolbar.addSeparator()

        btn_export = QPushButton("エクスポート")
        btn_export.setObjectName("accentButton")
        btn_export.clicked.connect(self.on_export_clicked)
        toolbar.addWidget(btn_export)

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
        self.lbl_clip_name_banner.setStyleSheet("color: #aaa;")

        tc_layout.addWidget(self.lbl_current_time)
        tc_layout.addWidget(tc_sep)
        tc_layout.addWidget(self.lbl_total_time)
        tc_layout.addSpacing(16)
        tc_layout.addWidget(self.lbl_clip_name_banner)
        tc_layout.addStretch()
        preview_layout.addLayout(tc_layout)

        # WMM風の再生シークバー
        self.video_slider = VideoSlider(self)
        self.video_slider.positionChanged.connect(self.on_slider_position_changed)
        self.video_slider.wheelStep.connect(self.on_wheel_step)
        preview_layout.addWidget(self.video_slider)

        # ========================================================
        # コントロール行（WMM風：再生、コマ送り、スキップ、そして大きな「分割」ボタン）
        # ========================================================
        ctrl_layout = QHBoxLayout()

        btn_skip_m15 = QPushButton("◀ 15秒")
        btn_skip_m15.clicked.connect(lambda: self.skip_seconds(-15))
        btn_skip_m1 = QPushButton("◀ 1秒")
        btn_skip_m1.clicked.connect(lambda: self.skip_seconds(-1))
        btn_step_m1f = QPushButton("◀ 1コマ")
        btn_step_m1f.clicked.connect(lambda: self.step_frames(-1))

        self.btn_play = QPushButton("▶ 再生")
        self.btn_play.setStyleSheet("background-color: #0078d4; color: #fff; font-weight: bold; min-width: 70px; padding: 6px 14px;")
        self.btn_play.clicked.connect(self.toggle_play)

        btn_step_p1f = QPushButton("1コマ ▶")
        btn_step_p1f.clicked.connect(lambda: self.step_frames(1))
        btn_skip_p1 = QPushButton("1秒 ▶")
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

        ctrl_layout.addSpacing(24)

        # WMMの主役：「ここで分割」ボタン！
        self.btn_split = QPushButton("✂ ここで分割 (S)")
        self.btn_split.setObjectName("splitButton")
        self.btn_split.setStyleSheet("font-size: 14px; font-weight: bold; padding: 6px 20px; background-color: rgba(96, 205, 255, 0.18); border: 2px solid #60cdff; color: #60cdff;")
        self.btn_split.clicked.connect(lambda: self.split_current_clip())
        ctrl_layout.addWidget(self.btn_split)

        ctrl_layout.addStretch()

        btn_delete_clip = QPushButton("🗑️ 選択クリップを削除 (Del)")
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
        gb_tips_layout.addWidget(QLabel("Space : 再生 / 一時停止"))
        gb_tips_layout.addWidget(QLabel("S : ここで分割"))
        gb_tips_layout.addWidget(QLabel("Delete : 選択クリップを削除"))
        gb_tips_layout.addWidget(QLabel("← / → : 1コマ戻し / 送り"))
        gb_tips_layout.addWidget(QLabel("Ctrl + ←/→ : 1秒送り / 戻し"))
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
        sb_hint = QLabel("（不要なクリップは選択して Delete キーで削除 / ドラッグで並び替え）")
        sb_hint.setStyleSheet("color: #888; font-size: 11px;")
        sb_header.addWidget(sb_title)
        sb_header.addWidget(sb_hint)
        sb_header.addStretch()
        sb_layout.addLayout(sb_header)

        self.storyboard = StoryboardWidget(self)
        self.storyboard.filesDropped.connect(self.add_videos)
        self.storyboard.itemClicked.connect(self.on_clip_selected)
        self.storyboard.clipDeleted.connect(self.on_clip_deleted)
        self.storyboard.orderChanged.connect(self.update_stats)
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
        QShortcut(QKeySequence("Ctrl+M"), self, self.split_current_clip)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.step_frames(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.step_frames(1))
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.skip_seconds(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.skip_seconds(1))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.skip_seconds(15))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.skip_seconds(-15))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.storyboard.delete_selected)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self, self.storyboard.delete_selected)

    def _setup_player_events(self):
        self.player.positionChanged.connect(self.on_player_position_changed)
        self.exportProgressSignal.connect(self._update_export_progress)
        self.exportFinishedSignal.connect(self._finish_export)

    def set_mode(self, mode: str):
        self.mode = mode
        msg = "完全無劣化モード" if mode == "lossless" else "スマートカットモード"
        self.statusBar().showMessage(msg, 3000)

    # --- 動画追加 ---
    def add_videos(self, file_paths: List[str]):
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

                if self.storyboard.count() == 1:
                    self.storyboard.setCurrentItem(item)
                    self.on_clip_selected(item)

                self.statusBar().showMessage(f"追加: {meta.file_name}", 2500)

            except Exception as e:
                QMessageBox.critical(self, "エラー", f"動画の読み込みに失敗しました:\n{e}")

        self.update_stats()

    def _on_thumbnail_ready(self, clip_id: str, pixmap):
        for i in range(self.storyboard.count()):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem) and item.clip_id == clip_id:
                scaled = pixmap.scaled(160, 90, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(scaled))
                break

    # --- クリップ選択 ---
    def on_clip_selected(self, item: StoryboardItem):
        self.active_clip_item = item
        file_path = item.file_path

        current_source = self.player.source().toLocalFile()
        if os.path.abspath(current_source) != os.path.abspath(file_path):
            self.player.setSource(QUrl.fromLocalFile(file_path))

        # シークバーのスケールは「そのクリップの長さ」
        dur_ms = int((item.end_sec - item.start_sec) * 1000)
        self.video_slider.set_duration(dur_ms)
        self.player.setPosition(int(item.start_sec * 1000))

        self._update_clip_info()

    def on_clip_deleted(self, clip_id: str):
        if self.storyboard.count() == 0:
            self.base_meta = None
            self.active_clip_item = None
            self.player.stop()
            self.player.setSource(QUrl())
            self._update_base_info()
            self._update_clip_info()
        else:
            cur = self.storyboard.currentItem()
            if cur and isinstance(cur, StoryboardItem):
                self.on_clip_selected(cur)
        self.update_stats()

    # --- プレイヤー再生制御 ---
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 再生")
        else:
            self.player.play()
            self.btn_play.setText("⏸ 一時停止")

    def skip_seconds(self, sec: float):
        if not self.active_clip_item:
            return
        cur_abs_ms = self.player.position()
        target_abs_ms = cur_abs_ms + int(sec * 1000)
        min_abs_ms = int(self.active_clip_item.start_sec * 1000)
        max_abs_ms = int(self.active_clip_item.end_sec * 1000)
        new_abs_ms = max(min_abs_ms, min(max_abs_ms, target_abs_ms))
        self.player.setPosition(new_abs_ms)

    def step_frames(self, frames: int):
        self.skip_seconds(frames * 0.033)

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

        # クリップ内相対時間 (0 〜 クリップの長さ)
        rel_pos_ms = max(0, abs_pos_ms - start_ms)
        self.video_slider.set_position(rel_pos_ms)

        # 表示更新 (クリップ内現在位置 / クリップの長さ)
        cur_sec = rel_pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(cur_sec))

    def on_slider_position_changed(self, rel_pos_ms: int):
        if not self.active_clip_item:
            return
        start_ms = int(self.active_clip_item.start_sec * 1000)
        abs_pos_ms = start_ms + rel_pos_ms
        self.player.setPosition(abs_pos_ms)

    # ========================================================
    # 【WMM風 切り貼り編集の核心】現在位置でクリップをパカッと分割！
    # ========================================================
    def split_current_clip(self, at_sec: Optional[float] = None):
        """プレビューしている位置でクリップを左右2つに分割する"""
        if not self.active_clip_item:
            return

        item = self.active_clip_item
        if isinstance(at_sec, bool) or at_sec is None:
            # ユーザーがシークバーで指定した位置を基準に計算
            rel_sec = self.video_slider.position_ms / 1000.0
            cur_abs_sec = item.start_sec + rel_sec
        else:
            cur_abs_sec = float(at_sec)

        # 端すぎないかチェック (先頭/末尾から0.1秒以上内側)
        if cur_abs_sec <= item.start_sec + 0.05 or cur_abs_sec >= item.end_sec - 0.05:
            self.statusBar().showMessage("分割位置がクリップの端に近すぎます", 2000)
            return

        orig_end = item.end_sec

        # 1. 前半クリップ
        item.end_sec = cur_abs_sec
        item.update_text()

        # 2. 後半クリップをストーリーボードの直後に挿入
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

        # 後半のサムネイル生成
        ThumbnailManager.instance().request_thumbnail(
            clip_id=new_id,
            video_path=item.file_path,
            time_sec=cur_abs_sec,
            callback=self._on_thumbnail_ready
        )

        # 後半を選択状態にしてプレビュー
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
        return {
            "mode": self.mode,
            "output_file": output_file,
            "clips": clips
        }

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

    def on_export_clicked(self):
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
                execute_recipe(recipe, progress_callback=progress_cb)
                self.exportFinishedSignal.emit(True, f"書き出しが完了しました:\n{save_path}")
            except Exception as e:
                self.exportFinishedSignal.emit(False, str(e))

        threading.Thread(target=run_task, daemon=True).start()

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
