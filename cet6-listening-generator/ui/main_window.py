# -*- coding: utf-8 -*-
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
import os

import torch
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QIcon
from PyQt5.QtNetwork import QLocalServer
from PyQt5.QtWidgets import (
    QAbstractSpinBox,
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QSplitter,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from core.exam_exporter import (
    export_exam_package,
    parse_and_validate_exam_json,
    sanitize_folder_name,
)
from core.settings import (
    APP_NAME,
    DEFAULT_CONFIG,
    DEFAULT_KOKORO_ROOT,
    PRESET_VOICES,
    SAMPLE_RATE,
    create_qsettings,
    get_ai_prompt,
    get_default_output_dir,
    get_project_root,
    get_tray_icon_path,
)
from core.tts_engine import ExamSynthThread
from ui.guide_dialog import PromptEditorDialog, WorkflowGuideDialog
from ui.quiz_dialog import QuizPracticeDialog
from ui.styles import CLEAN_WARM_STYLESHEET, FramelessWindowMixin


class ExportSetupDialog(QDialog, FramelessWindowMixin):
    """生成前的存放路径与文件夹命名引导弹窗"""

    def __init__(self, suggested_title: str, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.init_frameless_drag(allow_resize=False)
        self.resize(520, 360)

        self.base_dir = get_default_output_dir()
        date_prefix = datetime.now().strftime("%Y%m%d")
        clean_slug = sanitize_folder_name(suggested_title)[:36]
        default_sub = f"{date_prefix}_{clean_slug}" if clean_slug else f"{date_prefix}_cet6_set"

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        container = QFrame()
        container.setObjectName("dialogContainer")
        container.setStyleSheet(CLEAN_WARM_STYLESHEET)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setColor(QColor(25, 30, 40, 32))
        shadow.setOffset(0, 8)
        container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 16, 24, 20)
        layout.setSpacing(10)

        top_bar = QHBoxLayout()
        lbl_title = QLabel("EXPORT SETTINGS // 设置文件夹与文件名")
        lbl_title.setObjectName("lblSectionTitle")
        btn_close = QPushButton("✕")
        btn_close.setObjectName("btnTopAction")
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.clicked.connect(self.reject)
        top_bar.addWidget(lbl_title)
        top_bar.addStretch()
        top_bar.addWidget(btn_close)
        layout.addLayout(top_bar)

        lbl_base = QLabel("1. 存放根目录（默认项目 output 文件夹，也可自定义）：")
        layout.addWidget(lbl_base)

        base_row = QHBoxLayout()
        self.edit_base_dir = QLineEdit(self.base_dir)
        self.edit_base_dir.textChanged.connect(self._update_preview)

        btn_browse = QPushButton("Browse...")
        btn_browse.setObjectName("btnSecondary")
        btn_browse.setFocusPolicy(Qt.NoFocus)
        btn_browse.clicked.connect(self._choose_base_dir)

        base_row.addWidget(self.edit_base_dir, stretch=1)
        base_row.addWidget(btn_browse)
        layout.addLayout(base_row)

        self.chk_use_subfolder = QCheckBox("在上方目录下新建独立子文件夹（推荐，按套归档）")
        self.chk_use_subfolder.setChecked(True)
        self.chk_use_subfolder.setFocusPolicy(Qt.NoFocus)
        self.chk_use_subfolder.toggled.connect(self._on_subfolder_toggled)
        layout.addWidget(self.chk_use_subfolder)

        self.edit_subfolder = QLineEdit(default_sub)
        self.edit_subfolder.setPlaceholderText("输入本次练习的文件夹名称...")
        self.edit_subfolder.textChanged.connect(self._update_preview)
        layout.addWidget(self.edit_subfolder)

        lbl_wav = QLabel("2. 生成的音频文件名（自动附加 .wav 后缀）：")
        layout.addWidget(lbl_wav)
        self.edit_filename = QLineEdit("cet6_exam")
        self.edit_filename.textChanged.connect(self._update_preview)
        layout.addWidget(self.edit_filename)

        self.lbl_preview = QLabel("")
        self.lbl_preview.setObjectName("lblHighlightStats")
        self.lbl_preview.setWordWrap(True)
        layout.addWidget(self.lbl_preview)
        self._update_preview()

        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setObjectName("btnSecondary")
        btn_cancel.setFocusPolicy(Qt.NoFocus)
        btn_cancel.clicked.connect(self.reject)

        btn_confirm = QPushButton("▶ Confirm && Generate")
        btn_confirm.setObjectName("btnPrimary")
        btn_confirm.setFocusPolicy(Qt.NoFocus)
        btn_confirm.clicked.connect(self.accept)

        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_confirm)
        layout.addLayout(btn_row)

        root.addWidget(container)

    def _choose_base_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self,
            "选择存放根目录",
            self.edit_base_dir.text().strip() or get_default_output_dir(),
        )
        if chosen:
            self.edit_base_dir.setText(chosen)

    def _on_subfolder_toggled(self, checked: bool):
        self.edit_subfolder.setEnabled(checked)
        self._update_preview()

    def _update_preview(self):
        target_dir, safe_name = self.get_export_target()
        full_wav = os.path.join(target_dir, f"{safe_name}.wav")
        self.lbl_preview.setText(f"Output: {full_wav}")

    def get_export_target(self):
        base = self.edit_base_dir.text().strip() or get_default_output_dir()
        if self.chk_use_subfolder.isChecked():
            sub = sanitize_folder_name(self.edit_subfolder.text().strip())
            target_dir = os.path.join(base, sub)
        else:
            target_dir = base
        safe_file = sanitize_folder_name(self.edit_filename.text().strip() or "cet6_exam")
        return target_dir, safe_file

    def mousePressEvent(self, event):
        if not self.handle_mouse_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.handle_mouse_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.handle_mouse_release(event)
        super().mouseReleaseEvent(event)


