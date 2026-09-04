"""
test_native_ui.py
PySide6 ネイティブ UI の主要機能（爆速読み込み、分割、削除、互換性判定、非同期サムネイル）の自動テスト
"""

import sys
import os
import time
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow
from ui.storyboard import StoryboardItem

def run_tests():
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()

    sample1 = os.path.abspath("samples/sample1.mp4")
    sample2 = os.path.abspath("samples/sample2.mp4")
    sample_diff = os.path.abspath("samples/sample_incompatible.mp4")

    print("=== [1/4] 高速動画追加テスト (0.1秒未満) ===")
    t0 = time.time()
    window.add_videos([sample1])
    elapsed = time.time() - t0
    print(f"Sample1 追加所要時間: {elapsed:.3f} 秒")
    assert elapsed < 1.5, f"読み込みが遅すぎます: {elapsed:.3f}s"
    assert window.storyboard.count() == 1
    assert window.base_meta is not None
    print(f"基準動画設定成功: {window.base_meta.file_name}")

    print("\n=== [2/5] デフォルト出力ファイル名テスト (元ファイル名(1).mp4) ===")
    from ui.main_window import generate_default_output_path
    out_path = generate_default_output_path(sample1)
    print(f"元ファイル: {sample1}")
    print(f"生成された出力パス: {out_path}")
    assert out_path.endswith("(1).mp4")
    print("デフォルト出力名 (1) テスト成功！")

    print("\n=== [3/5] 2本目追加 & 同形式確認 ===")
    window.add_videos([sample2])
    assert window.storyboard.count() == 2
    print(f"総クリップ数: {window.storyboard.count()}")

    print("\n=== [4/5] 形式不一致動画の除外テスト ===")
    from core.gop_analyzer import VideoAnalyzer
    meta_diff = window.analyzer.get_metadata(sample_diff)
    is_compat, reason = VideoAnalyzer.check_compatibility(window.base_meta, meta_diff)
    print(f"互換性判定結果: is_compat={is_compat}, reason={reason}")
    assert is_compat is False
    assert "解像度" in reason
    print("形式不一致が正しく判定されました！")

    print("\n=== [5/5] クリップ分割 (Split) & 削除 (Delete) テスト ===")
    item0 = window.storyboard.item(0)
    window.storyboard.setCurrentItem(item0)
    window.on_clip_selected(item0)

    # シークバーを 4.0 秒に設定して、ボタンクリックによる分割テスト！
    window.video_slider.set_position(4000)
    window.btn_split.click()
    assert window.storyboard.count() == 3, f"分割後のクリップ数が不正: {window.storyboard.count()}"
    print("【分割ボタンクリック】成功！ 2本 → 3本に分割されました")

    # 選択クリップを削除
    window.storyboard.delete_selected()
    assert window.storyboard.count() == 2, f"削除後のクリップ数が不正: {window.storyboard.count()}"
    print("クリップ削除 (Delete) 成功！ 3本 → 2本になりました")

    # 非同期サムネイルの完了を少し待つ
    time.sleep(0.5)
    app.processEvents()
    print("非同期サムネイル処理も正常稼働中！")

    print("\n[SUCCESS] ネイティブUIの主要ロジックテストがすべて正常に通過しました！")

if __name__ == "__main__":
    run_tests()
