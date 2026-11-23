# -*- coding: utf-8 -*-
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from core.settings import get_ai_prompt, reset_ai_prompt, save_ai_prompt
from ui.styles import CLEAN_WARM_STYLESHEET, FramelessWindowMixin


class PromptEditorDialog(QDialog, FramelessWindowMixin):
    """自定义出题提示词编辑器（支持保存修改、一键复制与恢复默认）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.init_frameless_drag(allow_resize=True)
        self.resize(740, 580)
        self.setMinimumSize(600, 460)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        container = QFrame()
        container.setObjectName("dialogContainer")
        container.setStyleSheet(CLEAN_WARM_STYLESHEET)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setColor(QColor(25, 30, 40, 32))
        shadow.setOffset(0, 8)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(12)

        # 顶栏
        top_bar = QHBoxLayout()
        title = QLabel("CUSTOM AI PROMPT // 自定义出题提示词模板")
        title.setObjectName("lblSectionTitle")
        btn_close = QPushButton("✕")
        btn_close.setObjectName("btnTopAction")
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.clicked.connect(self.reject)
        top_bar.addWidget(title)
        top_bar.addStretch()
        top_bar.addWidget(btn_close)
        layout.addLayout(top_bar)

        lbl_tip = QLabel(
            "说明：在此直接修改你发给 AI 的出题指令（如调整词数、题量、难度或增加特定要求）。"
            "保存后，点击主面板「Copy Prompt」将始终复制此处的内容。"
        )
        lbl_tip.setWordWrap(True)
        layout.addWidget(lbl_tip)

        self.editor = QPlainTextEdit()
        self.editor.setPlainText(get_ai_prompt())
        layout.addWidget(self.editor, stretch=1)

        # 底部操作栏
        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)

        btn_reset = QPushButton("Reset Default")
        btn_reset.setObjectName("btnSecondary")
        btn_reset.setToolTip("恢复为初始内置的六级标准提示词")
        btn_reset.setFocusPolicy(Qt.NoFocus)
        btn_reset.clicked.connect(self._on_reset)

        self.lbl_feedback = QLabel("")
        self.lbl_feedback.setObjectName("lblHighlightStats")

        btn_save_copy = QPushButton("Save && Copy")
        btn_save_copy.setObjectName("btnAccentOutline")
        btn_save_copy.setFocusPolicy(Qt.NoFocus)
        btn_save_copy.clicked.connect(self._on_save_and_copy)

        btn_save = QPushButton("Save Prompt")
        btn_save.setObjectName("btnPrimary")
        btn_save.setFocusPolicy(Qt.NoFocus)
        btn_save.clicked.connect(self._on_save)

        bottom_bar.addWidget(btn_reset)
        bottom_bar.addWidget(self.lbl_feedback)
        bottom_bar.addStretch()
        bottom_bar.addWidget(btn_save_copy)
        bottom_bar.addWidget(btn_save)
        layout.addLayout(bottom_bar)

        root.addWidget(container)

    def _on_reset(self):
        reply = QMessageBox.question(
            self,
            "恢复默认提示词",
            "确定要放弃当前修改并恢复为初始默认提示词吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            default_txt = reset_ai_prompt()
            self.editor.setPlainText(default_txt)
            self.lbl_feedback.setText("已恢复默认模板")

    def _on_save(self):
        text = self.editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "提示词内容不能为空。")
            return
        save_ai_prompt(text)
        self.accept()

    def _on_save_and_copy(self):
        text = self.editor.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "提示词内容不能为空。")
            return
        save_ai_prompt(text)
        QApplication.clipboard().setText(text)
        self.accept()

    def mousePressEvent(self, event):
        if not self.handle_mouse_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.handle_mouse_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.handle_mouse_release(event)
        super().mouseReleaseEvent(event)


class WorkflowGuideDialog(QDialog, FramelessWindowMixin):
    """从零到生成一套六级听力的全流程步骤说明书"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.init_frameless_drag(allow_resize=True)
        self.resize(720, 590)
        self.setMinimumSize(600, 480)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        container = QFrame()
        container.setObjectName("dialogContainer")
        container.setStyleSheet(CLEAN_WARM_STYLESHEET)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(32)
        shadow.setColor(QColor(25, 30, 40, 32))
        shadow.setOffset(0, 8)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        title = QLabel("WORKFLOW MANUAL // 从零制作六级听力材料指南")
        title.setObjectName("lblSectionTitle")
        btn_close = QPushButton("✕")
        btn_close.setObjectName("btnTopAction")
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.clicked.connect(self.accept)
        top_bar.addWidget(title)
        top_bar.addStretch()
        top_bar.addWidget(btn_close)
        layout.addLayout(top_bar)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #FFFFFF;
                border: 1px solid #E3E6EB;
                border-radius: 16px;
                padding: 16px;
                color: #2C313A;
                font-size: 13px;
                line-height: 1.65;
            }
        """)
        browser.setHtml("""
        <h3 style="color:#D96543; margin-top:0;">[Step 1] 获取 AI 出题指令并准备参考材料</h3>
        <p>找一篇你想模仿的六级真题长对话（Section A）或阅读文章。点击下方 <b>「Copy Prompt」</b>（如需定制要求可点 <b>「Edit Prompt」</b>），打开任意常用 AI 网页端，粘贴指令并在末尾附上你的参考素材发送。</p>
        <p style="color:#717885;"><i>提示词已内置 Kokoro-TTS 语音规范，会让 AI 自动把数字（如 2026、90%、$45,000）展开为纯英文单词，避免本地语音引擎误读。</i></p>

        <h3 style="color:#D96543;">[Step 2] 粘贴 AI 返回的 JSON 并执行校验</h3>
        <p>AI 回复后，全选复制整段回答（无需手动去掉代码块外壳），回到主面板点击 <b>「Paste」</b> 或直接粘贴到左侧输入框，程序会自动格式化并检查是否存在未展开的数字。</p>

        <h3 style="color:#D96543;">[Step 3] 调整语速/停顿并命名导出听力包</h3>
        <p>在右侧面板设定男主、女主、考官音色，以及 <b>读题前看选项时间（默认 8s）</b> 和 <b>读题后作答时间（默认 8s）</b>。点击 <b>「▶ Generate Exam」</b>，在弹窗中为本次练习命名文件夹，即可生成：</p>
        <ul>
            <li><b>音频文件 (.wav)</b>：双人对话 + 柔和提示音 + 考官读题与静音窗口。</li>
            <li><b>print_sheet.md</b>：纯选项打印卷（隐藏原文与题干，支持打印盲听）。</li>
            <li><b>answer_key.md / exam_data.json</b>：完整双人对话原文、答案解析与刷题元数据。</li>
        </ul>

        <h3 style="color:#D96543;">[Step 4] 打开「Quiz Practice」刷题面板实战演练</h3>
        <p>点击主面板右侧的 <b>「■ Quiz Practice」</b>，选择刚刚生成的练习文件夹，即可边听考场音频边在线选择 A/B/C/D，交卷后自动批改对错并解锁原文解析。</p>
        """)
        layout.addWidget(browser, stretch=3)

        lbl_prompt = QLabel("当前生效的 AI 出题指令预览：")
        lbl_prompt.setObjectName("lblSectionTitle")
        layout.addWidget(lbl_prompt)

        self.prompt_preview = QPlainTextEdit()
        self.prompt_preview.setReadOnly(True)
        self.prompt_preview.setPlainText(get_ai_prompt())
        self.prompt_preview.setMaximumHeight(125)
        layout.addWidget(self.prompt_preview, stretch=1)

        bottom_bar = QHBoxLayout()
        bottom_bar.setSpacing(10)
        self.lbl_copy_status = QLabel("")
        self.lbl_copy_status.setObjectName("lblHighlightStats")

        btn_edit_prompt = QPushButton("Edit Prompt")
        btn_edit_prompt.setObjectName("btnSecondary")
        btn_edit_prompt.setFocusPolicy(Qt.NoFocus)
        btn_edit_prompt.clicked.connect(self._open_prompt_editor)

        btn_copy_prompt = QPushButton("Copy Prompt")
        btn_copy_prompt.setObjectName("btnPrimary")
        btn_copy_prompt.setFocusPolicy(Qt.NoFocus)
        btn_copy_prompt.clicked.connect(self.copy_prompt_to_clipboard)

        btn_done = QPushButton("Close")
        btn_done.setObjectName("btnSecondary")
        btn_done.setFocusPolicy(Qt.NoFocus)
        btn_done.clicked.connect(self.accept)

        bottom_bar.addWidget(self.lbl_copy_status)
        bottom_bar.addStretch()
        bottom_bar.addWidget(btn_edit_prompt)
        bottom_bar.addWidget(btn_copy_prompt)
        bottom_bar.addWidget(btn_done)
        layout.addLayout(bottom_bar)

        root.addWidget(container)

    def _open_prompt_editor(self):
        dlg = PromptEditorDialog(parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self.prompt_preview.setPlainText(get_ai_prompt())
            self.lbl_copy_status.setText("自定义提示词已更新！")

    def copy_prompt_to_clipboard(self):
        QApplication.clipboard().setText(get_ai_prompt())
        self.lbl_copy_status.setText("Copied! Paste to AI + your reference text.")

    def mousePressEvent(self, event):
        if not self.handle_mouse_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.handle_mouse_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.handle_mouse_release(event)
        super().mouseReleaseEvent(event)