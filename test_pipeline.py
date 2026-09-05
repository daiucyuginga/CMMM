"""
test_pipeline.py
CMMM のコア機能（解析・互換性判定・完全無劣化カット・スマートカット・CLIレシピ実行）の自動検証
"""

import os
import sys
import json
import subprocess

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from core.ffmpeg_tools import FFmpegRunner
from core.gop_analyzer import VideoAnalyzer
from core.smart_cutter import SmartCutter, ClipSegment
from core.engine import execute_recipe

def test_all():
    print("=== [1/5] FFmpeg / FFprobe 検出テスト ===")
    runner = FFmpegRunner()
    assert runner.is_available(), "FFmpeg または FFprobe が利用できません"
    print(f"FFmpeg: {runner.ffmpeg_path}")
    print(f"FFprobe: {runner.ffprobe_path}")

    print("\n=== [2/5] メタデータ & キーフレーム解析テスト ===")
    analyzer = VideoAnalyzer(runner)
    meta1 = analyzer.get_metadata("samples/sample1.mp4")
    meta2 = analyzer.get_metadata("samples/sample2.mp4")
    meta_diff = analyzer.get_metadata("samples/sample_incompatible.mp4")

    print(f"Sample 1: {meta1.width}x{meta1.height}, {meta1.fps}fps, codec={meta1.video_codec}, duration={meta1.duration:.2f}s")
    print(f"Sample Incompatible: {meta_diff.width}x{meta_diff.height}, {meta_diff.fps}fps")

    kfs1 = analyzer.get_keyframes("samples/sample1.mp4")
    print(f"Sample 1 キーフレーム数: {len(kfs1)} (先頭3個: {kfs1[:3]})")
    assert len(kfs1) > 0

    print("\n=== [3/5] フォーマット互換性判定テスト ===")
    # 互換性ありのケース
    ok1, reason1 = VideoAnalyzer.check_compatibility(meta1, meta2)
    print(f"Sample1 vs Sample2: 互換性={ok1} ({reason1})")
    assert ok1 is True, "同スペックの動画が互換性なしと判定されました"

    # 互換性なしのケース (解像度違い)
    ok2, reason2 = VideoAnalyzer.check_compatibility(meta1, meta_diff)
    print(f"Sample1 vs SampleIncompatible: 互換性={ok2} ({reason2})")
    assert ok2 is False, "解像度違いの動画が互換性ありと誤判定されました"
    assert "解像度" in reason2

    print("\n=== [4/5] 完全無劣化カット＆結合テスト ===")
    cutter = SmartCutter(runner)
    lossless_out = os.path.abspath("samples/out_lossless.mp4")
    if os.path.exists(lossless_out):
        os.remove(lossless_out)

    clips_lossless = [
        ClipSegment(os.path.abspath("samples/sample1.mp4"), 0.0, 4.0),
        ClipSegment(os.path.abspath("samples/sample2.mp4"), 2.0, 6.0)
    ]
    cutter.process_clips(clips_lossless, lossless_out, mode="lossless")
    assert os.path.exists(lossless_out), "完全無劣化出力ファイルが存在しません"
    out_meta = analyzer.get_metadata(lossless_out)
    print(f"完全無劣化出力成功！ 長さ: {out_meta.duration:.2f}s, サイズ: {os.path.getsize(lossless_out)} bytes")

    print("\n=== [5/5] スマートカット（ミリ秒精度）結合テスト ===")
    smart_out = os.path.abspath("samples/out_smart.mp4")
    if os.path.exists(smart_out):
        os.remove(smart_out)

    # キーフレームの合間（任意ミリ秒）のカット
    clips_smart = [
        ClipSegment(os.path.abspath("samples/sample1.mp4"), 1.250, 4.750), # 3.5秒
        ClipSegment(os.path.abspath("samples/sample2.mp4"), 2.100, 5.600)  # 3.5秒
    ]
    cutter.process_clips(clips_smart, smart_out, mode="smart")
    assert os.path.exists(smart_out), "スマートカット出力ファイルが存在しません"
    smart_meta = analyzer.get_metadata(smart_out)
    print(f"スマートカット出力成功！ 長さ: {smart_meta.duration:.2f}s, サイズ: {os.path.getsize(smart_out)} bytes")

    print("\n=== レシピJSON経由のCLI実行テスト ===")
    recipe_path = os.path.abspath("samples/test_recipe.json")
    cli_out = os.path.abspath("samples/out_cli.mp4")
    if os.path.exists(cli_out):
        os.remove(cli_out)

    recipe_data = {
        "mode": "smart",
        "output_file": cli_out,
        "clips": [
            {"source": os.path.abspath("samples/sample1.mp4"), "start": 0.5, "end": 3.5},
            {"source": os.path.abspath("samples/sample2.mp4"), "start": 1.0, "end": 4.0}
        ]
    }
    with open(recipe_path, "w", encoding="utf-8") as f:
        json.dump(recipe_data, f, indent=2)

    res = subprocess.run(["python", "run_engine.py", recipe_path], capture_output=True)
    out_str = res.stdout.decode("utf-8", errors="replace")
    err_str = res.stderr.decode("utf-8", errors="replace")
    print(out_str)
    assert res.returncode == 0, f"CLI実行失敗: {err_str}"
    assert os.path.exists(cli_out), "CLI出力ファイルが存在しません"
    print("CLI経由のレシピ実行も完全パス！")

    print("\n[SUCCESS] すべての単体・統合テストが正常に通過しました！")

if __name__ == "__main__":
    test_all()
