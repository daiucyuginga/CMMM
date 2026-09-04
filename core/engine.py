"""
core/engine.py
レシピ (JSON) を読み込んで動画の無劣化・スマートカット結合を実行する CLI エンジン
"""

import sys
import os
import json
import argparse
import logging
from typing import Dict, Any

from .ffmpeg_tools import FFmpegRunner
from .gop_analyzer import VideoAnalyzer, VideoMetadata
from .smart_cutter import SmartCutter, ClipSegment

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger("cmmm_engine")


def execute_recipe(recipe_data: Dict[str, Any], progress_callback=None) -> bool:
    """レシピ辞書を解析して動画処理を実行する"""
    mode = recipe_data.get("mode", "smart")
    output_file = recipe_data.get("output_file")
    raw_clips = recipe_data.get("clips", [])

    if not output_file:
        raise ValueError("レシピに output_file が指定されていません。")
    if not raw_clips:
        raise ValueError("レシピにクリップが含まれていません。")

    runner = FFmpegRunner()
    if not runner.is_available():
        raise RuntimeError("FFmpeg または FFprobe が見つかりません。")

    analyzer = VideoAnalyzer(runner)
    cutter = SmartCutter(runner)

    # クリップのバリデーションと互換性チェック
    clip_segments = []
    base_meta = None

    for i, c in enumerate(raw_clips):
        src = c.get("source")
        if not src or not os.path.exists(src):
            raise FileNotFoundError(f"クリップ {i + 1} の元動画が見つかりません: {src}")

        meta = analyzer.get_metadata(src)
        if base_meta is None:
            base_meta = meta
        else:
            is_compat, reason = VideoAnalyzer.check_compatibility(base_meta, meta)
            if not is_compat:
                raise ValueError(f"クリップ {i + 1} ({os.path.basename(src)}) は無劣化結合できません: {reason}")

        start = float(c.get("start", 0.0))
        end = float(c.get("end", meta.duration))
        clip_segments.append(ClipSegment(source_path=src, start_time=start, end_time=end))

    logger.info(f"処理モード: {mode.upper()}")
    logger.info(f"クリップ数: {len(clip_segments)}")
    logger.info(f"出力先: {output_file}")

    # カット・結合の実行
    return cutter.process_clips(
        clips=clip_segments,
        output_file=output_file,
        mode=mode,
        progress_callback=progress_callback
    )


def main():
    parser = argparse.ArgumentParser(description="CMMM 無劣化・スマートカット実行エンジン")
    parser.add_argument("recipe", help="実行するレシピファイル (JSON) のパス")
    parser.add_argument("--mode", choices=["lossless", "smart"], help="処理モードの上書き")
    parser.add_argument("--output", help="出力ファイルパスの上書き")

    args = parser.parse_args()

    if not os.path.exists(args.recipe):
        logger.error(f"レシピファイルが見つかりません: {args.recipe}")
        sys.exit(1)

    with open(args.recipe, "r", encoding="utf-8") as f:
        recipe_data = json.load(f)

    if args.mode:
        recipe_data["mode"] = args.mode
    if args.output:
        recipe_data["output_file"] = args.output

    def on_progress(current, total, msg):
        percent = int((current / total) * 100) if total > 0 else 0
        print(f"[{percent:3d}%] {msg}", flush=True)

    try:
        execute_recipe(recipe_data, progress_callback=on_progress)
        logger.info("[OK] 処理が正常に完了しました！")
    except Exception as e:
        logger.error(f"[ERROR] 処理失敗: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
