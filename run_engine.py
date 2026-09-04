"""
run_engine.py
CMMM 実行CLIエンジンの起動スクリプト
"""

import sys
import os

# カレントディレクトリをパスに追加
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.engine import main

if __name__ == "__main__":
    main()
