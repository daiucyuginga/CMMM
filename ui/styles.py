"""
ui/styles.py
Windows 11 Fluent Design 風 ダークテーマ QSS スタイルシート
"""

MODERN_DARK_STYLE = """
/* 全体基本設定 */
QWidget {
    background-color: #202020;
    color: #f3f3f3;
    font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
    font-size: 13px;
    outline: none;
}

/* ツールバー */
QToolBar {
    background-color: #181818;
    border-bottom: 1px solid rgba(255, 255, 255, 0.08);
    spacing: 8px;
    padding: 6px 12px;
}

/* ボタン共通 */
QPushButton {
    background-color: #2b2b2b;
    color: #f3f3f3;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 5px;
    padding: 6px 14px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #353535;
    border-color: rgba(255, 255, 255, 0.2);
}

QPushButton:pressed {
    background-color: #222222;
}

/* アクセントボタン (エクスポート等) */
QPushButton#accentButton {
    background-color: #0078d4;
    color: #ffffff;
    font-weight: bold;
    border: none;
}

QPushButton#accentButton:hover {
    background-color: #1084d9;
}

QPushButton#accentButton:pressed {
    background-color: #006cbe;
}

/* 分割ボタン */
QPushButton#splitButton {
    background-color: rgba(96, 205, 255, 0.15);
    color: #60cdff;
    border: 1px solid #60cdff;
    font-weight: bold;
}

QPushButton#splitButton:hover {
    background-color: rgba(96, 205, 255, 0.25);
}

/* 再生・コントロールボタン */
QPushButton.ctrlButton {
    background-color: #2b2b2b;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 4px;
    padding: 5px 10px;
    font-size: 12px;
}

QPushButton.ctrlButton:hover {
    background-color: #383838;
}

/* モード切替ボタングループ */
QPushButton.modeButton {
    background-color: #1c1c1c;
    color: #888888;
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 4px;
    padding: 5px 12px;
}

QPushButton.modeButton:checked {
    background-color: #0078d4;
    color: #ffffff;
    font-weight: bold;
    border-color: transparent;
}

/* グループボックス / カード */
QGroupBox {
    background-color: #262626;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    margin-top: 20px;
    font-weight: bold;
    padding: 10px;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
    color: #60cdff;
}

/* ストーリーボード (QListWidget) */
QListWidget#storyboard {
    background-color: #181818;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 8px;
    padding: 8px;
}

QListWidget#storyboard::item {
    background-color: #2b2b2b;
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 6px;
    padding: 4px;
    margin-right: 8px;
}

QListWidget#storyboard::item:hover {
    background-color: #353535;
    border-color: rgba(255, 255, 255, 0.25);
}

QListWidget#storyboard::item:selected {
    background-color: #323a45;
    border: 2px solid #60cdff;
}

/* ラベル */
QLabel {
    color: #f3f3f3;
}

QLabel.timeCode {
    color: #60cdff;
    font-size: 14px;
    font-weight: bold;
    font-family: "Consolas", monospace;
}

QLabel.labelMuted {
    color: #999999;
    font-size: 12px;
}

/* プログレスバー */
QProgressBar {
    background-color: #1a1a1a;
    border: 1px solid rgba(255, 255, 255, 0.1);
    border-radius: 5px;
    height: 12px;
    text-align: center;
    font-size: 10px;
}

QProgressBar::chunk {
    background-color: #0078d4;
    border-radius: 4px;
}

/* スクロールバー */
QScrollBar:horizontal {
    background: #181818;
    height: 8px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal {
    background: #444444;
    border-radius: 4px;
}

QScrollBar::handle:horizontal:hover {
    background: #666666;
}
"""
