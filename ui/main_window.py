"""
ui/main_window.py
CMMM メインウィンドウ - PySide6 完全ネイティブ UI
テレビCMカット・場面抜き出し編集に特化した
「①ザックリ分割 → ②クリップごとの拡大＆コマ送りIn/Out微調整」ワークフロー
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
from PySide6.QtCore import Qt, QUrl, Slot, Signal, QTimer
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from core.ffmpeg_tools import FFmpegRunner
from core.gop_analyzer import VideoAnalyzer, VideoMetadata
from core.engine import execute_recipe
from .styles import MODERN_DARK_STYLE
from .storyboard import StoryboardWidget, StoryboardItem
from .range_slider import ClipTimelineSlider
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
        self.setWindowTitle("CMMM Studio - 無劣化スマート動画エディタ (CMカット・場面抜き出し特化)")
        self.resize(1260, 840)
        self.setMinimumSize(980, 640)
        self.setStyleSheet(MODERN_DARK_STYLE)

        self.runner = FFmpegRunner()
        self.analyzer = VideoAnalyzer(self.runner)

        # 状態
        self.base_meta: Optional[VideoMetadata] = None
        self.mode = "lossless"  # "lossless" または "smart"
        self.active_clip_item: Optional[StoryboardItem] = None
        self.preview_end_limit_ms: Optional[int] = None  # 先頭/末尾プレビュー用停止リミット

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

        # 画面
        self.video_widget.setStyleSheet("background-color: #000; border-radius: 6px;")
        self.video_widget.setMinimumHeight(300)
        preview_layout.addWidget(self.video_widget, stretch=1)

        # タイムコード表示行
        tc_layout = QHBoxLayout()
        self.lbl_current_time = QLabel("00:00:00.000")
        self.lbl_current_time.setProperty("class", "timeCode")
        tc_sep = QLabel("/")
        tc_sep.setStyleSheet("color: #666;")
        self.lbl_total_time = QLabel("00:00:00.000")
        self.lbl_total_time.setProperty("class", "timeCode")

        self.lbl_clip_range = QLabel("選択中クリップ範囲: 未設定")
        self.lbl_clip_range.setStyleSheet("color: #60cdff; font-weight: 500;")

        tc_layout.addWidget(self.lbl_current_time)
        tc_layout.addWidget(tc_sep)
        tc_layout.addWidget(self.lbl_total_time)
        tc_layout.addStretch()
        tc_layout.addWidget(self.lbl_clip_range)
        preview_layout.addLayout(tc_layout)

        # クリップ拡大タイムラインスライダー (ミニマップ付き)
        self.timeline_slider = ClipTimelineSlider(self)
        self.timeline_slider.positionChanged.connect(self.on_slider_position_changed)
        self.timeline_slider.wheelStep.connect(self.on_wheel_step)
        preview_layout.addWidget(self.timeline_slider)

        # ========================================================
        # 【微調整コントローラー】先頭(In点)と末尾(Out点)のコマ送り伸縮
        # ========================================================
        fine_tune_box = QGroupBox("クリップの In点 / Out点 微調整（CM境界やシーン切れ目にピタッと合わせる）")
        fine_tune_layout = QHBoxLayout(fine_tune_box)
        fine_tune_layout.setContentsMargins(8, 8, 8, 8)

        # --- 先頭 (In点) コントロール ---
        in_ctrl_layout = QHBoxLayout()
        lbl_in_title = QLabel("◀ 先頭 (In点):")
        lbl_in_title.setStyleSheet("font-weight: bold; color: #60cdff;")

        btn_in_m1s = QPushButton("-1秒")
        btn_in_m1s.setToolTip("In点を1秒手前に戻す (削った部分を復活)")
        btn_in_m1s.clicked.connect(lambda: self.adjust_in_time(-1.0))

        btn_in_m1f = QPushButton("-1コマ")
        btn_in_m1f.setToolTip("In点を1コマ手前に戻す")
        btn_in_m1f.clicked.connect(lambda: self.adjust_in_time(-0.033))

        self.lbl_in_value = QLabel("00:00:00.000")
        self.lbl_in_value.setStyleSheet("font-family: Consolas; font-weight: bold; padding: 2px 6px; background: #1a1a1a; border-radius: 3px;")

        btn_in_p1f = QPushButton("+1コマ")
        btn_in_p1f.setToolTip("In点を1コマ進める (頭を削る)")
        btn_in_p1f.clicked.connect(lambda: self.adjust_in_time(0.033))

        btn_in_p1s = QPushButton("+1秒")
        btn_in_p1s.setToolTip("In点を1秒進める")
        btn_in_p1s.clicked.connect(lambda: self.adjust_in_time(1.0))

        btn_set_in_current = QPushButton("現在位置に設定 (I)")
        btn_set_in_current.setStyleSheet("color: #60cdff;")
        btn_set_in_current.clicked.connect(self.set_current_as_in)

        in_ctrl_layout.addWidget(lbl_in_title)
        in_ctrl_layout.addWidget(btn_in_m1s)
        in_ctrl_layout.addWidget(btn_in_m1f)
        in_ctrl_layout.addWidget(self.lbl_in_value)
        in_ctrl_layout.addWidget(btn_in_p1f)
        in_ctrl_layout.addWidget(btn_in_p1s)
        in_ctrl_layout.addWidget(btn_set_in_current)
        fine_tune_layout.addLayout(in_ctrl_layout)

        fine_tune_layout.addSpacing(16)

        # --- 末尾 (Out点) コントロール ---
        out_ctrl_layout = QHBoxLayout()
        lbl_out_title = QLabel("▶ 末尾 (Out点):")
        lbl_out_title.setStyleSheet("font-weight: bold; color: #ffaa00;")

        btn_out_m1s = QPushButton("-1秒")
        btn_out_m1s.setToolTip("Out点を1秒手前に縮める (末尾を削る)")
        btn_out_m1s.clicked.connect(lambda: self.adjust_out_time(-1.0))

        btn_out_m1f = QPushButton("-1コマ")
        btn_out_m1f.setToolTip("Out点を1コマ手前に縮める")
        btn_out_m1f.clicked.connect(lambda: self.adjust_out_time(-0.033))

        self.lbl_out_value = QLabel("00:00:00.000")
        self.lbl_out_value.setStyleSheet("font-family: Consolas; font-weight: bold; padding: 2px 6px; background: #1a1a1a; border-radius: 3px;")

        btn_out_p1f = QPushButton("+1コマ")
        btn_out_p1f.setToolTip("Out点を1コマ進める (削った末尾を復活)")
        btn_out_p1f.clicked.connect(lambda: self.adjust_out_time(0.033))

        btn_out_p1s = QPushButton("+1秒")
        btn_out_p1s.setToolTip("Out点を1秒進める")
        btn_out_p1s.clicked.connect(lambda: self.adjust_out_time(1.0))

        btn_set_out_current = QPushButton("現在位置に設定 (O)")
        btn_set_out_current.setStyleSheet("color: #ffaa00;")
        btn_set_out_current.clicked.connect(self.set_current_as_out)

        out_ctrl_layout.addWidget(lbl_out_title)
        out_ctrl_layout.addWidget(btn_out_m1s)
        out_ctrl_layout.addWidget(btn_out_m1f)
        out_ctrl_layout.addWidget(self.lbl_out_value)
        out_ctrl_layout.addWidget(btn_out_p1f)
        out_ctrl_layout.addWidget(btn_out_p1s)
        out_ctrl_layout.addWidget(btn_set_out_current)
        fine_tune_layout.addLayout(out_ctrl_layout)

        preview_layout.addWidget(fine_tune_box)

        # ========================================================
        # 【ナビゲーション・確認再生コントロール】テレビ仕様スキップ
        # ========================================================
        ctrl_layout = QHBoxLayout()

        # スキップボタン群
        btn_skip_m30 = QPushButton("◀ 30s")
        btn_skip_m30.clicked.connect(lambda: self.skip_seconds(-30))
        btn_skip_m15 = QPushButton("◀ 15s")
        btn_skip_m15.clicked.connect(lambda: self.skip_seconds(-15))
        btn_skip_m1 = QPushButton("◀ 1s")
        btn_skip_m1.clicked.connect(lambda: self.skip_seconds(-1))
        btn_step_m1f = QPushButton("◀ 1F")
        btn_step_m1f.clicked.connect(lambda: self.step_frames(-1))

        self.btn_play = QPushButton("▶ 再生 (Space)")
        self.btn_play.setStyleSheet("background-color: #0078d4; color: #fff; font-weight: bold; min-width: 80px; padding: 6px;")
        self.btn_play.clicked.connect(self.toggle_play)

        btn_step_p1f = QPushButton("1F ▶")
        btn_step_p1f.clicked.connect(lambda: self.step_frames(1))
        btn_skip_p1 = QPushButton("1s ▶")
        btn_skip_p1.clicked.connect(lambda: self.skip_seconds(1))
        btn_skip_p15 = QPushButton("15s ▶")
        btn_skip_p15.clicked.connect(lambda: self.skip_seconds(15))
        btn_skip_p30 = QPushButton("30s ▶")
        btn_skip_p30.clicked.connect(lambda: self.skip_seconds(30))

        ctrl_layout.addWidget(btn_skip_m30)
        ctrl_layout.addWidget(btn_skip_m15)
        ctrl_layout.addWidget(btn_skip_m1)
        ctrl_layout.addWidget(btn_step_m1f)
        ctrl_layout.addWidget(self.btn_play)
        ctrl_layout.addWidget(btn_step_p1f)
        ctrl_layout.addWidget(btn_skip_p1)
        ctrl_layout.addWidget(btn_skip_p15)
        ctrl_layout.addWidget(btn_skip_p30)

        ctrl_layout.addSpacing(16)

        # 確認再生ボタン
        btn_check_head = QPushButton("👁️ 先頭5秒確認")
        btn_check_head.setToolTip("In点から5秒再生してCM切れ目をチェック")
        btn_check_head.clicked.connect(self.preview_clip_head)

        btn_check_tail = QPushButton("👁️ 末尾5秒確認")
        btn_check_tail.setToolTip("Out点直前の5秒間を再生してCM切れ目をチェック")
        btn_check_tail.clicked.connect(self.preview_clip_tail)

        ctrl_layout.addWidget(btn_check_head)
        ctrl_layout.addWidget(btn_check_tail)

        ctrl_layout.addSpacing(16)

        # ざっくり分割
        btn_split = QPushButton("✂ ここで分割 (S)")
        btn_split.setObjectName("splitButton")
        btn_split.setToolTip("現在位置でクリップを2つにざっくり分割")
        btn_split.clicked.connect(self.split_current_clip)
        ctrl_layout.addWidget(btn_split)

        ctrl_layout.addStretch()

        btn_reset = QPushButton("↺ 全体に戻す")
        btn_reset.setToolTip("クリップ範囲を元動画全体にリセット")
        btn_reset.clicked.connect(self.reset_clip_range)
        ctrl_layout.addWidget(btn_reset)

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

        gb_clip = QGroupBox("選択中クリップ情報")
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

        gb_tips = QGroupBox("ショートカット一覧")
        gb_tips_layout = QVBoxLayout(gb_tips)
        gb_tips_layout.addWidget(QLabel("Space : 再生 / 一時停止"))
        gb_tips_layout.addWidget(QLabel("S : 現在位置で分割"))
        gb_tips_layout.addWidget(QLabel("I / O : In点 / Out点設定"))
        gb_tips_layout.addWidget(QLabel("← / → : 1コマ戻し / 送り"))
        gb_tips_layout.addWidget(QLabel("Ctrl + ←/→ : 1秒送り / 戻し"))
        gb_tips_layout.addWidget(QLabel("Shift + ←/→ : 5秒送り / 戻し"))
        gb_tips_layout.addWidget(QLabel("↑ / ↓ : 15秒 / 30秒スキップ"))
        gb_tips_layout.addWidget(QLabel("Delete : クリップ削除"))
        side_layout.addWidget(gb_tips)

        side_layout.addStretch()
        h_splitter.addWidget(side_panel)
        h_splitter.setStretchFactor(0, 1)

        # --- 下部：ストーリーボード ---
        sb_container = QWidget()
        sb_layout = QVBoxLayout(sb_container)
        sb_layout.setContentsMargins(0, 0, 0, 0)
        sb_layout.setSpacing(4)

        sb_header = QHBoxLayout()
        sb_title = QLabel("🎞️ ストーリーボード（クリップを選んで上で微調整 / マウスで並び替え / Delete で削除）")
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

        self.statusBar().showMessage("準備完了 - 動画ファイルをドラッグ＆ドロップしてください")

    def _setup_shortcuts(self):
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_S), self, self.split_current_clip)
        QShortcut(QKeySequence(Qt.Key.Key_I), self, self.set_current_as_in)
        QShortcut(QKeySequence(Qt.Key.Key_O), self, self.set_current_as_out)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self.step_frames(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self.step_frames(1))
        QShortcut(QKeySequence("Ctrl+Left"), self, lambda: self.skip_seconds(-1))
        QShortcut(QKeySequence("Ctrl+Right"), self, lambda: self.skip_seconds(1))
        QShortcut(QKeySequence("Shift+Left"), self, lambda: self.skip_seconds(-5))
        QShortcut(QKeySequence("Shift+Right"), self, lambda: self.skip_seconds(5))
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.skip_seconds(15))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.skip_seconds(-15))
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, lambda: self.skip_seconds(30))
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, lambda: self.skip_seconds(-30))
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, self.storyboard.delete_selected)

    def _setup_player_events(self):
        self.player.positionChanged.connect(self.on_player_position_changed)
        self.exportProgressSignal.connect(self._update_export_progress)
        self.exportFinishedSignal.connect(self._finish_export)

    def set_mode(self, mode: str):
        self.mode = mode
        msg = "⚡ 完全無劣化モード (再エンコードなし・超高速)" if mode == "lossless" else "🎯 スマートカットモード (1フレーム精度・境界のみ再エンコード)"
        self.statusBar().showMessage(msg, 3000)

    # --- 高速動画追加 & 互換性チェック ---
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
                            "フォーマット不一致 (除外)",
                            f"【{meta.file_name}】は基準動画と形式が異なるため追加できませんでした。\n\n理由: {reason}"
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

                self.statusBar().showMessage(f"追加しました: {meta.file_name}", 2500)

            except Exception as e:
                QMessageBox.critical(self, "エラー", f"動画の読み込みに失敗しました: {path}\n{e}")

        self.update_stats()

    def _on_thumbnail_ready(self, clip_id: str, pixmap):
        for i in range(self.storyboard.count()):
            item = self.storyboard.item(i)
            if isinstance(item, StoryboardItem) and item.clip_id == clip_id:
                scaled = pixmap.scaled(160, 90, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                item.setIcon(QIcon(scaled))
                break

    # --- クリップ選択 & タイムライン切り替え ---
    def on_clip_selected(self, item: StoryboardItem):
        self.active_clip_item = item
        file_path = item.file_path

        current_source = self.player.source().toLocalFile()
        if os.path.abspath(current_source) != os.path.abspath(file_path):
            self.player.setSource(QUrl.fromLocalFile(file_path))

        # クリップ拡大タイムラインスライダーを設定
        total_ms = int(item.total_sec * 1000)
        in_ms = int(item.start_sec * 1000)
        out_ms = int(item.end_sec * 1000)
        self.timeline_slider.set_clip_info(total_ms, in_ms, out_ms, in_ms)
        self.player.setPosition(in_ms)

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

    # --- プレイヤー再生 & スキップ制御 ---
    def toggle_play(self):
        self.preview_end_limit_ms = None
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 再生 (Space)")
        else:
            self.player.play()
            self.btn_play.setText("⏸ 一時停止")

    def skip_seconds(self, sec: float):
        """指定秒数前後にスキップ (15秒/30秒/1秒など)"""
        new_pos = max(0, self.player.position() + int(sec * 1000))
        self.player.setPosition(new_pos)

    def step_frames(self, frames: int):
        """1コマ送り / 戻し (約33ms)"""
        new_pos = max(0, self.player.position() + frames * 33)
        self.player.setPosition(new_pos)

    def on_wheel_step(self, step: int):
        self.step_frames(step)

    def on_player_position_changed(self, pos_ms: int):
        self.timeline_slider.set_position(pos_ms)
        sec = pos_ms / 1000.0
        self.lbl_current_time.setText(self._format_time(sec))

        # 先頭/末尾プレビュー中の停止リミット判定
        if self.preview_end_limit_ms is not None and pos_ms >= self.preview_end_limit_ms:
            self.player.pause()
            self.preview_end_limit_ms = None
            self.btn_play.setText("▶ 再生 (Space)")
            return

        # 通常再生時、クリップ末尾に到達したら一時停止
        if self.active_clip_item:
            out_ms = int(self.active_clip_item.end_sec * 1000)
            if pos_ms >= out_ms and self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.player.pause()
                self.player.setPosition(int(self.active_clip_item.start_sec * 1000))
                self.btn_play.setText("▶ 再生 (Space)")

    def on_slider_position_changed(self, pos_ms: int):
        self.preview_end_limit_ms = None
        self.player.setPosition(pos_ms)

    # --- 【微調整機能】In点 / Out点 コマ送り・秒送り伸縮 ---
    def adjust_in_time(self, delta_sec: float):
        """先頭(In点)を前後させる (削り込み or 外側への余白復活)"""
        if not self.active_clip_item:
            return
        item = self.active_clip_item
        # 0.0 以上、out_sec - 0.1 以下の範囲で自由に伸縮
        new_in = max(0.0, min(item.start_sec + delta_sec, item.end_sec - 0.1))
        item.start_sec = new_in
        item.update_text()

        # タイムラインスライダー更新
        self._sync_slider_ranges()
        self._update_clip_info()
        self.update_stats()

        # プレビューを即座に In 点の位置に合わせて目で確認できるようにする！
        self.player.setPosition(int(new_in * 1000))

    def adjust_out_time(self, delta_sec: float):
        """末尾(Out点)を前後させる (削り込み or 外側への余白復活)"""
        if not self.active_clip_item:
            return
        item = self.active_clip_item
        # in_sec + 0.1 以上、total_sec 以下の範囲で自由に伸縮
        new_out = max(item.start_sec + 0.1, min(item.end_sec + delta_sec, item.total_sec))
        item.end_sec = new_out
        item.update_text()

        self._sync_slider_ranges()
        self._update_clip_info()
        self.update_stats()

        # プレビューを即座に Out 点の位置に合わせて目で確認！
        self.player.setPosition(int(new_out * 1000))

    def set_current_as_in(self):
        if not self.active_clip_item:
            return
        cur_sec = self.player.position() / 1000.0
        self.active_clip_item.start_sec = min(cur_sec, self.active_clip_item.end_sec - 0.1)
        self.active_clip_item.update_text()
        self._sync_slider_ranges()
        self._update_clip_info()
        self.update_stats()
        self.statusBar().showMessage(f"In点を設定: {self._format_time(self.active_clip_item.start_sec)}", 2000)

    def set_current_as_out(self):
        if not self.active_clip_item:
            return
        cur_sec = self.player.position() / 1000.0
        self.active_clip_item.end_sec = max(cur_sec, self.active_clip_item.start_sec + 0.1)
        self.active_clip_item.update_text()
        self._sync_slider_ranges()
        self._update_clip_info()
        self.update_stats()
        self.statusBar().showMessage(f"Out点を設定: {self._format_time(self.active_clip_item.end_sec)}", 2000)

    def reset_clip_range(self):
        if not self.active_clip_item:
            return
        self.active_clip_item.start_sec = 0.0
        self.active_clip_item.end_sec = self.active_clip_item.total_sec
        self.active_clip_item.update_text()
        self._sync_slider_ranges()
        self._update_clip_info()
        self.update_stats()
        self.statusBar().showMessage("クリップ範囲を元動画全体に戻しました", 2000)

    def _sync_slider_ranges(self):
        if not self.active_clip_item:
            return
        item = self.active_clip_item
        total_ms = int(item.total_sec * 1000)
        in_ms = int(item.start_sec * 1000)
        out_ms = int(item.end_sec * 1000)
        cur_ms = self.player.position()
        self.timeline_slider.set_clip_info(total_ms, in_ms, out_ms, cur_ms)

    # --- 【確認再生】先頭5秒・末尾5秒チェック ---
    def preview_clip_head(self):
        """In点から5秒間プレビュー再生"""
        if not self.active_clip_item:
            return
        in_ms = int(self.active_clip_item.start_sec * 1000)
        out_ms = int(self.active_clip_item.end_sec * 1000)
        self.preview_end_limit_ms = min(out_ms, in_ms + 5000)
        self.player.setPosition(in_ms)
        self.player.play()
        self.btn_play.setText("⏸ 一時停止")

    def preview_clip_tail(self):
        """Out点直前5秒からOut点までプレビュー再生"""
        if not self.active_clip_item:
            return
        in_ms = int(self.active_clip_item.start_sec * 1000)
        out_ms = int(self.active_clip_item.end_sec * 1000)
        start_ms = max(in_ms, out_ms - 5000)
        self.preview_end_limit_ms = out_ms
        self.player.setPosition(start_ms)
        self.player.play()
        self.btn_play.setText("⏸ 一時停止")

    # --- ざっくり分割 (Split) ---
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

        ThumbnailManager.instance().request_thumbnail(
            clip_id=new_id,
            video_path=item.file_path,
            time_sec=cur_sec,
            callback=self._on_thumbnail_ready
        )

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
            self.lbl_clip_range.setText("選択中クリップ範囲: 未選択")
            self.lbl_in_value.setText("00:00:00.000")
            self.lbl_out_value.setText("00:00:00.000")
            return

        item = self.active_clip_item
        dur = item.end_sec - item.start_sec
        self.lbl_clip_name.setText(f"ファイル: {item.file_name}")
        self.lbl_clip_in.setText(f"In 点: {self._format_time(item.start_sec)}")
        self.lbl_clip_out.setText(f"Out 点: {self._format_time(item.end_sec)}")
        self.lbl_clip_len.setText(f"長さ: {self._format_time(dur)}")
        self.lbl_total_time.setText(self._format_time(item.total_sec))

        self.lbl_in_value.setText(self._format_time(item.start_sec))
        self.lbl_out_value.setText(self._format_time(item.end_sec))
        self.lbl_clip_range.setText(
            f"クリップ範囲: {self._format_time(item.start_sec)} 〜 {self._format_time(item.end_sec)} ({self._format_time(dur)})"
        )

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

        # デフォルト名は元ファイル名(1)_recipe.json
        first_item = self.storyboard.item(0)
        default_name = "cmmm_recipe.json"
        if isinstance(first_item, StoryboardItem):
            base, _ = os.path.splitext(first_item.file_path)
            default_name = f"{base}_recipe.json"

        save_path, _ = QFileDialog.getSaveFileName(
            self, "レシピ保存先", default_name, "JSON レシピ (*.json)"
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

        # 出力ファイル名: 元の名前(1).mp4 を初期値として自動生成！
        first_item = self.storyboard.item(0)
        default_out = "output_cmmm.mp4"
        if isinstance(first_item, StoryboardItem):
            default_out = generate_default_output_path(first_item.file_path)

        save_path, _ = QFileDialog.getSaveFileName(
            self, "動画書き出し先", default_out, "動画ファイル (*.mp4 *.mkv);;すべてのファイル (*.*)"
        )
        if not save_path:
            return

        recipe = self._build_recipe(save_path)

        self.progress_dialog = QProgressDialog("動画を書き出し中...", "キャンセル", 0, 100, self)
        self.progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.progress_dialog.setMinimumDuration(0)
        self.progress_dialog.setValue(0)

        def run_task():
            def progress_cb(cur, total, msg):
                self.exportProgressSignal.emit(cur, total, msg)

            try:
                execute_recipe(recipe, progress_callback=progress_cb)
                self.exportFinishedSignal.emit(True, f"書き出しが完了しました！\n\n出力先:\n{save_path}")
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
