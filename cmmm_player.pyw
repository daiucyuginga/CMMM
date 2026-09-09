"""
cmmm_player.pyw
CMMM 連携用動画プレイヤー ランチャー
ダブルクリックで直接起動できる Windows 用ランチャー
"""

import sys
import os

# プロジェクトルートディレクトリをインポートパスに追加
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from ui.player_window import PlayerWindow


def launch():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    initial_video = ""
    initial_recipe = ""
    for arg in sys.argv[1:]:
        if not arg or arg.startswith("-"):
            continue
        clean_arg = arg.strip('\'"')
        if not clean_arg:
            continue
        arg_path = os.path.abspath(clean_arg)
        if os.path.exists(arg_path):
            lower = arg_path.lower()
            if lower.endswith((".mp4", ".mov", ".mkv", ".avi", ".webm", ".ts", ".m4v", ".wmv", ".flv", ".m2ts", ".mts", ".mpg", ".mpeg")):
                if not initial_video:
                    initial_video = arg_path
            elif lower.endswith((".cmmm.json", ".json")):
                if not initial_recipe:
                    initial_recipe = arg_path

    window = PlayerWindow(initial_video_path=initial_video, initial_recipe_path=initial_recipe)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    launch()
