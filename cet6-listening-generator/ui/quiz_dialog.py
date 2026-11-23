# -*- coding: utf-8 -*-
import json
import os
import time
import wave
from typing import Dict, List, Optional

import numpy as np
import sounddevice as sd
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.settings import get_default_output_dir
from ui.styles import CLEAN_WARM_STYLESHEET, ClickableSlider, FramelessWindowMixin


def load_wav_as_float32(wav_path: str):
    """读取 WAV 文件为 float32 numpy 数组与采样率"""
    with wave.open(wav_path, "rb") as wf:
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw_bytes = wf.readframes(n_frames)

    if sampwidth == 2:
        data = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        data = np.frombuffer(raw_bytes, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        data = (np.frombuffer(raw_bytes, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)

    return data.astype(np.float32), framerate


def resample_speed(audio: np.ndarray, speed: float) -> np.ndarray:
    """使用纯 NumPy 线性插值调整播放倍速"""
    if abs(speed - 1.0) < 1e-3 or len(audio) == 0:
        return audio
    new_len = max(1, int(len(audio) / speed))
    old_indices = np.arange(len(audio), dtype=np.float64)
    new_indices = np.linspace(0, len(audio) - 1, new_len, dtype=np.float64)
    return np.interp(new_indices, old_indices, audio).astype(np.float32)


class OptionRowWidget(QFrame):
    """支持长文本自动折行的可点击选项行，彻底杜绝横向截断与横向滚动条"""

    def __init__(self, opt_key: str, opt_text: str, parent=None):
        super().__init__(parent)
        self.opt_key = opt_key
        self._state = "normal"  # normal / selected / correct / wrong

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)

        self.radio = QRadioButton(f"{opt_key})")
        self.radio.setFocusPolicy(Qt.NoFocus)
        self.radio.setFixedWidth(44)
        self.radio.setStyleSheet("border: none; background: transparent; font-weight: bold;")
        self.radio.toggled.connect(self._on_toggled)

        self.lbl_text = QLabel(opt_text)
        self.lbl_text.setWordWrap(True)
        self.lbl_text.setStyleSheet("border: none; background: transparent; color: #24272C;")

        row.addWidget(self.radio, alignment=Qt.AlignTop)
        row.addWidget(self.lbl_text, stretch=1)
        self._apply_visual_state("normal")

    def _on_toggled(self, checked: bool):
        if self._state in ("correct", "wrong"):
            return
        self._apply_visual_state("selected" if checked else "normal")

    def set_grade_state(self, state: str):
        """state: 'normal' | 'correct' | 'wrong'"""
        self._state = state
        self._apply_visual_state(state)

    def _apply_visual_state(self, state: str):
        if state == "selected":
            self.setStyleSheet("""
                OptionRowWidget {
                    background-color: #FAF0EC;
                    border: 1px solid #F0C7BA;
                    border-radius: 9px;
                }
            """)
            self.lbl_text.setStyleSheet("border: none; background: transparent; color: #1F2329;")
        elif state == "correct":
            self.setStyleSheet("""
                OptionRowWidget {
                    background-color: #ECFDF5;
                    border: 1px solid #86EFAC;
                    border-radius: 9px;
                }
            """)
            self.lbl_text.setStyleSheet(
                "border: none; background: transparent; color: #15803D; font-weight: bold;"
            )
        elif state == "wrong":
            self.setStyleSheet("""
                OptionRowWidget {
                    background-color: #FEF2F2;
                    border: 1px solid #FCA5A5;
                    border-radius: 9px;
                }
            """)
            self.lbl_text.setStyleSheet(
                "border: none; background: transparent; color: #B91C1C;"
            )
        else:
            self.setStyleSheet("""
                OptionRowWidget {
                    background-color: #FFFFFF;
                    border: 1px solid transparent;
                    border-radius: 9px;
                }
                OptionRowWidget:hover {
                    background-color: #F2F4F7;
                }
            """)
            self.lbl_text.setStyleSheet("border: none; background: transparent; color: #24272C;")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.radio.isEnabled():
            self.radio.setChecked(True)
            event.accept()
            return
        super().mousePressEvent(event)


class QuizPracticeDialog(QDialog, FramelessWindowMixin):
    """独立刷题面板：自动识别练习文件夹中的音频与题目，支持盲听答题与自动批改"""

    def __init__(self, initial_dir: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.init_frameless_drag(allow_resize=True)
        self.resize(1040, 690)
        self.setMinimumSize(860, 560)

        self.current_dir = ""
        self.exam_data: dict = {}
        self.base_audio: Optional[np.ndarray] = None
        self.playback_audio: Optional[np.ndarray] = None
        self.sample_rate: int = 24000
        self.playback_speed: float = 1.0

        self.is_playing = False
        self.is_paused = False
        self.play_start_time = 0.0
        self.play_offset_sec = 0.0
        self.pause_offset_sec = 0.0
        self.total_duration = 0.0
        self.is_slider_dragging = False

        self.is_submitted = False
        self.question_widgets: List[dict] = []

        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(50)
        self.progress_timer.timeout.connect(self._update_progress_ui)

        self._init_ui()

        if initial_dir and os.path.isdir(initial_dir):
            self.load_exam_folder(initial_dir)

    def _init_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        container = QFrame()
        container.setObjectName("dialogContainer")
        container.setStyleSheet(CLEAN_WARM_STYLESHEET)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setColor(QColor(25, 30, 40, 32))
        shadow.setOffset(0, 8)
        container.setGraphicsEffect(shadow)

        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(20, 14, 20, 20)
        main_layout.setSpacing(10)

        # 1. 顶部标题与文件夹切换栏
        top_bar = QHBoxLayout()
        self.lbl_exam_title = QLabel("QUIZ PRACTICE // 请选择练习文件夹")
        self.lbl_exam_title.setObjectName("lblBrandTitle")

        btn_choose_dir = QPushButton("Open Folder")
        btn_choose_dir.setObjectName("pillBtn")
        btn_choose_dir.setFocusPolicy(Qt.NoFocus)
        btn_choose_dir.clicked.connect(self.prompt_and_load_folder)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("btnTopAction")
        btn_close.setFocusPolicy(Qt.NoFocus)
        btn_close.clicked.connect(self.close)

        top_bar.addWidget(self.lbl_exam_title)
        top_bar.addStretch()
        top_bar.addWidget(btn_choose_dir)
        top_bar.addWidget(btn_close)
        main_layout.addLayout(top_bar)

        # 2. 考场音频播放器卡片
        player_card = QFrame()
        player_card.setObjectName("cardPanel")
        player_layout = QHBoxLayout(player_card)
        player_layout.setContentsMargins(16, 11, 16, 11)
        player_layout.setSpacing(10)

        self.btn_play_pause = QPushButton("▶ Play")
        self.btn_play_pause.setObjectName("btnPrimary")
        self.btn_play_pause.setFocusPolicy(Qt.NoFocus)
        self.btn_play_pause.setEnabled(False)
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)

        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setObjectName("btnDark")
        self.btn_stop.setFocusPolicy(Qt.NoFocus)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(lambda: self.stop_playback(reset=True))

        self.slider_progress = ClickableSlider(Qt.Horizontal)
        self.slider_progress.setRange(0, 1000)
        self.slider_progress.setValue(0)
        self.slider_progress.setEnabled(False)
        self.slider_progress.setFocusPolicy(Qt.NoFocus)
        self.slider_progress.sliderPressed.connect(self._on_slider_pressed)
        self.slider_progress.sliderReleased.connect(self._on_slider_released)
        self.slider_progress.jump_requested.connect(self._on_slider_jump)

        self.lbl_timer = QLabel("00:00 / 00:00")
        self.lbl_timer.setObjectName("lblSectionTitle")

        player_layout.addWidget(self.btn_play_pause)
        player_layout.addWidget(self.btn_stop)
        player_layout.addWidget(self.slider_progress, stretch=1)
        player_layout.addWidget(self.lbl_timer)

        self.speed_buttons: Dict[float, QPushButton] = {}
        for spd in (0.75, 1.0, 1.15, 1.25):
            btn_spd = QPushButton(f"{spd:g}x")
            btn_spd.setFocusPolicy(Qt.NoFocus)
            btn_spd.clicked.connect(lambda _, s=spd: self.set_playback_speed(s))
            self.speed_buttons[spd] = btn_spd
            player_layout.addWidget(btn_spd)
        self._refresh_speed_button_styles()

        main_layout.addWidget(player_card)

        # 3. 中部左右分栏
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(8)
        splitter.setChildrenCollapsible(False)

        # 左侧答题卡
        left_card = QFrame()
        left_card.setObjectName("cardPanel")
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(16, 14, 16, 14)
        left_layout.setSpacing(10)

        q_header = QHBoxLayout()
        lbl_q_title = QLabel("选择题作答区（盲听模式默认隐藏题干）")
        lbl_q_title.setObjectName("lblSectionTitle")

        self.chk_show_prompts = QCheckBox("显示题干")
        self.chk_show_prompts.setFocusPolicy(Qt.NoFocus)
        self.chk_show_prompts.toggled.connect(self._toggle_question_prompts_visibility)

        q_header.addWidget(lbl_q_title)
        q_header.addStretch()
        q_header.addWidget(self.chk_show_prompts)
        left_layout.addLayout(q_header)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_content = QWidget()
        self.scroll_content.setStyleSheet("background-color: transparent;")
        self.questions_vbox = QVBoxLayout(self.scroll_content)
        self.questions_vbox.setContentsMargins(0, 2, 6, 2)
        self.questions_vbox.setSpacing(12)
        self.questions_vbox.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        left_layout.addWidget(self.scroll_area, stretch=1)

        submit_bar = QHBoxLayout()
        submit_bar.setSpacing(10)
        self.lbl_score = QLabel("听完对话与提问后选择答案")
        self.lbl_score.setObjectName("lblHighlightStats")

        self.btn_reset_quiz = QPushButton("Reset")
        self.btn_reset_quiz.setObjectName("btnSecondary")
        self.btn_reset_quiz.setFocusPolicy(Qt.NoFocus)
        self.btn_reset_quiz.setEnabled(False)
        self.btn_reset_quiz.clicked.connect(self.reset_quiz_answers)

        self.btn_submit = QPushButton("▶ Submit && Grade")
        self.btn_submit.setObjectName("btnPrimary")
        self.btn_submit.setFocusPolicy(Qt.NoFocus)
        self.btn_submit.setEnabled(False)
        self.btn_submit.clicked.connect(self.submit_and_grade)

        submit_bar.addWidget(self.lbl_score)
        submit_bar.addStretch()
        submit_bar.addWidget(self.btn_reset_quiz)
        submit_bar.addWidget(self.btn_submit)
        left_layout.addLayout(submit_bar)

        # 右侧原文与解析
        right_card = QFrame()
        right_card.setObjectName("cardPanel")
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(16, 14, 16, 14)
        right_layout.setSpacing(10)

        lbl_rev_title = QLabel("听力原文与中文解析（交卷后自动解锁）")
        lbl_rev_title.setObjectName("lblSectionTitle")
        right_layout.addWidget(lbl_rev_title)

        self.review_browser = QTextBrowser()
        self.review_browser.setStyleSheet("""
            QTextBrowser {
                background-color: #F9FAFB;
                border: 1px solid #ECEEF2;
                border-radius: 12px;
                padding: 14px;
                color: #24272C;
                font-size: 13px;
                line-height: 1.65;
            }
        """)
        self._render_locked_review_placeholder()
        right_layout.addWidget(self.review_browser, stretch=1)

        splitter.addWidget(left_card)
        splitter.addWidget(right_card)
        splitter.setSizes([580, 420])
        main_layout.addWidget(splitter, stretch=1)

        root.addWidget(container)

    def _render_locked_review_placeholder(self):
        self.review_browser.setHtml("""
        <div style="text-align:center; padding: 60px 20px; color:#8C94A3;">
            <p style="font-size:15px; font-weight:bold; color:#5C626E;">[LOCKED] 盲听模拟作答中</p>
            <p>为还原真实六级考场体验，当前已锁定对话原文与解析。</p>
            <p>请在左侧完成所有题目并点击 <b>「Submit & Grade」</b> 后查看逐句文本与答案定位。</p>
        </div>
        """)

    def prompt_and_load_folder(self) -> bool:
        start_dir = self.current_dir or get_default_output_dir()
        if not os.path.exists(start_dir):
            os.makedirs(start_dir, exist_ok=True)

        chosen = QFileDialog.getExistingDirectory(
            self, "选择包含六级听力练习文件的文件夹", start_dir
        )
        if not chosen:
            return False
        return self.load_exam_folder(chosen)

    def load_exam_folder(self, folder_path: str) -> bool:
        self.stop_playback(reset=True)

        json_path = os.path.join(folder_path, "exam_data.json")
        if not os.path.exists(json_path):
            json_files = [
                f for f in os.listdir(folder_path) if f.lower().endswith(".json")
            ]
            if json_files:
                json_path = os.path.join(folder_path, json_files[0])
            else:
                QMessageBox.warning(
                    self,
                    "未识别到题目数据",
                    f"所选文件夹中未找到 exam_data.json：\n{folder_path}",
                )
                return False

        try:
            with open(json_path, "rb") as f:
                raw = f.read()
            try:
                data = json.loads(raw.decode("utf-8-sig"))
            except UnicodeDecodeError:
                data = json.loads(raw.decode("gbk"))
        except Exception as e:
            QMessageBox.critical(self, "读取失败", f"解析题目 JSON 失败：\n{e}")
            return False

        wav_name = data.get("audio_file", "")
        wav_path = os.path.join(folder_path, wav_name) if wav_name else ""
        if not wav_path or not os.path.exists(wav_path):
            wav_files = [
                f for f in os.listdir(folder_path) if f.lower().endswith(".wav")
            ]
            if wav_files:
                wav_path = os.path.join(folder_path, wav_files[0])
            else:
                QMessageBox.warning(
                    self,
                    "未找到音频文件",
                    f"所选文件夹中缺少 .wav 音频文件：\n{folder_path}",
                )
                return False

        try:
            audio_arr, sr = load_wav_as_float32(wav_path)
        except Exception as e:
            QMessageBox.critical(self, "音频加载失败", f"无法读取 WAV 文件：\n{e}")
            return False

        self.current_dir = folder_path
        self.exam_data = data
        self.base_audio = audio_arr
        self.sample_rate = sr
        self.playback_speed = 1.0
        self.playback_audio = audio_arr
        self.total_duration = len(self.playback_audio) / max(1, self.sample_rate)
        self._refresh_speed_button_styles()

        title = data.get("title", os.path.basename(folder_path))
        self.lbl_exam_title.setText(title)

        self.btn_play_pause.setEnabled(True)
        self.btn_play_pause.setText("▶ Play")
        self.btn_stop.setEnabled(False)
        self.slider_progress.setEnabled(True)
        self.slider_progress.setValue(0)
        self._update_timer_label(0.0, self.total_duration)

        self._build_question_cards(data.get("questions", []))
        self.is_submitted = False
        self.chk_show_prompts.setChecked(False)
        self.btn_submit.setEnabled(True)
        self.btn_reset_quiz.setEnabled(True)
        self.lbl_score.setText(f"Loaded {len(self.question_widgets)} questions.")
        self._render_locked_review_placeholder()
        return True

    def _build_question_cards(self, questions: list):
        while self.questions_vbox.count():
            item = self.questions_vbox.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.question_widgets.clear()

        for q in questions:
            card = QFrame()
            card.setStyleSheet("""
                QFrame {
                    background-color: #F8F9FB;
                    border: 1px solid #E5E8EE;
                    border-radius: 14px;
                }
            """)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(6)

            header_row = QHBoxLayout()
            q_id = q.get("id", "?")
            q_prompt_text = q.get("display_text") or q.get("spoken_text", "")
            lbl_q = QLabel(f"Question {q_id}（请听音频提问）")
            lbl_q.setStyleSheet("font-weight: bold; color: #1F2329; border: none;")
            lbl_q.setWordWrap(True)

            lbl_result = QLabel("")
            lbl_result.setStyleSheet("font-weight: bold; border: none;")

            header_row.addWidget(lbl_q, stretch=1)
            header_row.addWidget(lbl_result)
            card_layout.addLayout(header_row)

            btn_group = QButtonGroup(card)
            option_rows: Dict[str, OptionRowWidget] = {}
            options = q.get("options", {})

            for opt_key in ("A", "B", "C", "D"):
                opt_text = options.get(opt_key, "")
                opt_row = OptionRowWidget(opt_key, opt_text, parent=card)
                btn_group.addButton(opt_row.radio)
                option_rows[opt_key] = opt_row
                card_layout.addWidget(opt_row)

            self.questions_vbox.addWidget(card)
            self.question_widgets.append(
                {
                    "id": q_id,
                    "prompt_text": q_prompt_text,
                    "answer": str(q.get("answer", "")).strip().upper(),
                    "explanation": q.get("explanation", ""),
                    "lbl_q": lbl_q,
                    "lbl_result": lbl_result,
                    "btn_group": btn_group,
                    "option_rows": option_rows,
                }
            )

        self.questions_vbox.addStretch()

    def _toggle_question_prompts_visibility(self, show: bool):
        for item in self.question_widgets:
            q_id = item["id"]
            if show or self.is_submitted:
                item["lbl_q"].setText(f"Question {q_id}: {item['prompt_text']}")
            else:
                item["lbl_q"].setText(f"Question {q_id}（请听音频提问）")

    def submit_and_grade(self):
        if not self.question_widgets:
            return

        total = len(self.question_widgets)
        correct_count = 0
        unanswered = 0

        for item in self.question_widgets:
            chosen = ""
            for k, row in item["option_rows"].items():
                if row.radio.isChecked():
                    chosen = k
                    break
            if not chosen:
                unanswered += 1

        if unanswered > 0 and not self.is_submitted:
            reply = QMessageBox.question(
                self,
                "确认交卷",
                f"还有 {unanswered} 道题未选择答案，确定现在交卷批改吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply != QMessageBox.Yes:
                return

        self.is_submitted = True
        self.chk_show_prompts.setChecked(True)

        for item in self.question_widgets:
            chosen = ""
            for k, row in item["option_rows"].items():
                row.radio.setEnabled(False)
                if row.radio.isChecked():
                    chosen = k

            correct_ans = item["answer"]
            if chosen == correct_ans:
                correct_count += 1
                item["lbl_result"].setText("[正确]")
                item["lbl_result"].setStyleSheet("color: #16A34A; font-weight: bold; border: none;")
            else:
                chosen_disp = chosen if chosen else "未选"
                item["lbl_result"].setText(f"[错误] 选 {chosen_disp} / 正解 {correct_ans}")
                item["lbl_result"].setStyleSheet("color: #DC2626; font-weight: bold; border: none;")

            for k, row in item["option_rows"].items():
                if k == correct_ans:
                    row.set_grade_state("correct")
                elif k == chosen and chosen != correct_ans:
                    row.set_grade_state("wrong")
                else:
                    row.set_grade_state("normal")

        pct = int((correct_count / total) * 100)
        self.lbl_score.setText(f"Score: {correct_count}/{total} ({pct}%)")
        self._render_full_transcript_and_explanations()

    def reset_quiz_answers(self):
        self.is_submitted = False
        self.chk_show_prompts.setChecked(False)
        for item in self.question_widgets:
            item["lbl_result"].setText("")
            item["btn_group"].setExclusive(False)
            for row in item["option_rows"].values():
                row.radio.setChecked(False)
                row.radio.setEnabled(True)
                row.set_grade_state("normal")
            item["btn_group"].setExclusive(True)

        self._toggle_question_prompts_visibility(False)
        self.lbl_score.setText("Reset complete.")
        self._render_locked_review_placeholder()

    def _render_full_transcript_and_explanations(self):
        dialogue = self.exam_data.get("dialogue", [])
        questions = self.exam_data.get("questions", [])

        html_parts = ['<h3 style="color:#D96543; margin-top:0;">一、 对话原文（Transcript）</h3>']
        for turn in dialogue:
            role = str(turn.get("role", "M")).strip().upper()
            role_tag = (
                '<span style="color:#2563EB; font-weight:bold;">Man:</span>'
                if role == "M"
                else '<span style="color:#D96543; font-weight:bold;">Woman:</span>'
            )
            disp = turn.get("display_text") or turn.get("spoken_text", "")
            html_parts.append(f"<p>{role_tag} {disp}</p>")

        html_parts.append('<hr style="border:none; border-top:1px solid #E5E7EB; margin:16px 0;">')
        html_parts.append('<h3 style="color:#D96543;">二、 逐题答案与解析</h3>')
        for q in questions:
            q_id = q.get("id", "?")
            q_disp = q.get("display_text") or q.get("spoken_text", "")
            ans = q.get("answer", "")
            exp = q.get("explanation", "")
            html_parts.append(
                f'<p style="margin-bottom:4px;"><b>Question {q_id}:</b> {q_disp}</p>'
                f'<p style="margin-top:0; color:#16A34A;"><b>正确答案：{ans}</b></p>'
                f'<p style="color:#4A515E; background:#FFFFFF; padding:8px 10px; border-radius:8px; border:1px solid #E5E7EB;">{exp}</p>'
            )

        self.review_browser.setHtml("".join(html_parts))

    def set_playback_speed(self, speed: float):
        if self.base_audio is None:
            return
        was_playing = self.is_playing
        current_ratio = 0.0
        if self.total_duration > 0:
            elapsed = (
                (time.time() - self.play_start_time) + self.play_offset_sec
                if self.is_playing
                else self.pause_offset_sec
            )
            current_ratio = max(0.0, min(1.0, elapsed / self.total_duration))

        self.stop_playback(reset=False)
        self.playback_speed = speed
        self.playback_audio = resample_speed(self.base_audio, speed)
        self.total_duration = len(self.playback_audio) / max(1, self.sample_rate)
        self.pause_offset_sec = current_ratio * self.total_duration
        self._refresh_speed_button_styles()

        if was_playing:
            self._start_audio_from(self.pause_offset_sec)
        else:
            self._update_timer_label(self.pause_offset_sec, self.total_duration)

    def _refresh_speed_button_styles(self):
        for spd, btn in self.speed_buttons.items():
            if abs(spd - self.playback_speed) < 1e-3:
                btn.setObjectName("pillBtnActive")
            else:
                btn.setObjectName("pillBtn")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def toggle_play_pause(self):
        if self.playback_audio is None:
            return
        if self.is_playing:
            elapsed = (time.time() - self.play_start_time) + self.play_offset_sec
            self.pause_offset_sec = max(0.0, min(elapsed, self.total_duration))
            sd.stop()
            self.is_playing = False
            self.is_paused = True
            self.progress_timer.stop()
            self.btn_play_pause.setText("▶ Resume")
        elif self.is_paused:
            if self.pause_offset_sec >= self.total_duration:
                self.pause_offset_sec = 0.0
            self._start_audio_from(self.pause_offset_sec)
        else:
            self._start_audio_from(0.0)

    def _start_audio_from(self, from_sec: float):
        if self.playback_audio is None or len(self.playback_audio) == 0:
            return
        start_idx = int(from_sec * self.sample_rate)
        start_idx = max(0, min(start_idx, len(self.playback_audio)))

        sd.stop()
        sd.play(self.playback_audio[start_idx:], samplerate=self.sample_rate)

        self.play_start_time = time.time()
        self.play_offset_sec = from_sec
        self.is_playing = True
        self.is_paused = False
        self.btn_play_pause.setText("❚❚ Pause")
        self.btn_stop.setEnabled(True)
        self.progress_timer.start()

    def stop_playback(self, reset: bool = True):
        sd.stop()
        self.is_playing = False
        self.is_paused = False
        self.progress_timer.stop()
        if reset:
            self.pause_offset_sec = 0.0
            self.slider_progress.setValue(0)
            self._update_timer_label(0.0, self.total_duration)
        self.btn_play_pause.setText("▶ Play")
        self.btn_stop.setEnabled(False)

    def _update_progress_ui(self):
        if not self.is_playing:
            return
        elapsed = (time.time() - self.play_start_time) + self.play_offset_sec
        if elapsed >= self.total_duration:
            self.stop_playback(reset=True)
            return

        if not self.is_slider_dragging and self.total_duration > 0:
            ratio = elapsed / self.total_duration
            self.slider_progress.setValue(int(ratio * 1000))
            self._update_timer_label(elapsed, self.total_duration)

    def _update_timer_label(self, cur_sec: float, tot_sec: float):
        cm, cs = divmod(int(cur_sec), 60)
        tm, ts = divmod(int(tot_sec), 60)
        self.lbl_timer.setText(f"{cm:02d}:{cs:02d} / {tm:02d}:{ts:02d}")

    def _on_slider_pressed(self):
        self.is_slider_dragging = True

    def _on_slider_released(self):
        self.is_slider_dragging = False
        if self.playback_audio is not None and self.total_duration > 0:
            target = (self.slider_progress.value() / 1000.0) * self.total_duration
            self._start_audio_from(target)

    def _on_slider_jump(self, ratio: float):
        if self.playback_audio is not None and self.total_duration > 0:
            target = ratio * self.total_duration
            self._start_audio_from(target)

    def closeEvent(self, event):
        self.stop_playback(reset=True)
        super().closeEvent(event)

    def mousePressEvent(self, event):
        if not self.handle_mouse_press(event):
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.handle_mouse_move(event):
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.handle_mouse_release(event)
        super().mouseReleaseEvent(event)