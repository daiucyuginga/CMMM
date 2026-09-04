"""
run_editor.pyw
黒いコンソールを出さずに起動できるランチャー
"""

import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from ui.app import launch

if __name__ == "__main__":
    launch()
