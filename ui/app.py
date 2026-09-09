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
        for arg in sys.argv[1:]:
            clean = arg.strip('\'"')
            if clean and os.path.exists(clean):
                if clean.lower().endswith(".json"):
                    QTimer.singleShot(100, lambda p=clean: window.load_recipe(p))
                    break
                else:
                    QTimer.singleShot(100, lambda p=clean: window.add_videos([p]))
                    break

    sys.exit(app.exec())


if __name__ == "__main__":
    launch()