class Cet6GeneratorMainWindow(QWidget, FramelessWindowMixin):
    """六级听力高仿模拟器主工作台（极简无边框暖色卡片风格）"""

    def __init__(self, single_instance_key: str):
        super().__init__()
        self.single_instance_key = single_instance_key
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.init_frameless_drag(allow_resize=True)
        self.setMinimumSize(760, 520)
        self.resize(910, 615)

        self.settings = create_qsettings()
        self.last_generated_dir = ""
        self.pending_exam_data: dict = {}
        self.pending_target_dir: str = ""
        self.pending_base_name: str = "cet6_exam"
        self.pending_timing_meta: dict = {}

        self.synth_thread = ExamSynthThread()
        self.synth_thread.progress_updated.connect(self._on_synth_progress)
        self.synth_thread.synth_finished.connect(self._on_synth_finished)
        self.synth_thread.synth_error.connect(self._on_synth_error)

        self._init_native_taskbar_listener()
        self._init_ui()
        self._init_tray_icon()
        self._init_single_instance_ipc()
        self._load_preferences()

    def _init_native_taskbar_listener(self):
        try:
            self.msg_taskbar_created = ctypes.windll.user32.RegisterWindowMessageW(
                "TaskbarCreated"
            )
        except Exception:
            self.msg_taskbar_created = 0

    def nativeEvent(self, eventType, message):
        try:
            msg = wintypes.MSG.from_address(int(message))
            if self.msg_taskbar_created and msg.message == self.msg_taskbar_created:
                QTimer.singleShot(200, self._restore_tray_icon)
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def _restore_tray_icon(self):
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.hide()
            self.tray_icon.show()

    def _init_single_instance_ipc(self):
        self.local_server = QLocalServer(self)
        self.local_server.removeServer(self.single_instance_key)
        self.local_server.newConnection.connect(self._on_new_instance_connected)
        self.local_server.listen(self.single_instance_key)

    def _on_new_instance_connected(self):
        client = self.local_server.nextPendingConnection()
        if client:
            client.readyRead.connect(lambda: self._handle_ipc_msg(client))
            self.force_bring_to_foreground()

    def _handle_ipc_msg(self, client):
        try:
            client.readAll()
        except Exception:
            pass
        self.force_bring_to_foreground()
        client.disconnectFromServer()

    def _init_tray_icon(self):
        icon_path = get_tray_icon_path()
        if os.path.exists(icon_path):
            self.app_icon = QIcon(icon_path)
        else:
            self.app_icon = self.style().standardIcon(
                QApplication.style().SP_MediaVolume
            )

        self.setWindowIcon(self.app_icon)
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.app_icon)
        self.tray_icon.setToolTip(f"{APP_NAME} (Running)")

        tray_menu = QMenu()
        act_toggle = QAction("显示 / 隐藏主面板", self)
        act_toggle.triggered.connect(self.toggle_window_visibility)

        act_quiz = QAction("打开刷题面板", self)
        act_quiz.triggered.connect(self.open_quiz_panel)

        act_prompt = QAction("修改出题提示词", self)
        act_prompt.triggered.connect(self.open_prompt_editor)

        act_guide = QAction("查看从零生成流程指南", self)
        act_guide.triggered.connect(self.open_workflow_guide)

        act_quit = QAction("退出程序", self)
        act_quit.triggered.connect(self.quit_application)

        tray_menu.addAction(act_toggle)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quiz)
        tray_menu.addAction(act_prompt)
        tray_menu.addAction(act_guide)
        tray_menu.addSeparator()
        tray_menu.addAction(act_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_window_visibility()

    def toggle_window_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.force_bring_to_foreground()

    def force_bring_to_foreground(self):
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        if os.name == "nt":
            try:
                hwnd = int(self.winId())
                ctypes.windll.user32.ShowWindow(hwnd, 9)
                ctypes.windll.user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

    def _init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(12, 12, 12, 12)

        self.container = QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet(CLEAN_WARM_STYLESHEET)
        self.container.setMouseTracking(True)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setColor(QColor(25, 30, 40, 30))
        shadow.setOffset(0, 10)
        self.container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(20, 12, 20, 20)
        container_layout.setSpacing(8)

        # 顶栏：完全留白清爽拖拽区 + 右上角极简最小化与关闭按钮
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.setSpacing(4)

        btn_min = QPushButton("—")
        btn_min.setObjectName("btnTopAction")
        btn_min.setToolTip("最小化到托盘 (Esc)")
        btn_min.setFocusPolicy(Qt.NoFocus)
        btn_min.clicked.connect(self.hide)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("btnTopAction")
        btn_close.setToolTip("关闭到托盘")
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.setStyleSheet(
            "QPushButton#btnTopAction:hover { background-color: #FBEAE6; color: #D96543; }"
        )
        btn_close.clicked.connect(self.hide)

        top_bar.addStretch()
        top_bar.addWidget(btn_min)
        top_bar.addWidget(btn_close)
        container_layout.addLayout(top_bar)

        # 主体左右分栏
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(8)
        self.splitter.setChildrenCollapsible(False)

        # 左侧：JSON 输入框 + 进度条 + 底部状态与胶囊快捷操作栏
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(10)

        self.json_editor = QPlainTextEdit()
        self.json_editor.setPlaceholderText(
            "Step 1: 点击右下角 [Step Guide] 查看流程，或点 [Copy Prompt] / [Edit Prompt] 获取指令\n"
            "Step 2: 将 AI 返回的 JSON 粘贴至此（自动剥离代码块外壳并校验格式）\n"
            "Step 3: 点击右侧 [▶ Generate Exam] 命名导出考场听力包"
        )
        left_layout.addWidget(self.json_editor, stretch=1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        left_layout.addWidget(self.progress_bar)

        meta_bar = QHBoxLayout()
        meta_bar.setSpacing(8)

        self.lbl_status = QLabel("Ready: Paste JSON")
        self.lbl_status.setObjectName("lblHighlightStats")

        btn_copy_prompt = QPushButton("Copy Prompt")
        btn_copy_prompt.setObjectName("pillBtn")
        btn_copy_prompt.setToolTip("一键复制当前出题 Prompt")
        btn_copy_prompt.setFocusPolicy(Qt.NoFocus)
        btn_copy_prompt.clicked.connect(self._quick_copy_prompt)

        btn_paste = QPushButton("Paste")
        btn_paste.setObjectName("pillBtn")
        btn_paste.setToolTip("粘贴剪贴板并自动校验")
        btn_paste.setFocusPolicy(Qt.NoFocus)
        btn_paste.clicked.connect(self._paste_from_clipboard)

        btn_validate = QPushButton("Check")
        btn_validate.setObjectName("pillBtn")
        btn_validate.setToolTip("校验并格式化 JSON")
        btn_validate.setFocusPolicy(Qt.NoFocus)
        btn_validate.clicked.connect(self._validate_current_json)

        btn_clear = QPushButton("Clear")
        btn_clear.setObjectName("pillBtn")
        btn_clear.setFocusPolicy(Qt.NoFocus)
        btn_clear.clicked.connect(self._clear_json_editor)

        meta_bar.addWidget(self.lbl_status, stretch=1)
        meta_bar.addWidget(btn_copy_prompt)
        meta_bar.addWidget(btn_paste)
        meta_bar.addWidget(btn_validate)
        meta_bar.addWidget(btn_clear)
        left_layout.addLayout(meta_bar)

        # 右侧：单一纯白卡片面板
        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_panel.setMinimumWidth(295)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(18, 18, 18, 18)
        right_layout.setSpacing(10)

        # Device
        dev_row = QHBoxLayout()
        dev_row.addWidget(QLabel("Device:"))
        self.combo_device = QComboBox()
        self.combo_device.setFocusPolicy(Qt.NoFocus)
        gpu_label = (
            f"GPU ({torch.cuda.get_device_name(0)})"
            if torch.cuda.is_available()
            else "CPU (No CUDA)"
        )
        self.combo_device.addItem(gpu_label, "auto")
        self.combo_device.addItem("CUDA", "cuda")
        self.combo_device.addItem("CPU", "cpu")
        dev_row.addWidget(self.combo_device, stretch=1)
        right_layout.addLayout(dev_row)

        # Voices
        voice_items = self._collect_available_voices()

        male_row = QHBoxLayout()
        male_row.addWidget(QLabel("Male:"))
        self.combo_male = QComboBox()
        self.combo_male.setFocusPolicy(Qt.NoFocus)
        self.combo_male.addItems(voice_items)
        male_row.addWidget(self.combo_male, stretch=1)
        right_layout.addLayout(male_row)

        female_row = QHBoxLayout()
        female_row.addWidget(QLabel("Female:"))
        self.combo_female = QComboBox()
        self.combo_female.setFocusPolicy(Qt.NoFocus)
        self.combo_female.addItems(voice_items)
        female_row.addWidget(self.combo_female, stretch=1)
        right_layout.addLayout(female_row)

        exam_row = QHBoxLayout()
        exam_row.addWidget(QLabel("Examiner:"))
        self.combo_examiner = QComboBox()
        self.combo_examiner.setFocusPolicy(Qt.NoFocus)
        self.combo_examiner.addItems(voice_items)
        exam_row.addWidget(self.combo_examiner, stretch=1)
        right_layout.addLayout(exam_row)

        # Speed slider
        speed_row = QHBoxLayout()
        self.lbl_speed_val = QLabel("Speed: 1.0x")
        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setFocusPolicy(Qt.NoFocus)
        self.slider_speed.setRange(70, 140)
        self.slider_speed.setValue(100)
        self.slider_speed.valueChanged.connect(
            lambda v: self.lbl_speed_val.setText(f"Speed: {v / 100:.2f}x")
        )
        speed_row.addWidget(self.lbl_speed_val)
        speed_row.addWidget(self.slider_speed, stretch=1)
        right_layout.addLayout(speed_row)

        # Pre-Q Read Pause
        pre_row = QHBoxLayout()
        pre_row.addWidget(QLabel("Pre-Q Read:"))
        pre_row.addStretch()
        self.spin_pre_sec = self._create_clean_spinbox(0.0, 30.0, 1.0, 8.0, " s")
        pre_row.addWidget(self.spin_pre_sec)
        right_layout.addLayout(pre_row)

        # Post-Q Answer Pause
        post_row = QHBoxLayout()
        post_row.addWidget(QLabel("Post-Q Ans:"))
        post_row.addStretch()
        self.spin_post_sec = self._create_clean_spinbox(0.0, 30.0, 1.0, 8.0, " s")
        post_row.addWidget(self.spin_post_sec)
        right_layout.addLayout(post_row)

        # Turn Gap
        turn_row = QHBoxLayout()
        turn_row.addWidget(QLabel("Turn Gap:"))
        turn_row.addStretch()
        self.spin_turn_gap = self._create_clean_spinbox(0.0, 3.0, 0.05, 0.25, " s")
        turn_row.addWidget(self.spin_turn_gap)
        right_layout.addLayout(turn_row)

        # Checkboxes
        self.chk_ding = QCheckBox("Ding Chime Before Question")
        self.chk_ding.setFocusPolicy(Qt.NoFocus)
        self.chk_ding.setChecked(True)
        right_layout.addWidget(self.chk_ding)

        self.chk_on_top = QCheckBox("Keep Window on Top")
        self.chk_on_top.setFocusPolicy(Qt.NoFocus)
        self.chk_on_top.toggled.connect(self._on_topmost_toggled)
        right_layout.addWidget(self.chk_on_top)

        right_layout.addStretch()

        # 底部操作按钮区（三层清晰视觉层级）
        self.btn_generate = QPushButton("▶ Generate Exam")
        self.btn_generate.setObjectName("btnPrimary")
        self.btn_generate.setFocusPolicy(Qt.NoFocus)
        self.btn_generate.clicked.connect(self.start_generation_flow)

        self.btn_quiz_or_cancel = QPushButton("■ Quiz Practice")
        self.btn_quiz_or_cancel.setObjectName("btnDark")
        self.btn_quiz_or_cancel.setFocusPolicy(Qt.NoFocus)
        self.btn_quiz_or_cancel.clicked.connect(self._on_dark_btn_clicked)

        sub_btn_row = QHBoxLayout()
        sub_btn_row.setSpacing(8)

        self.btn_guide_bottom = QPushButton("Step Guide")
        self.btn_guide_bottom.setObjectName("btnSecondary")
        self.btn_guide_bottom.setToolTip("查看从零生成听力材料的操作指引")
        self.btn_guide_bottom.setFocusPolicy(Qt.NoFocus)
        self.btn_guide_bottom.clicked.connect(self.open_workflow_guide)

        self.btn_edit_prompt = QPushButton("Edit Prompt")
        self.btn_edit_prompt.setObjectName("btnAccentOutline")
        self.btn_edit_prompt.setToolTip("自定义修改 AI 出题提示词模板")
        self.btn_edit_prompt.setFocusPolicy(Qt.NoFocus)
        self.btn_edit_prompt.clicked.connect(self.open_prompt_editor)

        sub_btn_row.addWidget(self.btn_guide_bottom, stretch=1)
        sub_btn_row.addWidget(self.btn_edit_prompt, stretch=1)

        right_layout.addWidget(self.btn_generate)
        right_layout.addWidget(self.btn_quiz_or_cancel)
        right_layout.addLayout(sub_btn_row)

        self.splitter.addWidget(left_widget)
        self.splitter.addWidget(right_panel)
        self.splitter.setSizes([600, 300])
        container_layout.addWidget(self.splitter)

        root_layout.addWidget(self.container)

    def _create_clean_spinbox(
        self, min_val: float, max_val: float, step: float, default: float, suffix: str
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setFocusPolicy(Qt.NoFocus)
        spin.setButtonSymbols(QAbstractSpinBox.PlusMinus)
        spin.setFixedWidth(104)
        spin.setRange(min_val, max_val)
        spin.setSingleStep(step)
        spin.setValue(default)
        spin.setSuffix(suffix)
        return spin

    def _collect_available_voices(self) -> list:
        voices = list(PRESET_VOICES)
        known_keys = {v.split(" ")[0] for v in voices}
        base_dir = get_project_root()
        for folder in (
            os.path.join(base_dir, "voices"),
            os.path.join(os.path.dirname(base_dir), "voices"),
            os.path.join(DEFAULT_KOKORO_ROOT, "voices"),
        ):
            if os.path.isdir(folder):
                for fname in sorted(os.listdir(folder)):
                    if fname.endswith(".pt"):
                        key = fname[:-3]
                        if key not in known_keys:
                            voices.append(f"{key} (Local Custom)")
                            known_keys.add(key)
        return voices

    def _quick_copy_prompt(self):
        QApplication.clipboard().setText(get_ai_prompt())
        self.lbl_status.setText("Prompt copied! Paste to AI + material.")

    def _paste_from_clipboard(self):
        text = QApplication.clipboard().text()
        if text:
            self.json_editor.setPlainText(text)
            self._validate_current_json()

    def _clear_json_editor(self):
        self.json_editor.clear()
        self.progress_bar.setValue(0)
        self.lbl_status.setText("Cleared.")

    def _validate_current_json(self) -> bool:
        raw = self.json_editor.toPlainText()
        try:
            data, warnings_list = parse_and_validate_exam_json(raw)
            self.json_editor.setPlainText(
                json.dumps(data, ensure_ascii=False, indent=2)
            )
            turns = len(data.get("dialogue", []))
            q_cnt = len(data.get("questions", []))
            if warnings_list:
                self.lbl_status.setText(
                    f"Valid ({turns} turns, {q_cnt} Qs) - Warning: {warnings_list[0]}"
                )
            else:
                self.lbl_status.setText(
                    f"Valid JSON: {turns} turns, {q_cnt} questions ready."
                )
            return True
        except ValueError as e:
            self.lbl_status.setText(f"Invalid JSON: {str(e).splitlines()[0]}")
            QMessageBox.warning(self, "JSON 校验未通过", str(e))
            return False

    def _on_dark_btn_clicked(self):
        if self.synth_thread.isRunning():
            self.synth_thread.cancel()
            self.lbl_status.setText("Cancelling...")
        else:
            self.open_quiz_panel()

    def start_generation_flow(self):
        if self.synth_thread.isRunning():
            return

        raw = self.json_editor.toPlainText()
        try:
            data, warnings_list = parse_and_validate_exam_json(raw)
        except ValueError as e:
            QMessageBox.warning(self, "无法开始合成", str(e))
            return

        if warnings_list:
            msg = "\n".join(f"- {w}" for w in warnings_list[:5])
            reply = QMessageBox.question(
                self,
                "语音规范化提醒",
                f"语音轨中发现未展开的数字/符号：\n\n{msg}\n\n是否继续合成？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return

        suggested_title = data.get("title", "cet6_practice")
        export_dlg = ExportSetupDialog(suggested_title=suggested_title, parent=self)
        if export_dlg.exec_() != QDialog.Accepted:
            return

        target_dir, safe_base_name = export_dlg.get_export_target()
        self._save_preferences()

        params = {
            "device": self.combo_device.currentData() or "auto",
            "voice_male": self.combo_male.currentText().split(" ")[0],
            "voice_female": self.combo_female.currentText().split(" ")[0],
            "voice_examiner": self.combo_examiner.currentText().split(" ")[0],
            "speech_rate": round(self.slider_speed.value() / 100.0, 2),
            "turn_gap_sec": self.spin_turn_gap.value(),
            "section_gap_sec": DEFAULT_CONFIG["section_gap_sec"],
            "pre_question_read_sec": self.spin_pre_sec.value(),
            "post_question_answer_sec": self.spin_post_sec.value(),
            "enable_ding_chime": self.chk_ding.isChecked(),
            "ding_volume": DEFAULT_CONFIG["ding_volume"],
        }

        self.pending_exam_data = data
        self.pending_target_dir = target_dir
        self.pending_base_name = safe_base_name
        self.pending_timing_meta = {
            "speech_rate": params["speech_rate"],
            "pre_question_read_sec": params["pre_question_read_sec"],
            "post_question_answer_sec": params["post_question_answer_sec"],
            "turn_gap_sec": params["turn_gap_sec"],
            "enable_ding_chime": params["enable_ding_chime"],
        }

        self.btn_generate.setEnabled(False)
        self.btn_generate.setText("▶ Synthesizing...")
        self.btn_quiz_or_cancel.setText("■ Cancel")
        self.progress_bar.setValue(0)
        self.lbl_status.setText("Starting Kokoro TTS...")

        self.synth_thread.start_task(data, params)

    def _on_synth_progress(self, pct: int, msg: str):
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    def _on_synth_finished(self, audio_np, elapsed_sec: float):
        try:
            paths = export_exam_package(
                data=self.pending_exam_data,
                audio_data=audio_np,
                target_dir=self.pending_target_dir,
                base_filename=self.pending_base_name,
                timing_meta=self.pending_timing_meta,
            )
            self.last_generated_dir = paths["dir"]
            dur_sec = len(audio_np) / SAMPLE_RATE
            m, s = divmod(int(dur_sec), 60)

            self.progress_bar.setValue(100)
            self.lbl_status.setText(
                f"Done in {elapsed_sec:.1f}s ({m:02d}:{s:02d})"
            )

            box = QMessageBox(self)
            box.setWindowTitle("生成成功")
            box.setText(
                f"考场音频与配套试卷已生成（时长 {m:02d}:{s:02d}）：\n\n"
                f"存放路径：{paths['dir']}\n"
                f"- 音频：{os.path.basename(paths['wav'])}\n"
                f"- 打印卷：print_sheet.md\n"
                f"- 解析卷：answer_key.md"
            )
            btn_quiz_now = box.addButton("进入刷题面板", QMessageBox.AcceptRole)
            btn_open_dir = box.addButton("打开文件夹", QMessageBox.ActionRole)
            box.addButton("关闭", QMessageBox.RejectRole)
            box.exec_()

            clicked = box.clickedButton()
            if clicked == btn_quiz_now:
                dlg = QuizPracticeDialog(initial_dir=self.last_generated_dir, parent=self)
                dlg.exec_()
            elif clicked == btn_open_dir and os.name == "nt":
                os.startfile(paths["dir"])

        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"写入文件时发生错误：\n{e}")
        finally:
            self._reset_synth_ui_state()

    def _on_synth_error(self, err_msg: str):
        self.lbl_status.setText("Synthesis Error.")
        self._reset_synth_ui_state()
        QMessageBox.critical(self, "语音合成错误", err_msg)

    def _reset_synth_ui_state(self):
        self.btn_generate.setEnabled(True)
        self.btn_generate.setText("▶ Generate Exam")
        self.btn_quiz_or_cancel.setText("■ Quiz Practice")

    def open_prompt_editor(self):
        dlg = PromptEditorDialog(parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self.lbl_status.setText("Custom prompt saved.")

    def open_workflow_guide(self):
        dlg = WorkflowGuideDialog(parent=self)
        dlg.exec_()

    def open_quiz_panel(self):
        dlg = QuizPracticeDialog(parent=self)
        if dlg.prompt_and_load_folder():
            dlg.exec_()

    def _on_topmost_toggled(self, checked: bool):
        flags = Qt.FramelessWindowHint | Qt.Window
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        self._save_preferences()

    def _load_preferences(self):
        w = self.settings.value("win_width", 910, type=int)
        h = self.settings.value("win_height", 615, type=int)
        self.resize(max(760, w), max(520, h))

        dev = self.settings.value("device", DEFAULT_CONFIG["device"], type=str)
        idx = self.combo_device.findData(dev)
        if idx >= 0:
            self.combo_device.setCurrentIndex(idx)

        for combo, key, default_val in (
            (self.combo_male, "voice_male", DEFAULT_CONFIG["voice_male"]),
            (self.combo_female, "voice_female", DEFAULT_CONFIG["voice_female"]),
            (self.combo_examiner, "voice_examiner", DEFAULT_CONFIG["voice_examiner"]),
        ):
            saved_v = self.settings.value(key, default_val, type=str)
            v_idx = combo.findText(saved_v, Qt.MatchStartsWith)
            if v_idx >= 0:
                combo.setCurrentIndex(v_idx)

        rate = self.settings.value("speech_rate", DEFAULT_CONFIG["speech_rate"], type=float)
        self.slider_speed.setValue(int(round(rate * 100)))

        self.spin_pre_sec.setValue(
            self.settings.value(
                "pre_question_read_sec", DEFAULT_CONFIG["pre_question_read_sec"], type=float
            )
        )
        self.spin_post_sec.setValue(
            self.settings.value(
                "post_question_answer_sec", DEFAULT_CONFIG["post_question_answer_sec"], type=float
            )
        )
        self.spin_turn_gap.setValue(
            self.settings.value(
                "turn_gap_sec", DEFAULT_CONFIG["turn_gap_sec"], type=float
            )
        )
        self.chk_ding.setChecked(
            self.settings.value(
                "enable_ding_chime", DEFAULT_CONFIG["enable_ding_chime"], type=bool
            )
        )
        on_top = self.settings.value(
            "always_on_top", DEFAULT_CONFIG["always_on_top"], type=bool
        )
        self.chk_on_top.setChecked(on_top)

    def _save_preferences(self):
        if not self.isMinimized() and not self.isMaximized():
            self.settings.setValue("win_width", self.width())
            self.settings.setValue("win_height", self.height())
        self.settings.setValue("device", self.combo_device.currentData())
        self.settings.setValue("voice_male", self.combo_male.currentText().split(" ")[0])
        self.settings.setValue("voice_female", self.combo_female.currentText().split(" ")[0])
        self.settings.setValue("voice_examiner", self.combo_examiner.currentText().split(" ")[0])
        self.settings.setValue("speech_rate", round(self.slider_speed.value() / 100.0, 2))
        self.settings.setValue("pre_question_read_sec", self.spin_pre_sec.value())
        self.settings.setValue("post_question_answer_sec", self.spin_post_sec.value())
        self.settings.setValue("turn_gap_sec", self.spin_turn_gap.value())
        self.settings.setValue("enable_ding_chime", self.chk_ding.isChecked())
        self.settings.setValue("always_on_top", self.chk_on_top.isChecked())
        self.settings.sync()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)

    def cleanup_resources(self):
        self._save_preferences()
        if self.synth_thread.isRunning():
            self.synth_thread.cancel()
            self.synth_thread.wait(1500)
        if hasattr(self, "local_server") and self.local_server:
            self.local_server.close()
            QLocalServer.removeServer(self.single_instance_key)
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.hide()

    def closeEvent(self, event):
        self._save_preferences()
        if hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)

    def quit_application(self):
        self.cleanup_resources()
        QApplication.quit()

    def mousePressEvent(self, event):
        if not self.handle_mouse_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.handle_mouse_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.handle_mouse_release(event)
        super().mouseReleaseEvent(event)