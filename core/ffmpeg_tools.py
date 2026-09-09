"""
core/ffmpeg_tools.py
FFmpeg / FFprobe のパス自動検出およびコマンド実行ヘルパー
"""

import os
import shutil
import subprocess
import json
import logging
import threading
from typing import Optional, Tuple, Dict, Any, List

logger = logging.getLogger(__name__)


def find_ffmpeg_binaries() -> Tuple[Optional[str], Optional[str]]:
    """
    システム内から ffmpeg と ffprobe の実行可能バイナリパスを探索して返す。
    Returns:
        (ffmpeg_path, ffprobe_path)
    """
    ffmpeg_path = shutil.which("ffmpeg")
    ffprobe_path = shutil.which("ffprobe")

    if ffmpeg_path and ffprobe_path:
        return ffmpeg_path, ffprobe_path

    # PATH にない場合、一般的な Windows インストール先を探索
    candidate_dirs = [
        # WinGet パッケージディレクトリ / Links
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"),
        # プロジェクトローカル bin
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools", "ffmpeg", "bin")),
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")),
        # 一般的なProgram Files
        r"C:\Program Files\ffmpeg\bin",
        r"C:\ffmpeg\bin",
    ]

    for c_dir in candidate_dirs:
        if not os.path.exists(c_dir):
            continue
        for root, _, files in os.walk(c_dir):
            for f in files:
                if f.lower() == "ffmpeg.exe" and not ffmpeg_path:
                    ffmpeg_path = os.path.join(root, f)
                if f.lower() == "ffprobe.exe" and not ffprobe_path:
                    ffprobe_path = os.path.join(root, f)
            if ffmpeg_path and ffprobe_path:
                break
        if ffmpeg_path and ffprobe_path:
            break

    return ffmpeg_path, ffprobe_path


class FFmpegRunner:
    def __init__(self, ffmpeg_path: Optional[str] = None, ffprobe_path: Optional[str] = None):
        auto_ffmpeg, auto_ffprobe = find_ffmpeg_binaries()
        self.ffmpeg_path = ffmpeg_path or auto_ffmpeg
        self.ffprobe_path = ffprobe_path or auto_ffprobe
        self._active_process: Optional[subprocess.Popen] = None
        self._process_lock = threading.Lock()
        self.last_command: List[str] = []
        self.last_stdout: str = ""
        self.last_stderr: str = ""

    def _set_active_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._active_process = process

    def _clear_active_process(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            if self._active_process is process:
                self._active_process = None

    def cancel(self) -> None:
        """実行中の FFmpeg/FFprobe 子プロセスを停止する。"""
        with self._process_lock:
            process = self._active_process

        if process is None or process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        except OSError:
            pass

    def is_available(self) -> bool:
        return bool(self.ffmpeg_path and self.ffprobe_path and 
                    os.path.exists(self.ffmpeg_path) and os.path.exists(self.ffprobe_path))

    def run_ffprobe_json(self, args: List[str]) -> Dict[str, Any]:
        """ffprobe を実行し、結果の JSON をパースして返す"""
        if not self.ffprobe_path:
            raise FileNotFoundError("ffprobe が見つかりません。FFmpeg をインストールしてください。")

        cmd = [
            self.ffprobe_path,
            "-v", "quiet",
            "-print_format", "json",
            *args
        ]
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs
        )
        self._set_active_process(process)
        try:
            stdout_bytes, stderr_bytes = process.communicate()
        finally:
            self._clear_active_process(process)

        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise RuntimeError(f"ffprobe エラー (code {process.returncode}): {stderr_str}")
        try:
            return json.loads(stdout_str)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"ffprobe の出力 JSON パースに失敗しました: {e}\n出力: {stdout_str}")

    def run_ffmpeg(self, args: List[str], progress_callback=None) -> bool:
        """
        ffmpeg を実行する。
        進捗状況を監視する場合は progress_callback を呼ぶ。
        """
        if not self.ffmpeg_path:
            raise FileNotFoundError("ffmpeg が見つかりません。FFmpeg をインストールしてください。")

        cmd = [self.ffmpeg_path, "-nostdin", "-hide_banner", "-y", *args]
        self.last_command = cmd
        self.last_stdout = ""
        self.last_stderr = ""
        logger.info(f"Running ffmpeg: {' '.join(cmd)}")

        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **kwargs
        )
        self._set_active_process(process)
        try:
            stdout_bytes, stderr_bytes = process.communicate()
        finally:
            self._clear_active_process(process)
        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")
        self.last_stdout = stdout_str
        self.last_stderr = stderr_str

        if process.returncode != 0:
            logger.error(f"FFmpeg failed with code {process.returncode}: {stderr_str}")
            raise RuntimeError(f"FFmpeg 処理エラー (code {process.returncode}): {stderr_str}")

        return True
