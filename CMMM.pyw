"""
CMMM.pyw
黒いコンソール画面を出さずに、ダブルクリックで直接起動できる Windows 用ランチャー
"""

import sys
import os

# プロジェクトルートディレクトリをインポートパスに追加
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from ui.app import launch

if __name__ == "__main__":
    launch()
