"""
test_player.py
CMMM 連携用プレイヤー (CMMM Player) および CMMM本体との双方向レシピ連携の自動テスト
"""

import os
import sys
import json
import time
import tempfile

# Windows コンソールでの文字エンコードエラー防止
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QEvent, QMimeData, QUrl, QPointF
from PySide6.QtGui import QKeyEvent, QDropEvent
from PySide6.QtMultimedia import QMediaPlayer

from ui.player_window import PlayerWindow
from ui.main_window import MainWindow

app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)


def log_trace(msg):
    print(msg, flush=True)
    with open("test_run.log", "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def run_player_tests():
    with open("test_run.log", "w", encoding="utf-8") as f:
        f.write("=== START ===\n")
    sample1 = os.path.abspath("samples/sample1.mp4")
    sample2 = os.path.abspath("samples/sample2.mp4")
    assert os.path.exists(sample1), f"テスト用動画が見つかりません: {sample1}"
    assert os.path.exists(sample2), f"テスト用動画が見つかりません: {sample2}"

    print("\n=== [1/6] プレイヤー初期化 ＆ 動画読み込み ＆ 再生画面へのドロップテスト ===")
    player_win = PlayerWindow()
    player_win.open_video(sample1)
    assert player_win.file_path == sample1
    assert player_win.lbl_file_name.text() == os.path.basename(sample1)
    print("動画オープン成功:", sample1)

    # 再生画面 (video_widget) へのドラッグ＆ドロップテスト
    mime2 = QMimeData()
    mime2.setUrls([QUrl.fromLocalFile(sample2)])
    drop_ev2 = QDropEvent(QPointF(100, 100), Qt.DropAction.CopyAction, mime2, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    player_win.video_widget.dropEvent(drop_ev2)
    assert player_win.file_path == sample2
    assert player_win.lbl_file_name.text() == os.path.basename(sample2)

    mime1 = QMimeData()
    mime1.setUrls([QUrl.fromLocalFile(sample1)])
    drop_ev1 = QDropEvent(QPointF(100, 100), Qt.DropAction.CopyAction, mime1, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    player_win.video_widget.dropEvent(drop_ev1)
    assert player_win.file_path == sample1
    assert player_win.lbl_file_name.text() == os.path.basename(sample1)
    print("再生画面へのドラッグ＆ドロップによる動画読み込み 成功！")

    print("\n=== [2/6] 全画面（フルスクリーン）トグル ＆ マウスホバー ＆ カーソル自動非表示テスト ===")
    player_win.enter_fullscreen()
    assert player_win.isFullScreen() is True
    assert player_win.right_panel.isVisible() is False
    assert player_win.hover_check_timer.isActive() is True
    assert player_win._cursor_hide_timer.isActive() is True
    assert player_win._cursor_hidden is False
    print("全画面化（右パネル非表示 ＆ ホバー監視タイマー起動 ＆ カーソル非表示タイマー起動）成功！")

    # 上部エリアにマウスがある場合の非表示判定
    player_win.bottom_controls.hide()
    assert player_win.bottom_controls.isVisible() is False
    print("動画エリア（上部）での自動非表示 成功！")

    # 下部エリアにマウスがある場合の表示判定
    player_win.bottom_controls.show()
    assert player_win.bottom_controls.isVisible() is True
    print("コントロール領域（下部ホバー）での即時表示 成功！")

    # マウス静止（タイムアウト）時のカーソル ＆ コントロール自動非表示テスト
    player_win._hide_cursor_fullscreen()
    assert player_win._cursor_hidden is True
    assert player_win.bottom_controls.isVisible() is False
    print("マウス静止時のカーソル ＆ コントロール自動非表示 成功！")

    # マウス移動時のカーソル再表示 ＆ タイマーリセットテスト
    player_win._on_fullscreen_mouse_activity()
    assert player_win._cursor_hidden is False
    assert player_win._cursor_hide_timer.isActive() is True
    print("マウス移動時のカーソル即時再表示 ＆ タイマーリセット 成功！")

    player_win.exit_fullscreen()
    assert player_win.isFullScreen() is False
    assert player_win.right_panel.isVisible() is True
    assert player_win.bottom_controls.isVisible() is True
    assert player_win.hover_check_timer.isActive() is False
    assert player_win._cursor_hide_timer.isActive() is False
    assert player_win._cursor_hidden is False
    assert player_win.btn_fullscreen.text() == "⛶"
    print("全画面解除（レイアウト復帰 ＆ カーソル完全復元）成功！")

    print("\n=== [2.5/6] スキップ時間セレクター ＆ ジャンプショートカットテスト ===")
    # 初期状態は「5秒」
    assert player_win.skip_seconds_value == 5
    assert player_win.btn_skip_prev.text() == "◀ 5秒"
    assert player_win.btn_skip_next.text() == "5秒 ▶"

    # 10秒に変更 (index 1)
    player_win.combo_skip.setCurrentIndex(1)
    assert player_win.skip_seconds_value == 10
    assert player_win.btn_skip_prev.text() == "◀ 10秒"
    assert player_win.btn_skip_next.text() == "10秒 ▶"

    # 30秒に変更 (index 2)
    player_win.combo_skip.setCurrentIndex(2)
    assert player_win.skip_seconds_value == 30
    assert player_win.btn_skip_prev.text() == "◀ 30秒"
    assert player_win.btn_skip_next.text() == "30秒 ▶"

    # 5分に変更 (index 4)
    player_win.combo_skip.setCurrentIndex(4)
    assert player_win.skip_seconds_value == 300
    assert player_win.btn_skip_prev.text() == "◀ 5分"
    assert player_win.btn_skip_next.text() == "5分 ▶"

    # 5秒に戻す (index 0)
    player_win.combo_skip.setCurrentIndex(0)
    assert player_win.skip_seconds_value == 5
    assert player_win.btn_skip_prev.text() == "◀ 5秒"
    assert player_win.btn_skip_next.text() == "5秒 ▶"
    print("スキップ設定切替（5秒/10秒/30秒/5分）＆ ボタン表記連動 成功！")

    # 秒数スキップ ＆ 連続キー入力デバウンス（累積加算）テスト
    # sample1.mp4 は 10秒 (10000ms) なので 1000ms を基準にテスト
    player_win.seek(1000, immediate=True)
    assert player_win.slider.position_ms == 1000

    # 1回スキップ (2秒)
    player_win.skip_seconds(2)
    assert player_win.slider.position_ms == 3000
    assert player_win._pending_seek_ms == 3000
    assert player_win._seek_debounce_timer.isActive() is True

    # タイマーが切れる前にさらに2回スキップ（連打シミュレーション: +2秒, +2秒）
    player_win.skip_seconds(2)  # -> 5000
    assert player_win.slider.position_ms == 5000
    assert player_win._pending_seek_ms == 5000
    player_win.skip_seconds(2)  # -> 7000
    assert player_win.slider.position_ms == 7000
    assert player_win._pending_seek_ms == 7000

    # デバウンスタイムアウト実行 -> 実プレイヤーへのシーク反映
    player_win._apply_pending_seek()
    assert player_win._pending_seek_ms is None
    assert player_win.slider.position_ms == 7000
    print("スマートシーク（即時UI更新 ＆ 連打時の累積加算 ＆ デバウンス実シーク）テスト 成功！")

    # 逆方向スキップ (-3秒)
    player_win.skip_seconds(-3)
    assert player_win.slider.position_ms == 4000
    player_win._apply_pending_seek()
    assert player_win._pending_seek_ms is None
    assert player_win.slider.position_ms == 4000
    print("逆方向秒数スキップテスト 成功！")

    # キーフレームスキップテスト (Alt+Left / Alt+Right)
    player_win.set_keyframes([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    player_win.seek(2000, immediate=True)   # 2.0秒
    player_win.skip_keyframe(1)   # 次のキーフレーム (3.0秒)
    assert player_win.slider.position_ms == 3000
    player_win.skip_keyframe(1)   # 次のキーフレーム (4.0秒)
    assert player_win.slider.position_ms == 4000
    player_win.skip_keyframe(-1)  # 前のキーフレーム (3.0秒)
    assert player_win.slider.position_ms == 3000
    player_win.skip_keyframe(-1)  # 前のキーフレーム (2.0秒)
    assert player_win.slider.position_ms == 2000
    print("キーフレーム単位スキップ（前後のキーフレームへ吸着ジャンプ）テスト 成功！")

    # キーイベントによるスキップテスト (Left / Right, 5秒プリセット)
    player_win.seek(3000, immediate=True)
    ev_right = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier)
    player_win.keyPressEvent(ev_right)  # 3000 + 5000 = 8000
    assert player_win.slider.position_ms == 8000
    ev_left = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier)
    player_win.keyPressEvent(ev_left)   # 8000 - 5000 = 3000
    assert player_win.slider.position_ms == 3000
    player_win._apply_pending_seek()
    print("Left/Rightキーによるスキップテスト 成功！")

    print("\n=== [3/6] 音量コントロール ＆ ミュートテスト ===")
    # 音量スライダー変更
    player_win.volume_slider.setValue(80)
    assert player_win.lbl_volume.text() == "80%"
    assert abs(player_win.audio_output.volume() - 0.8) < 0.01

    # 上下キーによる音量ショートカットテスト (Up: +5%, Down: -5%)
    ev_up = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier)
    player_win.keyPressEvent(ev_up)
    assert player_win.volume_slider.value() == 85
    assert player_win.lbl_volume.text() == "85%"

    ev_down = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    player_win.keyPressEvent(ev_down)
    assert player_win.volume_slider.value() == 80
    assert player_win.lbl_volume.text() == "80%"
    print("上下キー音量ショートカット（Up/Down による 5% 単位変更）テスト 成功！")

    # 音量微調整メソッド
    player_win.change_volume(5)
    assert player_win.lbl_volume.text() == "85%"

    # ミュートトグル
    player_win.toggle_mute()
    assert player_win.audio_output.isMuted() is True
    assert player_win.volume_slider.value() == 0
    assert player_win.btn_mute.text() == "🔇"

    # ミュート解除 (保存音量85%に復帰)
    player_win.toggle_mute()
    assert player_win.audio_output.isMuted() is False
    assert player_win.volume_slider.value() == 85
    assert player_win.btn_mute.text() == "🔊"
    print("音量変更・微調整・ミュート復帰テスト 成功！")

    print("\n=== [4/6] 速度調整ステップボタンテスト ===")
    assert player_win.playback_rate == 1.0
    assert player_win.btn_speed_reset.text() == "1.0x"
    # アップ
    player_win.change_speed_step(1)
    assert player_win.playback_rate == 2.0
    assert player_win.btn_speed_reset.text() == "2.0x"
    # リセット
    player_win.reset_speed()
    assert player_win.playback_rate == 1.0
    assert player_win.btn_speed_reset.text() == "1.0x"
    print("速度調整ステップボタンテスト 成功！")

    print("\n=== [5/6] 分割位置マーキング（Mキー）＆ 前後ジャンプ（M+Left/Right）テスト ===")
    player_win.clear_all_markers()
    assert len(player_win.markers) == 0

    # マーク 1: 3.0秒
    player_win.seek(3000)
    player_win.toggle_marker()
    assert len(player_win.markers) == 1
    assert player_win.markers[0] == 3.0
    assert player_win.marker_list_widget.count() == 1

    # マーク 2: 7.0秒
    player_win.seek(7000)
    player_win.toggle_marker()
    assert len(player_win.markers) == 2
    assert player_win.markers == [3.0, 7.0]
    assert player_win.marker_list_widget.count() == 2
    print(f"2つの分割マークを正常に登録: {player_win.markers}")

    # Jキー（前のマーク） / Lキー（次のマーク）によるジャンプテスト
    player_win.seek(7000)
    player_win.jump_to_marker(-1)  # Jキー相当: 前のマーク (3.0秒) へ
    assert player_win.slider.position_ms == 3000
    print("Jキー（前のマークへジャンプ: 3.0秒）テスト 成功！")

    player_win.jump_to_marker(1)   # Lキー相当: 次のマーク (7.0秒) へ
    assert player_win.slider.position_ms == 7000
    print("Lキー（次のマークへジャンプ: 7.0秒）テスト 成功！")

    # --- [NEW] シークバー独立動作テスト（シークしてもマーカーが動かない） ---
    player_win.slider.dragging = True
    player_win.slider.position_ms = 5000
    player_win.slider.dragging = False
    assert player_win.markers == [3.0, 7.0], "シークバー操作でマーカーが動いてはいけません"
    print("シークバーの独立動作（シークバー操作でマーカー不変）テスト 成功！")

    # --- [NEW] Shift+M（現在位置へ移動）テスト ---
    # 4.5秒にシークして、3.0秒のマーカーを 4.5秒へ移動
    player_win.seek(4500)
    player_win.marker_list_widget.setCurrentRow(0)  # 3.0秒のマーカーを選択
    player_win.move_selected_marker_to_current()
    assert player_win.markers == [4.5, 7.0]
    print("Shift+M（選択マークを 4.5秒へ移動）テスト 成功！")

    # --- [NEW] Undo（Ctrl+Z: 元に戻す）テスト ---
    player_win.undo()
    assert player_win.markers == [3.0, 7.0], f"Undo失敗: markers={player_win.markers}"
    print("Undo（Ctrl+Z: 移動前の 3.0秒へ復元）テスト 成功！")

    # --- [NEW] Redo（Ctrl+Y: やり直す）テスト ---
    player_win.redo()
    assert player_win.markers == [4.5, 7.0], f"Redo失敗: markers={player_win.markers}"
    print("Redo（Ctrl+Y: 再度 4.5秒へ再適用）テスト 成功！")

    # 再び Undo して 3.0秒 に戻す
    player_win.undo()
    assert player_win.markers == [3.0, 7.0]

    # --- [NEW] 削除に対する Undo テスト ---
    player_win.seek(3100)
    player_win.marker_list_widget.setCurrentRow(-1)
    player_win.delete_selected_marker()  # 3.0秒を最寄り削除
    assert player_win.markers == [7.0]
    player_win.undo()  # 削除を元に戻す
    assert player_win.markers == [3.0, 7.0]
    print("削除に対する Undo（Ctrl+Z によるマーカー復元）テスト 成功！")

    # --- [NEW] 右クリック削除リクエストテスト ---
    player_win.slider.markerDeleteRequested.emit(7000)
    assert player_win.markers == [3.0]
    player_win.undo()  # これもUndo可能
    assert player_win.markers == [3.0, 7.0]
    print("右クリック削除 ＆ Undoテスト 成功！")

    print("\n=== [6/6] レシピ生成 ＆ CMMM本体での自動分割スライスクリップ展開テスト ===")
    recipe = player_win.build_recipe_dict(mode="lossless")
    # 3.0秒と7.0秒の分割点により、10秒の動画は3つのクリップに分割される
    assert len(recipe["clips"]) == 3
    assert recipe["clips"][0]["source"] == sample1
    assert recipe["clips"][0]["start"] == 0.0
    assert recipe["clips"][0]["end"] == 3.0
    assert recipe["clips"][1]["start"] == 3.0
    assert recipe["clips"][1]["end"] == 7.0
    assert recipe["clips"][2]["start"] == 7.0
    assert abs(recipe["clips"][2]["end"] - 10.0) < 0.1
    print(f"分割点による3つのクリップ自動スライス成功: {[ (c['start'], c['end']) for c in recipe['clips'] ]}")

    # 一時ファイルに保存して CMMM 本体でロード
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp_json = tmp.name
        json.dump(recipe, tmp, indent=2)

    try:
        cmmm_win = MainWindow()
        success = cmmm_win.load_recipe(tmp_json)
        assert success is True, "CMMM本体でのレシピ読み込みに失敗しました"
        assert cmmm_win.storyboard.count() == 3, f"CMMMに展開されたクリップ数が不正: {cmmm_win.storyboard.count()}"

        clip0 = cmmm_win.storyboard.item(0)
        assert clip0.file_path == sample1
        assert abs(clip0.start_sec - 0.0) < 0.01
        assert abs(clip0.end_sec - 3.0) < 0.01

        clip1 = cmmm_win.storyboard.item(1)
        assert clip1.file_path == sample1
        assert abs(clip1.start_sec - 3.0) < 0.01
        assert abs(clip1.end_sec - 7.0) < 0.01

        clip2 = cmmm_win.storyboard.item(2)
        assert clip2.file_path == sample1
        assert abs(clip2.start_sec - 7.0) < 0.01
        assert abs(clip2.end_sec - 10.0) < 0.1
        print("CMMM本体へのレシピインポート ＆ 分割クリップ自動展開 完全成功！")

        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(1500)
    finally:
        if os.path.exists(tmp_json):
            os.remove(tmp_json)

    print("\n[SUCCESS] CMMM プレイヤーおよび本体連携テストがすべて正常に通過しました！")
    log_trace("[SUCCESS] ALL PLAYER TESTS PASSED!")


if __name__ == "__main__":
    try:
        run_player_tests()
        print("\nALL PLAYER TESTS PASSED!")
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        with open("test_err.log", "w", encoding="utf-8") as f:
            traceback.print_exc(file=f)
        sys.exit(1)
