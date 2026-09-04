"""
ui/app.py
PySide6 完全ネイティブエディタ起動エントリポイント
"""

import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from .main_window import MainWindow


def launch():
    # 高DPIサポート設定
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    launch()
