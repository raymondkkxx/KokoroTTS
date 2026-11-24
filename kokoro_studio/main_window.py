import ctypes
from ctypes import wintypes
import os
import re
import subprocess
import sys
import time

from PyQt5.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QPoint,
    QRect,
    QSettings,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QFontMetrics, QIcon, QPixmap, QTextCharFormat, QTextCursor
from PyQt5.QtNetwork import QLocalServer
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import sounddevice as sd
import torch

from kokoro_studio.audio_exporter import export_netease_mp3_file, resolve_unique_mp3_path
from kokoro_studio.config import (
    APP_ROOT_DIR,
    CORNER_MARGIN,
    DEFAULT_EXPORT_ARTIST,
    EDGE_MARGIN,
    PALETTE_BLACK,
    SAMPLE_RATE,
    SINGLE_INSTANCE_KEY,
    TRAY_ICON_PATH,
    build_main_stylesheet,
    build_text_edit_stylesheet,
    clamp_transparency,
    get_available_voices,
)
from kokoro_studio.http_server import HttpServerThread
from kokoro_studio.text_engine import (
    build_default_export_stem,
    build_timeline_segments,
    detect_is_pure_phonetic_input,
    parse_learning_material,
    sanitize_export_stem,
    sanitize_text,
    split_into_sentences,
)
from kokoro_studio.tts_engine import DrillSynthesizerThread, TtsSynthesizerThread
from kokoro_studio.widgets import (
    ClickableSlider,
    CyberHelpDialog,
    CyberTextEdit,
    DrillCardItem,
)
from kokoro_studio.win32_utils import (
    bring_window_to_foreground,
    dismiss_external_osd,
    register_taskbar_created_msg,
)


