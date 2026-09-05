"""
ui/app.py
PySide6 完全ネイティブエディタ起動エントリポイント
"""

import os
import sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from .main_window import MainWindow


def launch():
    # 高DPIサポート設定
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()

    # コマンドライン引数からレシピまたは動画の自動ロード (ウィンドウ表示後に実行)
    if len(sys.argv) > 1:
        target_path = sys.argv[1]
        if os.path.exists(target_path):
            if target_path.lower().endswith(".json"):
                QTimer.singleShot(100, lambda: window.load_recipe(target_path))
            else:
                QTimer.singleShot(100, lambda: window.add_videos([target_path]))

    sys.exit(app.exec())


if __name__ == "__main__":
    launch()
