# -*- coding: utf-8 -*-
from PyQt5.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt5.QtWidgets import QSlider

EDGE_MARGIN = 8

CLEAN_WARM_STYLESHEET = """
QWidget#mainContainer, QWidget#dialogContainer {
    background-color: #F2F4F7;
    border: 1px solid #E5E7EB;
    border-radius: 24px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QLabel {
    color: #5C626E;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 13px;
}
QLabel#lblBrandTitle {
    color: #5C626E;
    font-size: 13px;
    font-weight: bold;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QLabel#lblHighlightStats {
    color: #D96543;
    font-size: 13px;
    font-weight: bold;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QLabel#lblSectionTitle {
    color: #1F2329;
    font-size: 13px;
    font-weight: bold;
}
QPlainTextEdit, QTextEdit {
    background-color: #FFFFFF;
    border: 1px solid #E3E6EB;
    border-radius: 18px;
    color: #1F2329;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 13px;
    padding: 16px;
    selection-background-color: #F8D8CF;
    selection-color: #7D2C17;
}
QPlainTextEdit:focus, QTextEdit:focus {
    border: 1.5px solid #D96543;
}
QFrame#rightPanel, QFrame#cardPanel {
    background-color: #FFFFFF;
    border: 1px solid #E3E6EB;
    border-radius: 18px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QSplitter::handle {
    background-color: transparent;
    margin: 4px 2px;
}
QLineEdit {
    background-color: #F7F8FA;
    border: 1px solid #E0E3E8;
    border-radius: 10px;
    padding: 7px 12px;
    color: #1F2329;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 13px;
}
QLineEdit:focus {
    border: 1.5px solid #D96543;
    background-color: #FFFFFF;
}
QComboBox {
    background-color: #F7F8FA;
    border: 1px solid #E0E3E8;
    border-radius: 10px;
    padding: 6px 12px;
    color: #1F2329;
    font-size: 13px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QComboBox:hover {
    border: 1px solid #C5CBD4;
    background-color: #F0F2F5;
}
QComboBox:focus {
    border: 1.5px solid #D96543;
}
QComboBox::drop-down {
    border: none;
    width: 22px;
}
QComboBox QAbstractItemView {
    background-color: #FFFFFF;
    color: #1F2329;
    selection-background-color: #FAF0EC;
    selection-color: #D96543;
    border: 1px solid #E0E3E8;
    border-radius: 8px;
    font-size: 13px;
    padding: 4px;
}
QDoubleSpinBox, QSpinBox {
    background-color: #F7F8FA;
    border: 1px solid #E0E3E8;
    border-radius: 10px;
    color: #1F2329;
    padding: 5px 24px 5px 10px;
    font-size: 13px;
    font-family: "Consolas", monospace;
}
QDoubleSpinBox:hover, QSpinBox:hover {
    border: 1px solid #C5CBD4;
    background-color: #F2F4F7;
}
QDoubleSpinBox:focus, QSpinBox:focus {
    border: 1.5px solid #D96543;
    background-color: #FFFFFF;
}
QDoubleSpinBox::up-button, QSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #E0E3E8;
    border-bottom: 1px solid #E0E3E8;
    border-top-right-radius: 10px;
    background-color: #F0F2F5;
}
QDoubleSpinBox::up-button:hover, QSpinBox::up-button:hover {
    background-color: #FAF0EC;
}
QDoubleSpinBox::down-button, QSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 22px;
    border-left: 1px solid #E0E3E8;
    border-bottom-right-radius: 10px;
    background-color: #F0F2F5;
}
QDoubleSpinBox::down-button:hover, QSpinBox::down-button:hover {
    background-color: #FAF0EC;
}
QSlider::groove:horizontal {
    border: none;
    height: 6px;
    background: #E5E7EB;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #D96543;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #FFFFFF;
    border: 2px solid #D96543;
    width: 16px;
    margin: -5px 0;
    border-radius: 8px;
}
QSlider::handle:horizontal:hover {
    border-color: #C85A32;
}
QProgressBar {
    background-color: #E5E7EB;
    border: none;
    border-radius: 3px;
    height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk {
    background-color: #D96543;
    border-radius: 3px;
}
QCheckBox, QRadioButton {
    color: #4A515E;
    font-size: 13px;
    spacing: 8px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1.5px solid #CBD1DA;
    background-color: #F7F8FA;
}
QCheckBox::indicator:hover {
    border-color: #D96543;
}
QCheckBox::indicator:checked {
    background-color: #D96543;
    border: 1.5px solid #D96543;
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
    border: 1.5px solid #CBD1DA;
    background-color: #FFFFFF;
}
QRadioButton::indicator:hover {
    border-color: #D96543;
}
QRadioButton::indicator:checked {
    background-color: #D96543;
    border: 3px solid #FADCD3;
}
QPushButton {
    border-radius: 11px;
    font-size: 13px;
    font-weight: bold;
    padding: 9px 14px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QPushButton#btnPrimary {
    background-color: #D96543;
    color: #FFFFFF;
    border: none;
}
QPushButton#btnPrimary:hover {
    background-color: #C85A32;
}
QPushButton#btnPrimary:pressed {
    background-color: #B24E2B;
}
QPushButton#btnPrimary:disabled {
    background-color: #E5B4A6;
}
QPushButton#btnDark {
    background-color: #24272C;
    color: #FFFFFF;
    border: none;
}
QPushButton#btnDark:hover {
    background-color: #363B42;
}
QPushButton#btnDark:pressed {
    background-color: #181A1E;
}
QPushButton#btnSecondary {
    background-color: #F7F8FA;
    border: 1px solid #DCE0E6;
    color: #383D47;
}
QPushButton#btnSecondary:hover {
    background-color: #ECEFF4;
    color: #1F2329;
    border-color: #C5CBD4;
}
QPushButton#btnSecondary:pressed {
    background-color: #E2E6EC;
}
QPushButton#btnAccentOutline {
    background-color: #FAF0EC;
    border: 1px solid #F0C7BA;
    color: #D96543;
}
QPushButton#btnAccentOutline:hover {
    background-color: #F5E2DB;
    border-color: #D96543;
}
QPushButton#btnTopAction {
    background-color: transparent;
    color: #8C94A3;
    font-size: 13px;
    font-weight: bold;
    border-radius: 8px;
    padding: 4px 10px;
    font-family: "Consolas", monospace;
}
QPushButton#btnTopAction:hover {
    background-color: #E2E6EC;
    color: #383D47;
}
QPushButton#pillBtn {
    background-color: #FFFFFF;
    border: 1px solid #DCE0E6;
    color: #4A515E;
    border-radius: 9px;
    padding: 5px 11px;
    font-size: 12px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QPushButton#pillBtn:hover {
    background-color: #FAF0EC;
    border-color: #D96543;
    color: #D96543;
}
QPushButton#pillBtnActive {
    background-color: #FAF0EC;
    border: 1px solid #D96543;
    color: #D96543;
    border-radius: 9px;
    padding: 5px 11px;
    font-size: 12px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
}
QScrollArea {
    border: none;
    background-color: transparent;
}
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 6px;
    margin: 2px 0;
}
QScrollBar::handle:vertical {
    background: #D1D5DB;
    min-height: 24px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background: #9CA3AF;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QMenu {
    background-color: #FFFFFF;
    border: 1px solid #E0E3E8;
    border-radius: 10px;
    padding: 6px;
    color: #1F2329;
    font-size: 13px;
}
QMenu::item {
    padding: 8px 20px;
    border-radius: 6px;
}
QMenu::item:selected {
    background-color: #FAF0EC;
    color: #D96543;
}
QMenu::separator {
    height: 1px;
    background: #E5E7EB;
    margin: 4px 8px;
}
"""


