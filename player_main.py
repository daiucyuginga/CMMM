"""
player_main.py
CMMM Player 単体起動用エントリーポイント
"""

import os
import sys
import ctypes

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt

# プロジェクトルートを sys.path に追加
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from ui.player_window import PlayerWindow


def main():
    # Windows タスクバーで独自のアプリIDを設定し、アイコンを確実に表示
    try:
        myappid = "cmmm.video.player.app"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    # 高DPI対応
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("CMMM Player")

    # アイコン設定
    icon_path = os.path.join(BASE_DIR, "resources", "player_icon.ico")
    png_icon_path = os.path.join(BASE_DIR, "resources", "player_icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    elif os.path.exists(png_icon_path):
        app.setWindowIcon(QIcon(png_icon_path))

    # コマンドライン引数（関連付け起動やD&D起動対応）
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
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    elif os.path.exists(png_icon_path):
        window.setWindowIcon(QIcon(png_icon_path))

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
