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
    def __init__(self, runner: Optional[FFmpegRunner] = None, analyzer: Optional[VideoAnalyzer] = None):
        self.runner = runner or FFmpegRunner()
        self.analyzer = analyzer or VideoAnalyzer(self.runner)

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
        try:
            temp_ctx = tempfile.TemporaryDirectory(prefix=".cmmm_temp_", dir=out_dir)
        except Exception as e:
            logger.warning(f"出力先ディレクトリに一時フォルダを作成できないため、システムTempを使用します: {e}")
            temp_ctx = tempfile.TemporaryDirectory(prefix=".cmmm_temp_")

        with temp_ctx as temp_dir:
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
        """完全無劣化モード：高速入力シーク（-ss before -i）＋ストリームコピーによるキーフレーム単位カット"""
        duration = clip.end_time - clip.start_time
        args = []
        if clip.start_time > 0.001:
            args.extend(["-ss", f"{clip.start_time:.3f}"])
        args.extend([
            "-i", clip.source_path,
            "-t", f"{duration:.3f}",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ])
        self.runner.run_ffmpeg(args)

    def _cut_smart_single(self, clip: ClipSegment, output_path: str, temp_dir: str):
        """
        単一クリップ用のスマートカット：
        映像（境界再エンコード＋中間入力シークコピー）と音声（全区間一括抽出）を分離して mux。
        中間全編動画を作らず、直接 output_path に出力する。
        """
        self._process_single_smart_clip(clip, output_path, temp_dir, clip_idx=0)

    def _prepare_clip_parts(
        self,
        clip: ClipSegment,
        mode: str,
        temp_dir: str,
        clip_idx: int
    ) -> List[str]:
        """
        クリップを構成する完成動画パーツを生成し、パス一覧を返す。
        元動画全編（無編集）の場合は中間ファイル生成をスキップして元動画を直接結合対象にする。
        """
        try:
            meta = self.analyzer.get_metadata(clip.source_path)
            if clip.start_time <= 0.05 and meta.duration > 0 and clip.end_time >= meta.duration - 0.05:
                return [os.path.abspath(clip.source_path)]
        except Exception:
            pass

        if mode == "lossless":
            part_out = os.path.join(temp_dir, f"clip_{clip_idx:04d}_lossless.mp4")
            self._cut_lossless(clip, part_out)
            return [part_out]

        # mode == "smart"
        part_out = os.path.join(temp_dir, f"clip_{clip_idx:04d}_smart.mp4")
        self._process_single_smart_clip(clip, part_out, temp_dir, clip_idx=clip_idx)
        return [part_out]

    def _process_single_smart_clip(
        self,
        clip: ClipSegment,
        output_path: str,
        temp_dir: str,
        clip_idx: int
    ):
        """
        単一クリップのスマートカット処理：
        1. In点・Out点周辺の局所キーフレーム解析（-read_intervals）で全編走査の待ち時間をゼロ化。
        2. 映像：境界極小パートは元動画プロファイルに合わせた再エンコード、中間は入力シークで完全コピー。
        3. 音声：全区間から元動画の音声を一括無劣化コピー（再エンコード・concatなしでプチノイズ・ズレ完全ゼロ）。
        4. 映像と音声を 1 回で mux して output_path に出力。
        """
        meta = self.analyzer.get_metadata(clip.source_path)

        start = max(0.0, clip.start_time)
        end = min(meta.duration, clip.end_time)
        duration = end - start

        if duration <= 0:
            raise ValueError(f"無効なカット範囲: start={start}, end={end}")

        keyframes = self.analyzer.get_keyframes_around(clip.source_path, [start, end])
        has_audio = bool(meta.audio_codec)

        # 区間内のキーフレームを探索
        kf_before_start = max([k for k in keyframes if k <= start], default=0.0)
        kf_after_start = min([k for k in keyframes if k > start], default=end)
        kf_before_end = max([k for k in keyframes if k <= end], default=start)

        eps = 0.04  # 約1フレーム分の許容誤差

        # 最速ショートカット: 先頭・末尾ともにキーフレーム上（または動画終端）で再エンコード不要な場合は1パス無劣化カットで即完了
        need_head_reencode = abs(start - kf_before_start) > eps
        need_tail_reencode = abs(end - kf_before_end) > eps and abs(end - meta.duration) > eps
        if not need_head_reencode and not need_tail_reencode:
            self._cut_lossless(ClipSegment(clip.source_path, start, end), output_path)
            return

        v_parts = []
        sub_idx = 0

        # ケース1: キーフレーム間に収まる微小区間 (GOPまたぎなし)
        if kf_after_start >= end or abs(kf_before_start - kf_before_end) < eps:
            v_sub = os.path.join(temp_dir, f"vsub_{clip_idx:04d}_0.mp4")
            self._encode_video_subsegment(clip.source_path, start, end, v_sub, meta)
            v_parts.append(v_sub)
        else:
            # ケース2: 複数GOPにまたがる
            # 1. 先頭パート（start 〜 次のキーフレーム）: 映像のみ再エンコード
            if need_head_reencode:
                head_out = os.path.join(temp_dir, f"vsub_{clip_idx:04d}_{sub_idx}.mp4")
                self._encode_video_subsegment(clip.source_path, start, kf_after_start, head_out, meta)
                v_parts.append(head_out)
                sub_idx += 1
                mid_start = kf_after_start
            else:
                mid_start = start

            # 2. 中間パート（mid_start 〜 kf_before_end）：無劣化ストリームコピー（映像のみ、入力シーク！）
            if kf_before_end - mid_start > eps:
                mid_out = os.path.join(temp_dir, f"vsub_{clip_idx:04d}_{sub_idx}.mp4")
                mid_dur = kf_before_end - mid_start
                args = [
                    "-ss", f"{mid_start:.3f}",
                    "-i", clip.source_path,
                    "-t", f"{mid_dur:.3f}",
                    "-c:v", "copy",
                    "-an",
                    "-avoid_negative_ts", "make_zero",
                    mid_out
                ]
                self.runner.run_ffmpeg(args)
                v_parts.append(mid_out)
                sub_idx += 1

            # 3. 末尾パート（kf_before_end 〜 end）: 映像のみ再エンコード
            if need_tail_reencode:
                tail_out = os.path.join(temp_dir, f"vsub_{clip_idx:04d}_{sub_idx}.mp4")
                self._encode_video_subsegment(clip.source_path, kf_before_end, end, tail_out, meta)
                v_parts.append(tail_out)
                sub_idx += 1

        # 映像パーツの統合
        if len(v_parts) == 1:
            merged_video = v_parts[0]
        else:
            merged_video = os.path.join(temp_dir, f"vmerged_{clip_idx:04d}.mp4")
            self._concat_videos(v_parts, merged_video, temp_dir)

        # 音声がない場合は映像のみを出力先へ配置
        if not has_audio:
            if os.path.abspath(merged_video) != os.path.abspath(output_path):
                if os.path.exists(output_path):
                    os.remove(output_path)
                shutil.move(merged_video, output_path)
            return

        # 音声がある場合：クリップ全区間から音声を1本の連続ストリームとして抽出（無劣化コピー）
        clip_audio = os.path.join(temp_dir, f"a_{clip_idx:04d}.mp4")
        audio_args = []
        if start > 0.001:
            audio_args.extend(["-ss", f"{start:.3f}"])
        audio_args.extend([
            "-i", clip.source_path,
            "-t", f"{duration:.3f}",
            "-vn",
            "-c:a", "copy",
            "-avoid_negative_ts", "make_zero",
            clip_audio
        ])
        self.runner.run_ffmpeg(audio_args)

        # 映像ストリームと音声ストリームを 1 回で mux して出力 (faststartによる2重書き換えを排除)
        if os.path.exists(output_path):
            os.remove(output_path)
        mux_args = [
            "-i", merged_video,
            "-i", clip_audio,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-avoid_negative_ts", "make_zero",
            output_path
        ]
        self.runner.run_ffmpeg(mux_args)

    def _encode_video_subsegment(
        self,
        source_path: str,
        start: float,
        end: float,
        output_path: str,
        meta: VideoMetadata
    ):
        """境界部分の極小セグメントを元動画のプロファイル・設定に合わせて高速再エンコード（映像のみ）"""
        duration = end - start
        codec = "libx264" if "264" in meta.video_codec.lower() else "libx265" if "265" in meta.video_codec.lower() or "hevc" in meta.video_codec.lower() else meta.video_codec

        args = [
            "-ss", f"{start:.3f}",
            "-i", source_path,
            "-t", f"{duration:.3f}",
            "-c:v", codec,
            "-preset", "veryfast",  # ultrafastはBフレームを破棄してBaselineに退行するためveryfastを使用
            "-crf", "18",            # 視覚的ロスレス
            "-pix_fmt", meta.video_pix_fmt if meta.video_pix_fmt != "unknown" else "yuv420p",
            "-an",
            "-avoid_negative_ts", "make_zero"
        ]

        # H.264の場合、元動画のプロファイルを明示してBフレームやSPS/PPSの整合性を保つ
        if codec == "libx264":
            prof = meta.video_profile.lower()
            if "high" in prof:
                args.extend(["-profile:v", "high"])
            elif "main" in prof:
                args.extend(["-profile:v", "main"])
            elif "baseline" in prof:
                args.extend(["-profile:v", "baseline"])

            # 元動画にBフレームがない場合はBフレームを0に
            if meta.has_b_frames == 0:
                args.extend(["-x264-params", "bframes=0"])

        # タイムスケール（タイムベース）の維持
        if meta.time_base and "/" in meta.time_base:
            try:
                num, den = meta.time_base.split("/")
                if int(num) == 1 and int(den) > 0:
                    args.extend(["-video_track_timescale", den])
            except Exception:
                pass

        args.append(output_path)
        self.runner.run_ffmpeg(args)

    def _concat_videos(self, video_paths: List[str], output_path: str, temp_dir: str):
        """FFmpeg concat demuxer を使って無劣化結合"""
        if not video_paths:
            return

        if len(video_paths) == 1:
            src = os.path.abspath(video_paths[0])
            dst = os.path.abspath(output_path)
            if src != dst:
                if os.path.exists(dst):
                    os.remove(dst)
                # temp_dir 内のファイルなら move、元動画ファイルなら copy2 で保護
                abs_temp = os.path.abspath(temp_dir)
                if os.path.commonpath([src, abs_temp]) == abs_temp:
                    shutil.move(src, dst)
                else:
                    shutil.copy2(src, dst)
            return

        list_file = os.path.join(temp_dir, f"concat_list_{os.getpid()}_{id(video_paths)}.txt")
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
            "-avoid_negative_ts", "make_zero",
            output_path
        ]
        self.runner.run_ffmpeg(args)