class ClickableSlider(QSlider):
    """支持鼠标左键点击直接跳转进度的滑块"""

    jump_requested = pyqtSignal(float)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            val_ratio = event.pos().x() / max(1, self.width())
            val_ratio = max(0.0, min(1.0, val_ratio))
            self.setValue(int(val_ratio * self.maximum()))
            self.jump_requested.emit(val_ratio)
            event.accept()
        super().mousePressEvent(event)


class FramelessWindowMixin:
    """为无边框圆角窗口提供统一的拖拽移动与四边/四角缩放能力"""

    def init_frameless_drag(self, allow_resize: bool = True):
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self._drag_pos = QPoint()
        self._drag_edge = None
        self._drag_start_geo = QRect()
        self._drag_start_pos = QPoint()
        self._allow_resize = allow_resize

    def _detect_edge(self, pos):
        if not self._allow_resize:
            return None
        r = self.rect()
        left = pos.x() <= EDGE_MARGIN
        right = pos.x() >= r.width() - EDGE_MARGIN
        top = pos.y() <= EDGE_MARGIN
        bottom = pos.y() >= r.height() - EDGE_MARGIN

        if left and top:
            return "top-left"
        if right and top:
            return "top-right"
        if left and bottom:
            return "bottom-left"
        if right and bottom:
            return "bottom-right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    def _apply_cursor(self, edge):
        if edge in ("top-left", "bottom-right"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif edge in ("top-right", "bottom-left"):
            self.setCursor(Qt.SizeBDiagCursor)
        elif edge in ("left", "right"):
            self.setCursor(Qt.SizeHorCursor)
        elif edge in ("top", "bottom"):
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    def handle_mouse_press(self, event) -> bool:
        if event.button() == Qt.LeftButton:
            edge = self._detect_edge(event.pos())
            if edge:
                self._drag_edge = edge
                self._drag_start_geo = self.geometry()
                self._drag_start_pos = event.globalPos()
                event.accept()
                return True
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()
            return True
        return False

    def handle_mouse_move(self, event) -> bool:
        if self._drag_edge and (event.buttons() & Qt.LeftButton):
            delta = event.globalPos() - self._drag_start_pos
            geo = QRect(self._drag_start_geo)
            min_w = self.minimumWidth()
            min_h = self.minimumHeight()

            if "right" in self._drag_edge:
                geo.setWidth(max(min_w, self._drag_start_geo.width() + delta.x()))
            elif "left" in self._drag_edge:
                new_w = max(min_w, self._drag_start_geo.width() - delta.x())
                geo.setLeft(self._drag_start_geo.right() - new_w)

            if "bottom" in self._drag_edge:
                geo.setHeight(max(min_h, self._drag_start_geo.height() + delta.y()))
            elif "top" in self._drag_edge:
                new_h = max(min_h, self._drag_start_geo.height() - delta.y())
                geo.setTop(self._drag_start_geo.bottom() - new_h)

            self.setGeometry(geo)
            event.accept()
            return True

        if (event.buttons() & Qt.LeftButton) and not self._drag_pos.isNull():
            self.move(event.globalPos() - self._drag_pos)
            event.accept()
            return True

        self._apply_cursor(self._detect_edge(event.pos()))
        return False

    def handle_mouse_release(self, event):
        self._drag_edge = None
        self._drag_pos = QPoint()
        self._apply_cursor(None)