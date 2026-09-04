"""
ui/storyboard.py
Windows Movie Maker 風のカード型ストーリーボードウィジェット
ネイティブ DnD 並び替え、ファイルドロップ、Delete キー削除を完全サポート
"""

import os
from typing import List, Optional
from PySide6.QtWidgets import (
    QListWidget, QListWidgetItem, QMenu, QAbstractItemView
)
from PySide6.QtCore import Qt, Signal, QSize, QPoint
from PySide6.QtGui import QIcon, QPixmap, QColor, QAction

from .async_thumbnail import ThumbnailManager


class StoryboardItem(QListWidgetItem):
    def __init__(self, clip_id: str, file_path: str, start_sec: float, end_sec: float, total_sec: float):
        super().__init__()
        self.clip_id = clip_id
        self.file_path = file_path
        self.start_sec = start_sec
        self.end_sec = end_sec
        self.total_sec = total_sec
        self.file_name = os.path.basename(file_path)

        # 初期プレースホルダーアイコン
        placeholder = QPixmap(160, 90)
        placeholder.fill(QColor(40, 40, 40))
        self.setIcon(QIcon(placeholder))
        self.update_text()

    def update_text(self):
        dur = self.end_sec - self.start_sec
        m, s = divmod(int(dur), 60)
        self.setText(f"{self.file_name}\n[{m:02d}:{s:02d}]")


class StoryboardWidget(QListWidget):
    filesDropped = Signal(list)       # 外部ファイルドロップ時 [path, ...]
    clipDeleted = Signal(str)         # 削除された clip_id
    orderChanged = Signal()           # 並び替え発生時

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("storyboard")
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(False)
        self.setIconSize(QSize(160, 90))
        self.setGridSize(QSize(180, 160))
        self.setSpacing(10)
        self.setMinimumHeight(180)

        # ネイティブ DnD 設定
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        # 右クリックメニュー
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        # 1. 外部エクスプローラーからのファイルドロップの場合
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            valid_paths = []
            for u in urls:
                if u.isLocalFile():
                    p = u.toLocalFile()
                    ext = os.path.splitext(p)[1].lower()
                    if ext in [".mp4", ".mkv", ".mov", ".avi", ".webm", ".ts", ".flv", ".wmv", ".m4v"]:
                        valid_paths.append(p)

            if valid_paths:
                event.acceptProposedAction()
                # 外部ファイルは上書きせず、確実に追加シグナルを発行
                self.filesDropped.emit(valid_paths)
                return

        # 2. 内部カードのドラッグ並び替えの場合
        super().dropEvent(event)
        self.orderChanged.emit()

    def keyPressEvent(self, event):
        # Delete / Backspace キーで選択中クリップを削除
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def delete_selected(self):
        cur_item = self.currentItem()
        if not cur_item or not isinstance(cur_item, StoryboardItem):
            return

        row = self.row(cur_item)
        clip_id = cur_item.clip_id
        self.takeItem(row)
        self.clipDeleted.emit(clip_id)

        # 次または前のアイテムを選択
        new_count = self.count()
        if new_count > 0:
            new_row = min(row, new_count - 1)
            self.setCurrentRow(new_row)

    def show_context_menu(self, pos: QPoint):
        item = self.itemAt(pos)
        if not item or not isinstance(item, StoryboardItem):
            return

        menu = QMenu(self)
        del_action = QAction("クリップを削除 (Delete)", self)
        del_action.triggered.connect(self.delete_selected)
        menu.addAction(del_action)

        menu.exec(self.mapToGlobal(pos))
