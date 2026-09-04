"""
ui/bridge.py
PySide6 QWebChannel を介して JavaScript と通信するブリッジモジュール
"""

import os
import sys
import json
import base64
import tempfile
import threading
from typing import Optional

from PySide6.QtCore import QObject, Slot, Signal
from PySide6.QtWidgets import QFileDialog

# プロジェクトルートをインポートパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.ffmpeg_tools import FFmpegRunner
from core.gop_analyzer import VideoAnalyzer, VideoMetadata
from core.smart_cutter import SmartCutter
from core.engine import execute_recipe


class Bridge(QObject):
    # JS側に通知するシグナル
    progressUpdated = Signal(int, int, str)  # current, total, message
    exportFinished = Signal(bool, str)        # success, message
    filesDroppedSignal = Signal(str)          # JSON array of paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.runner = FFmpegRunner()
        self.analyzer = VideoAnalyzer(self.runner)
        self.cutter = SmartCutter(self.runner)
        self.parent_widget = parent

    @Slot(str, result=str)
    def getVideoInfo(self, file_path: str) -> str:
        """動画のメタデータとキーフレーム一覧を取得してJSONで返す"""
        try:
            meta = self.analyzer.get_metadata(file_path)
            keyframes = self.analyzer.get_keyframes(file_path)
            res = {
                "success": True,
                "metadata": meta.to_dict(),
                "keyframes": keyframes
            }
        except Exception as e:
            res = {
                "success": False,
                "error": str(e)
            }
        return json.dumps(res, ensure_ascii=False)

    @Slot(str, str, result=str)
    def checkCompatibility(self, target_path: str, base_meta_json: str) -> str:
        """2本の動画が無劣化結合可能か判定する"""
        try:
            base_dict = json.loads(base_meta_json)
            base_meta = VideoMetadata(**base_dict)
            target_meta = self.analyzer.get_metadata(target_path)

            is_compat, reason = VideoAnalyzer.check_compatibility(base_meta, target_meta)
            res = {
                "success": True,
                "compatible": is_compat,
                "reason": reason,
                "target_meta": target_meta.to_dict()
            }
        except Exception as e:
            res = {
                "success": False,
                "error": str(e)
            }
        return json.dumps(res, ensure_ascii=False)

    @Slot(result=str)
    def openFileDialog(self) -> str:
        """Windows標準の複数ファイル選択ダイアログを開く"""
        files, _ = QFileDialog.getOpenFileNames(
            self.parent_widget,
            "動画ファイルを選択",
            "",
            "動画ファイル (*.mp4 *.mkv *.mov *.avi *.webm *.ts *.flv);;すべてのファイル (*.*)"
        )
        return json.dumps(files, ensure_ascii=False)

    @Slot(str, result=str)
    def saveFileDialog(self, default_name: str) -> str:
        """保存先選択ダイアログ"""
        file_path, _ = QFileDialog.getSaveFileName(
            self.parent_widget,
            "書き出し先を選択",
            default_name,
            "MP4 動画 (*.mp4);;MKV 動画 (*.mkv);;すべてのファイル (*.*)"
        )
        return file_path or ""

    @Slot(str, float, result=str)
    def generateThumbnail(self, file_path: str, time_sec: float) -> str:
        """指定秒のサムネイルを生成し Base64 Data URL で返す"""
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp_path = tmp.name

            # 高速に1フレームだけ切り出し
            args = [
                "-ss", f"{time_sec:.3f}",
                "-i", file_path,
                "-vframes", "1",
                "-q:v", "2",
                "-s", "160x90",  # サムネイル用軽量サイズ
                "-y",
                tmp_path
            ]
            self.runner.run_ffmpeg(args)

            with open(tmp_path, "rb") as f:
                data = f.read()
            os.remove(tmp_path)

            b64 = base64.b64encode(data).decode("utf-8")
            return f"data:image/jpeg;base64,{b64}"
        except Exception as e:
            return ""

    @Slot(str, str, result=bool)
    def saveRecipe(self, recipe_json: str, file_path: str) -> bool:
        """レシピをファイルに保存"""
        try:
            data = json.loads(recipe_json)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    @Slot(str)
    def runExport(self, recipe_json: str):
        """別スレッドでエクスポートを実行"""
        try:
            recipe_data = json.loads(recipe_json)
        except Exception as e:
            self.exportFinished.emit(False, f"レシピJSONが無効です: {e}")
            return

        def task():
            def on_progress(cur, total, msg):
                self.progressUpdated.emit(cur, total, msg)

            try:
                execute_recipe(recipe_data, progress_callback=on_progress)
                self.exportFinished.emit(True, "エクスポートが正常に完了しました！")
            except Exception as e:
                self.exportFinished.emit(False, f"エラーが発生しました: {e}")

        t = threading.Thread(target=task, daemon=True)
        t.start()
