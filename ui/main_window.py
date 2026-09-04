"""
ui/main_window.py
CMMM メインウィンドウ - PySide6 完全ネイティブ UI
動画再生 (QMediaPlayer)、WMM風ストーリーボード、高速非同期サムネイル、
完全無劣化/スマートカット切替を統合
"""

import os
import sys
import json
import threading
from typing import Optional, List

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QPushButton, QLabel, QFileDialog, QMessageBox, QProgressDialog,
    QButtonGroup, QGroupBox, QToolBar, QStatusBar
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
from .range_slider import RangeSlider
from .async_thumbnail import ThumbnailManager


class MainWindow(QMainWindow):
    exportProgressSignal = Signal(int, int, str)
    exportFinishedSignal = Signal(bool, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("CMMM Studio - 無劣化スマート動画エディタ")
        self.resize(1200, 800)
        self.setMinimumSize(960, 600)
        self.setStyleSheet(MODERN_DARK_STYLE)

        self.runner = FFmpegRunner()
        self.analyzer = VideoAnalyzer(self.runner)

        # 状態
        self.base_meta: Optional[VideoMetadata] = None
        self.mode = "lossless"  # "lossless" または "smart"
        self.active_clip_item: Optional[StoryboardItem] = None

        # メディアプレイヤー初期化 (Windowsネイティブ DirectShow/WMF)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.video_widget = QVideoWidget(self)
        self.player.setVideoOutput(self.video_widget)

        self._setup_ui()
        self._setup_shortcuts()
        self._setup_player_events()

        # ドラッグ＆ドロップ受け入れ (ウィンドウ全体)
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

        # モード切替ボタングループ
        lbl_mode = QLabel(" 処理モード: ")
        lbl_mode.setStyleSheet("color: #aaa; font-weight: bold;")
        toolbar.addWidget(lbl_mode)

        self.btn_lossless = QPushButton("⚡ 完全無劣化 (Keyframe)")
        self.btn_lossless.setCheckable(True)
        self.btn_lossless.setChecked(True)
        self.btn_lossless.setProperty("class", "modeButton")
        self.btn_lossless.clicked.connect(lambda: self.set_mode("lossless"))
        toolbar.addWidget(self.btn_lossless)

        self.btn_smart = QPushButton("🎯 スマートカット (Frame-Accurate)")
        self.btn_smart.setCheckable(True)
        self.btn_smart.setProperty("class", "modeButton")
        self.btn_smart.clicked.connect(lambda: self.set_mode("smart"))
        toolbar.addWidget(self.btn_smart)

        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.btn_lossless)
        self.mode_group.addButton(self.btn_smart)

        toolbar.addSeparator()

        btn_recipe = QPushButton("💾 レシピ保存")
        btn_recipe.clicked.connect(self.on_save_recipe_clicked)
        toolbar.addWidget(btn_recipe)

        toolbar.addSeparator()

        btn_export = QPushButton("🚀 エクスポート")
        btn_export.setObjectName("accentButton")
        btn_export.clicked.connect(self.on_export_clicked)
        toolbar.addWidget(btn_export)

        # 2. メインレイアウト (垂直スプリッター)
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(8)

        v_splitter = QSplitter(Qt.Orientation.Vertical)
        main_layout.addWidget(v_splitter)

        # 上部エリア (水平スプリッター: プレビュー画面 + 右側情報パネル)
        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        v_splitter.addWidget(h_splitter)

        # --- プレビューエリア ---
        preview_container = QWidget()
        preview_layout = QVBoxLayout(preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)

        # ビデオ表示
        self.video_widget.setStyleSheet("background-color: #000; border-radius: 6px;")
        self.video_widget.setMinimumHeight(320)
        preview_layout.addWidget(self.video_widget, stretch=1)

        # タイムコード表示行
        tc_layout = QHBoxLayout()
        self.lbl_current_time = QLabel("00:00:00.000")
        self.lbl_current_time.setProperty("class", "timeCode")
        tc_sep = QLabel("/")
        tc_sep.setStyleSheet("color: #666;")
        self.lbl_total_time = QLabel("00:00:00.000")
        self.lbl_total_time.setProperty("class", "timeCode")

        self.lbl_clip_range = QLabel("クリップ範囲: 00:00:00.000 〜 00:00:00.000")
        self.lbl_clip_range.setProperty("class", "labelMuted")

        tc_layout.addWidget(self.lbl_current_time)
        tc_layout.addWidget(tc_sep)
        tc_layout.addWidget(self.lbl_total_time)
        tc_layout.addStretch()
        tc_layout.addWidget(self.lbl_clip_range)
        preview_layout.addLayout(tc_layout)

        # In/Out シークバー
        self.range_slider = RangeSlider(self)
        self.range_slider.positionChanged.connect(self.on_slider_position_changed)
        self.range_slider.rangeChanged.connect(self.on_slider_range_changed)
        preview_layout.addWidget(self.range_slider)

        # コントロールボタン列
        ctrl_layout = QHBoxLayout()
        self.btn_step_back = QPushButton("◀ 1F")
        self.btn_step_back.setProperty("class", "ctrlButton")
        self.btn_step_back.clicked.connect(self.step_back_frame)

        self.btn_play = QPushButton("▶ 再生")
        self.btn_play.setProperty("class", "ctrlButton")
        self.btn_play.setStyleSheet("background-color: #0078d4; color: #fff; font-weight: bold; min-width: 60px;")
        self.btn_play.clicked.connect(self.toggle_play)

        self.btn_step_forward = QPushButton("1F ▶")
        self.btn_step_forward.setProperty("class", "ctrlButton")
        self.btn_step_forward.clicked.connect(self.step_forward_frame)

        ctrl_layout.addWidget(self.btn_step_back)
        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addWidget(self.btn_step_forward)

        ctrl_layout.addSpacing(16)

        self.btn_set_in = QPushButton("[ In点設定 (I)")
        self.btn_set_in.setProperty("class", "ctrlButton")
        self.btn_set_in.clicked.connect(self.set_current_as_in)

        self.btn_split = QPushButton("✂ ここで分割 (S)")
        self.btn_split.setObjectName("splitButton")
        self.btn_split.clicked.connect(self.split_current_clip)

        self.btn_set_out = QPushButton("Out点設定 (O) ]")
        self.btn_set_out.setProperty("class", "ctrlButton")
        self.btn_set_out.clicked.connect(self.set_current_as_out)

        ctrl_layout.addWidget(self.btn_set_in)
        ctrl_layout.addWidget(self.btn_split)
        ctrl_layout.addWidget(self.btn_set_out)

        ctrl_layout.addStretch()

        self.btn_reset_range = QPushButton("↺ リセット")
        self.btn_reset_range.setProperty("class", "ctrlButton")
        self.btn_reset_range.clicked.connect(self.reset_clip_range)
        ctrl_layout.addWidget(self.btn_reset_range)

        preview_layout.addLayout(ctrl_layout)
        h_splitter.addWidget(preview_container)

        # --- 右側情報パネル ---
        side_panel = QWidget()
        side_panel.setMaximumWidth(280)
        side_layout = QVBoxLayout(side_panel)
        side_layout.setContentsMargins(4, 0, 4, 0)
        side_layout.setSpacing(10)

        gb_base = QGroupBox("プロジェクト基準 (UniteMovie)")
        gb_base_layout = QVBoxLayout(gb_base)
        self.lbl_base_file = QLabel("基準動画: 未設定")
        self.lbl_base_res = QLabel("解像度: -")
        self.lbl_base_codec = QLabel("コーデック: -")
        self.lbl_base_clips = QLabel("総クリップ数: 0 本")
        self.lbl_base_dur = QLabel("合計時間: 00:00:00")
        gb_base_layout.addWidget(self.lbl_base_file)
        gb_base_layout.addWidget(self.lbl_base_res)
        gb_base_layout.addWidget(self.lbl_base_codec)
        gb_base_layout.addWidget(self.lbl_base_clips)
        gb_base_layout.addWidget(self.lbl_base_dur)
        side_layout.addWidget(gb_base)

        gb_clip = QGroupBox("選択中クリップ")
        gb_clip_layout = QVBoxLayout(gb_clip)
        self.lbl_clip_name = QLabel("ファイル: -")
        self.lbl_clip_in = QLabel("In 点: -")
        self.lbl_clip_out = QLabel("Out 点: -")
        self.lbl_clip_len = QLabel("長さ: -")
        gb_clip_layout.addWidget(self.lbl_clip_name)
        gb_clip_layout.addWidget(self.lbl_clip_in)
        gb_clip_layout.addWidget(self.lbl_clip_out)
        gb_clip_layout.addWidget(self.lbl_clip_len)
        side_layout.addWidget(gb_clip)

        gb_tips = QGroupBox("ショートカット")
        gb_tips_layout = QVBoxLayout(gb_tips)
        gb_tips_layout.addWidget(QLabel("Space : 再生 / 一時停止"))
        gb_tips_layout.addWidget(QLabel("S : 現在位置で分割"))
        gb_tips_layout.addWidget(QLabel("I / O : In点 / Out点設定"))
        gb_tips_layout.addWidget(QLabel("Delete : クリップ削除"))
        gb_tips_layout.addWidget(QLabel("← / → : 1コマ送り / 戻し"))
        side_layout.addWidget(gb_tips)

        side_layout.addStretch()
        h_splitter.addWidget(side_panel)
        h_splitter.setStretchFactor(0, 1)

        # --- 下部：ストーリーボード (WMM風) ---
        sb_container = QWidget()
        sb_layout = QVBoxLayout(sb_container)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(4)

        sb_header = QHBoxLayout()
        sb_title = QLabel("🎞️ ストーリーボード（マウスでドラッグして並び替え / Delete で削除）")
        sb_title.setStyleSheet("font-weight: bold; color: #ddd;")
        sb_header.addWidget(sb_title)
        sb_header.addStretch()
        sb_layout.addLayout(sb_header)

        self.storyboard = StoryboardWidget(self)
        self.storyboard.filesDropped.connect(self.add_videos)
        self.storyboard.itemClicked.connect(self.on_clip_selected)
        self.storyboard.clipDeleted.connect(self.on_clip_deleted)
        self.storyboard.orderChanged.connect(self.update_stats)
        sb_layout.addWidget(self.storyboard)

        v_splitter.addWidget(sb_container)
        v_splitter.setStretchFactor(0, 3)
        v_splitter.setStretchFactor(1, 1)

        # ステータスバー
        self.statusBar().showMessage("準備完了 - 動画ファイルをドラッグ＆ドロップしてください")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_S), self, self.split_current_clip)
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self.set_current_as_in)
        QShortcut(QKeySequence(Qt.Key.Key_O), self, self.set_current_as_out)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.step_back_frame)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.step_forward_frame)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.storyboard.delete_selected)

    def _setup_player_events(self):
        self.player.positionChanged.connect(self.on_player_position_changed)
        self.exportProgressSignal.connect(self._update_export_progress)
        self.exportFinishedSignal.connect(self._finish_export)

    # --- モード切替 ---
    def set_mode(self, mode: str):
        self.mode = mode
        msg = "⚡ 完全無劣化モード (再エンコードなし・超高速)" if mode == "lossless" else "🎯 スマートカットモード (1フレーム精度・境界のみ再エンコード)"
        self.statusBar().showMessage(msg, 3000)

    # --- 高速動画追加 & 互換性チェック ---
    def add_videos(self, file_paths: List[str]):
        """動画ファイルを高速に追加（メタデータのみ即時取得、サムネイルは非同期）"""
        for path in file_paths:
            try:
                # 0.05秒でメタデータ取得 (パケット走査はしない！)
                meta = self.analyzer.get_metadata(path)

                # 1本目の場合: 基準メタデータに設定
                if self.base_meta is None:
                    self.base_meta = meta
                    self._update_base_info()
                else:
                    # 2本目以降: 無劣化互換性判定！
                    is_compat, reason = VideoAnalyzer.check_compatibility(self.base_meta, meta)
                    if not is_compat:
                        # 互換性なしのため自動で弾く！
                        QMessageBox.warning(
                            self,
                            "フォーマット不一致 (除外)",
                            f"【{meta.file_name}】は基準動画と形式が異なるため追加できませんでした。\n\n理由: {reason}"
                        )
                        continue

                # ストーリーボードに即座にカードを追加！
                clip_id = f"clip_{os.urandom(4).hex()}"
                item = StoryboardItem(
                    clip_id=clip_id,
                    file_path=path,
                    start_sec=0.0,
                    end_sec=meta.duration,
                    total_sec=meta.duration
                )
                self.storyboard.addItem(item)

                # 非同期でサムネイルを生成して貼る (UIスレッドは待たせない)
                ThumbnailManager.instance().request_thumbnail(
                    clip_id=clip_id,
                    video_path=path,
                    time_sec=0.5,
                    callback=self._on_thumbnail_ready
                )

                # 最初の動画なら自動選択
                if self.storyboard.count() == 1:
                    self.storyboard.setCurrentItem(item)
                    self.on_clip_selected(item)

                self.statusBar().showMessage(f"追加しました: {meta.file_name}", 2500)

            except Exception as e:
                QMessageBox.critical(self, "エラー", f"動画の読み込みに失敗しました: {path}\n{e}")

        self.update_stats()

    def _on_thumbnail_ready(self, clip_id: str, pixmap):
        """バックグラウンドでサムネイルが出来上がったらアイコンを更新"""
        for i in range(self.storyboard.count()):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem) and item.clip_id == clip_id:
                scaled = pixmap.scaled(160, 90, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(scaled))
                break

    # --- クリップ選択 & プレビュー同期 ---
    def on_clip_selected(self, item: StoryboardItem):
        self.active_clip_item = item
        file_path = item.file_path

        # プレイヤーのソース設定
        current_source = self.player.source().toLocalFile()
        if os.path.abspath(current_source) != os.path.abspath(file_path):
            self.player.setSource(QUrl.fromLocalFile(file_path))

        # シークバーの更新
        self.range_slider.set_duration(int(item.total_sec * 1000))
        self.range_slider.set_range(int(item.start_sec * 1000), int(item.end_sec * 1000))
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

    def step_forward_frame(self):
        self.player.setPosition(self.player.position() + 33)

    def step_back_frame(self):
        self.player.setPosition(max(0, self.player.position() - 33))

    def on_player_position_changed(self, pos_ms: int):
        self.range_slider.set_position(pos_ms)
        sec = pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(sec))

        # クリップの末尾に達したら一時停止
        if self.active_clip_item:
            out_ms = int(self.active_clip_item.end_sec * 1000)
            if pos_ms >= out_ms and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
                self.player.setPosition(int(self.active_clip_item.start_sec * 1000))
                self.btn_play.setText("▶ 再生")

    def on_slider_position_changed(self, pos_ms: int):
        self.player.setPosition(pos_ms)

    def on_slider_range_changed(self, in_ms: int, out_ms: int):
        if not self.active_clip_item:
            return
        self.active_clip_item.start_sec = in_ms / 1000.0
        self.active_clip_item.end_sec = out_ms / 1000.0
        self.active_clip_item.update_text()
        self._update_clip_info()
        self.update_stats()

    # --- トリミング操作 ---
    def set_current_as_in(self):
        if not self.active_clip_item:
            return
        cur_sec = self.player.position() / 1000.0
        self.active_clip_item.start_sec = min(cur_sec, self.active_clip_item.end_sec - 0.1)
        self.range_slider.set_range(int(self.active_clip_item.start_sec * 1000), int(self.active_clip_item.end_sec * 1000))
        self.active_clip_item.update_text()
        self._update_clip_info()
        self.update_stats()

    def set_current_as_out(self):
        if not self.active_clip_item:
            return
        cur_sec = self.player.position() / 1000.0
        self.active_clip_item.end_sec = max(cur_sec, self.active_clip_item.start_sec + 0.1)
        self.range_slider.set_range(int(self.active_clip_item.start_sec * 1000), int(self.active_clip_item.end_sec * 1000))
        self.active_clip_item.update_text()
        self._update_clip_info()
        self.update_stats()

    def reset_clip_range(self):
        if not self.active_clip_item:
            return
        self.active_clip_item.start_sec = 0.0
        self.active_clip_item.end_sec = self.active_clip_item.total_sec
        self.range_slider.set_range(0, int(self.active_clip_item.total_sec * 1000))
        self.active_clip_item.update_text()
        self._update_clip_info()
        self.update_stats()

    # --- クリップ分割 (Split) ---
    def split_current_clip(self, at_sec: Optional[float] = None):
        if not self.active_clip_item:
            return

        cur_sec = at_sec if at_sec is not None else (self.player.position() / 1000.0)
        item = self.active_clip_item
        if cur_sec <= item.start_sec + 0.1 or cur_sec >= item.end_sec - 0.1:
            self.statusBar().showMessage("分割位置がクリップの端に近すぎます", 2000)
            return

        orig_end = item.end_sec
        # 前半
        item.end_sec = cur_sec
        item.update_text()

        # 後半アイテムを直後に挿入
        cur_row = self.storyboard.row(item)
        new_id = f"clip_{os.urandom(4).hex()}"
        new_item = StoryboardItem(
            clip_id=new_id,
            file_path=item.file_path,
            start_sec=cur_sec,
            end_sec=orig_end,
            total_sec=item.total_sec
        )
        self.storyboard.insertItem(cur_row + 1, new_item)

        # サムネイル生成
        ThumbnailManager.instance().request_thumbnail(
            clip_id=new_id,
            video_path=item.file_path,
            time_sec=cur_sec,
            callback=self._on_thumbnail_ready
        )

        # 選択切り替え
        self.storyboard.setCurrentItem(new_item)
        self.on_clip_selected(new_item)
        self.update_stats()
        self.statusBar().showMessage(f"✂ クリップを分割しました: {self._format_time(cur_sec)}", 3000)

    # --- UI表示更新 ---
    def _update_base_info(self):
        if not self.base_meta:
            self.lbl_base_file.setText("基準動画: 未設定")
            self.lbl_base_res.setText("解像度: -")
            self.lbl_base_codec.setText("コーデック: -")
            return
        self.lbl_base_file.setText(f"基準: {self.base_meta.file_name}")
        self.lbl_base_res.setText(f"解像度: {self.base_meta.width}x{self.base_meta.height} ({self.base_meta.fps}fps)")
        self.lbl_base_codec.setText(f"形式: {self.base_meta.video_codec} / {self.base_meta.audio_codec or '無音'}")

    def _update_clip_info(self):
        if not self.active_clip_item:
            self.lbl_clip_name.setText("ファイル: -")
            self.lbl_clip_in.setText("In 点: -")
            self.lbl_clip_out.setText("Out 点: -")
            self.lbl_clip_len.setText("長さ: -")
            self.lbl_total_time.setText("00:00:00.000")
            self.lbl_clip_range.setText("クリップ範囲: 未選択")
            return

        item = self.active_clip_item
        dur = item.end_sec - item.start_sec
        self.lbl_clip_name.setText(f"ファイル: {item.file_name}")
        self.lbl_clip_in.setText(f"In 点: {self._format_time(item.start_sec)}")
        self.lbl_clip_out.setText(f"Out 点: {self._format_time(item.end_sec)}")
        self.lbl_clip_len.setText(f"長さ: {self._format_time(dur)}")
        self.lbl_total_time.setText(self._format_time(item.total_sec))
        self.lbl_clip_range.setText(f"クリップ範囲: {self._format_time(item.start_sec)} 〜 {self._format_time(item.end_sec)}")

    def update_stats(self):
        count = self.storyboard.count()
        total_sec = 0.0
        for i in range(count):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem):
                total_sec += (item.end_sec - item.start_sec)

        self.lbl_base_clips.setText(f"総クリップ数: {count} 本")
        m, s = divmod(int(total_sec), 60)
        h, m = divmod(m, 60)
        self.lbl_base_dur.setText(f"合計時間: {h:02d}:{m:02d}:{s:02d}")

    # --- ドラッグ＆ドロップ (ウィンドウ全体) ---
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

        save_path, _ = QFileDialog.getSaveFileName(
            self, "レシピ保存先", "cmmm_recipe.json", "JSON レシピ (*.json)"
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

        save_path, _ = QFileDialog.getSaveFileName(
            self, "動画書き出し先", "output_cmmm.mp4", "MP4 動画 (*.mp4);;MKV 動画 (*.mkv)"
        )
        if not save_path:
            return

        recipe = self._build_recipe(save_path)

        # プログレスダイアログ
        self.progress_dialog = QProgressDialog("動画を処理中...", "キャンセル", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(0)

        # バックグラウンド実行
        def run_task():
            def progress_cb(cur, total, msg):
                self.exportProgressSignal.emit(cur, total, msg)

            try:
                execute_recipe(recipe, progress_callback=progress_cb)
                self.exportFinishedSignal.emit(True, f"書き出しが完了しました！\n{save_path}")
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
            QMessageBox.critical(self, "エクスポート失敗", f"エラーが発生しました:\n{msg}")

    @staticmethod
    def _format_time(sec: float) -> str:
        if sec < 0: sec = 0
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        ms = int((sec % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
