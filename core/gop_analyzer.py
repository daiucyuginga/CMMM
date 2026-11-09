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
    video_profile: str = "high"      # 例: High, Main, Baseline
    video_level: Optional[str] = None # 例: 40, 41
    has_b_frames: int = 0            # Bフレーム数 (0, 2等)
    time_base: Optional[str] = None  # 例: 1/15360

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class VideoAnalyzer:
    # プロセス内で共有するメタデータ・キーフレームキャッシュ (UIとエンジン間の重複ffprobe実行を排除)
    _global_metadata_cache: Dict[Tuple[str, float, int], VideoMetadata] = {}
    _global_keyframe_cache: Dict[str, List[float]] = {}

    def __init__(self, runner: Optional[FFmpegRunner] = None):
        self.runner = runner or FFmpegRunner()
        self._keyframe_cache: Dict[str, List[float]] = self._global_keyframe_cache

    @staticmethod
    def _parse_keyframe_csv(csv_text: str) -> List[float]:
        """ffprobe の csv=p=0 出力 (pts_time,flags) からキーフレームの秒リストを高速抽出"""
        keyframes = []
        for line in csv_text.splitlines():
            if "K" in line:
                pts_str = line.split(",", 1)[0]
                try:
                    keyframes.append(float(pts_str))
                except ValueError:
                    pass
        keyframes = sorted(list(set(keyframes)))
        if not keyframes or keyframes[0] > 0.05:
            keyframes.insert(0, 0.0)
        return keyframes

    def get_metadata(self, video_path: str) -> VideoMetadata:
        """ffprobe を用いて動画の詳細情報を取得する（同一ファイルの重複実行はキャッシュで即座に返却）"""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"動画ファイルが見つかりません: {video_path}")

        abs_path = os.path.abspath(video_path)
        try:
            st = os.stat(abs_path)
            cache_key = (abs_path, st.st_mtime, st.st_size)
        except OSError:
            cache_key = (abs_path, 0.0, 0)

        if cache_key in self._global_metadata_cache:
            return self._global_metadata_cache[cache_key]

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

        # Video Profile / B-frames / Timebase
        video_profile = str(video_stream.get("profile", "high")).lower()
        video_level = str(video_stream.get("level", "")) if video_stream.get("level") is not None else None
        has_b_frames = int(video_stream.get("has_b_frames", 0) or 0)
        time_base = video_stream.get("time_base")

        meta = VideoMetadata(
            file_path=abs_path,
            file_name=os.path.basename(video_path),
            duration=duration,
            width=int(video_stream.get("width", 0)),
            height=int(video_stream.get("height", 0)),
            video_codec=video_stream.get("codec_name", "unknown"),
            video_pix_fmt=video_stream.get("pix_fmt", "unknown"),
            fps=round(fps, 3),
            audio_codec=audio_codec,
            audio_sample_rate=audio_sr,
            audio_channels=audio_ch,
            video_profile=video_profile,
            video_level=video_level,
            has_b_frames=has_b_frames,
            time_base=time_base
        )
        self._global_metadata_cache[cache_key] = meta
        return meta

    def set_cached_keyframes(self, video_path: str, keyframes: List[float]):
        """
        外部から取得済みのキーフレーム一覧をキャッシュに注入する。
        ffprobe の重複実行を完全に回避するために使用。
        """
        if not keyframes:
            return
        abs_path = os.path.abspath(video_path)
        kfs = sorted(list(set(keyframes)))
        if not kfs or kfs[0] > 0.05:
            kfs.insert(0, 0.0)
        self._keyframe_cache[abs_path] = kfs

    def get_keyframes(self, video_path: str) -> List[float]:
        """
        動画内のすべてのキーフレーム（Iフレーム）のタイムスタンプ（秒）を取得する。
        """
        abs_path = os.path.abspath(video_path)
        if abs_path in self._keyframe_cache:
            return list(self._keyframe_cache[abs_path])

        # CSV形式 (csv=p=0) でパケット情報を軽量・高速取得
        args = [
            "-select_streams", "v:0",
            "-show_packets",
            "-show_entries", "packet=pts_time,flags",
            video_path
        ]
        csv_text = self.runner.run_ffprobe_csv(args)
        keyframes = self._parse_keyframe_csv(csv_text)

        self._keyframe_cache[abs_path] = keyframes
        return list(keyframes)

    def get_keyframes_around(
        self,
        video_path: str,
        timestamps: List[float],
        window_sec: float = 15.0
    ) -> List[float]:
        """
        指定したタイムスタンプ（In点・Out点等）の周辺のみのキーフレームを
        ffprobe -read_intervals を使ってピンポイントで超高速（約0.05秒）に取得する。
        キャッシュに全キーフレームがある場合はそれを即座に返す。
        """
        abs_path = os.path.abspath(video_path)
        if abs_path in self._keyframe_cache:
            return list(self._keyframe_cache[abs_path])

        if not timestamps:
            return self.get_keyframes(video_path)

        # 区間 [max(0, t - window_sec), t + window_sec] を作成して重なりをマージ
        raw_intervals = []
        for t in sorted(timestamps):
            st = max(0.0, t - window_sec)
            en = max(st + 1.0, t + window_sec)
            raw_intervals.append([st, en])

        merged = []
        for iv in raw_intervals:
            if not merged or iv[0] > merged[-1][1]:
                merged.append(iv)
            else:
                merged[-1][1] = max(merged[-1][1], iv[1])

        interval_specs = [f"{st:.2f}%+{en - st:.2f}" for st, en in merged]
        read_intervals_arg = ",".join(interval_specs)

        try:
            args = [
                "-select_streams", "v:0",
                "-read_intervals", read_intervals_arg,
                "-show_packets",
                "-show_entries", "packet=pts_time,flags",
                video_path
            ]
            csv_text = self.runner.run_ffprobe_csv(args)
            kfs = self._parse_keyframe_csv(csv_text)

            # 最初のタイムスタンプの後にキーフレームが1つも見つからない等の異常時のみ全体解析へフォールバック
            min_t = min(timestamps)
            if len(kfs) <= 1 and min_t > 1.0:
                return self.get_keyframes(video_path)
            return kfs
        except Exception:
            return self.get_keyframes(video_path)

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
