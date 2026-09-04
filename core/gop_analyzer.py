"""
core/gop_analyzer.py
動画のメタデータ取得、キーフレーム（Iフレーム）タイムスタンプ解析、
および無劣化結合のためのフォーマット互換性チェック
"""

import os
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, asdict
from .ffmpeg_tools import FFmpegRunner


@dataclass
class VideoMetadata:
    file_path: str
    file_name: str
    duration: float              # 秒
    width: int
    height: int
    video_codec: str             # 例: h264, hevc, vp9
    video_pix_fmt: str           # 例: yuv420p
    fps: float                   # 例: 30.0, 59.94
    audio_codec: Optional[str]   # 例: aac, mp3, None
    audio_sample_rate: Optional[int] # 例: 48000, 44100
    audio_channels: Optional[int]    # 例: 2

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VideoAnalyzer:
    def __init__(self, runner: Optional[FFmpegRunner] = None):
        self.runner = runner or FFmpegRunner()

    def get_metadata(self, video_path: str) -> VideoMetadata:
        """ffprobe を用いて動画の詳細情報を取得する"""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

        data = self.runner.run_ffprobe_json([
            "-show_format",
            "-show_streams",
            video_path
        ])

        streams = data.get("streams", [])
        fmt = data.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            raise ValueError(f"映像ストリームが見つかりません: {video_path}")

        # Duration
        duration_str = video_stream.get("duration") or fmt.get("duration", "0")
        try:
            duration = float(duration_str)
        except ValueError:
            duration = 0.0

        # FPS 計算
        fps_str = video_stream.get("r_frame_rate", "30/1")
        try:
            num, den = map(float, fps_str.split("/"))
            fps = num / den if den != 0 else 30.0
        except Exception:
            fps = 30.0

        # Audio
        audio_codec = audio_stream.get("codec_name") if audio_stream else None
        audio_sr = int(audio_stream.get("sample_rate")) if audio_stream and audio_stream.get("sample_rate") else None
        audio_ch = int(audio_stream.get("channels")) if audio_stream and audio_stream.get("channels") else None

        return VideoMetadata(
            file_path=os.path.abspath(video_path),
            file_name=os.path.basename(video_path),
            duration=duration,
            width=int(video_stream.get("width", 0)),
            height=int(video_stream.get("height", 0)),
            video_codec=video_stream.get("codec_name", "unknown"),
            video_pix_fmt=video_stream.get("pix_fmt", "unknown"),
            fps=round(fps, 3),
            audio_codec=audio_codec,
            audio_sample_rate=audio_sr,
            audio_channels=audio_ch
        )

    def get_keyframes(self, video_path: str) -> List[float]:
        """
        動画内のすべてのキーフレーム（Iフレーム）のタイムスタンプ（秒）を取得する。
        """
        # packets で key=K_ の pts_time を高速取得
        args = [
            "-select_streams", "v:0",
            "-show_packets",
            "-show_entries", "packet=pts_time,flags",
            video_path
        ]
        data = self.runner.run_ffprobe_json(args)
        packets = data.get("packets", [])

        keyframes = []
        for p in packets:
            flags = p.get("flags", "")
            pts_time = p.get("pts_time")
            if "K" in flags and pts_time is not None:
                try:
                    keyframes.append(float(pts_time))
                except ValueError:
                    pass

        # 重複削除＆ソート
        keyframes = sorted(list(set(keyframes)))
        # 0.0 がなければ先頭に追加
        if not keyframes or keyframes[0] > 0.05:
            keyframes.insert(0, 0.0)

        return keyframes

    @staticmethod
    def check_compatibility(base: VideoMetadata, target: VideoMetadata) -> Tuple[bool, str]:
        """
        2本の動画が無劣化結合可能か判定する。
        Returns:
            (is_compatible, reason_message)
        """
        # 1. 映像コーデック
        if base.video_codec.lower() != target.video_codec.lower():
            return False, f"映像コーデックが一致しません (基準: {base.video_codec} / 対象: {target.video_codec})"

        # 2. 解像度
        if base.width != target.width or base.height != target.height:
            return False, f"解像度が一致しません (基準: {base.width}x{base.height} / 対象: {target.width}x{target.height})"

        # 3. 音声の有無とコーデック
        if (base.audio_codec is not None) != (target.audio_codec is not None):
            return False, "音声ストリームの有無が一致しません"

        if base.audio_codec and target.audio_codec:
            if base.audio_codec.lower() != target.audio_codec.lower():
                return False, f"音声コーデックが一致しません (基準: {base.audio_codec} / 対象: {target.audio_codec})"
            if base.audio_sample_rate != target.audio_sample_rate:
                return False, f"音声サンプリングレートが一致しません (基準: {base.audio_sample_rate}Hz / 対象: {target.audio_sample_rate}Hz)"

        return True, "互換性あり（無劣化結合可能）"
