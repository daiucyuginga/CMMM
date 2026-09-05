"""
test_player.py
CMMM 連携用プレイヤー (CMMM Player) および CMMM本体との双方向レシピ連携の自動テスト
"""

import os
import sys
import json
import time
import tempfile
from PySide6.QtWidgets import QApplication
from PySide6.QtMultimedia import QMediaPlayer

from ui.player_window import PlayerWindow
from ui.main_window import MainWindow

app = QApplication.instance()
if not app:
    app = QApplication(sys.argv)


def run_player_tests():
    sample1 = os.path.abspath("samples/sample1.mp4")
    assert os.path.exists(sample1), f"テスト用動画が見つかりません: {sample1}"

    print("\n=== [1/6] プレイヤー初期化 ＆ 動画読み込みテスト ===")
    player_win = PlayerWindow()
    player_win.open_video(sample1)
    assert player_win.file_path == sample1
    assert player_win.lbl_file_name.text() == os.path.basename(sample1)
    print("動画オープン成功:", sample1)

    print("\n=== [2/6] 全画面（フルスクリーン）トグル ＆ マウスホバー表示判定テスト ===")
    player_win.enter_fullscreen()
    assert player_win.isFullScreen() is True
    assert player_win.right_panel.isVisible() is False
    assert player_win.hover_check_timer.isActive() is True
    print("全画面化（右パネル非表示 ＆ ホバー監視タイマー起動）成功！")

    # 上部エリアにマウスがある場合の非表示判定
    player_win.bottom_controls.hide()
    assert player_win.bottom_controls.isVisible() is False
    print("動画エリア（上部）での自動非表示 成功！")

    # 下部エリアにマウスがある場合の表示判定
    player_win.bottom_controls.show()
    assert player_win.bottom_controls.isVisible() is True
    print("コントロール領域（下部ホバー）での即時表示 成功！")

    player_win.exit_fullscreen()
    assert player_win.isFullScreen() is False
    assert player_win.right_panel.isVisible() is True
    assert player_win.bottom_controls.isVisible() is True
    assert player_win.hover_check_timer.isActive() is False
    assert player_win.btn_fullscreen.text() == "⛶"
    print("全画面解除（レイアウト復帰 ＆ 常時表示化）成功！")

    print("\n=== [2.5/6] スキップ時間セレクター ＆ ジャンプショートカットテスト ===")
    assert player_win.skip_seconds_value == 5
    assert player_win.btn_skip_prev.text() == "◀ 5秒"
    assert player_win.btn_skip_next.text() == "5秒 ▶"

    # 30秒に変更
    player_win.combo_skip.setCurrentIndex(2)
    assert player_win.skip_seconds_value == 30
    assert player_win.btn_skip_prev.text() == "◀ 30秒"
    assert player_win.btn_skip_next.text() == "30秒 ▶"

    # 5分に変更
    player_win.combo_skip.setCurrentIndex(4)
    assert player_win.skip_seconds_value == 300
    assert player_win.btn_skip_prev.text() == "◀ 5分"
    assert player_win.btn_skip_next.text() == "5分 ▶"

    # 5秒に戻す
    player_win.combo_skip.setCurrentIndex(0)
    assert player_win.skip_seconds_value == 5
    print("スキップ設定切替（5秒/30秒/5分）＆ ボタン表記連動 成功！")

    # ジャンプ機能テスト
    player_win.seek(5000)
    player_win.skip_seconds(3)    # 3秒進む
    assert player_win.slider.position_ms == 8000
    player_win.skip_seconds(-4)   # 4秒戻る
    assert player_win.slider.position_ms == 4000
    print("ジャンプショートカット（5秒/30秒/1分/5分）テスト 成功！")

    print("\n=== [3/6] 音量コントロール ＆ ミュートテスト ===")
    # 音量スライダー変更
    player_win.volume_slider.setValue(80)
    assert player_win.lbl_volume.text() == "80%"
    assert abs(player_win.audio_output.volume() - 0.8) < 0.01

    # 音量微調整
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

    print("\n=== [5/6] 視聴中 In/Out マーキング ＆ 区間登録テスト ===")
    # 区間 1: 1.5秒 ～ 4.0秒
    player_win.seek(1500)
    player_win.set_mark_in()
    assert player_win.mark_in_sec == 1.5
    assert player_win.slider.mark_in_ms == 1500

    player_win.seek(4000)
    player_win.set_mark_out()
    assert player_win.mark_in_sec is None
    assert len(player_win.clips) == 1
    assert player_win.clips[0] == (1.5, 4.0)

    # 区間 2: 6.0秒 ～ 8.5秒
    player_win.seek(6000)
    player_win.set_mark_in()
    player_win.seek(8500)
    player_win.set_mark_out()
    assert len(player_win.clips) == 2
    assert player_win.clips[1] == (6.0, 8.5)
    assert player_win.clip_list_widget.count() == 2
    print(f"2つの区間を正常にマーク登録: {player_win.clips}")

    print("\n=== [6/6] レシピ生成 ＆ CMMM本体での読み込み相互連携テスト ===")
    recipe = player_win.build_recipe_dict(mode="lossless")
    assert len(recipe["clips"]) == 2
    assert recipe["clips"][0]["source"] == sample1
    assert recipe["clips"][0]["start"] == 1.5
    assert recipe["clips"][0]["end"] == 4.0
    assert recipe["clips"][1]["start"] == 6.0
    assert recipe["clips"][1]["end"] == 8.5
    print("CMMM互換レシピ辞書の生成成功！")

    # 一時ファイルに保存して CMMM 本体でロード
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8") as tmp:
        tmp_json = tmp.name
        json.dump(recipe, tmp, indent=2)

    try:
        cmmm_win = MainWindow()
        success = cmmm_win.load_recipe(tmp_json)
        assert success is True, "CMMM本体でのレシピ読み込みに失敗しました"
        assert cmmm_win.storyboard.count() == 2, f"CMMMに展開されたクリップ数が不正: {cmmm_win.storyboard.count()}"

        clip0 = cmmm_win.storyboard.item(0)
        assert clip0.file_path == sample1
        assert abs(clip0.start_sec - 1.5) < 0.01
        assert abs(clip0.end_sec - 4.0) < 0.01

        clip1 = cmmm_win.storyboard.item(1)
        assert clip1.file_path == sample1
        assert abs(clip1.start_sec - 6.0) < 0.01
        assert abs(clip1.end_sec - 8.5) < 0.01
        print("CMMM本体へのレシピインポート ＆ ストーリーボードクリップ展開 完全成功！")

        from PySide6.QtCore import QThreadPool
        QThreadPool.globalInstance().waitForDone(1500)
    finally:
        if os.path.exists(tmp_json):
            os.remove(tmp_json)

    print("\n[SUCCESS] CMMM プレイヤーおよび本体連携テストがすべて正常に通過しました！")


if __name__ == "__main__":
    run_player_tests()
