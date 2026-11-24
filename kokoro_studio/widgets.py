from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAction,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
)

from kokoro_studio.config import (
    FONT_STACK,
    PALETTE_BLACK,
    PALETTE_BLACK_SUB,
    PALETTE_ROSE,
    PALETTE_ROSE_DEEP,
)
from kokoro_studio.text_engine import (
    has_learning_annotations,
    render_annotated_html,
    sanitize_text,
)


class DrillCardItem(QFrame):
    """Two-column drill card displaying index badge, English phrase, and Chinese trap tip."""

    clicked = pyqtSignal(str)

    def __init__(self, index: int, phrase: str, tip: str = "", parent=None):
        super().__init__(parent)
        self.phrase = phrase
        self.tip = tip

        self.setObjectName("drillCardItem")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(46 if tip else 34)

        tooltip = f"点击以 0.85x 慢速点读:\n{phrase}"
        if tip:
            tooltip += f"\n\n避坑解析:\n{tip}"
        self.setToolTip(tooltip)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(2)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        lbl_idx = QLabel(f"{index:02d}")
        lbl_idx.setObjectName("drillCardIdx")
        lbl_idx.setFixedWidth(20)
        lbl_idx.setAlignment(Qt.AlignCenter)

        lbl_phrase = QLabel(phrase)
        lbl_phrase.setObjectName("drillCardPhrase")

        lbl_play = QLabel("🔊")
        lbl_play.setObjectName("drillCardPlayIcon")

        top_row.addWidget(lbl_idx)
        top_row.addWidget(lbl_phrase, 1)
        top_row.addWidget(lbl_play)
        layout.addLayout(top_row)

        if tip:
            lbl_tip = QLabel(tip)
            lbl_tip.setObjectName("drillCardTip")
            lbl_tip.setWordWrap(False)
            layout.addWidget(lbl_tip)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.phrase)
            event.accept()
            return
        super().mousePressEvent(event)


class CyberTextEdit(QTextEdit):
    """Smart editor supporting rich learning annotations, double-click sentence loop, and Alt+P drill."""

    sentence_double_clicked = pyqtSignal(int)
    selection_read_requested = pyqtSignal(str)

    def __init__(self, parent_player=None):
        super().__init__()
        self.player = parent_player
        self.is_rich_annotated = False
        self._raw_annotated_text = ""

    def set_annotated_content(
        self, display_text: str, font_size: int = 18, is_dark: bool = False
    ):
        self.blockSignals(True)
        if has_learning_annotations(display_text):
            self.is_rich_annotated = True
            self._raw_annotated_text = display_text
            self.setHtml(
                render_annotated_html(display_text, font_size=font_size, is_dark=is_dark)
            )
        else:
            self.is_rich_annotated = False
            self._raw_annotated_text = ""
            self.setPlainText(display_text)
        self.blockSignals(False)
        if self.player:
            self.player.on_text_changed()

    def refresh_theme(self, font_size: int = 18, is_dark: bool = False):
        """Re-render rich HTML annotations when switching between light and dark-glass themes."""
        if self.is_rich_annotated and self._raw_annotated_text:
            v_scroll = self.verticalScrollBar().value()
            self.blockSignals(True)
            self.setHtml(
                render_annotated_html(
                    self._raw_annotated_text, font_size=font_size, is_dark=is_dark
                )
            )
            self.verticalScrollBar().setValue(v_scroll)
            self.blockSignals(False)

    refresh_annotated_theme = refresh_theme

    def clear(self):
        self.is_rich_annotated = False
        self._raw_annotated_text = ""
        super().clear()

    def clear_annotated_state(self):
        self.clear()

    def insertFromMimeData(self, source):
        if source.hasText():
            pasted_text = source.text()
            if self.player:
                self.player.ingest_learning_text(pasted_text, auto_play=False)
                return
            cleaned = sanitize_text(pasted_text)
            self.insertPlainText(cleaned)
            return
        super().insertFromMimeData(source)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            cursor = self.cursorForPosition(event.pos())
            self.sentence_double_clicked.emit(cursor.position())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() == Qt.AltModifier and event.key() == Qt.Key_P:
            selected = self.textCursor().selectedText().replace("\u2029", " ").strip()
            if selected:
                self.selection_read_requested.emit(selected)
                event.accept()
                return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        menu = self.createStandardContextMenu()
        selected = self.textCursor().selectedText().replace("\u2029", " ").strip()
        if selected:
            menu.addSeparator()
            act_read_sel = QAction("🔊 Read Selected Phrase (Alt+P)", self)
            act_read_sel.triggered.connect(
                lambda: self.selection_read_requested.emit(selected)
            )
            menu.addAction(act_read_sel)
        menu.exec_(event.globalPos())