class CyberTtsPlayer(QWidget):
    ahk_text_signal = pyqtSignal(object)
    mode_change_signal = pyqtSignal(str)
    opacity_change_signal = pyqtSignal(str)

    def __init__(self, mutex_handle=None):
        super().__init__()
        self.mutex_handle = mutex_handle
        self._is_loading_prefs = False

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.setMinimumSize(720, 560)
        self.resize(940, 660)

        self.transparency = 30  # Default 30% Dark Glass Mode
        self.drag_pos = QPoint()
        self.drag_edge = None
        self.drag_start_geo = QRect()
        self.drag_start_pos = QPoint()
        self.is_slider_dragging = False
        self._override_cursor_shape = None

        self.click_count = 0
        self.last_click_time = 0.0

        self.current_audio = None
        self.timeline = []
        self.loop_segment = None
        self.active_highlight_range = None

        self.export_cover_path = TRAY_ICON_PATH
        self.export_dir = APP_ROOT_DIR
        self._custom_filename_edited = False

        self.play_start_time = 0.0
        self.play_offset_sec = 0.0
        self.total_duration = 0.0
        self.is_playing = False
        self.is_paused = False
        self.pause_offset_sec = 0.0
        self.current_loop = 1
        self.current_target_loops = 1

        self.last_clipboard_text = ""
        self.auto_read_timer = QTimer(self)
        self.auto_read_timer.setSingleShot(True)
        self.auto_read_timer.setInterval(200)
        self.auto_read_timer.timeout.connect(self.process_auto_clipboard_read)

        self.settings = QSettings("KokoroTTS", "WarmCleanPlayerV3")

        self.synth_thread = TtsSynthesizerThread()
        self.synth_thread.synth_finished.connect(self.on_synth_finished)
        self.synth_thread.synth_error.connect(self.on_synth_error)
        self.synth_thread.status_updated.connect(self.on_engine_status)

        self.drill_thread = DrillSynthesizerThread()
        self.drill_thread.drill_ready.connect(self.on_drill_audio_ready)

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(45)
        self.progress_timer.timeout.connect(self.update_progress_ui)

        self.msg_taskbar_created = register_taskbar_created_msg()
        self.init_ui()
        self.init_tray_icon()
        self.init_single_instance_ipc()
        self.init_file_watcher()
        self.init_clipboard_watcher()
        self.load_user_preferences()

        # Global event filter ensures reliable 4-corner & edge window resizing over child widgets
        app_inst = QApplication.instance()
        if app_inst is not None:
            app_inst.installEventFilter(self)

        self.ahk_text_signal.connect(self.on_ahk_text_received)
        self.mode_change_signal.connect(self.on_remote_mode_changed)
        self.opacity_change_signal.connect(self.on_remote_opacity_changed)
        self.http_thread = HttpServerThread(
            self.ahk_text_signal,
            self.mode_change_signal,
            self.opacity_change_signal,
        )
        self.http_thread.set_current_mode(
            "2" if self.combo_mode.currentData() == "auto_clipboard" else "1"
        )
        self.http_thread.set_current_transparency(self.transparency)
        self.http_thread.start()

    # ---------------------------------------------------------------------
    # Native Taskbar & Clipboard Watchers
    # ---------------------------------------------------------------------
    def nativeEvent(self, eventType, message):
        try:
            msg = wintypes.MSG.from_address(int(message))
            if self.msg_taskbar_created and msg.message == self.msg_taskbar_created:
                QTimer.singleShot(200, self.restore_tray_icon)
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def restore_tray_icon(self):
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.hide()
            self.tray_icon.show()

    def init_clipboard_watcher(self):
        self.clipboard = QApplication.clipboard()
        self.clipboard.dataChanged.connect(self.on_clipboard_data_changed)

    def on_clipboard_data_changed(self):
        if self.combo_mode.currentData() == "auto_clipboard":
            self.auto_read_timer.start()

    def process_auto_clipboard_read(self):
        try:
            raw_text = self.clipboard.text()
        except Exception:
            return

        will_be_phonetic = (
            detect_is_pure_phonetic_input(raw_text)
            if self.is_auto_ipa_enabled()
            else self.is_phonetic_mode()
        )
        letter_pattern = r"[^\W\d_]" if will_be_phonetic else r"[a-zA-Z]"
        if not raw_text or not re.search(letter_pattern, raw_text):
            return

        if raw_text.strip() == self.last_clipboard_text:
            return

        max_allowed_chars = self.spin_auto_limit.value()
        if len(raw_text) > max_allowed_chars:
            self.last_clipboard_text = raw_text.strip()
            self.tray_icon.showMessage(
                "Auto-read Suppressed",
                f"Copied text ({len(raw_text)} chars) exceeds threshold ({max_allowed_chars} chars).\nPaste manually into the player to read.",
                QSystemTrayIcon.Information,
                3500,
            )
            return

        self.last_clipboard_text = raw_text.strip()
        target_loops = self.get_loop_count_from_index(self.combo_clip_loop.currentIndex())
        is_prog = self.is_progressive_index(self.combo_clip_loop.currentIndex())
        self.ingest_learning_text(
            raw_text,
            auto_play=True,
            target_loops=target_loops,
            progressive=is_prog,
        )

    # ---------------------------------------------------------------------
    # Hot-Reload Watcher (Monitors Entry Script & All kokoro_studio Modules)
    # ---------------------------------------------------------------------
    def init_file_watcher(self):
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.setInterval(450)
        self.reload_timer.timeout.connect(self.reload_application)

        self.file_watcher = QFileSystemWatcher(self)
        watch_targets = [os.path.join(APP_ROOT_DIR, "tts_player.py")]
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        if os.path.exists(pkg_dir):
            for fname in os.listdir(pkg_dir):
                if fname.endswith(".py"):
                    watch_targets.append(os.path.join(pkg_dir, fname))

        for path in watch_targets:
            if os.path.exists(path):
                self.file_watcher.addPath(path)

        self.file_watcher.fileChanged.connect(self.on_source_file_changed)

    def on_source_file_changed(self, path):
        QTimer.singleShot(120, lambda: self.rearm_file_watcher(path))
        self.reload_timer.start()

    def rearm_file_watcher(self, path):
        if os.path.exists(path) and path not in self.file_watcher.files():
            self.file_watcher.addPath(path)

    def get_loop_count_from_index(self, index: int) -> int:
        return {0: 1, 1: 2, 2: 3, 3: 999999, 4: 1}.get(index, 1)

    def is_progressive_index(self, index: int) -> bool:
        return index == 4

    def is_phonetic_mode(self) -> bool:
        return hasattr(self, "btn_phonetic_mode") and self.btn_phonetic_mode.isChecked()

    def is_auto_ipa_enabled(self) -> bool:
        return hasattr(self, "btn_auto_ipa") and self.btn_auto_ipa.isChecked()

    # ---------------------------------------------------------------------
    # UI Layout Construction
    # ---------------------------------------------------------------------
    def init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        self.container = QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet(build_main_stylesheet(transparency=self.transparency))
        self.container.setMouseTracking(True)

        self.container_shadow = QGraphicsDropShadowEffect(self)
        self.container_shadow.setBlurRadius(28)
        self.container_shadow.setColor(QColor(24, 24, 27, 45))
        self.container_shadow.setOffset(0, 6)
        self.container_shadow.setEnabled(self.transparency == 0)
        self.container.setGraphicsEffect(self.container_shadow)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(16, 12, 16, 16)
        container_layout.setSpacing(8)

        # Top Action Bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 2, 0)
        top_bar.setSpacing(6)

        self.btn_phonetic_mode = QPushButton("IPA: OFF")
        self.btn_phonetic_mode.setObjectName("btnPhoneticMode")
        self.btn_phonetic_mode.setCheckable(True)
        self.btn_phonetic_mode.setChecked(False)
        self.btn_phonetic_mode.setFocusPolicy(Qt.NoFocus)
        self.btn_phonetic_mode.setToolTip(
            "Phonetic Reading Mode: OFF (Normal English + Inline [IPA] Support)\n"
            "Click to switch to Full IPA Phoneme Mode"
        )
        self.btn_phonetic_mode.toggled.connect(self.on_phonetic_mode_toggled)
        top_bar.addWidget(self.btn_phonetic_mode)

        self.btn_auto_ipa = QPushButton("✨ Auto: ON")
        self.btn_auto_ipa.setObjectName("btnAutoIpa")
        self.btn_auto_ipa.setCheckable(True)
        self.btn_auto_ipa.setChecked(True)
        self.btn_auto_ipa.setFocusPolicy(Qt.NoFocus)
        self.btn_auto_ipa.setToolTip(
            "Auto-Detect IPA Mode: ON\n"
            "• Starts with '[' or '\\'' -> Switches to IPA: ON;\n"
            "• Normal text (including inline [IPA] annotations) -> Uses IPA: OFF"
        )
        self.btn_auto_ipa.toggled.connect(self.on_auto_ipa_toggled)
        top_bar.addWidget(self.btn_auto_ipa)

        self.btn_exit_loop = QPushButton("🔁 Single-Sentence Loop (Click or Esc to Exit)")
        self.btn_exit_loop.setObjectName("btnExitLoop")
        self.btn_exit_loop.setFocusPolicy(Qt.NoFocus)
        self.btn_exit_loop.setVisible(False)
        self.btn_exit_loop.clicked.connect(self.clear_sentence_loop)
        top_bar.addWidget(self.btn_exit_loop)

        top_bar.addStretch()

        btn_clear = QPushButton("Clear")
        btn_clear.setObjectName("btnTopAction")
        btn_clear.setToolTip("Clear Text & Drill Cards")
        btn_clear.setFocusPolicy(Qt.NoFocus)
        btn_clear.clicked.connect(self.clear_all_content)

        btn_reset = QPushButton("Reset")
        btn_reset.setObjectName("btnTopAction")
        btn_reset.setToolTip(
            "Restore All Settings to Default (Auto: ON, 30% Glass, Default Cover & Folder)"
        )
        btn_reset.setFocusPolicy(Qt.NoFocus)
        btn_reset.clicked.connect(self.reset_to_defaults)

        btn_reload = QPushButton("↻")
        btn_reload.setObjectName("btnTopAction")
        btn_reload.setToolTip("Reload Application (Ctrl+R)")
        btn_reload.setFocusPolicy(Qt.NoFocus)
        btn_reload.clicked.connect(self.reload_application)

        btn_help = QPushButton("?")
        btn_help.setObjectName("btnTopAction")
        btn_help.setToolTip("Show Help Manual")
        btn_help.setFocusPolicy(Qt.NoFocus)
        btn_help.clicked.connect(self.show_help_dialog)

        btn_min = QPushButton("—")
        btn_min.setObjectName("btnTopAction")
        btn_min.setToolTip("Minimize to Tray (Esc)")
        btn_min.setFocusPolicy(Qt.NoFocus)
        btn_min.clicked.connect(self.hide)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("btnTopAction")
        btn_close.setToolTip("Close to Tray")
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.setStyleSheet(
            "QPushButton#btnTopAction:hover { background-color: rgba(225, 29, 72, 24); color: #E11D48; }"
        )
        btn_close.clicked.connect(self.hide)

        top_bar.addWidget(btn_clear)
        top_bar.addWidget(btn_reset)
        top_bar.addWidget(btn_reload)
        top_bar.addWidget(btn_help)
        top_bar.addWidget(btn_min)
        top_bar.addWidget(btn_close)
        container_layout.addLayout(top_bar)

        # Horizontal Splitter (Left Study Workspace | Right Control Panel)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(8)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.splitterMoved.connect(
            lambda pos, index: self.save_user_preferences()
        )

        # Left Text Input & 2-Column Drill Area
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 2, 0)
        left_layout.setSpacing(8)

        self.left_splitter = QSplitter(Qt.Vertical)
        self.left_splitter.setHandleWidth(7)
        self.left_splitter.setChildrenCollapsible(False)

        self.text_edit = CyberTextEdit(self)
        self.text_edit.setPlaceholderText(
            "Paste English dialogue, annotated study guides, or trap tables here...\n"
            "• Supports W:/M: dual voices, **stress**, (silent), inline word [IPA], ‿ linking, and / pauses\n"
            "• Double-click any sentence to loop it; select any phrase + Alt+P to drill-read"
        )
        self.text_edit.textChanged.connect(self.on_text_changed)
        self.text_edit.sentence_double_clicked.connect(self.on_sentence_double_clicked)
        self.text_edit.selection_read_requested.connect(self.play_drill_phrase)

        # 2-Column Drill Cards Panel
        self.drill_frame = QFrame()
        self.drill_frame.setObjectName("drillBarFrame")
        self.drill_frame.setVisible(False)
        drill_frame_layout = QVBoxLayout(self.drill_frame)
        drill_frame_layout.setContentsMargins(12, 9, 12, 10)
        drill_frame_layout.setSpacing(7)

        drill_header = QHBoxLayout()
        drill_header.setContentsMargins(2, 0, 2, 0)
        drill_header.setSpacing(8)

        lbl_drill_title = QLabel("DRILLS · 雷区词块 (点击 0.85x 慢读)")
        lbl_drill_title.setObjectName("drillHeaderTitle")

        self.lbl_drill_count = QLabel("0")
        self.lbl_drill_count.setObjectName("drillCountBadge")

        drill_header.addWidget(lbl_drill_title)
        drill_header.addWidget(self.lbl_drill_count)
        drill_header.addStretch()
        drill_frame_layout.addLayout(drill_header)

        self.drill_scroll = QScrollArea()
        self.drill_scroll.setWidgetResizable(True)
        self.drill_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.drill_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.drill_scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.drill_container = QWidget()
        self.drill_container.setStyleSheet("background: transparent;")
        self.drill_grid = QGridLayout(self.drill_container)
        self.drill_grid.setContentsMargins(0, 0, 4, 0)
        self.drill_grid.setHorizontalSpacing(8)
        self.drill_grid.setVerticalSpacing(6)
        self.drill_grid.setColumnStretch(0, 1)
        self.drill_grid.setColumnStretch(1, 1)

        self.drill_scroll.setWidget(self.drill_container)
        drill_frame_layout.addWidget(self.drill_scroll)

        self.left_splitter.addWidget(self.text_edit)
        self.left_splitter.addWidget(self.drill_frame)
        self.left_splitter.setStretchFactor(0, 3)
        self.left_splitter.setStretchFactor(1, 2)

        # Progress Slider Row
        progress_bar_layout = QHBoxLayout()
        progress_bar_layout.setSpacing(12)

        self.slider_progress = ClickableSlider(Qt.Horizontal)
        self.slider_progress.setRange(0, 1000)
        self.slider_progress.setValue(0)
        self.slider_progress.setEnabled(False)
        self.slider_progress.setFocusPolicy(Qt.NoFocus)
        self.slider_progress.sliderPressed.connect(self.on_slider_pressed)
        self.slider_progress.sliderReleased.connect(self.on_slider_released)
        self.slider_progress.jump_requested.connect(self.on_slider_jump)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setObjectName("lblTimeStatus")

        progress_bar_layout.addWidget(self.slider_progress)
        progress_bar_layout.addWidget(self.lbl_time)

        # Bottom Meta & Speed Row
        meta_bar = QHBoxLayout()
        meta_bar.setSpacing(8)

        self.lbl_stats = QLabel("Words: 0 | Chars: 0")
        self.lbl_stats.setObjectName("lblHighlightStats")

        self.chk_split = QCheckBox("Sentence Split")
        self.chk_split.setFocusPolicy(Qt.NoFocus)
        self.chk_split.toggled.connect(self.on_split_mode_toggled)

        btn_speed_075 = QPushButton("0.75x")
        btn_speed_075.setObjectName("quickSpeedBtn")
        btn_speed_075.setFocusPolicy(Qt.NoFocus)
        btn_speed_075.clicked.connect(lambda: self.slider_speed.setValue(7))

        btn_speed_10 = QPushButton("1.0x")
        btn_speed_10.setObjectName("quickSpeedBtn")
        btn_speed_10.setFocusPolicy(Qt.NoFocus)
        btn_speed_10.clicked.connect(lambda: self.slider_speed.setValue(10))

        btn_speed_125 = QPushButton("1.25x")
        btn_speed_125.setObjectName("quickSpeedBtn")
        btn_speed_125.setFocusPolicy(Qt.NoFocus)
        btn_speed_125.clicked.connect(lambda: self.slider_speed.setValue(13))

        meta_bar.addWidget(self.lbl_stats)
        meta_bar.addSpacing(8)
        meta_bar.addWidget(self.chk_split)
        meta_bar.addStretch()
        meta_bar.addWidget(btn_speed_075)
        meta_bar.addWidget(btn_speed_10)
        meta_bar.addWidget(btn_speed_125)

        left_layout.addWidget(self.left_splitter, 1)
        left_layout.addLayout(progress_bar_layout)
        left_layout.addLayout(meta_bar)

        # Right Control Panel
        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_panel.setMinimumWidth(280)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(14, 11, 14, 11)
        right_layout.setSpacing(5)

        # 1. Operation Mode (Default: Mode 2 Auto Clipboard)
        mode_box = QHBoxLayout()
        lbl_mode = QLabel("Mode:")
        self.combo_mode = QComboBox()
        self.combo_mode.setFocusPolicy(Qt.NoFocus)
        self.combo_mode.addItem("Mode 1: Manual (Click / Push)", "manual")
        self.combo_mode.addItem("Mode 2: Auto Clipboard (Direct)", "auto_clipboard")
        self.combo_mode.setCurrentIndex(1)
        self.combo_mode.setToolTip(
            "[Mode 1]: Read on click or selection push.\n"
            "[Mode 2]: Automatically read newly copied English text or IPA."
        )
        self.combo_mode.currentIndexChanged.connect(self.on_mode_changed)
        mode_box.addWidget(lbl_mode)
        mode_box.addWidget(self.combo_mode, 1)

        # 2. Auto-read Limit
        auto_limit_box = QHBoxLayout()
        lbl_auto_limit = QLabel("Auto Max:")
        self.spin_auto_limit = QSpinBox()
        self.spin_auto_limit.setFocusPolicy(Qt.NoFocus)
        self.spin_auto_limit.setRange(50, 5000)
        self.spin_auto_limit.setValue(1000)
        self.spin_auto_limit.setSingleStep(50)
        self.spin_auto_limit.setSuffix(" chars")
        self.spin_auto_limit.setToolTip(
            "Maximum character limit for auto-reading in Mode 2.\n"
            "Exceeding text is blocked to prevent lag. Manual paste is unlimited."
        )
        self.spin_auto_limit.valueChanged.connect(self.save_user_preferences)
        auto_limit_box.addWidget(lbl_auto_limit)
        auto_limit_box.addWidget(self.spin_auto_limit, 1)

        # 3. Always On Top
        self.chk_always_visible = QCheckBox("Keep Window on Top")
        self.chk_always_visible.setFocusPolicy(Qt.NoFocus)
        self.chk_always_visible.setToolTip(
            "Checked: Keep window visible and pinned on top.\n"
            "Unchecked: Background silent mode; audio plays without window popup."
        )
        self.chk_always_visible.toggled.connect(self.on_always_visible_toggled)

        # 4. Device Selector
        device_box = QHBoxLayout()
        lbl_device = QLabel("Device:")
        self.combo_device = QComboBox()
        self.combo_device.setFocusPolicy(Qt.NoFocus)

        cuda_available = torch.cuda.is_available()
        gpu_name = (
            f"GPU ({torch.cuda.get_device_name(0)})"
            if cuda_available
            else "GPU (CUDA Unavailable)"
        )
        self.combo_device.addItem(gpu_name, "cuda")
        self.combo_device.addItem("CPU", "cpu")
        self.combo_device.currentIndexChanged.connect(self.on_device_changed)

        device_box.addWidget(lbl_device)
        device_box.addWidget(self.combo_device, 1)

        # 5. Preset Voices (Female/Primary + Male for W:/M: Dialogue)
        preset_voices = get_available_voices()

        voice_w_box = QHBoxLayout()
        lbl_voice_w = QLabel("Voice W:")
        lbl_voice_w.setToolTip("Primary Voice / Female speaker (W:) voice")
        self.combo_voice = QComboBox()
        self.combo_voice.setFocusPolicy(Qt.NoFocus)
        self.combo_voice.addItems(preset_voices)
        self.combo_voice.currentIndexChanged.connect(self.on_primary_voice_changed)
        voice_w_box.addWidget(lbl_voice_w)
        voice_w_box.addWidget(self.combo_voice, 1)

        voice_m_box = QHBoxLayout()
        lbl_voice_m = QLabel("Voice M:")
        lbl_voice_m.setToolTip("Male speaker (M:) voice in two-person dialogues")
        self.combo_male_voice = QComboBox()
        self.combo_male_voice.setFocusPolicy(Qt.NoFocus)
        self.combo_male_voice.addItems(preset_voices)
        default_m_idx = self.combo_male_voice.findText("am_michael", Qt.MatchStartsWith)
        if default_m_idx >= 0:
            self.combo_male_voice.setCurrentIndex(default_m_idx)
        self.combo_male_voice.currentIndexChanged.connect(self.save_user_preferences)
        voice_m_box.addWidget(lbl_voice_m)
        voice_m_box.addWidget(self.combo_male_voice, 1)

        # Speed Slider
        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setFocusPolicy(Qt.NoFocus)
        self.slider_speed.setRange(5, 20)
        self.slider_speed.setValue(10)
        self.slider_speed.setToolTip("Speed: 1.0x")
        self.slider_speed.valueChanged.connect(self.on_speed_changed)

        # Shadowing & Pause
        shadow_row = QHBoxLayout()
        self.chk_shadowing = QCheckBox("Shadowing")
        self.chk_shadowing.setFocusPolicy(Qt.NoFocus)
        self.chk_shadowing.setToolTip("Insert silence gap after each sentence for shadowing practice")
        self.chk_shadowing.toggled.connect(self.save_user_preferences)

        self.spin_pause = QSpinBox()
        self.spin_pause.setFocusPolicy(Qt.NoFocus)
        self.spin_pause.setRange(1, 5)
        self.spin_pause.setValue(2)
        self.spin_pause.setSuffix(" s")
        self.spin_pause.setToolTip("Shadowing pause duration in seconds")
        self.spin_pause.valueChanged.connect(self.save_user_preferences)

        shadow_row.addWidget(self.chk_shadowing)
        shadow_row.addStretch()
        shadow_row.addWidget(self.spin_pause)

        # 6. Loop Counts
        loop_options = [
            "1x Once",
            "2x Repeat",
            "3x Repeat",
            "Loop (∞)",
            "Progressive (0.75x→1x)",
        ]

        push_loop_box = QHBoxLayout()
        lbl_push_loop = QLabel("Push Loop:")
        self.combo_push_loop = QComboBox()
        self.combo_push_loop.setFocusPolicy(Qt.NoFocus)
        self.combo_push_loop.addItems(loop_options)
        self.combo_push_loop.setCurrentIndex(2)
        self.combo_push_loop.setToolTip("Loop mode for manual playback and push")
        self.combo_push_loop.currentIndexChanged.connect(self.save_user_preferences)
        push_loop_box.addWidget(lbl_push_loop)
        push_loop_box.addWidget(self.combo_push_loop, 1)

        clip_loop_box = QHBoxLayout()
        lbl_clip_loop = QLabel("Clip Loop:")
        self.combo_clip_loop = QComboBox()
        self.combo_clip_loop.setFocusPolicy(Qt.NoFocus)
        self.combo_clip_loop.addItems(loop_options)
        self.combo_clip_loop.setCurrentIndex(0)
        self.combo_clip_loop.setToolTip("Loop mode for Mode 2 clipboard auto-read")
        self.combo_clip_loop.currentIndexChanged.connect(self.save_user_preferences)
        clip_loop_box.addWidget(lbl_clip_loop)
        clip_loop_box.addWidget(self.combo_clip_loop, 1)

        # 7. Font Size
        font_size_box = QHBoxLayout()
        lbl_font_size = QLabel("Size:")
        self.spin_font_size = QSpinBox()
        self.spin_font_size.setFocusPolicy(Qt.NoFocus)
        self.spin_font_size.setRange(12, 48)
        self.spin_font_size.setValue(18)
        self.spin_font_size.setSuffix(" px")
        self.spin_font_size.valueChanged.connect(self.on_font_size_changed)
        font_size_box.addWidget(lbl_font_size)
        font_size_box.addWidget(self.spin_font_size, 1)

        # 8. Adjustable Window Transparency (Default: 30% Dark Glass Mode)
        opacity_box = QHBoxLayout()
        opacity_box.setSpacing(8)
        lbl_opacity = QLabel("Transp.:")
        lbl_opacity.setToolTip(
            "Adjust glass transparency (0% = Light Mode, 1%–95% = Dark Glass, or Ctrl+Up/Down)"
        )

        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setFocusPolicy(Qt.NoFocus)
        self.slider_opacity.setRange(0, 95)
        self.slider_opacity.setValue(30)
        self.slider_opacity.setToolTip("Transparency: 30% Dark Glass")
        self.slider_opacity.valueChanged.connect(self.on_transparency_changed)

        self.lbl_opacity_val = QLabel("30%")
        self.lbl_opacity_val.setObjectName("lblHighlightStats")
        self.lbl_opacity_val.setFixedWidth(36)
        self.lbl_opacity_val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        opacity_box.addWidget(lbl_opacity)
        opacity_box.addWidget(self.slider_opacity, 1)
        opacity_box.addWidget(self.lbl_opacity_val)

        # 9. MP3 Export Cover Selector (Thumbnail + Filename + Choose + Reset)
        cover_box = QHBoxLayout()
        cover_box.setSpacing(5)
        lbl_cover = QLabel("Cover:")
        lbl_cover.setFixedWidth(48)

        self.lbl_cover_thumb = QLabel()
        self.lbl_cover_thumb.setObjectName("lblCoverThumb")
        self.lbl_cover_thumb.setFixedSize(24, 24)
        self.lbl_cover_thumb.setAlignment(Qt.AlignCenter)

        self.lbl_cover_name = QLabel("tts.png")
        self.lbl_cover_name.setObjectName("lblExportInfo")
        self.lbl_cover_name.setMinimumWidth(60)

        self.btn_choose_cover = QPushButton("📂")
        self.btn_choose_cover.setObjectName("btnMiniTool")
        self.btn_choose_cover.setFixedSize(28, 24)
        self.btn_choose_cover.setFocusPolicy(Qt.NoFocus)
        self.btn_choose_cover.setToolTip("Select default MP3 cover image")
        self.btn_choose_cover.clicked.connect(self.choose_export_cover)

        self.btn_reset_cover = QPushButton("↺")
        self.btn_reset_cover.setObjectName("btnMiniTool")
        self.btn_reset_cover.setFixedSize(26, 24)
        self.btn_reset_cover.setFocusPolicy(Qt.NoFocus)
        self.btn_reset_cover.setToolTip("Reset cover to default tts.png")
        self.btn_reset_cover.clicked.connect(self.reset_export_cover)

        cover_box.addWidget(lbl_cover)
        cover_box.addWidget(self.lbl_cover_thumb)
        cover_box.addWidget(self.lbl_cover_name, 1)
        cover_box.addWidget(self.btn_choose_cover)
        cover_box.addWidget(self.btn_reset_cover)

        # 10. MP3 Default Save Folder Selector
        folder_box = QHBoxLayout()
        folder_box.setSpacing(5)
        lbl_folder = QLabel("Folder:")
        lbl_folder.setFixedWidth(48)

        self.lbl_export_dir = QLabel(APP_ROOT_DIR)
        self.lbl_export_dir.setObjectName("lblExportInfo")
        self.lbl_export_dir.setMinimumWidth(80)

        self.btn_choose_dir = QPushButton("📂")
        self.btn_choose_dir.setObjectName("btnMiniTool")
        self.btn_choose_dir.setFixedSize(28, 24)
        self.btn_choose_dir.setFocusPolicy(Qt.NoFocus)
        self.btn_choose_dir.setToolTip("Choose default MP3 export folder")
        self.btn_choose_dir.clicked.connect(self.choose_export_directory)

        folder_box.addWidget(lbl_folder)
        folder_box.addWidget(self.lbl_export_dir, 1)
        folder_box.addWidget(self.btn_choose_dir)

        # 11. MP3 Initial Filename Input (Editable, auto-populated from text)
        name_box = QHBoxLayout()
        name_box.setSpacing(5)
        lbl_name = QLabel("Name:")
        lbl_name.setFixedWidth(48)

        self.edit_export_name = QLineEdit()
        self.edit_export_name.setObjectName("editExportName")
        self.edit_export_name.setPlaceholderText("kokoro_speech")
        self.edit_export_name.setToolTip(
            "Initial MP3 filename (auto-generated from text; click to customize).\n"
            "Duplicate filenames are numbered automatically."
        )
        self.edit_export_name.textEdited.connect(self.on_export_name_edited)

        lbl_ext = QLabel(".mp3")
        lbl_ext.setObjectName("lblExportExt")

        name_box.addWidget(lbl_name)
        name_box.addWidget(self.edit_export_name, 1)
        name_box.addWidget(lbl_ext)

        # Action Buttons
        self.btn_play = QPushButton("▶ Read Text")
        self.btn_play.setObjectName("btnPlay")
        self.btn_play.setFocusPolicy(Qt.NoFocus)
        self.btn_play.clicked.connect(
            lambda: self.play_audio(
                target_loops=self.get_loop_count_from_index(
                    self.combo_push_loop.currentIndex()
                ),
                progressive=self.is_progressive_index(
                    self.combo_push_loop.currentIndex()
                ),
            )
        )

        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setObjectName("btnStop")
        self.btn_stop.setFocusPolicy(Qt.NoFocus)
        self.btn_stop.clicked.connect(self.toggle_stop_or_resume)
        self.btn_stop.setEnabled(False)

        self.btn_export_netease = QPushButton("🎵 Export MP3")
        self.btn_export_netease.setObjectName("btnNetease")
        self.btn_export_netease.setFocusPolicy(Qt.NoFocus)
        self.btn_export_netease.setToolTip(
            "Export MP3 directly to the configured folder with embedded LRC lyrics & cover (Ctrl+S)\n"
            "• <5s: 3x loop | <20s: 2x loop | >=20s: 1x loop\n"
            "• Auto-numbers duplicate filenames"
        )
        self.btn_export_netease.clicked.connect(self.export_netease_mp3)

        right_layout.addLayout(mode_box)
        right_layout.addLayout(auto_limit_box)
        right_layout.addWidget(self.chk_always_visible)
        right_layout.addLayout(device_box)
        right_layout.addLayout(voice_w_box)
        right_layout.addLayout(voice_m_box)
        right_layout.addWidget(self.slider_speed)
        right_layout.addLayout(shadow_row)
        right_layout.addLayout(push_loop_box)
        right_layout.addLayout(clip_loop_box)
        right_layout.addLayout(font_size_box)
        right_layout.addLayout(opacity_box)
        right_layout.addLayout(cover_box)
        right_layout.addLayout(folder_box)
        right_layout.addLayout(name_box)
        right_layout.addStretch()
        right_layout.addWidget(self.btn_play)
        right_layout.addWidget(self.btn_stop)
        right_layout.addWidget(self.btn_export_netease)

        self.splitter.addWidget(left_widget)
        self.splitter.addWidget(right_panel)
        container_layout.addWidget(self.splitter)

        root_layout.addWidget(self.container)

    # ---------------------------------------------------------------------
    # MP3 Export Cover, Folder & Filename Helpers
    # ---------------------------------------------------------------------
    def _refresh_cover_ui(self):
        cover_path = self.export_cover_path
        if not cover_path or not os.path.isfile(cover_path):
            cover_path = TRAY_ICON_PATH
            self.export_cover_path = cover_path

        fname = os.path.basename(cover_path) if cover_path else "tts.png"
        fm = QFontMetrics(self.lbl_cover_name.font())
        self.lbl_cover_name.setText(fm.elidedText(fname, Qt.ElideMiddle, 115))

        tooltip = f"Current Cover: {cover_path}"
        if os.path.isfile(cover_path):
            pix = QPixmap(cover_path)
            if not pix.isNull():
                tooltip += f"\nResolution: {pix.width()}×{pix.height()}"
                scaled = pix.scaled(
                    20, 20, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.lbl_cover_thumb.setPixmap(scaled)
            else:
                self.lbl_cover_thumb.clear()
                self.lbl_cover_thumb.setText("🖼")
        else:
            self.lbl_cover_thumb.clear()
            self.lbl_cover_thumb.setText("🖼")

        self.lbl_cover_thumb.setToolTip(tooltip)
        self.lbl_cover_name.setToolTip(tooltip)

    def _refresh_export_dir_ui(self):
        target_dir = self.export_dir
        if not target_dir or not os.path.isdir(target_dir):
            target_dir = APP_ROOT_DIR
            self.export_dir = target_dir

        fm = QFontMetrics(self.lbl_export_dir.font())
        self.lbl_export_dir.setText(fm.elidedText(target_dir, Qt.ElideLeft, 155))
        self.lbl_export_dir.setToolTip(f"Default Export Folder:\n{target_dir}")

    def choose_export_cover(self):
        start_dir = (
            os.path.dirname(self.export_cover_path)
            if self.export_cover_path and os.path.exists(os.path.dirname(self.export_cover_path))
            else APP_ROOT_DIR
        )
        img_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Default MP3 Cover Image",
            start_dir,
            "Image Files (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not img_path:
            return

        self.export_cover_path = os.path.normpath(img_path)
        self._refresh_cover_ui()
        self.save_user_preferences()

    def reset_export_cover(self):
        self.export_cover_path = TRAY_ICON_PATH
        self._refresh_cover_ui()
        self.save_user_preferences()

    def choose_export_directory(self):
        start_dir = (
            self.export_dir
            if self.export_dir and os.path.isdir(self.export_dir)
            else APP_ROOT_DIR
        )
        chosen_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Default MP3 Export Folder",
            start_dir,
        )
        if not chosen_dir:
            return

        self.export_dir = os.path.normpath(chosen_dir)
        self._refresh_export_dir_ui()
        self.save_user_preferences()

    def on_export_name_edited(self, _text: str):
        self._custom_filename_edited = True

    def _update_auto_export_stem(self, force: bool = False):
        if not hasattr(self, "edit_export_name"):
            return
        if not force and self._custom_filename_edited:
            return

        visible_text = self.text_edit.toPlainText().strip() if hasattr(self, "text_edit") else ""
        stem = build_default_export_stem(
            visible_text=visible_text,
            timeline=self.timeline,
            phonetic_mode=self.is_phonetic_mode(),
        )
        self.edit_export_name.blockSignals(True)
        self.edit_export_name.setText(stem)
        self.edit_export_name.blockSignals(False)
        if force:
            self._custom_filename_edited = False

    def open_file_in_explorer(self, file_path: str):
        norm_path = os.path.normpath(file_path)
        try:
            if os.path.exists(norm_path):
                subprocess.Popen(["explorer", "/select,", norm_path])
            elif os.path.isdir(os.path.dirname(norm_path)):
                os.startfile(os.path.dirname(norm_path))
        except Exception:
            pass

    # ---------------------------------------------------------------------
    # Phonetic Reading Mode & Auto-Detect Toggles
    # ---------------------------------------------------------------------
    def on_phonetic_mode_toggled(self, checked: bool):
        if checked:
            self.btn_phonetic_mode.setText("🔊 IPA: ON")
            self.btn_phonetic_mode.setToolTip(
                "Phonetic Reading Mode: ON\n"
                "Auto-converts dictionary IPA (e.g. [ˈhaʊdʒə ˈfiːləˌbaʊt]) into native Kokoro phonemes"
            )
            self.text_edit.setPlaceholderText(
                "Phonetic Mode ON — Paste IPA phonemes directly...\n"
                "• Auto-converts dictionary IPA ([ˈhaʊdʒə ˈfiːləˌbaʊt]), slashes (/.../), and W:/M: speakers\n"
                "• Automatically shifts syllable stress markers to vowels and maps diphthongs"
            )
        else:
            self.btn_phonetic_mode.setText("IPA: OFF")
            self.btn_phonetic_mode.setToolTip(
                "Phonetic Reading Mode: OFF (Normal English + Inline [IPA] Support)\n"
                "Click to switch to Full IPA Phoneme Mode"
            )
            self.text_edit.setPlaceholderText(
                "Paste English dialogue, annotated study guides, or trap tables here...\n"
                "• Supports W:/M: dual voices, **stress**, (silent), inline word [IPA], ‿ linking, and / pauses\n"
                "• Double-click any sentence to loop it; select any phrase + Alt+P to drill-read"
            )

        self.on_text_changed()
        self.save_user_preferences()

    def on_auto_ipa_toggled(self, checked: bool):
        if checked:
            self.btn_auto_ipa.setText("✨ Auto: ON")
            self.btn_auto_ipa.setToolTip(
                "Auto-Detect IPA Mode: ON\n"
                "• Starts with '[' or '\\'' -> Switches to IPA: ON;\n"
                "• Normal text (including inline [IPA] annotations) -> Uses IPA: OFF"
            )
            current_text = self.text_edit.toPlainText().strip()
            if current_text:
                self.btn_phonetic_mode.setChecked(
                    detect_is_pure_phonetic_input(current_text)
                )
        else:
            self.btn_auto_ipa.setText("Auto: OFF")
            self.btn_auto_ipa.setToolTip(
                "Auto-Detect IPA Mode: OFF\n"
                "Click to enable automatic IPA mode detection when pasting text"
            )
        self.save_user_preferences()

    def on_speed_changed(self, value: int):
        self.slider_speed.setToolTip(f"Speed: {value / 10.0:.1f}x")
        self.save_user_preferences()

    # ---------------------------------------------------------------------
    # Adjustable Transparency & Light/Dark Glass Theme Handlers
    # ---------------------------------------------------------------------
    def on_transparency_changed(self, value: int):
        t = clamp_transparency(value)
        self.transparency = t
        is_dark = t > 0

        self.lbl_opacity_val.setText(f"{t}%")
        mode_desc = f"{t}% Dark Glass" if is_dark else "0% Opaque Light"
        self.slider_opacity.setToolTip(f"Transparency: {mode_desc}")

        if hasattr(self, "container_shadow"):
            self.container_shadow.setEnabled(not is_dark)

        self.container.setStyleSheet(build_main_stylesheet(transparency=t))
        font_sz = self.spin_font_size.value() if hasattr(self, "spin_font_size") else 18
        self.text_edit.setStyleSheet(
            build_text_edit_stylesheet(font_sz, transparency=t)
        )
        self.text_edit.refresh_theme(font_size=font_sz, is_dark=is_dark)

        if hasattr(self, "http_thread") and self.http_thread:
            self.http_thread.set_current_transparency(t)

        self.save_user_preferences()

    def on_remote_opacity_changed(self, payload_str: str):
        try:
            s = payload_str.strip()
            if s.startswith("T:"):
                raw_val = float(s[2:])
                t = int(round(raw_val * 100)) if 0 < raw_val < 1.0 else int(round(raw_val))
            elif s.startswith("O:"):
                raw_val = float(s[2:])
                op_pct = int(round(raw_val * 100)) if raw_val <= 1.0 else int(round(raw_val))
                t = 100 - op_pct
            else:
                raw_val = float(s)
                t = int(round(raw_val * 100)) if 0 < raw_val < 1.0 else int(round(raw_val))
            self.slider_opacity.setValue(clamp_transparency(t))
        except ValueError:
            pass

    # ---------------------------------------------------------------------
    # Learning Material Ingestion, 2-Column Drill Cards & Sentence Loop Helpers
    # ---------------------------------------------------------------------
    def set_plain_editor_text(self, text: str):
        self.text_edit.blockSignals(True)
        self.text_edit.is_rich_annotated = False
        self.text_edit._raw_annotated_text = ""
        self.text_edit.setPlainText(text)
        self.text_edit.blockSignals(False)
        self.on_text_changed()

    def ingest_learning_text(
        self, raw_payload, auto_play=False, target_loops=None, progressive=False
    ):
        if self.is_auto_ipa_enabled():
            detected_ipa = detect_is_pure_phonetic_input(raw_payload)
            if self.btn_phonetic_mode.isChecked() != detected_ipa:
                self.btn_phonetic_mode.setChecked(detected_ipa)

        if self.is_phonetic_mode():
            if isinstance(raw_payload, dict):
                raw_str = raw_payload.get("dialogue") or raw_payload.get("text") or ""
            else:
                raw_str = str(raw_payload or "")
            lines = [ln.strip() for ln in raw_str.splitlines() if ln.strip()]
            display_text = "\n".join(lines)

            if self.chk_split.isChecked() and display_text:
                expanded_lines = []
                for line in display_text.splitlines():
                    sents = split_into_sentences(line)
                    expanded_lines.extend(sents if sents else [line])
                display_text = "\n\n".join(expanded_lines)

            self.clear_sentence_loop()
            self.clear_active_highlight()
            self.populate_drill_cards([])

            if display_text:
                self.set_plain_editor_text(display_text)
                self._update_auto_export_stem(force=True)

            if auto_play and display_text:
                self.play_audio(target_loops=target_loops, progressive=progressive)
            return

        parsed = parse_learning_material(raw_payload)
        display_text = parsed["display_text"]
        drills = parsed["drills"]

        if self.chk_split.isChecked() and display_text:
            expanded_lines = []
            for line in display_text.splitlines():
                sents = split_into_sentences(line)
                expanded_lines.extend(sents if sents else [line])
            display_text = "\n\n".join(expanded_lines)

        self.clear_sentence_loop()
        self.clear_active_highlight()
        self.populate_drill_cards(drills)

        if display_text:
            self.text_edit.set_annotated_content(
                display_text,
                font_size=self.spin_font_size.value(),
                is_dark=(self.transparency > 0),
            )
            self._update_auto_export_stem(force=True)

        if auto_play and display_text:
            self.play_audio(target_loops=target_loops, progressive=progressive)

    def populate_drill_cards(self, drills: list):
        while self.drill_grid.count():
            item = self.drill_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not drills:
            self.lbl_drill_count.setText("0")
            self.drill_frame.setVisible(False)
            return

        self.lbl_drill_count.setText(str(len(drills)))

        for idx, d in enumerate(drills):
            tts_target = d.get("tts_phrase") or d["phrase"]
            card = DrillCardItem(
                index=idx + 1,
                phrase=d["phrase"],
                tip=d.get("tip", ""),
                parent=self.drill_container,
            )
            card.clicked.connect(lambda _p, target=tts_target: self.play_drill_phrase(target))
            row = idx // 2
            col = idx % 2
            self.drill_grid.addWidget(card, row, col)

        total_rows = (len(drills) + 1) // 2
        for r in range(total_rows):
            self.drill_grid.setRowStretch(r, 0)
        self.drill_grid.setRowStretch(total_rows, 1)

        self.drill_frame.setVisible(True)
        total_h = max(360, self.left_splitter.height())
        self.left_splitter.setSizes([int(total_h * 0.62), int(total_h * 0.38)])

    def play_drill_phrase(self, phrase: str):
        if not phrase or not phrase.strip():
            return
        if self.is_playing:
            self.toggle_stop_or_resume()

        phonetic_mode = self.is_phonetic_mode()
        voice_key = self.combo_voice.currentText().split(" ")[0]
        device = self.combo_device.currentData() or "cpu"
        self.drill_thread.play_phrase(
            phrase,
            voice=voice_key,
            speed=0.85,
            device=device,
            phonetic_mode=phonetic_mode,
        )

    def on_drill_audio_ready(self, drill_audio):
        if drill_audio is not None and len(drill_audio) > 0:
            sd.stop()
            sd.play(drill_audio, samplerate=SAMPLE_RATE)

    def clear_all_content(self):
        self.stop_all(reset_progress=True)
        self.clear_sentence_loop()
        self.clear_active_highlight()
        self.populate_drill_cards([])
        self.text_edit.clear()
        self.current_audio = None
        self.timeline = []
        self.slider_progress.setEnabled(False)
        self.slider_progress.setValue(0)
        self.lbl_time.setText("00:00 / 00:00")
        self._update_auto_export_stem(force=True)

    def on_sentence_double_clicked(self, char_pos: int):
        if not self.timeline or self.current_audio is None:
            return

        matched_seg = None
        for seg in self.timeline:
            if seg["start_char"] <= char_pos <= seg["end_char"]:
                matched_seg = seg
                break

        if not matched_seg:
            return

        self.loop_segment = matched_seg
        self.btn_exit_loop.setVisible(True)
        self.highlight_text_span(
            matched_seg["start_char"], matched_seg["end_char"], is_loop=True
        )
        self.start_playback(from_sec=matched_seg["start_sec"])

    def clear_sentence_loop(self):
        self.loop_segment = None
        if hasattr(self, "btn_exit_loop"):
            self.btn_exit_loop.setVisible(False)

    def highlight_text_span(self, start_char: int, end_char: int, is_loop: bool = False):
        doc_len = len(self.text_edit.toPlainText())
        if doc_len == 0:
            return
        start_char = max(0, min(start_char, doc_len))
        end_char = max(start_char, min(end_char, doc_len))

        cursor = self.text_edit.textCursor()
        cursor.setPosition(start_char)
        cursor.setPosition(end_char, QTextCursor.KeepAnchor)

        fmt = QTextCharFormat()
        if self.transparency > 0:
            fmt.setBackground(QColor(225, 29, 72, 95 if is_loop else 65))
            fmt.setForeground(QColor("#FFFFFF"))
        else:
            fmt.setBackground(QColor("#FECDD3" if is_loop else "#FFE4E6"))
            fmt.setForeground(QColor(PALETTE_BLACK))

        extra = QTextEdit.ExtraSelection()
        extra.cursor = cursor
        extra.format = fmt
        self.text_edit.setExtraSelections([extra])

    def clear_active_highlight(self):
        self.active_highlight_range = None
        if hasattr(self, "text_edit"):
            self.text_edit.setExtraSelections([])

    # ---------------------------------------------------------------------
    # Mode, Voice & Window Handlers
    # ---------------------------------------------------------------------
    def on_primary_voice_changed(self):
        v_key = self.combo_voice.currentText().split(" ")[0]
        if v_key.startswith("bf_"):
            uk_m = self.combo_male_voice.findText("bm_george", Qt.MatchStartsWith)
            if uk_m >= 0 and not self.combo_male_voice.currentText().startswith("bm_"):
                self.combo_male_voice.setCurrentIndex(uk_m)
        elif v_key.startswith("af_"):
            us_m = self.combo_male_voice.findText("am_michael", Qt.MatchStartsWith)
            if us_m >= 0 and not self.combo_male_voice.currentText().startswith("am_"):
                self.combo_male_voice.setCurrentIndex(us_m)
        self.save_user_preferences()

    def on_mode_changed(self):
        mode_data = self.combo_mode.currentData()
        self.save_user_preferences()

        if hasattr(self, "http_thread") and self.http_thread:
            self.http_thread.set_current_mode("2" if mode_data == "auto_clipboard" else "1")

        if hasattr(self, "act_mode1") and hasattr(self, "act_mode2"):
            self.act_mode1.setChecked(mode_data == "manual")
            self.act_mode2.setChecked(mode_data == "auto_clipboard")

        if mode_data == "auto_clipboard":
            self.tray_icon.setToolTip("Kokoro TTS Player [Mode 2: Auto Clipboard]")
            if not self._is_loading_prefs:
                self.tray_icon.showMessage(
                    "Switched to Mode 2",
                    f"Clipboard auto-read enabled (Limit: {self.spin_auto_limit.value()} chars, Mode: {self.combo_clip_loop.currentText()}).\nManual paste is required for larger texts.",
                    QSystemTrayIcon.Information,
                    2500,
                )
        else:
            self.tray_icon.setToolTip("Kokoro TTS Player [Mode 1: Manual Mode]")

    def on_remote_mode_changed(self, mode_str: str):
        if mode_str in ("2", "auto", "clipboard"):
            idx = self.combo_mode.findData("auto_clipboard")
            if idx >= 0:
                self.combo_mode.setCurrentIndex(idx)
        else:
            idx = self.combo_mode.findData("manual")
            if idx >= 0:
                self.combo_mode.setCurrentIndex(idx)

    def on_always_visible_toggled(self, checked: bool):
        flags = Qt.FramelessWindowHint | Qt.Window
        if checked:
            flags |= Qt.WindowStaysOnTopHint

        self.setWindowFlags(flags)
        if checked:
            self.show()
            self.force_bring_to_foreground()
        self.save_user_preferences()

    def init_single_instance_ipc(self):
        self.local_server = QLocalServer(self)
        self.local_server.removeServer(SINGLE_INSTANCE_KEY)
        self.local_server.newConnection.connect(self.on_new_instance_detected)
        self.local_server.listen(SINGLE_INSTANCE_KEY)

    def on_new_instance_detected(self):
        client = self.local_server.nextPendingConnection()
        if client:
            client.readyRead.connect(lambda: self.handle_single_instance_msg(client))
            self.force_bring_to_foreground()

    def handle_single_instance_msg(self, client):
        try:
            client.readAll()
        except Exception:
            pass
        self.force_bring_to_foreground()
        client.disconnectFromServer()

    def init_tray_icon(self):
        if os.path.exists(TRAY_ICON_PATH):
            self.app_icon = QIcon(TRAY_ICON_PATH)
        else:
            self.app_icon = self.style().standardIcon(
                QApplication.style().SP_MediaVolume
            )

        self.setWindowIcon(self.app_icon)
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.app_icon)
        self.tray_icon.setToolTip("Kokoro TTS Player (Running)")

        tray_menu = QMenu()
        act_toggle = QAction("Show / Hide Window", self)
        act_toggle.triggered.connect(self.toggle_window_visibility)

        self.act_mode1 = QAction("Mode 1: Manual (Click / Push)", self, checkable=True)
        self.act_mode2 = QAction("Mode 2: Auto Clipboard", self, checkable=True)
        self.act_mode1.triggered.connect(lambda: self.combo_mode.setCurrentIndex(0))
        self.act_mode2.triggered.connect(lambda: self.combo_mode.setCurrentIndex(1))

        act_reload = QAction("Reload Player (Ctrl+R)", self)
        act_reload.triggered.connect(self.reload_application)

        act_quit = QAction("Exit", self)
        act_quit.triggered.connect(self.quit_application)

        tray_menu.addAction(act_toggle)
        tray_menu.addSeparator()
        tray_menu.addAction(self.act_mode1)
        tray_menu.addAction(self.act_mode2)
        tray_menu.addSeparator()
        tray_menu.addAction(act_reload)
        tray_menu.addAction(act_quit)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()

    def toggle_window_visibility(self):
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.force_bring_to_foreground()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_window_visibility()

    def cleanup_resources(self):
        self._apply_resize_cursor(None)
        app_inst = QApplication.instance()
        if app_inst is not None:
            try:
                app_inst.removeEventFilter(self)
            except Exception:
                pass
        self.save_user_preferences()
        self.stop_all(reset_progress=True)
        if hasattr(self, "http_thread") and self.http_thread:
            self.http_thread.stop()
        if hasattr(self, "local_server") and self.local_server:
            self.local_server.close()
            QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.hide()
        if self.mutex_handle:
            try:
                ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
                self.mutex_handle = None
            except Exception:
                pass

    def reload_application(self):
        self.cleanup_resources()
        args = [sys.executable] + sys.argv
        subprocess.Popen(args)
        QApplication.quit()

    def quit_application(self):
        self.cleanup_resources()
        QApplication.quit()

    def on_device_changed(self):
        selected_device = self.combo_device.currentData()
        if selected_device == "cuda" and not torch.cuda.is_available():
            if not self._is_loading_prefs:
                QMessageBox.warning(
                    self,
                    "CUDA Unavailable",
                    "PyTorch is currently not configured with CUDA GPU support.\n\n"
                    "To enable GPU acceleration, run:\n"
                    "pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124",
                )
            cpu_idx = self.combo_device.findData("cpu")
            if cpu_idx >= 0:
                self.combo_device.setCurrentIndex(cpu_idx)
            return
        self.save_user_preferences()

    def on_font_size_changed(self, size: int):
        self.text_edit.setStyleSheet(
            build_text_edit_stylesheet(size, transparency=self.transparency)
        )
        self.text_edit.refresh_theme(
            font_size=size, is_dark=(self.transparency > 0)
        )
        self.save_user_preferences()

    # ---------------------------------------------------------------------
    # Frameless Window Drag, 4-Corner & Edge Resize & Keyboard Shortcuts
    # ---------------------------------------------------------------------
    def get_edge_direction(self, pos):
        r = self.rect()
        w, h = r.width(), r.height()
        x, y = pos.x(), pos.y()

        if x < 0 or x > w or y < 0 or y > h:
            return None

        in_corner_left = x <= CORNER_MARGIN
        in_corner_right = x >= w - CORNER_MARGIN
        in_corner_top = y <= CORNER_MARGIN
        in_corner_bottom = y >= h - CORNER_MARGIN

        if in_corner_left and in_corner_top:
            return "top-left"
        if in_corner_right and in_corner_top:
            return "top-right"
        if in_corner_left and in_corner_bottom:
            return "bottom-left"
        if in_corner_right and in_corner_bottom:
            return "bottom-right"

        if x <= EDGE_MARGIN:
            return "left"
        if x >= w - EDGE_MARGIN:
            return "right"
        if y <= EDGE_MARGIN:
            return "top"
        if y >= h - EDGE_MARGIN:
            return "bottom"
        return None

    def _apply_resize_cursor(self, edge):
        if edge in ("top-left", "bottom-right"):
            target = Qt.SizeFDiagCursor
        elif edge in ("top-right", "bottom-left"):
            target = Qt.SizeBDiagCursor
        elif edge in ("left", "right"):
            target = Qt.SizeHorCursor
        elif edge in ("top", "bottom"):
            target = Qt.SizeVerCursor
        else:
            target = None

        if target != self._override_cursor_shape:
            if self._override_cursor_shape is not None:
                QApplication.restoreOverrideCursor()
            if target is not None:
                QApplication.setOverrideCursor(target)
            self._override_cursor_shape = target

    def _apply_geometry_resize(self, global_pos):
        delta = global_pos - self.drag_start_pos
        geo = QRect(self.drag_start_geo)
        min_w = self.minimumWidth()
        min_h = self.minimumHeight()

        if "right" in self.drag_edge:
            geo.setWidth(max(min_w, self.drag_start_geo.width() + delta.x()))
        elif "left" in self.drag_edge:
            new_w = max(min_w, self.drag_start_geo.width() - delta.x())
            geo.setLeft(self.drag_start_geo.right() - new_w)

        if "bottom" in self.drag_edge:
            geo.setHeight(max(min_h, self.drag_start_geo.height() + delta.y()))
        elif "top" in self.drag_edge:
            new_h = max(min_h, self.drag_start_geo.height() - delta.y())
            geo.setTop(self.drag_start_geo.bottom() - new_h)

        self.setGeometry(geo)

    def leaveEvent(self, event):
        if not self.drag_edge:
            self._apply_resize_cursor(None)
        super().leaveEvent(event)

    def hideEvent(self, event):
        self.drag_edge = None
        self._apply_resize_cursor(None)
        super().hideEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            now = time.time()
            if now - self.last_click_time < 0.35:
                self.click_count += 1
            else:
                self.click_count = 1
            self.last_click_time = now

            if self.click_count >= 3:
                self.click_count = 0
                self.hide()
                event.accept()
                return

            edge = self.get_edge_direction(event.pos())
            if edge:
                self.drag_edge = edge
                self.drag_start_geo = self.geometry()
                self.drag_start_pos = event.globalPos()
                self._apply_resize_cursor(edge)
                event.accept()
                return

            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_edge and (event.buttons() & Qt.LeftButton):
            self._apply_geometry_resize(event.globalPos())
            event.accept()
            return

        if (event.buttons() & Qt.LeftButton) and not self.drag_pos.isNull():
            self.move(event.globalPos() - self.drag_pos)
            event.accept()
            return

        edge = self.get_edge_direction(event.pos())
        self._apply_resize_cursor(edge)

    def mouseReleaseEvent(self, event):
        self.drag_edge = None
        self.drag_pos = QPoint()
        self._apply_resize_cursor(self.get_edge_direction(event.pos()))
        self.save_user_preferences()

    def closeEvent(self, event):
        self.save_user_preferences()
        if hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)

    def force_bring_to_foreground(self):
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        bring_window_to_foreground(int(self.winId()))

    def eventFilter(self, obj, event):
        etype = event.type()

        # Intercept 4-corner & edge resize events across all child widgets inside this window
        if self.isVisible() and isinstance(obj, QWidget) and (obj is self or self.isAncestorOf(obj)):
            if etype == QEvent.MouseMove:
                if self.drag_edge and (event.buttons() & Qt.LeftButton):
                    self._apply_geometry_resize(event.globalPos())
                    return True
                if event.buttons() == Qt.NoButton:
                    local_pos = self.mapFromGlobal(event.globalPos())
                    self._apply_resize_cursor(self.get_edge_direction(local_pos))
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                local_pos = self.mapFromGlobal(event.globalPos())
                edge = self.get_edge_direction(local_pos)
                if edge:
                    self.drag_edge = edge
                    self.drag_start_geo = self.geometry()
                    self.drag_start_pos = event.globalPos()
                    self._apply_resize_cursor(edge)
                    return True
            elif etype == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self.drag_edge:
                    self.drag_edge = None
                    self.drag_pos = QPoint()
                    local_pos = self.mapFromGlobal(event.globalPos())
                    self._apply_resize_cursor(self.get_edge_direction(local_pos))
                    self.save_user_preferences()
                    return True

        if obj == self.text_edit and etype == QEvent.KeyPress:
            if event.key() == Qt.Key_Escape:
                if self.loop_segment is not None:
                    self.clear_sentence_loop()
                else:
                    self.hide()
                return True
            if event.key() == Qt.Key_Space:
                if self.is_playing or self.is_paused:
                    self.toggle_stop_or_resume()
                    return True
            if event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Up, Qt.Key_Down):
                delta = 5 if event.key() == Qt.Key_Up else -5
                self.slider_opacity.setValue(self.slider_opacity.value() + delta)
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if hasattr(self, "edit_export_name") and self.edit_export_name.hasFocus():
                self.edit_export_name.clearFocus()
            elif self.loop_segment is not None:
                self.clear_sentence_loop()
            else:
                self.hide()
            event.accept()
        elif event.key() == Qt.Key_Space:
            if hasattr(self, "edit_export_name") and self.edit_export_name.hasFocus():
                super().keyPressEvent(event)
                return
            self.toggle_stop_or_resume()
            event.accept()
        elif event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Up, Qt.Key_Down):
            delta = 5 if event.key() == Qt.Key_Up else -5
            self.slider_opacity.setValue(self.slider_opacity.value() + delta)
            event.accept()
        elif event.modifiers() == Qt.ControlModifier and (
            event.key() in (Qt.Key_Return, Qt.Key_Enter)
        ):
            idx = self.combo_push_loop.currentIndex()
            self.play_audio(
                target_loops=self.get_loop_count_from_index(idx),
                progressive=self.is_progressive_index(idx),
            )
            event.accept()
        elif event.modifiers() in (
            Qt.ControlModifier,
            Qt.ControlModifier | Qt.ShiftModifier,
        ) and event.key() == Qt.Key_S:
            self.export_netease_mp3()
            event.accept()
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_R:
            self.reload_application()
            event.accept()
        else:
            super().keyPressEvent(event)

    def show_help_dialog(self):
        dialog = CyberHelpDialog(self)
        geo = self.geometry()
        dialog.move(geo.center() - dialog.rect().center())
        dialog.exec_()

    # ---------------------------------------------------------------------
    # Synthesis & Playback State Machine
    # ---------------------------------------------------------------------
    def on_text_changed(self):
        visible_text = self.text_edit.toPlainText().strip()
        if not visible_text:
            self.text_edit.is_rich_annotated = False
            self.text_edit._raw_annotated_text = ""
        clean_tts = sanitize_text(visible_text, phonetic_mode=self.is_phonetic_mode())
        words = len(clean_tts.split()) if clean_tts else 0
        chars = len(clean_tts)
        self.lbl_stats.setText(f"Words: {words} | Chars: {chars}")
        self._update_auto_export_stem(force=False)

    def on_split_mode_toggled(self, checked: bool):
        self.save_user_preferences()
        visible_text = self.text_edit.toPlainText().strip()
        if not visible_text:
            return

        if checked:
            expanded = []
            for line in visible_text.splitlines():
                sents = split_into_sentences(line)
                expanded.extend(sents if sents else [line])
            updated_text = "\n\n".join(expanded)
        else:
            updated_text = re.sub(r"\s*\n+\s*", " ", visible_text).strip()

        if self.is_phonetic_mode():
            self.set_plain_editor_text(updated_text)
        else:
            self.text_edit.set_annotated_content(
                updated_text,
                font_size=self.spin_font_size.value(),
                is_dark=(self.transparency > 0),
            )

    def on_engine_status(self, status: str):
        if "SYNTHESIZING" in status:
            self.btn_play.setText("▶ Synthesizing...")
        elif "LOADING" in status:
            self.btn_play.setText("▶ Loading Model...")

    def on_ahk_text_received(self, payload):
        dismiss_external_osd()
        QTimer.singleShot(250, dismiss_external_osd)

        self.force_bring_to_foreground()
        idx = self.combo_push_loop.currentIndex()
        self.ingest_learning_text(
            payload,
            auto_play=True,
            target_loops=self.get_loop_count_from_index(idx),
            progressive=self.is_progressive_index(idx),
        )

    def play_audio(self, target_loops=None, progressive=False):
        visible_text = self.text_edit.toPlainText().strip()
        if not visible_text:
            return

        phonetic_mode = self.is_phonetic_mode()

        if self.chk_split.isChecked():
            expanded = []
            for line in visible_text.splitlines():
                sents = split_into_sentences(line)
                expanded.extend(sents if sents else [line])
            split_joined = "\n\n".join(expanded)
            if split_joined != visible_text:
                if phonetic_mode:
                    self.set_plain_editor_text(split_joined)
                else:
                    self.text_edit.set_annotated_content(
                        split_joined,
                        font_size=self.spin_font_size.value(),
                        is_dark=(self.transparency > 0),
                    )
                visible_text = self.text_edit.toPlainText().strip()

        segments = build_timeline_segments(visible_text, phonetic_mode=phonetic_mode)
        if not segments:
            return

        self.stop_all(reset_progress=True)
        self.clear_sentence_loop()
        self.clear_active_highlight()

        female_voice = self.combo_voice.currentText().split(" ")[0]
        male_voice = self.combo_male_voice.currentText().split(" ")[0]
        speed = self.slider_speed.value() / 10.0
        shadowing = self.chk_shadowing.isChecked()
        pause_sec = float(self.spin_pause.value())
        selected_device = self.combo_device.currentData() or "cpu"

        if target_loops is not None:
            self.current_target_loops = target_loops
        else:
            idx = self.combo_push_loop.currentIndex()
            self.current_target_loops = self.get_loop_count_from_index(idx)
            progressive = self.is_progressive_index(idx)

        self.btn_stop.setEnabled(True)
        self.btn_stop.setText("■ Stop")
        self.btn_play.setEnabled(False)

        self.slider_progress.setEnabled(False)
        self.slider_progress.setValue(0)
        self.lbl_time.setText("00:00 / 00:00")
        self.current_loop = 1

        if self.chk_always_visible.isChecked():
            self.force_bring_to_foreground()
            self.text_edit.setFocus()

        self.synth_thread.start_synth(
            segments=segments,
            female_voice=female_voice,
            male_voice=male_voice,
            speed=speed,
            shadowing=shadowing,
            pause_sec=pause_sec,
            device=selected_device,
            progressive=progressive,
            phonetic_mode=phonetic_mode,
        )

    def on_synth_finished(self, result_dict: dict):
        self.current_audio = result_dict["audio"]
        self.timeline = result_dict.get("timeline", [])
        self.total_duration = len(self.current_audio) / SAMPLE_RATE
        self.slider_progress.setEnabled(True)
        self._update_auto_export_stem(force=False)
        self.start_playback(from_sec=0.0)

    def start_playback(self, from_sec: float = 0.0):
        if self.current_audio is None or len(self.current_audio) == 0:
            return

        start_idx = int(from_sec * SAMPLE_RATE)
        start_idx = max(0, min(start_idx, len(self.current_audio)))

        sd.stop()
        sd.play(self.current_audio[start_idx:], samplerate=SAMPLE_RATE)

        self.play_start_time = time.time()
        self.play_offset_sec = from_sec
        self.is_playing = True
        self.is_paused = False

        self.btn_stop.setText("■ Stop")
        self.btn_stop.setEnabled(True)
        self.btn_play.setText("▶ Playing")
        self.btn_play.setEnabled(False)

        if self.chk_always_visible.isChecked():
            self.force_bring_to_foreground()

        self.progress_timer.start()

    def on_synth_error(self, err_msg: str):
        self.stop_all(reset_progress=True)
        if self.chk_always_visible.isChecked() or self.isVisible():
            QMessageBox.warning(
                self, "Synthesis Error", f"Speech synthesis error:\n{err_msg}"
            )

    def toggle_stop_or_resume(self):
        if self.synth_thread.isRunning():
            self.synth_thread.cancel()
            self.stop_all(reset_progress=True)
            return

        if self.is_playing:
            elapsed = (
                time.time() - self.play_start_time
            ) + self.play_offset_sec
            self.pause_offset_sec = max(0.0, min(elapsed, self.total_duration))

            sd.stop()
            self.is_playing = False
            self.is_paused = True
            self.progress_timer.stop()

            self.btn_stop.setText("▶ Resume")
            self.btn_play.setText("▶ Read Text")
            self.btn_play.setEnabled(True)
        elif self.is_paused:
            if self.pause_offset_sec >= self.total_duration:
                self.pause_offset_sec = 0.0
            self.start_playback(from_sec=self.pause_offset_sec)
        else:
            idx = self.combo_push_loop.currentIndex()
            self.play_audio(
                target_loops=self.get_loop_count_from_index(idx),
                progressive=self.is_progressive_index(idx),
            )

    def stop_all(self, reset_progress: bool = True):
        if self.synth_thread.isRunning():
            self.synth_thread.cancel()

        sd.stop()
        self.is_playing = False
        self.is_paused = False
        self.progress_timer.stop()
        self.current_loop = 1
        self.clear_active_highlight()

        if reset_progress:
            self.pause_offset_sec = 0.0

        self.btn_play.setText("▶ Read Text")
        self.btn_play.setEnabled(True)
        self.btn_stop.setText("■ Stop")
        self.btn_stop.setEnabled(False)

    def update_progress_ui(self):
        if not self.is_playing:
            return

        elapsed = (time.time() - self.play_start_time) + self.play_offset_sec

        if self.loop_segment is not None:
            if elapsed >= self.loop_segment["end_sec"]:
                self.start_playback(from_sec=self.loop_segment["start_sec"])
                return

        if elapsed >= self.total_duration:
            if self.current_loop < self.current_target_loops:
                self.current_loop += 1
                self.start_playback(from_sec=0.0)
                return
            else:
                self.stop_all(reset_progress=True)
                self.slider_progress.setValue(1000)
                tot_m, tot_s = divmod(int(self.total_duration), 60)
                self.lbl_time.setText(
                    f"{tot_m:02d}:{tot_s:02d} / {tot_m:02d}:{tot_s:02d}"
                )
                return

        if self.loop_segment is None and self.timeline:
            current_span = None
            for seg in self.timeline:
                if seg["start_sec"] <= elapsed <= seg["end_sec"]:
                    current_span = (seg["start_char"], seg["end_char"])
                    break
            if current_span != self.active_highlight_range:
                self.active_highlight_range = current_span
                if current_span:
                    self.highlight_text_span(current_span[0], current_span[1], is_loop=False)
                else:
                    self.text_edit.setExtraSelections([])

        if not self.is_slider_dragging and self.total_duration > 0:
            ratio = elapsed / self.total_duration
            self.slider_progress.setValue(int(ratio * 1000))

            cur_m, cur_s = divmod(int(elapsed), 60)
            tot_m, tot_s = divmod(int(self.total_duration), 60)
            self.lbl_time.setText(
                f"{cur_m:02d}:{cur_s:02d} / {tot_m:02d}:{tot_s:02d}"
            )

    def on_slider_pressed(self):
        self.is_slider_dragging = True

    def on_slider_released(self):
        self.is_slider_dragging = False
        if self.current_audio is not None and self.total_duration > 0:
            self.clear_sentence_loop()
            target_sec = (
                self.slider_progress.value() / 1000.0
            ) * self.total_duration
            self.start_playback(from_sec=target_sec)

    def on_slider_jump(self, ratio: float):
        if self.current_audio is not None and self.total_duration > 0:
            self.clear_sentence_loop()
            target_sec = ratio * self.total_duration
            self.start_playback(from_sec=target_sec)

    def export_netease_mp3(self):
        if self.current_audio is None or len(self.current_audio) == 0:
            QMessageBox.information(
                self,
                "Notice",
                "No synthesized audio available. Please enter text and synthesize first.",
            )
            return

        visible_text = self.text_edit.toPlainText().strip()
        phonetic_mode = self.is_phonetic_mode()

        target_dir = (
            self.export_dir
            if self.export_dir and os.path.isdir(self.export_dir)
            else APP_ROOT_DIR
        )
        self.export_dir = target_dir
        self._refresh_export_dir_ui()

        raw_name = self.edit_export_name.text().strip()
        if raw_name:
            stem = sanitize_export_stem(raw_name, fallback="kokoro_speech")
        else:
            stem = build_default_export_stem(
                visible_text=visible_text,
                timeline=self.timeline,
                phonetic_mode=phonetic_mode,
            )

        self.edit_export_name.blockSignals(True)
        self.edit_export_name.setText(stem)
        self.edit_export_name.blockSignals(False)

        cover_path = (
            self.export_cover_path
            if self.export_cover_path and os.path.isfile(self.export_cover_path)
            else TRAY_ICON_PATH
        )
        if cover_path != self.export_cover_path:
            self.export_cover_path = cover_path
            self._refresh_cover_ui()

        try:
            file_path = resolve_unique_mp3_path(target_dir, stem)
            loop_count = export_netease_mp3_file(
                file_path=file_path,
                audio=self.current_audio,
                timeline=self.timeline,
                visible_text=visible_text,
                phonetic_mode=phonetic_mode,
                artist=DEFAULT_EXPORT_ARTIST,
                cover_path=cover_path,
            )
            self.save_user_preferences()

            single_dur = len(self.current_audio) / float(SAMPLE_RATE)
            cover_name = os.path.basename(cover_path) if cover_path else "tts.png"

            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Information)
            msg_box.setWindowTitle("Export Completed")
            msg_box.setText(
                f"MP3 exported successfully to:\n{file_path}\n\n"
                f"• Duration: {single_dur:.1f}s × {loop_count} loop(s)\n"
                f"• Lyrics: Embedded synced LRC (clean text)\n"
                f"• Cover: {cover_name}\n\n"
                f"Would you like to open the file location?"
            )
            btn_open = msg_box.addButton("Open Folder", QMessageBox.AcceptRole)
            msg_box.addButton("Close", QMessageBox.RejectRole)
            msg_box.setDefaultButton(btn_open)
            msg_box.exec_()

            if msg_box.clickedButton() == btn_open:
                self.open_file_in_explorer(file_path)
        except Exception as e:
            QMessageBox.critical(
                self, "Export Error", f"Failed to export MP3:\n{e}"
            )

    # ---------------------------------------------------------------------
    # User Preferences Persistence & One-Click Reset
    # ---------------------------------------------------------------------
    def reset_to_defaults(self):
        """Restore all settings to defaults (Auto: ON, 30% Glass, Default Cover & Folder) and persist."""
        self._is_loading_prefs = True
        try:
            self.btn_phonetic_mode.setChecked(False)
            self.on_phonetic_mode_toggled(False)

            self.btn_auto_ipa.setChecked(True)
            self.on_auto_ipa_toggled(True)

            self.chk_split.setChecked(False)

            mode_idx = self.combo_mode.findData("auto_clipboard")
            self.combo_mode.setCurrentIndex(mode_idx if mode_idx >= 0 else 1)

            self.spin_auto_limit.setValue(1000)

            self.chk_always_visible.setChecked(True)
            self.on_always_visible_toggled(True)

            default_dev = "cuda" if torch.cuda.is_available() else "cpu"
            dev_idx = self.combo_device.findData(default_dev)
            if dev_idx >= 0:
                self.combo_device.setCurrentIndex(dev_idx)

            self.combo_voice.setCurrentIndex(0)
            m_idx = self.combo_male_voice.findText("am_michael", Qt.MatchStartsWith)
            if m_idx >= 0:
                self.combo_male_voice.setCurrentIndex(m_idx)

            self.slider_speed.setValue(10)
            self.chk_shadowing.setChecked(False)
            self.spin_pause.setValue(2)

            if self.combo_push_loop.count() > 2:
                self.combo_push_loop.setCurrentIndex(2)
            if self.combo_clip_loop.count() > 0:
                self.combo_clip_loop.setCurrentIndex(0)

            self.spin_font_size.setValue(18)

            self.export_cover_path = TRAY_ICON_PATH
            self._refresh_cover_ui()

            self.export_dir = APP_ROOT_DIR
            self._refresh_export_dir_ui()
            self._update_auto_export_stem(force=True)

            self.slider_opacity.setValue(30)
            self.on_transparency_changed(30)
        finally:
            self._is_loading_prefs = False

        self.save_user_preferences()

    def load_user_preferences(self):
        self._is_loading_prefs = True
        try:
            # One-time migrations for baseline defaults
            if not self.settings.value("v3_2_defaults_migrated", False, type=bool):
                self.settings.setValue("win_transparency", 30)
                self.settings.setValue("work_mode", "auto_clipboard")
                self.settings.setValue("v3_2_defaults_migrated", True)
                self.settings.sync()

            if not self.settings.value("v3_3_auto_ipa_default_migrated", False, type=bool):
                self.settings.setValue("auto_ipa_enabled", True)
                self.settings.setValue("v3_3_auto_ipa_default_migrated", True)
                self.settings.sync()

            saved_width = self.settings.value("win_width", 940, type=int)
            saved_height = self.settings.value("win_height", 660, type=int)
            self.resize(max(720, saved_width), max(560, saved_height))

            saved_sizes = self.settings.value("splitter_sizes")
            if saved_sizes:
                try:
                    sizes = [int(s) for s in saved_sizes]
                    if len(sizes) == 2 and sum(sizes) > 0:
                        self.splitter.setSizes(sizes)
                except Exception:
                    self.splitter.setSizes([660, 280])
            else:
                self.splitter.setSizes([660, 280])

            # Load IPA & Auto-Detect IPA modes (Auto defaults to ON)
            saved_phonetic = self.settings.value("phonetic_mode_enabled", False, type=bool)
            self.btn_phonetic_mode.setChecked(saved_phonetic)
            self.on_phonetic_mode_toggled(saved_phonetic)

            saved_auto_ipa = self.settings.value("auto_ipa_enabled", True, type=bool)
            self.btn_auto_ipa.setChecked(saved_auto_ipa)
            self.on_auto_ipa_toggled(saved_auto_ipa)

            self.chk_split.setChecked(
                self.settings.value("sentence_split", False, type=bool)
            )

            saved_mode = self.settings.value("work_mode", "auto_clipboard", type=str)
            mode_idx = self.combo_mode.findData(saved_mode)
            if mode_idx >= 0:
                self.combo_mode.setCurrentIndex(mode_idx)
            else:
                self.combo_mode.setCurrentIndex(1)

            if hasattr(self, "act_mode1") and hasattr(self, "act_mode2"):
                cur_mode = self.combo_mode.currentData()
                self.act_mode1.setChecked(cur_mode == "manual")
                self.act_mode2.setChecked(cur_mode == "auto_clipboard")

            auto_limit = self.settings.value("auto_max_chars", 1000, type=int)
            self.spin_auto_limit.setValue(auto_limit)

            is_always_vis = self.settings.value("always_visible", True, type=bool)
            self.chk_always_visible.setChecked(is_always_vis)
            self.on_always_visible_toggled(is_always_vis)

            default_dev = "cuda" if torch.cuda.is_available() else "cpu"
            saved_dev = self.settings.value("device", default_dev, type=str)
            dev_idx = self.combo_device.findData(saved_dev)
            if dev_idx >= 0:
                self.combo_device.setCurrentIndex(dev_idx)

            saved_voice_name = self.settings.value("voice_name", "", type=str)
            if saved_voice_name:
                idx = self.combo_voice.findText(saved_voice_name, Qt.MatchStartsWith)
                if idx >= 0:
                    self.combo_voice.setCurrentIndex(idx)
            else:
                self.combo_voice.setCurrentIndex(
                    self.settings.value("voice_idx", 0, type=int)
                )

            saved_male_name = self.settings.value("male_voice_name", "am_michael", type=str)
            m_idx = self.combo_male_voice.findText(saved_male_name, Qt.MatchStartsWith)
            if m_idx >= 0:
                self.combo_male_voice.setCurrentIndex(m_idx)

            self.slider_speed.setValue(self.settings.value("speed", 10, type=int))
            self.chk_shadowing.setChecked(
                self.settings.value("shadowing", False, type=bool)
            )
            self.spin_pause.setValue(
                self.settings.value("pause_sec", 2, type=int)
            )

            push_loop = self.settings.value("push_loop_idx", 2, type=int)
            clip_loop = self.settings.value("clip_loop_idx", 0, type=int)
            if push_loop < self.combo_push_loop.count():
                self.combo_push_loop.setCurrentIndex(push_loop)
            if clip_loop < self.combo_clip_loop.count():
                self.combo_clip_loop.setCurrentIndex(clip_loop)

            font_size = self.settings.value("font_size", 18, type=int)
            self.spin_font_size.setValue(font_size)

            saved_cover = self.settings.value("export_cover_path", TRAY_ICON_PATH, type=str)
            if saved_cover and os.path.isfile(saved_cover):
                self.export_cover_path = os.path.normpath(saved_cover)
            else:
                self.export_cover_path = TRAY_ICON_PATH
            self._refresh_cover_ui()

            saved_dir = self.settings.value("last_export_dir", APP_ROOT_DIR, type=str)
            if saved_dir and os.path.isdir(saved_dir):
                self.export_dir = os.path.normpath(saved_dir)
            else:
                self.export_dir = APP_ROOT_DIR
            self._refresh_export_dir_ui()
            self._update_auto_export_stem(force=True)

            saved_transp = self.settings.value("win_transparency", 30, type=int)
            clamped_t = clamp_transparency(saved_transp)
            self.slider_opacity.setValue(clamped_t)
            self.on_transparency_changed(clamped_t)
        finally:
            self._is_loading_prefs = False

        self.save_user_preferences()

    def save_user_preferences(self):
        if getattr(self, "_is_loading_prefs", False):
            return

        if not self.isMinimized() and not self.isMaximized():
            self.settings.setValue("win_width", self.width())
            self.settings.setValue("win_height", self.height())
        if hasattr(self, "splitter"):
            self.settings.setValue("splitter_sizes", self.splitter.sizes())
        if hasattr(self, "btn_phonetic_mode"):
            self.settings.setValue("phonetic_mode_enabled", self.btn_phonetic_mode.isChecked())
        if hasattr(self, "btn_auto_ipa"):
            self.settings.setValue("auto_ipa_enabled", self.btn_auto_ipa.isChecked())
        self.settings.setValue("sentence_split", self.chk_split.isChecked())
        if hasattr(self, "combo_mode"):
            self.settings.setValue("work_mode", self.combo_mode.currentData())
        if hasattr(self, "spin_auto_limit"):
            self.settings.setValue("auto_max_chars", self.spin_auto_limit.value())
        if hasattr(self, "chk_always_visible"):
            self.settings.setValue(
                "always_visible", self.chk_always_visible.isChecked()
            )
        if hasattr(self, "combo_device"):
            self.settings.setValue("device", self.combo_device.currentData())
        self.settings.setValue("voice_idx", self.combo_voice.currentIndex())
        self.settings.setValue("voice_name", self.combo_voice.currentText())
        if hasattr(self, "combo_male_voice"):
            self.settings.setValue("male_voice_name", self.combo_male_voice.currentText())
        self.settings.setValue("speed", self.slider_speed.value())
        self.settings.setValue("shadowing", self.chk_shadowing.isChecked())
        self.settings.setValue("pause_sec", self.spin_pause.value())
        if hasattr(self, "combo_push_loop"):
            self.settings.setValue("push_loop_idx", self.combo_push_loop.currentIndex())
        if hasattr(self, "combo_clip_loop"):
            self.settings.setValue("clip_loop_idx", self.combo_clip_loop.currentIndex())
        if hasattr(self, "spin_font_size"):
            self.settings.setValue("font_size", self.spin_font_size.value())
        if hasattr(self, "slider_opacity"):
            self.settings.setValue("win_transparency", self.slider_opacity.value())
        if self.export_cover_path:
            self.settings.setValue("export_cover_path", os.path.normpath(self.export_cover_path))
        if self.export_dir:
            self.settings.setValue("last_export_dir", os.path.normpath(self.export_dir))
        self.settings.sync()