"""
core/smart_cutter.py
完全無劣化カット（キーフレーム）およびスマートカット（境界GOP再エンコード）と、
FFmpeg concat demuxer による無劣化結合処理
"""

import os
import tempfile
import logging
from typing import List, Optional, Callable
from dataclasses import dataclass

from .ffmpeg_tools import FFmpegRunner
from .gop_analyzer import VideoAnalyzer, VideoMetadata

logger = logging.getLogger(__name__)


@dataclass
class ClipSegment:
    source_path: str
    start_time: float   # 秒
    end_time: float     # 秒


class SmartCutter:
    def __init__(self, runner: Optional[FFmpegRunner] = None):
        self.runner = runner or FFmpegRunner()
        self.analyzer = VideoAnalyzer(self.runner)

    def process_clips(
        self,
        clips: List[ClipSegment],
        output_file: str,
        mode: str = "smart",
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> bool:
        """
        指定されたクリップ一覧を処理し、1本の動画として結合出力する。
        mode: 'lossless' (完全無劣化・キーフレーム) または 'smart' (スマートカット)
        """
        if not clips:
            raise ValueError("処理対象のクリップがありません。")

        output_file = os.path.abspath(output_file)
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="cmmm_temp_") as temp_dir:
            clip_outputs = []
            total_clips = len(clips)

            for idx, clip in enumerate(clips):
                clip_out = os.path.join(temp_dir, f"clip_{idx:04d}.mp4")
                msg = f"クリップ {idx + 1}/{total_clips} 処理中..."
                if progress_callback:
                    progress_callback(idx, total_clips, msg)

                if mode == "smart":
                    self._cut_smart(clip, clip_out, temp_dir, idx)
                else:
                    self._cut_lossless(clip, clip_out)

                clip_outputs.append(clip_out)

            # 全クリップを無劣化結合 (concat demuxer)
            if progress_callback:
                progress_callback(total_clips, total_clips, "全クリップを結合中...")

            self._concat_videos(clip_outputs, output_file, temp_dir)

            if progress_callback:
                progress_callback(total_clips, total_clips, "完了！")

        return True

    def _cut_lossless(self, clip: ClipSegment, output_path: str):
        """完全無劣化モード：ストリームコピーによるキーフレーム単位カット"""
        duration = clip.end_time - clip.start_time
        args = [
            "-ss", f"{clip.start_time:.3f}",
            "-i", clip.source_path,
            "-t", f"{duration:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ]
        self.runner.run_ffmpeg(args)

    def _cut_smart(self, clip: ClipSegment, output_path: str, temp_dir: str, clip_idx: int):
        """
        スマートカットモード：
        端数の境界GOPのみ再エンコードし、中間大部分はストリームコピーして結合。
        """
        keyframes = self.analyzer.get_keyframes(clip.source_path)
        meta = self.analyzer.get_metadata(clip.source_path)

        start = max(0.0, clip.start_time)
        end = min(meta.duration, clip.end_time)

        if end <= start:
            raise ValueError(f"無効なカット範囲: start={start}, end={end}")

        # 区間内のキーフレームを探索
        # start以下の最大のキーフレーム
        kf_before_start = max([k for k in keyframes if k <= start], default=0.0)
        # startより後の最小のキーフレーム
        kf_after_start = min([k for k in keyframes if k > start], default=end)
        # end以下の最大のキーフレーム
        kf_before_end = max([k for k in keyframes if k <= end], default=start)

        eps = 0.04  # 約1フレーム分の許容誤差

        # ケース1: キーフレーム間に収まる微小区間 (GOPまたぎなし)
        if kf_after_start >= end or abs(kf_before_start - kf_before_end) < eps:
            self._encode_subsegment(clip.source_path, start, end, output_path, meta)
            return

        # ケース2: 複数GOPにまたがる
        parts = []
        sub_idx = 0

        # 1. 先頭パート（start 〜 次のキーフレーム）
        if abs(start - kf_before_start) > eps:
            # startがキーフレームと一致していない場合、再エンコード
            head_out = os.path.join(temp_dir, f"sub_{clip_idx}_{sub_idx}.mp4")
            self._encode_subsegment(clip.source_path, start, kf_after_start, head_out, meta)
            parts.append(head_out)
            sub_idx += 1
            mid_start = kf_after_start
        else:
            mid_start = start

        # 2. 中間パート（mid_start 〜 kf_before_end）：無劣化ストリームコピー！
        if kf_before_end - mid_start > eps:
            mid_out = os.path.join(temp_dir, f"sub_{clip_idx}_{sub_idx}.mp4")
            mid_dur = kf_before_end - mid_start
            args = [
                "-ss", f"{mid_start:.3f}",
                "-i", clip.source_path,
                "-t", f"{mid_dur:.3f}",
                "-c", "copy",
                "-avoid_negative_ts", "make_zero",
                mid_out
            ]
            self.runner.run_ffmpeg(args)
            parts.append(mid_out)
            sub_idx += 1

        # 3. 末尾パート（kf_before_end 〜 end）
        if abs(end - kf_before_end) > eps:
            tail_out = os.path.join(temp_dir, f"sub_{clip_idx}_{sub_idx}.mp4")
            self._encode_subsegment(clip.source_path, kf_before_end, end, tail_out, meta)
            parts.append(tail_out)
            sub_idx += 1

        # サブパーツを concat
        if len(parts) == 1:
            os.replace(parts[0], output_path)
        else:
            self._concat_videos(parts, output_path, temp_dir)

    def _encode_subsegment(
        self,
        source_path: str,
        start: float,
        end: float,
        output_path: str,
        meta: VideoMetadata
    ):
        """境界部分の極小セグメントを元動画と同じコーデック・設定で高速再エンコード"""
        duration = end - start
        codec = "libx264" if "264" in meta.video_codec.lower() else "libx265" if "265" in meta.video_codec.lower() or "hevc" in meta.video_codec.lower() else meta.video_codec

        args = [
            "-ss", f"{start:.3f}",
            "-i", source_path,
            "-t", f"{duration:.3f}",
            "-c:v", codec,
            "-preset", "ultrafast",  # 数コマ〜数十コマの極小なので最速プリセット
            "-crf", "18",            # 視覚的ロスレス
            "-pix_fmt", meta.video_pix_fmt if meta.video_pix_fmt != "unknown" else "yuv420p"
        ]

        if meta.audio_codec:
            args.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            args.append("-an")

        args.extend(["-avoid_negative_ts", "make_zero", output_path])
        self.runner.run_ffmpeg(args)

    def _concat_videos(self, video_paths: List[str], output_path: str, temp_dir: str):
        """FFmpeg concat demuxer を使って無劣化結合"""
        if len(video_paths) == 1:
            # 1本だけの場合はそのままコピー
            args = ["-i", video_paths[0], "-c", "copy", output_path]
            self.runner.run_ffmpeg(args)
            return

        list_file = os.path.join(temp_dir, f"concat_list_{os.getpid()}.txt")
        with open(list_file, "w", encoding="utf-8") as f:
            for p in video_paths:
                # FFmpeg concat file format requires forward slashes and escaped quotes
                escaped = p.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        args = [
            "-f", "concat",
            "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path
        ]
        self.runner.run_ffmpeg(args)