class ClickableSlider(QSlider):
    """Horizontal slider supporting instant click-to-seek."""

    jump_requested = pyqtSignal(float)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            val_ratio = event.pos().x() / max(1, self.width())
            val_ratio = max(0.0, min(1.0, val_ratio))
            self.setValue(int(val_ratio * self.maximum()))
            self.jump_requested.emit(val_ratio)
            event.accept()
        super().mousePressEvent(event)


class CyberHelpDialog(QDialog):
    """Frameless manual & shortcuts modal."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint
        )
        self.drag_pos = QPoint()
        self.resize(580, 600)
        self.setStyleSheet(f"""
            QDialog {{
                background-color: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 14px;
            }}
            QLabel {{
                color: {PALETTE_BLACK_SUB};
                font-family: {FONT_STACK};
                font-size: 13px;
                line-height: 1.55;
            }}
            QPushButton {{
                background-color: {PALETTE_ROSE};
                color: white;
                font-weight: bold;
                border: none;
                border-radius: 7px;
                padding: 8px 20px;
                font-size: 13px;
                font-family: {FONT_STACK};
            }}
            QPushButton:hover {{
                background-color: {PALETTE_ROSE_DEEP};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(12)

        title = QLabel("SMART LISTENING STUDIO // MANUAL & SHORTCUTS")
        title.setStyleSheet(
            f"color: {PALETTE_BLACK}; font-weight: bold; font-size: 14.5px; letter-spacing: 0.4px;"
        )

        info = QLabel(
            "<b>[ Smart Listening & Annotation Engine ]</b><br>"
            "• <b>Dual-Track Rendering</b>: Visual markup (<code>**stress**</code>, <code>(silent)</code>, "
            "<code>[phonetic]</code>, <code>‿</code>, <code>/</code>) renders cleanly on screen while TTS reads pure English.<br>"
            "• <b>Multi-Speaker Dialogue</b>: Lines starting with <code>W:</code> / <code>M:</code> automatically switch between Female and Male voices.<br>"
            "• <b>2-Column Drill Cards</b>: Markdown tables, trap lists, or linked chunks auto-populate clickable 0.85x drill cards.<br>"
            "• <b>Direct NetEase MP3 Export</b>: Exports single MP3 with embedded synced LRC lyrics and customizable Cover, Folder & Name (<5s: 3x, <20s: 2x, ≥20s: 1x; auto-numbered on conflict).<br>"
            "• <b>Adjustable Transparency</b>: Adjust window transparency (0%–95%) via Settings or <code>Ctrl+Up/Down</code>.<br><br>"
            "<b>[ Interactive Controls & Shortcuts ]</b><br>"
            "• <b>Double-Click Sentence</b>: Loop single sentence continuously (Esc or badge to exit)<br>"
            "• <b>Select Phrase + Alt+P</b>: Instantly read highlighted chunk at 0.85x<br>"
            "• <b>Ctrl + Up / Down</b>: Adjust Window Transparency (±5%)<br>"
            "• <b>Space</b>: Pause / Resume Playback  |  <b>Ctrl + Enter</b>: Synthesize & Play<br>"
            "• <b>Ctrl + S / Ctrl + Shift + S</b>: Export NetEase MP3<br>"
            "• <b>Ctrl + R</b>: Reload  |  <b>Esc</b>: Minimize to Tray"
        )
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)

        btn_close = QPushButton("Understood")
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.clicked.connect(self.accept)

        layout.addWidget(title)
        layout.addWidget(info)
        layout.addStretch()
        layout.addWidget(btn_close, 0, Qt.AlignRight)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and not self.drag_pos.isNull():
            self.move(event.globalPos() - self.drag_pos)
            event.accept()