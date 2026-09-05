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
    if len(sys.argv) > 1:
        target = sys.argv[1]
        if target.lower().endswith(".json"):
            initial_recipe = target
        else:
            initial_video = target

    window = PlayerWindow()
    window.show()

    if initial_recipe:
        QTimer.singleShot(100, lambda: window.load_recipe(initial_recipe))
    elif initial_video:
        QTimer.singleShot(100, lambda: window.open_video(initial_video))

    sys.exit(app.exec())


if __name__ == "__main__":
    launch()
