"""
run_editor.py
CMMM 編集GUIツールの起動スクリプト
"""

import sys
import os

# カレントディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ui.app import launch

if __name__ == "__main__":
    launch()
