"""
core/smart_cutter.py
完全無劣化カット（キーフレーム）およびスマートカット（境界GOP再エンコード）と、
FFmpeg concat demuxer による無劣化結合処理
"""

import os
import shutil
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
        二重コピー・二重結合を徹底排除した最速パイプラインで実行。
        """
        if not clips:
            raise ValueError("処理対象のクリップがありません。")

        output_file = os.path.abspath(output_file)
        out_dir = os.path.dirname(output_file)
        os.makedirs(out_dir, exist_ok=True)

        total_clips = len(clips)

        # 最速パス: 単一クリップかつ完全無劣化モードの場合
        # 中間一時ファイルは一切生成せず、直接 output_file にストリームコピー
        if total_clips == 1 and mode == "lossless":
            if progress_callback:
                progress_callback(0, 1, "動画を書き出し中...")
            self._cut_lossless(clips[0], output_file)
            if progress_callback:
                progress_callback(1, 1, "完了！")
            return True

        # 出力先ディレクトリ内に一時作業用フォルダを作成（ドライブ間跨ぎのGB単位転送を防止）
        # 同一ドライブ・同一ファイルシステム内で完結させ、最速のI/Oを実現
        temp_dir_kwargs = {"prefix": ".cmmm_temp_"}
        try:
            test_tmp = tempfile.TemporaryDirectory(prefix=".cmmm_temp_test_", dir=out_dir)
            test_tmp.cleanup()
            temp_dir_kwargs["dir"] = out_dir
        except Exception as e:
            logger.warning(f"出力先ディレクトリに一時フォルダを作成できないため、システムTempを使用します: {e}")

        with tempfile.TemporaryDirectory(**temp_dir_kwargs) as temp_dir:
            # 単一クリップ（smartモード）の場合の最速パス
            if total_clips == 1:
                if progress_callback:
                    progress_callback(0, 1, "動画を書き出し中...")
                self._cut_smart_single(clips[0], output_file, temp_dir)
                if progress_callback:
                    progress_callback(1, 1, "完了！")
                return True

            # 複数クリップの場合：
            # クリップごとの中間全編動画（clip_0000.mp4 等）への結合を廃止し、
            # 全パーツを直接 output_file へ1回のみの concat demuxer で一括結合（二重コピー・二重結合の完全排除）
            all_parts = []
            total_steps = total_clips + 1

            for idx, clip in enumerate(clips):
                msg = f"クリップ {idx + 1}/{total_clips} 処理中..."
                if progress_callback:
                    progress_callback(idx, total_steps, msg)

                parts = self._prepare_clip_parts(clip, mode, temp_dir, idx)
                all_parts.extend(parts)

            if progress_callback:
                progress_callback(total_clips, total_steps, "最終動画を出力中...")

            self._concat_videos(all_parts, output_file, temp_dir)

            if progress_callback:
                progress_callback(total_steps, total_steps, "完了！")

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

    def _cut_smart_single(self, clip: ClipSegment, output_path: str, temp_dir: str):
        """
        単一クリップ用のスマートカット：
        中間全編動画を作らず、直接 output_path に出力・結合する。
        """
        parts = self._prepare_clip_parts(clip, mode="smart", temp_dir=temp_dir, clip_idx=0)
        if len(parts) == 1:
            # 1パーツ（微小GOP再エンコードのみ、または中間コピーのみ）の場合はそのまま出力先に配置
            if os.path.abspath(parts[0]) != os.path.abspath(output_path):
                if os.path.exists(output_path):
                    os.remove(output_path)
                shutil.move(parts[0], output_path)
        else:
            # 複数パーツ（先頭再エンコード、中間コピー、末尾再エンコード）を直接最終出力先に結合
            self._concat_videos(parts, output_path, temp_dir)

    def _prepare_clip_parts(
        self,
        clip: ClipSegment,
        mode: str,
        temp_dir: str,
        clip_idx: int
    ) -> List[str]:
        """
        クリップを構成するパーツ（一時動画）を生成し、パス一覧を返す。
        """
        if mode == "lossless":
            part_out = os.path.join(temp_dir, f"clip_{clip_idx:04d}_lossless.mp4")
            self._cut_lossless(clip, part_out)
            return [part_out]

        # mode == "smart"
        keyframes = self.analyzer.get_keyframes(clip.source_path)
        meta = self.analyzer.get_metadata(clip.source_path)

        start = max(0.0, clip.start_time)
        end = min(meta.duration, clip.end_time)

        if end <= start:
            raise ValueError(f"無効なカット範囲: start={start}, end={end}")

        # 区間内のキーフレームを探索
        kf_before_start = max([k for k in keyframes if k <= start], default=0.0)
        kf_after_start = min([k for k in keyframes if k > start], default=end)
        kf_before_end = max([k for k in keyframes if k <= end], default=start)

        eps = 0.04  # 約1フレーム分の許容誤差

        # ケース1: キーフレーム間に収まる微小区間 (GOPまたぎなし)
        if kf_after_start >= end or abs(kf_before_start - kf_before_end) < eps:
            sub_out = os.path.join(temp_dir, f"sub_{clip_idx:04d}_0.mp4")
            self._encode_subsegment(clip.source_path, start, end, sub_out, meta)
            return [sub_out]

        # ケース2: 複数GOPにまたがる
        parts = []
        sub_idx = 0

        # 1. 先頭パート（start 〜 次のキーフレーム）
        if abs(start - kf_before_start) > eps:
            head_out = os.path.join(temp_dir, f"sub_{clip_idx:04d}_{sub_idx}.mp4")
            self._encode_subsegment(clip.source_path, start, kf_after_start, head_out, meta)
            parts.append(head_out)
            sub_idx += 1
            mid_start = kf_after_start
        else:
            mid_start = start

        # 2. 中間パート（mid_start 〜 kf_before_end）：無劣化ストリームコピー！
        if kf_before_end - mid_start > eps:
            mid_out = os.path.join(temp_dir, f"sub_{clip_idx:04d}_{sub_idx}.mp4")
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
            tail_out = os.path.join(temp_dir, f"sub_{clip_idx:04d}_{sub_idx}.mp4")
            self._encode_subsegment(clip.source_path, kf_before_end, end, tail_out, meta)
            parts.append(tail_out)
            sub_idx += 1

        return parts

    def _cut_smart(self, clip: ClipSegment, output_path: str, temp_dir: str, clip_idx: int):
        """互換性のための内部ヘルパー（単一クリップのスマートカット）"""
        parts = self._prepare_clip_parts(clip, mode="smart", temp_dir=temp_dir, clip_idx=clip_idx)
        if len(parts) == 1:
            if os.path.abspath(parts[0]) != os.path.abspath(output_path):
                if os.path.exists(output_path):
                    os.remove(output_path)
                shutil.move(parts[0], output_path)
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
        if not video_paths:
            return

        if len(video_paths) == 1:
            src = video_paths[0]
            if os.path.abspath(src) != os.path.abspath(output_path):
                if os.path.exists(output_path):
                    os.remove(output_path)
                shutil.move(src, output_path)
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
