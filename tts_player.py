import ctypes
from ctypes import wintypes
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import re
import subprocess
import sys
import threading
import time
import warnings
import wave

# =========================================================================
# 1. Core Priority: PyTorch must be imported before any Qt modules
# =========================================================================
import numpy as np
import torch

# =========================================================================
# 2. eSpeak NG Auto-detection
# =========================================================================
def auto_detect_espeak():
    possible_paths = [
        r"C:\Program Files\eSpeak NG",
        r"C:\Program Files (x86)\eSpeak NG",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "espeak-ng"),
        os.path.expanduser(r"~\miniconda3\Library\bin"),
        os.path.expanduser(r"~\anaconda3\Library\bin"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            if p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = p + ";" + os.environ.get("PATH", "")
            os.environ["PHONEMIZER_ESPEAK_PATH"] = p
            break


auto_detect_espeak()
warnings.filterwarnings("ignore")

# =========================================================================
# Helper: Dismiss external lingering ToolTip / OSD popups
# =========================================================================
def dismiss_external_osd():
    """Clear lingering ToolTip OSD windows from external scripts or AHK."""
    try:
        def enum_proc(hwnd, lparam):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                class_name = ctypes.create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
                if class_name.value == "tooltips_class32":
                    ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
                    ctypes.windll.user32.ShowWindow(hwnd, 0)
                else:
                    title = ctypes.create_unicode_buffer(512)
                    ctypes.windll.user32.GetWindowTextW(hwnd, title, 512)
                    if "54322" in title.value or "Kokoro TTS" in title.value:
                        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
                        ctypes.windll.user32.ShowWindow(hwnd, 0)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    except Exception:
        pass


# =========================================================================
# 3. Audio & GUI Modules
# =========================================================================
from kokoro import KPipeline
from PyQt5.QtCore import (
    QEvent,
    QFileSystemWatcher,
    QPoint,
    QRect,
    QSettings,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QCursor, QFont, QIcon, QTextCharFormat, QTextCursor
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
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

SAMPLE_RATE = 24000
EDGE_MARGIN = 8
SINGLE_INSTANCE_KEY = "KokoroTTSPlayer_SingleInstance_Server"
MUTEX_NAME = "Local\\KokoroTTSPlayer_SingleInstance_Mutex"

# Unified Design System Tokens (Aligned with window_list_server.py)
FONT_STACK = '"Consolas", "Microsoft YaHei UI", monospace'
PALETTE_BLACK = "#18181B"
PALETTE_BLACK_SUB = "#52525B"
PALETTE_ROSE = "#E11D48"
PALETTE_ROSE_DEEP = "#BE123C"
PALETTE_ORANGE = "#C2410C"
PALETTE_ORANGE_DEEP = "#9A3412"

global_mutex = None


# =========================================================================
# 4. Dual-Track Text Processing & Learning Annotation Engine
# =========================================================================
SPEAKER_PREFIX_RE = re.compile(
    r"^\s*(?:[\*\-\•]\s*)?(?:\*{0,2})(W|Woman|F|Female|M|Man|Male)\s*:(?:\*{0,2})\s*",
    re.IGNORECASE,
)

HTML_TOKEN_RE = re.compile(
    r"(?P<speaker>^(?:\*{0,2})(?:W|Woman|F|Female|M|Man|Male)\s*:(?:\*{0,2}))"
    r"|(?P<weak>\*\([^)]+\)\*)"
    r"|(?P<bold>\*\*[^*]+\*\*)"
    r"|(?P<phonetic>\[[^\]]+\])"
    r"|(?P<silent>\([a-zA-Z\']+\))"
    r"|(?P<tie>‿)"
    r"|(?P<slash>\s*/\s*)",
    re.IGNORECASE,
)


def detect_speaker(line: str):
    """Return ('F'|'M'|'DEFAULT', line_without_speaker_prefix)."""
    m = SPEAKER_PREFIX_RE.match(line)
    if not m:
        return "DEFAULT", line.strip()
    tag = m.group(1).upper()
    speaker = "F" if tag in ("W", "WOMAN", "F", "FEMALE") else "M"
    return speaker, line[m.end() :].strip()


def clean_annotated_for_tts(text: str) -> str:
    """
    Convert annotated learning text into pure, natural English for Kokoro TTS:
    - Strip speaker tags (W:, M:)
    - Remove bracketed phonetics / citations: [ði], [mə-ˈtɪ-kjə-ləs]
    - Restore weak glide syllables: *(to de)* -> to de
    - Restore silent letters: nee(d) -> need
    - Remove stress markers: de**cide** -> decide
    - Convert linking tie '‿' into natural word space
    - Convert sense-group slash '/' into a soft comma pause
    - Strip CJK characters and non-vocal symbols
    """
    if not text:
        return ""

    _, t = detect_speaker(text)

    # Remove all bracketed annotations: [ði], [mə-ˈtɪ-kjə-ləs], etc.
    t = re.sub(r"\[[^\]]*\]", "", t)

    # Unwrap weak-form glide parentheses: *(I)* -> I, *(to de)* -> to de
    t = re.sub(r"\*\(([^)]+)\)\*", r"\1", t)

    # Unwrap silent-letter parentheses: nee(d) -> need, con**sis**(ts) -> con**sis**ts
    t = re.sub(r"\(([a-zA-Z\']+)\)", r"\1", t)

    # Remove bold/italic asterisks without inserting spaces inside words
    t = t.replace("**", "").replace("*", "")

    # Convert linking arc '‿' to word boundary space
    t = t.replace("‿", " ")

    # Convert sense-group slash '/' to natural comma pause
    t = re.sub(r"\s*/+\s*", ", ", t)
    t = re.sub(r"([.!?,;:])\s*,", r"\1", t)

    # Filter CJK characters and CJK punctuation
    t = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", " ", t)
    t = re.sub(
        r"[\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]",
        " ",
        t,
    )

    # Clean remaining non-vocal symbols while keeping standard punctuation
    t = re.sub(r"[`~@#$%^&*()_+=\|\\<>{}\[\]\t]+", " ", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"[ ]{2,}", " ", t).strip()
    return t


def sanitize_text(text: str) -> str:
    """Sanitize plain text lines while preserving line breaks."""
    if not text:
        return ""
    lines = []
    for line in text.splitlines():
        cleaned = clean_annotated_for_tts(line)
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines).strip()


def has_learning_annotations(text: str) -> bool:
    """Check if text contains visual learning markup or speaker labels."""
    if not text:
        return False
    patterns = [
        r"\*\*[^*]+\*\*",
        r"\*\([^)]+\)\*",
        r"\([a-zA-Z\']+\)",
        r"\[[^\]]+\]",
        r"‿",
        r"\s/\s",
        r"^\s*(?:[\*\-\•]\s*)?(?:\*{0,2})(?:W|M|Woman|Man)\s*:",
    ]
    return any(re.search(p, text, re.MULTILINE | re.IGNORECASE) for p in patterns)


def parse_learning_material(raw_input):
    """
    Parse raw text or JSON payload into:
      - display_text: cleaned markdown/annotated lines for rich display
      - drills: list of {"phrase": str, "tip": str} for interactive 2-column Drill Cards
    Supports Formats 1, 2, 3, 4 and structured JSON.
    """
    drills = []
    seen_drills = set()

    def add_drill(phrase: str, tip: str = ""):
        clean_p = clean_annotated_for_tts(phrase).strip(" ,.-:;")
        if not clean_p or not re.search(r"[a-zA-Z]", clean_p):
            return
        if len(clean_p) > 65:
            return
        key = clean_p.lower()
        if key not in seen_drills:
            seen_drills.add(key)
            clean_tip = tip.replace("`", "").strip()
            drills.append({"phrase": clean_p, "tip": clean_tip})

    # 1. Handle JSON payload if provided
    if isinstance(raw_input, dict):
        dialogue = raw_input.get("dialogue") or raw_input.get("text") or ""
        for item in raw_input.get("drills", []):
            if isinstance(item, dict):
                add_drill(item.get("phrase", ""), item.get("tip", ""))
            elif isinstance(item, str):
                add_drill(item, "")
        parsed = parse_learning_material(dialogue)
        merged_drills = drills + [
            d for d in parsed["drills"] if d["phrase"].lower() not in seen_drills
        ]
        return {"display_text": parsed["display_text"], "drills": merged_drills[:20]}

    text = str(raw_input or "").strip()
    if not text:
        return {"display_text": "", "drills": []}

    # Try parsing JSON string
    if text.startswith("{") and text.endswith("}"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return parse_learning_material(obj)
        except Exception:
            pass

    # Strip trailing citation tags if copied from AI output
    text = re.sub(r"\[" + "cite" + r":[^\]]*\]", "", text)

    dialogue_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # Skip Markdown headings, horizontal rules, and legend blockquotes
        if line.startswith("#") or re.match(r"^-{3,}$", line) or line.startswith(">"):
            continue

        # Format 2: Markdown table row -> extract bold phrase & explanation into Drill Cards
        if line.startswith("|") and line.endswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells or all(re.match(r"^:?-+:?$", c) for c in cells if c):
                continue
            first_col = cells[0]
            if re.search(r"[\u4e00-\u9fff]", first_col):
                # Header row like "原文词块"
                continue
            tip_parts = [c for c in cells[1:] if c]
            add_drill(first_col, " · ".join(tip_parts))
            continue

        # Check if line is a dialogue line (W: / M:)
        is_dialogue = bool(SPEAKER_PREFIX_RE.match(line))
        if is_dialogue:
            cleaned_diag = re.sub(r"^[\*\-\•]\s+", "", line)
            dialogue_lines.append(cleaned_diag)
            continue

        # Format 3: Bullet list explaining a trap phrase (contains CJK + bold phrase)
        if re.match(r"^[\*\-\•]\s+", line) and re.search(r"[\u4e00-\u9fff]", line):
            bold_matches = re.findall(r"\*\*([^*]+)\*\*", line)
            if bold_matches:
                tip_text = re.sub(r"\*\*[^*]+\*\*", "", line).lstrip("*-• ：:").strip()
                for bm in bold_matches:
                    add_drill(bm, tip_text)
            continue

        # Normal English prose/dialogue line without W:/M:
        if re.search(r"[a-zA-Z]", line):
            cjk_count = len(re.findall(r"[\u4e00-\u9fff]", line))
            eng_count = len(re.findall(r"[a-zA-Z]", line))
            if cjk_count > eng_count:
                continue
            dialogue_lines.append(re.sub(r"^[\*\-\•]\s+", "", line))

    # Auto-extract notable linking/phonetic chunks from annotated dialogue if drills are sparse
    if len(drills) < 4 and dialogue_lines:
        for dline in dialogue_lines:
            for m in re.finditer(
                r"(?:[A-Za-z\*\(\)]+‿[A-Za-z\*\(\)‿]+|[A-Za-z\*\(\)]+\s*\[[^\]]+\])",
                dline,
            ):
                snippet = m.group(0)
                phon_m = re.search(r"\[([^\]]+)\]", snippet)
                tip = f"实际耳感: [{phon_m.group(1)}]" if phon_m else "连读 / 音变滑音"
                add_drill(snippet, tip)
                if len(drills) >= 12:
                    break

    # Fallback: if user pasted ONLY a table/drill list without dialogue text, use drill phrases
    if not dialogue_lines and drills:
        dialogue_lines = [d["phrase"] for d in drills]

    return {
        "display_text": "\n".join(dialogue_lines).strip(),
        "drills": drills[:20],
    }


def render_annotated_html(annotated_text: str, font_size: int = 18) -> str:
    """Render visual annotations into clean, high-readability HTML paragraphs (Rose + Dark Orange + Black)."""
    if not annotated_text:
        return ""

    def token_replacer(match):
        if match.group("speaker"):
            raw_sp = match.group("speaker").replace("*", "").strip()
            is_female = raw_sp.upper().startswith(("W", "F"))
            bg = "rgba(225, 29, 72, 22)" if is_female else "rgba(194, 65, 12, 22)"
            fg = PALETTE_ROSE if is_female else PALETTE_ORANGE
            return (
                f'<span style="background-color:{bg}; color:{fg}; '
                f'font-weight:bold; padding:1px 7px; border-radius:5px;">'
                f"{raw_sp}</span>"
            )
        if match.group("weak"):
            inner = match.group("weak")[2:-2]
            return f'<span style="color:#A1A1AA; font-style:italic;">({inner})</span>'
        if match.group("bold"):
            inner = match.group("bold")[2:-2]
            return (
                f'<span style="font-weight:bold; color:{PALETTE_BLACK}; '
                f'background-color:#FFF1F2; border-radius:3px; padding:0px 2px;">{inner}</span>'
            )
        if match.group("phonetic"):
            val = match.group("phonetic")
            return (
                f'<span style="color:{PALETTE_ROSE}; font-size:{max(11, int(font_size * 0.78))}px; '
                f'font-weight:bold;">{val}</span>'
            )
        if match.group("silent"):
            val = match.group("silent")
            return f'<span style="color:#A1A1AA;">{val}</span>'
        if match.group("tie"):
            return f'<span style="color:{PALETTE_ORANGE}; font-weight:bold;">‿</span>'
        if match.group("slash"):
            return '<span style="color:#D4D4D8; font-weight:bold;"> │ </span>'
        return match.group(0)

    paragraphs = []
    for line in annotated_text.splitlines():
        if not line.strip():
            continue
        escaped = html.escape(line)
        styled_line = HTML_TOKEN_RE.sub(token_replacer, escaped)
        paragraphs.append(
            f'<p style="margin: 0px 0px 10px 0px; line-height: 1.58;">{styled_line}</p>'
        )

    return "".join(paragraphs)


# =========================================================================
# 5. Sentence Splitting & Timeline Alignment Engine
# =========================================================================
ABBREVIATIONS = [
    r"Mr\.",
    r"Mrs\.",
    r"Ms\.",
    r"Dr\.",
    r"Prof\.",
    r"Sr\.",
    r"Jr\.",
    r"vs\.",
    r"etc\.",
    r"e\.g\.",
    r"i\.e\.",
    r"U\.S\.",
    r"U\.K\.",
    r"approx\.",
    r"dept\.",
    r"est\.",
    r"min\.",
    r"max\.",
    r"no\.",
    r"vol\.",
]


def split_into_sentences(text):
    if not text:
        return []

    raw = text.strip()
    normalized = re.sub(r"[ \t]+", " ", raw)

    # Protect bracketed phonetics from internal splitting
    brackets = []

    def mask_bracket(m):
        brackets.append(m.group(0))
        return f"<BRK_{len(brackets) - 1}>"

    normalized = re.sub(r"\[[^\]]+\]", mask_bracket, normalized)
    normalized = re.sub(r"\.{3,}", "<ELLIPSIS>", normalized)
    normalized = re.sub(r"…", "<ELLIPSIS>", normalized)
    normalized = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", normalized)

    for abbr in ABBREVIATIONS:

        def replace_abbr(m):
            return m.group(0).replace(".", "<DOT>")

        normalized = re.sub(
            r"\b" + abbr, replace_abbr, normalized, flags=re.IGNORECASE
        )

    pattern = r"([.!?]+[\"\'”’\)\]]*)\s+"
    marked = re.sub(pattern, r"\1<SPLIT>", normalized)

    chunks = marked.split("<SPLIT>")
    sentences = []
    for s in chunks:
        s_clean = s.replace("<DOT>", ".").replace("<ELLIPSIS>", "...")
        for idx, brk in enumerate(brackets):
            s_clean = s_clean.replace(f"<BRK_{idx}>", brk)
        s_clean = s_clean.strip()
        if s_clean:
            sentences.append(s_clean)
    return sentences


def build_timeline_segments(visible_text: str):
    """
    Map visible text in QTextEdit into aligned sentence segments:
    Returns list of dicts:
      {"start_char": int, "end_char": int, "speaker": "F"|"M"|"DEFAULT", "tts_text": str}
    """
    segments = []
    if not visible_text:
        return segments

    offset = 0
    lines = visible_text.split("\n")
    for line_idx, line in enumerate(lines):
        line_len = len(line)
        if line.strip():
            speaker, _ = detect_speaker(line)
            sentences = split_into_sentences(line)
            search_cursor = 0
            for sent in sentences:
                pos = line.find(sent, search_cursor)
                if pos == -1:
                    pos = search_cursor
                end_pos = pos + len(sent)
                search_cursor = end_pos

                tts_clean = clean_annotated_for_tts(sent)
                if tts_clean and re.search(r"[a-zA-Z0-9]", tts_clean):
                    segments.append(
                        {
                            "start_char": offset + pos,
                            "end_char": offset + end_pos,
                            "speaker": speaker,
                            "tts_text": tts_clean,
                        }
                    )
        offset += line_len + (1 if line_idx < len(lines) - 1 else 0)

    return segments


# =========================================================================
# 6. Shared Thread-Safe Kokoro Engine
# =========================================================================
class SharedKokoroEngine:
    """Thread-safe singleton wrapper around KPipeline to share VRAM/RAM across threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.pipeline = None
        self.current_lang = None
        self.current_device = None

    def get_pipeline(self, lang_code: str, device: str, status_cb=None):
        if (
            self.pipeline is None
            or lang_code != self.current_lang
            or device != self.current_device
        ):
            if status_cb:
                status_cb("LOADING MODEL...")
            if self.pipeline is not None:
                del self.pipeline
                self.pipeline = None
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()

            self.current_lang = lang_code
            self.current_device = device
            self.pipeline = KPipeline(lang_code=lang_code, device=device)
        return self.pipeline


shared_kokoro = SharedKokoroEngine()


# =========================================================================
# 7. Two-Column Drill Card Item Widget (分栏词卡组件)
# =========================================================================
class DrillCardItem(QFrame):
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


# =========================================================================
# 8. Smart Interactive Text Input Box
# =========================================================================
class CyberTextEdit(QTextEdit):
    sentence_double_clicked = pyqtSignal(int)
    selection_read_requested = pyqtSignal(str)

    def __init__(self, parent_player=None):
        super().__init__()
        self.player = parent_player
        self.is_rich_annotated = False

    def set_annotated_content(self, display_text: str, font_size: int = 18):
        self.blockSignals(True)
        if has_learning_annotations(display_text):
            self.is_rich_annotated = True
            self.setHtml(render_annotated_html(display_text, font_size=font_size))
        else:
            self.is_rich_annotated = False
            self.setPlainText(display_text)
        self.blockSignals(False)
        if self.player:
            self.player.on_text_changed()

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


# =========================================================================
# 9. Clickable Seek Slider
# =========================================================================
class ClickableSlider(QSlider):
    jump_requested = pyqtSignal(float)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            val_ratio = event.pos().x() / max(1, self.width())
            val_ratio = max(0.0, min(1.0, val_ratio))
            self.setValue(int(val_ratio * self.maximum()))
            self.jump_requested.emit(val_ratio)
            event.accept()
        super().mousePressEvent(event)


# =========================================================================
# 10. Local HTTP Integration Service (Supports Plain Text & JSON)
# =========================================================================
class RequestHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length).decode("utf-8", errors="ignore")

        if self.path == "/tts":
            self.server.text_received_signal.emit(post_data)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        elif self.path == "/mode":
            mode_str = post_data.strip()
            if hasattr(self.server, "mode_change_signal"):
                self.server.mode_change_signal.emit(mode_str)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"MODE_UPDATED")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/mode":
            mode_val = getattr(self.server, "current_mode_val", "1")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(mode_val.encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class HttpServerThread(QThread):

    def __init__(self, text_signal, mode_signal=None):
        super().__init__()
        self.text_signal = text_signal
        self.mode_signal = mode_signal
        self.server = None

    def run(self):
        try:
            HTTPServer.allow_reuse_address = True
            self.server = HTTPServer(("127.0.0.1", 54322), RequestHandler)
            self.server.text_received_signal = self.text_signal
            if self.mode_signal:
                self.server.mode_change_signal = self.mode_signal
            self.server.current_mode_val = "1"
            self.server.serve_forever()
        except Exception as e:
            print("HTTP Server Notice:", e)

    def set_current_mode(self, mode_str):
        if self.server:
            self.server.current_mode_val = str(mode_str)

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass


# =========================================================================
# 11. TTS Synthesis Threads (Dialogue Timeline + Quick Drill Phrase)
# =========================================================================
def resolve_voice_path(voice_key: str) -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    local_voice_path = os.path.join(base_dir, "voices", f"{voice_key}.pt")
    if not os.path.exists(local_voice_path):
        local_voice_path = os.path.join("voices", f"{voice_key}.pt")
    return local_voice_path if os.path.exists(local_voice_path) else voice_key


class TtsSynthesizerThread(QThread):
    synth_finished = pyqtSignal(object)
    synth_error = pyqtSignal(str)
    status_updated = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.target_device = "cuda" if torch.cuda.is_available() else "cpu"
        self.is_cancelled = False

        self.segments = []
        self.female_voice = "af_heart"
        self.male_voice = "am_michael"
        self.speed = 1.0
        self.shadowing = False
        self.pause_sec = 1.5
        self.progressive = False

    def start_synth(
        self,
        segments,
        female_voice,
        male_voice,
        speed,
        shadowing,
        pause_sec,
        device="cpu",
        progressive=False,
    ):
        self.is_cancelled = False
        self.segments = segments
        self.female_voice = female_voice
        self.male_voice = male_voice
        self.speed = speed
        self.shadowing = shadowing
        self.pause_sec = pause_sec
        self.target_device = device
        self.progressive = progressive
        self.start()

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            f_key = self.female_voice.split(" ")[0]
            m_key = self.male_voice.split(" ")[0]

            valid_langs = ["a", "b", "z", "j", "e", "f", "i", "h", "p"]
            target_lang = f_key[0] if len(f_key) > 0 and f_key[0] in valid_langs else "a"

            actual_f_voice = resolve_voice_path(f_key)
            actual_m_voice = resolve_voice_path(m_key)

            speed_passes = (
                [max(0.5, round(self.speed * 0.75, 2)), self.speed]
                if self.progressive
                else [self.speed]
            )

            all_audios = []
            timeline = []
            current_samples = 0

            silence_padding = np.zeros(
                int(SAMPLE_RATE * self.pause_sec), dtype=np.float32
            )
            pass_gap = np.zeros(int(SAMPLE_RATE * 0.8), dtype=np.float32)

            with shared_kokoro.lock:
                pipeline = shared_kokoro.get_pipeline(
                    target_lang,
                    self.target_device,
                    status_cb=self.status_updated.emit,
                )

                for pass_idx, pass_speed in enumerate(speed_passes):
                    if self.is_cancelled:
                        return

                    if len(speed_passes) > 1:
                        self.status_updated.emit(
                            f"SYNTHESIZING PASS {pass_idx + 1}/{len(speed_passes)} ({pass_speed:.2f}x)..."
                        )
                    else:
                        self.status_updated.emit("SYNTHESIZING...")

                    for idx, seg in enumerate(self.segments):
                        if self.is_cancelled:
                            return

                        speaker = seg.get("speaker", "DEFAULT")
                        voice_to_use = (
                            actual_m_voice if speaker == "M" else actual_f_voice
                        )

                        seg_audios = []
                        generator = pipeline(
                            seg["tts_text"], voice=voice_to_use, speed=pass_speed
                        )
                        for _, _, audio in generator:
                            if self.is_cancelled:
                                return
                            if isinstance(audio, torch.Tensor):
                                audio_np = audio.detach().cpu().numpy()
                            else:
                                audio_np = np.asarray(audio)
                            seg_audios.append(audio_np.astype(np.float32))

                        if seg_audios:
                            seg_cat = np.concatenate(seg_audios)
                            seg_start_sec = current_samples / SAMPLE_RATE
                            current_samples += len(seg_cat)
                            seg_end_sec = current_samples / SAMPLE_RATE

                            all_audios.append(seg_cat)
                            timeline.append(
                                {
                                    "start_sec": seg_start_sec,
                                    "end_sec": seg_end_sec,
                                    "start_char": seg["start_char"],
                                    "end_char": seg["end_char"],
                                    "speaker": speaker,
                                    "speed": pass_speed,
                                }
                            )

                        if self.shadowing and idx < len(self.segments) - 1:
                            all_audios.append(silence_padding)
                            current_samples += len(silence_padding)

                    if pass_idx < len(speed_passes) - 1:
                        all_audios.append(pass_gap)
                        current_samples += len(pass_gap)

            if self.is_cancelled or not all_audios:
                return

            combined_audio = np.concatenate(all_audios)
            self.synth_finished.emit(
                {"audio": combined_audio, "timeline": timeline}
            )

        except Exception as e:
            err_str = str(e)
            if "[Errno 2]" in err_str or "espeak" in err_str.lower():
                self.synth_error.emit(
                    "eSpeak NG engine not found!\nPlease ensure espeak-ng is available in the root folder or system path."
                )
            else:
                self.synth_error.emit(err_str)


class DrillSynthesizerThread(QThread):
    """Lightweight worker for instant single-phrase Drill Card / selection playback."""
    drill_ready = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.phrase = ""
        self.voice = "af_heart"
        self.speed = 0.85
        self.device = "cpu"

    def play_phrase(self, phrase: str, voice: str, speed: float = 0.85, device: str = "cpu"):
        self.phrase = clean_annotated_for_tts(phrase)
        self.voice = voice
        self.speed = speed
        self.device = device
        if self.phrase:
            self.start()

    def run(self):
        try:
            v_key = self.voice.split(" ")[0]
            valid_langs = ["a", "b", "z", "j", "e", "f", "i", "h", "p"]
            target_lang = v_key[0] if len(v_key) > 0 and v_key[0] in valid_langs else "a"
            actual_voice = resolve_voice_path(v_key)

            chunks = []
            with shared_kokoro.lock:
                pipeline = shared_kokoro.get_pipeline(target_lang, self.device)
                for _, _, audio in pipeline(
                    self.phrase, voice=actual_voice, speed=self.speed
                ):
                    if isinstance(audio, torch.Tensor):
                        chunks.append(audio.detach().cpu().numpy().astype(np.float32))
                    else:
                        chunks.append(np.asarray(audio, dtype=np.float32))

            if chunks:
                self.drill_ready.emit(np.concatenate(chunks))
        except Exception:
            pass


# =========================================================================
# 12. Crisp Zinc + Rose Red + Dark Orange Stylesheet
#     (Unified with window_list_server.py Light Mode Palette & Typography)
# =========================================================================
CLEAN_ROSE_ZINC_STYLESHEET = f"""
QWidget#mainContainer {{
    background-color: #F4F4F5;
    border: 1px solid #E4E4E7;
    border-radius: 16px;
    font-family: {FONT_STACK};
}}
QLabel {{
    color: {PALETTE_BLACK_SUB};
    font-family: {FONT_STACK};
    font-size: 13px;
}}
QLabel#lblHighlightStats {{
    color: {PALETTE_ORANGE};
    font-size: 12.5px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLabel#lblTimeStatus {{
    color: {PALETTE_BLACK_SUB};
    font-size: 12.5px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QTextEdit {{
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 12px;
    color: {PALETTE_BLACK};
    font-family: {FONT_STACK};
    padding: 14px;
    selection-background-color: rgba(225, 29, 72, 32);
    selection-color: {PALETTE_ROSE_DEEP};
}}
QTextEdit:focus {{
    border: 1.5px solid {PALETTE_ROSE};
}}
QFrame#rightPanel {{
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 12px;
    font-family: {FONT_STACK};
}}
QFrame#drillBarFrame {{
    background-color: #FFFFFF;
    border: 1px solid #E4E4E7;
    border-radius: 12px;
    font-family: {FONT_STACK};
}}
QLabel#drillHeaderTitle {{
    color: {PALETTE_BLACK};
    font-size: 12px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLabel#drillCountBadge {{
    background-color: {PALETTE_BLACK};
    color: #FFFFFF;
    font-size: 11px;
    font-weight: bold;
    border-radius: 5px;
    padding: 1px 7px;
    font-family: {FONT_STACK};
}}
QFrame#drillCardItem {{
    background-color: #F4F4F5;
    border: 1px solid transparent;
    border-radius: 8px;
}}
QFrame#drillCardItem:hover {{
    background-color: rgba(225, 29, 72, 18);
    border: 1px solid rgba(225, 29, 72, 65);
}}
QLabel#drillCardIdx {{
    background-color: rgba(24, 24, 27, 16);
    color: {PALETTE_BLACK_SUB};
    font-size: 11px;
    font-weight: bold;
    border-radius: 4px;
    padding: 1px 3px;
    font-family: {FONT_STACK};
}}
QLabel#drillCardPhrase {{
    color: {PALETTE_BLACK};
    font-size: 13px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLabel#drillCardPlayIcon {{
    color: {PALETTE_ROSE};
    font-size: 12px;
}}
QLabel#drillCardTip {{
    color: {PALETTE_BLACK_SUB};
    font-size: 11.5px;
    padding-left: 26px;
    font-family: {FONT_STACK};
}}
QSplitter::handle {{
    background-color: transparent;
}}
QSplitter::handle:horizontal {{
    width: 8px;
    margin: 4px 2px;
}}
QSplitter::handle:vertical {{
    height: 7px;
    margin: 1px 28px;
    border-radius: 3px;
}}
QSplitter::handle:vertical:hover {{
    background-color: rgba(225, 29, 72, 45);
}}
QScrollBar:vertical {{
    width: 5px;
    background: transparent;
}}
QScrollBar::handle:vertical {{
    background: rgba(24, 24, 27, 65);
    border-radius: 2px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(225, 29, 72, 140);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    height: 5px;
    background: transparent;
}}
QScrollBar::handle:horizontal {{
    background: rgba(24, 24, 27, 65);
    border-radius: 2px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}
QComboBox {{
    background-color: #F4F4F5;
    border: none;
    border-radius: 7px;
    padding: 5px 10px;
    color: {PALETTE_BLACK};
    font-size: 12.5px;
    font-family: {FONT_STACK};
}}
QComboBox:hover {{
    background-color: #E4E4E7;
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: #FAFAFA;
    color: {PALETTE_BLACK};
    selection-background-color: rgba(225, 29, 72, 26);
    selection-color: {PALETTE_ROSE};
    border: 1px solid #D4D4D8;
    border-radius: 8px;
    font-size: 12.5px;
    padding: 4px;
    outline: none;
    font-family: {FONT_STACK};
}}
QSlider::groove:horizontal {{
    border: none;
    height: 5px;
    background: rgba(24, 24, 27, 36);
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {PALETTE_ROSE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: #FFFFFF;
    border: 2px solid {PALETTE_ROSE};
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    border: 2px solid {PALETTE_ORANGE};
}}
QCheckBox {{
    color: {PALETTE_BLACK};
    font-size: 12.5px;
    spacing: 8px;
    font-family: {FONT_STACK};
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1.5px solid #D4D4D8;
    background-color: #F4F4F5;
}}
QCheckBox::indicator:hover {{
    border-color: {PALETTE_ROSE};
}}
QCheckBox::indicator:checked {{
    background-color: {PALETTE_ROSE};
    border: 1.5px solid {PALETTE_ROSE};
}}
QSpinBox {{
    background-color: #F4F4F5;
    border: none;
    border-radius: 7px;
    color: {PALETTE_BLACK};
    padding: 4px 8px;
    font-size: 12.5px;
    font-family: {FONT_STACK};
}}
QSpinBox:hover {{
    background-color: #E4E4E7;
}}
QPushButton {{
    border-radius: 7px;
    font-size: 12.5px;
    font-weight: bold;
    padding: 7px 10px;
    font-family: {FONT_STACK};
}}
QPushButton#btnPlay {{
    background-color: {PALETTE_ROSE};
    color: #FFFFFF;
    border: none;
}}
QPushButton#btnPlay:hover {{
    background-color: {PALETTE_ROSE_DEEP};
}}
QPushButton#btnPlay:pressed {{
    background-color: #9F1239;
}}
QPushButton#btnStop {{
    background-color: {PALETTE_BLACK};
    color: #FFFFFF;
    border: none;
}}
QPushButton#btnStop:hover {{
    background-color: #27272A;
}}
QPushButton#btnSave {{
    background-color: #F4F4F5;
    border: none;
    color: {PALETTE_BLACK};
}}
QPushButton#btnSave:hover {{
    background-color: #E4E4E7;
    color: {PALETTE_ROSE};
}}
QPushButton#btnTopAction {{
    background-color: transparent;
    color: {PALETTE_BLACK_SUB};
    font-size: 12.5px;
    font-weight: bold;
    border-radius: 6px;
    padding: 3px 9px;
    font-family: {FONT_STACK};
}}
QPushButton#btnTopAction:hover {{
    background-color: #E4E4E7;
    color: {PALETTE_BLACK};
}}
QPushButton#quickSpeedBtn {{
    background-color: #FFFFFF;
    border: none;
    color: {PALETTE_BLACK_SUB};
    border-radius: 6px;
    padding: 4px 9px;
    font-size: 12px;
    font-family: {FONT_STACK};
}}
QPushButton#quickSpeedBtn:hover {{
    background-color: rgba(225, 29, 72, 22);
    color: {PALETTE_ROSE};
}}
QPushButton#btnExitLoop {{
    background-color: rgba(194, 65, 12, 24);
    border: none;
    color: {PALETTE_ORANGE};
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 12px;
}}
QPushButton#btnExitLoop:hover {{
    background-color: {PALETTE_ORANGE};
    color: #FFFFFF;
}}
QMenu {{
    background-color: #FAFAFA;
    border: 1px solid #D4D4D8;
    border-radius: 8px;
    padding: 4px;
    color: {PALETTE_BLACK};
    font-size: 12.5px;
    font-family: {FONT_STACK};
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: rgba(225, 29, 72, 26);
    color: {PALETTE_ROSE};
}}
QMenu::separator {{
    height: 1px;
    background: #E4E4E7;
    margin: 4px 8px;
}}
"""


# =========================================================================
# 13. Help & Shortcut Dialog
# =========================================================================
class CyberHelpDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Dialog | Qt.WindowStaysOnTopHint
        )
        self.resize(580, 560)
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
            "• <b>Progressive Loop</b>: Plays Pass 1 at <b>0.75x</b> slow speed, then Pass 2 at <b>1.0x</b> normal speed.<br>"
            "• <b>JSON HTTP Push</b>: POST <code>{\"dialogue\": \"...\", \"drills\": [...]}</code> to <code>127.0.0.1:54322/tts</code>.<br><br>"
            "<b>[ Interactive Controls & Shortcuts ]</b><br>"
            "• <b>Double-Click Sentence</b>: Loop single sentence continuously (Esc or badge to exit)<br>"
            "• <b>Select Phrase + Alt+P</b>: Instantly read highlighted chunk at 0.85x<br>"
            "• <b>Space</b>: Pause / Resume Playback  |  <b>Ctrl + Enter</b>: Synthesize & Play<br>"
            "• <b>Ctrl + S</b>: Export WAV  |  <b>Ctrl + R</b>: Reload  |  <b>Esc</b>: Minimize to Tray"
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
        if event.buttons() == Qt.LeftButton and hasattr(self, "drag_pos"):
            self.move(event.globalPos() - self.drag_pos)
            event.accept()


# =========================================================================
# 14. Main Player Window
# =========================================================================
class CyberTtsPlayer(QWidget):
    ahk_text_signal = pyqtSignal(object)
    mode_change_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.setMinimumSize(720, 500)
        self.resize(940, 640)

        self.drag_pos = QPoint()
        self.drag_edge = None
        self.drag_start_geo = QRect()
        self.drag_start_pos = QPoint()
        self.is_slider_dragging = False

        self.click_count = 0
        self.last_click_time = 0.0

        self.current_audio = None
        self.timeline = []
        self.loop_segment = None
        self.active_highlight_range = None

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

        self.init_native_taskbar_listener()
        self.init_ui()
        self.init_tray_icon()
        self.init_single_instance_ipc()
        self.init_file_watcher()
        self.init_clipboard_watcher()
        self.load_user_preferences()

        self.ahk_text_signal.connect(self.on_ahk_text_received)
        self.mode_change_signal.connect(self.on_remote_mode_changed)
        self.http_thread = HttpServerThread(self.ahk_text_signal, self.mode_change_signal)
        self.http_thread.start()

    def init_native_taskbar_listener(self):
        try:
            self.msg_taskbar_created = ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")
        except Exception:
            self.msg_taskbar_created = 0

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

        if not raw_text or not re.search(r"[a-zA-Z]", raw_text):
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

    def init_file_watcher(self):
        self.reload_timer = QTimer(self)
        self.reload_timer.setSingleShot(True)
        self.reload_timer.setInterval(400)
        self.reload_timer.timeout.connect(self.reload_application)

        current_file = os.path.abspath(__file__)
        self.file_watcher = QFileSystemWatcher(self)
        if os.path.exists(current_file):
            self.file_watcher.addPath(current_file)
            self.file_watcher.fileChanged.connect(self.on_source_file_changed)

    def on_source_file_changed(self, path):
        QTimer.singleShot(100, lambda: self.rearm_file_watcher(path))
        self.reload_timer.start()

    def rearm_file_watcher(self, path):
        if os.path.exists(path) and path not in self.file_watcher.files():
            self.file_watcher.addPath(path)

    def get_loop_count_from_index(self, index):
        return {0: 1, 1: 2, 2: 3, 3: 999999, 4: 1}.get(index, 1)

    def is_progressive_index(self, index):
        return index == 4

    def init_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        self.container = QFrame()
        self.container.setObjectName("mainContainer")
        self.container.setStyleSheet(CLEAN_ROSE_ZINC_STYLESHEET)
        self.container.setMouseTracking(True)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(24, 24, 27, 45))
        shadow.setOffset(0, 6)
        self.container.setGraphicsEffect(shadow)

        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(16, 12, 16, 16)
        container_layout.setSpacing(8)

        # Top Action Bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 2, 0)

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
            f"QPushButton#btnTopAction:hover {{ background-color: rgba(225, 29, 72, 24); color: {PALETTE_ROSE}; }}"
        )
        btn_close.clicked.connect(self.hide)

        top_bar.addWidget(btn_clear)
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

        # Vertical Splitter between Main Text Editor & 2-Column Drill Cards Panel
        self.left_splitter = QSplitter(Qt.Vertical)
        self.left_splitter.setHandleWidth(7)
        self.left_splitter.setChildrenCollapsible(False)

        self.text_edit = CyberTextEdit(self)
        self.text_edit.setPlaceholderText(
            "Paste English dialogue, annotated study guides, or trap tables here...\n"
            "• Supports W:/M: dual voices, **stress**, (silent), [phonetic], ‿ linking, and / pauses\n"
            "• Double-click any sentence to loop it; select any phrase + Alt+P to drill-read"
        )
        self.text_edit.textChanged.connect(self.on_text_changed)
        self.text_edit.sentence_double_clicked.connect(self.on_sentence_double_clicked)
        self.text_edit.selection_read_requested.connect(self.play_drill_phrase)
        self.text_edit.installEventFilter(self)

        # 2-Column Drill Cards Panel (分栏词卡面板)
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

        # Right Control Panel (Functions preserved intact)
        right_panel = QFrame()
        right_panel.setObjectName("rightPanel")
        right_panel.setMinimumWidth(275)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(15, 15, 15, 15)
        right_layout.setSpacing(9)

        # 1. Operation Mode
        mode_box = QHBoxLayout()
        lbl_mode = QLabel("Mode:")
        self.combo_mode = QComboBox()
        self.combo_mode.setFocusPolicy(Qt.NoFocus)
        self.combo_mode.addItem("Mode 1: Manual (Click / Push)", "manual")
        self.combo_mode.addItem("Mode 2: Auto Clipboard (Direct)", "auto_clipboard")
        self.combo_mode.setToolTip(
            "[Mode 1]: Read on click or selection push.\n"
            "[Mode 2]: Automatically read newly copied English text."
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
        preset_voices = [
            "af_heart (US Heart - Natural)",
            "af_bella (US Bella - Expressive)",
            "af_sarah (US Sarah - Studio)",
            "af_sky (US Sky - Bright)",
            "af_nicole (US Nicole - Gentle)",
            "af_alloy (US Alloy - Modern)",
            "af_aoede (US Aoede - Melodic)",
            "af_jessica (US Jessica - Friendly)",
            "af_kore (US Kore - Calm)",
            "af_nova (US Nova - Clear)",
            "af_river (US River - Smooth)",
            "am_michael (US Michael - Narrative)",
            "am_adam (US Adam - Deep)",
            "am_echo (US Echo - Dynamic)",
            "am_eric (US Eric - Conversational)",
            "am_fenrir (US Fenrir - Energetic)",
            "am_liam (US Liam - Youthful)",
            "am_onyx (US Onyx - Authoritative)",
            "am_puck (US Puck - Playful)",
            "am_santa (US Santa - Warm Bass)",
            "bf_emma (UK Emma - Elegant)",
            "bf_isabella (UK Isabella - Soft)",
            "bf_alice (UK Alice - Clear)",
            "bf_lily (UK Lily - Refined)",
            "bm_george (UK George - Classic)",
            "bm_lewis (UK Lewis - Articulate)",
            "bm_daniel (UK Daniel - Storyteller)",
            "bm_fable (UK Fable - Character)",
        ]

        voices_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
        known_keys = {v.split(" ")[0] for v in preset_voices}
        if os.path.exists(voices_dir):
            for fname in sorted(os.listdir(voices_dir)):
                if fname.endswith(".pt"):
                    v_key = fname[:-3]
                    if v_key not in known_keys:
                        preset_voices.append(f"{v_key} (Custom / Local)")
                        known_keys.add(v_key)

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

        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setFocusPolicy(Qt.NoFocus)
        self.slider_speed.setRange(5, 20)
        self.slider_speed.setValue(10)
        self.slider_speed.setToolTip("Speed: 1.0x")
        self.slider_speed.valueChanged.connect(
            lambda v: self.slider_speed.setToolTip(f"Speed: {v/10:.1f}x")
        )

        # Shadowing & Pause on compact row
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

        self.btn_save_wav = QPushButton("⭳ Save WAV")
        self.btn_save_wav.setObjectName("btnSave")
        self.btn_save_wav.setFocusPolicy(Qt.NoFocus)
        self.btn_save_wav.setToolTip("Export as a local WAV audio file.")
        self.btn_save_wav.clicked.connect(self.save_audio_file)

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
        right_layout.addStretch()
        right_layout.addWidget(self.btn_play)
        right_layout.addWidget(self.btn_stop)
        right_layout.addWidget(self.btn_save_wav)

        self.splitter.addWidget(left_widget)
        self.splitter.addWidget(right_panel)
        container_layout.addWidget(self.splitter)

        root_layout.addWidget(self.container)

    # ---------------------------------------------------------------------
    # Learning Material Ingestion, 2-Column Drill Cards & Sentence Loop Helpers
    # ---------------------------------------------------------------------
    def ingest_learning_text(
        self, raw_payload, auto_play=False, target_loops=None, progressive=False
    ):
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
                display_text, font_size=self.spin_font_size.value()
            )

        if auto_play and display_text:
            self.play_audio(target_loops=target_loops, progressive=progressive)

    def populate_drill_cards(self, drills):
        # Clear existing grid widgets
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
            card = DrillCardItem(
                index=idx + 1,
                phrase=d["phrase"],
                tip=d.get("tip", ""),
                parent=self.drill_container,
            )
            card.clicked.connect(self.play_drill_phrase)
            row = idx // 2
            col = idx % 2
            self.drill_grid.addWidget(card, row, col)

        # Push rows to top nicely if fewer items
        total_rows = (len(drills) + 1) // 2
        for r in range(total_rows):
            self.drill_grid.setRowStretch(r, 0)
        self.drill_grid.setRowStretch(total_rows, 1)

        self.drill_frame.setVisible(True)
        total_h = max(360, self.left_splitter.height())
        self.left_splitter.setSizes([int(total_h * 0.62), int(total_h * 0.38)])

    def play_drill_phrase(self, phrase: str):
        clean_p = clean_annotated_for_tts(phrase)
        if not clean_p:
            return
        if self.is_playing:
            self.toggle_stop_or_resume()

        voice_key = self.combo_voice.currentText().split(" ")[0]
        device = self.combo_device.currentData() or "cpu"
        self.drill_thread.play_phrase(clean_p, voice=voice_key, speed=0.85, device=device)

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
        self.highlight_text_span(matched_seg["start_char"], matched_seg["end_char"], is_loop=True)
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
            self.tray_icon.showMessage(
                "Switched to Mode 2",
                f"Clipboard auto-read enabled (Limit: {self.spin_auto_limit.value()} chars, Mode: {self.combo_clip_loop.currentText()}).\nManual paste is required for larger texts.",
                QSystemTrayIcon.Information,
                2500,
            )
        else:
            self.tray_icon.setToolTip("Kokoro TTS Player [Mode 1: Manual Mode]")

    def on_remote_mode_changed(self, mode_str):
        if mode_str in ("2", "auto", "clipboard"):
            idx = self.combo_mode.findData("auto_clipboard")
            if idx >= 0:
                self.combo_mode.setCurrentIndex(idx)
        else:
            idx = self.combo_mode.findData("manual")
            if idx >= 0:
                self.combo_mode.setCurrentIndex(idx)

    def on_always_visible_toggled(self, checked):
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
        script_dir = os.path.dirname(os.path.abspath(__file__))
        local_icon = os.path.join(script_dir, "tts.png")

        if os.path.exists(local_icon):
            self.app_icon = QIcon(local_icon)
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
        global global_mutex
        self.save_user_preferences()
        self.stop_all(reset_progress=True)
        if hasattr(self, "http_thread") and self.http_thread:
            self.http_thread.stop()
        if hasattr(self, "local_server") and self.local_server:
            self.local_server.close()
            QLocalServer.removeServer(SINGLE_INSTANCE_KEY)
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.hide()
        if global_mutex:
            try:
                ctypes.windll.kernel32.CloseHandle(global_mutex)
                global_mutex = None
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

    def on_font_size_changed(self, size):
        self.text_edit.setStyleSheet(f"""
            QTextEdit {{
                background-color: #FFFFFF;
                border: 1px solid #E4E4E7;
                border-radius: 12px;
                color: {PALETTE_BLACK};
                font-family: {FONT_STACK};
                font-size: {size}px;
                padding: 14px;
                selection-background-color: rgba(225, 29, 72, 32);
                selection-color: {PALETTE_ROSE_DEEP};
            }}
            QTextEdit:focus {{
                border: 1.5px solid {PALETTE_ROSE};
            }}
        """)
        self.save_user_preferences()

    def get_edge_direction(self, pos):
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

    def update_cursor_shape(self, edge):
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
                event.accept()
                return

            self.drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_edge and (event.buttons() & Qt.LeftButton):
            delta = event.globalPos() - self.drag_start_pos
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
            event.accept()
            return

        if (event.buttons() & Qt.LeftButton) and not self.drag_pos.isNull():
            self.move(event.globalPos() - self.drag_pos)
            event.accept()
            return

        edge = self.get_edge_direction(event.pos())
        self.update_cursor_shape(edge)

    def mouseReleaseEvent(self, event):
        self.drag_edge = None
        self.drag_pos = QPoint()
        self.update_cursor_shape(None)
        self.save_user_preferences()

    def closeEvent(self, event):
        self.save_user_preferences()
        if hasattr(self, "tray_icon") and self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            super().closeEvent(event)

    def force_bring_to_foreground(self):
        """Force window to restore, elevate, and focus."""
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetWindowPos(
                hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040
            )
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj == self.text_edit and event.type() == QEvent.KeyPress:
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
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            if self.loop_segment is not None:
                self.clear_sentence_loop()
            else:
                self.hide()
            event.accept()
        elif event.key() == Qt.Key_Space:
            self.toggle_stop_or_resume()
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
        elif event.modifiers() == Qt.ControlModifier and event.key() == Qt.Key_S:
            self.save_audio_file()
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

    def on_text_changed(self):
        visible_text = self.text_edit.toPlainText().strip()
        clean_tts = sanitize_text(visible_text)
        words = len(clean_tts.split()) if clean_tts else 0
        chars = len(clean_tts)
        self.lbl_stats.setText(f"Words: {words} | Chars: {chars}")

    def on_split_mode_toggled(self, checked):
        self.save_user_preferences()
        visible_text = self.text_edit.toPlainText().strip()
        if not visible_text:
            return

        if checked:
            expanded = []
            for line in visible_text.splitlines():
                sents = split_into_sentences(line)
                expanded.extend(sents if sents else [line])
            self.text_edit.set_annotated_content(
                "\n\n".join(expanded), font_size=self.spin_font_size.value()
            )
        else:
            compact = re.sub(r"\s*\n+\s*", " ", visible_text).strip()
            self.text_edit.set_annotated_content(
                compact, font_size=self.spin_font_size.value()
            )

    def on_engine_status(self, status):
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

        if self.chk_split.isChecked():
            expanded = []
            for line in visible_text.splitlines():
                sents = split_into_sentences(line)
                expanded.extend(sents if sents else [line])
            split_joined = "\n\n".join(expanded)
            if split_joined != visible_text:
                self.text_edit.set_annotated_content(
                    split_joined, font_size=self.spin_font_size.value()
                )
                visible_text = self.text_edit.toPlainText().strip()

        segments = build_timeline_segments(visible_text)
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
        )

    def on_synth_finished(self, result_dict):
        self.current_audio = result_dict["audio"]
        self.timeline = result_dict.get("timeline", [])
        self.total_duration = len(self.current_audio) / SAMPLE_RATE
        self.slider_progress.setEnabled(True)
        self.start_playback(from_sec=0.0)

    def start_playback(self, from_sec=0.0):
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

    def on_synth_error(self, err_msg):
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

    def stop_all(self, reset_progress=True):
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

    def on_slider_jump(self, ratio):
        if self.current_audio is not None and self.total_duration > 0:
            self.clear_sentence_loop()
            target_sec = ratio * self.total_duration
            self.start_playback(from_sec=target_sec)

    def save_audio_file(self):
        if self.current_audio is None or len(self.current_audio) == 0:
            QMessageBox.information(
                self, "Notice", "No synthesized audio available. Please enter text and synthesize first."
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Audio", "synthesized_speech.wav", "Audio Files (*.wav)"
        )
        if not file_path:
            return

        try:
            scaled = np.clip(self.current_audio, -1.0, 1.0) * 32767.0
            int16_audio = scaled.astype(np.int16)

            with wave.open(file_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(int16_audio.tobytes())

            QMessageBox.information(
                self, "Success", f"Audio file exported successfully to:\n{file_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Failed to export audio: {e}")

    def load_user_preferences(self):
        saved_width = self.settings.value("win_width", 940, type=int)
        saved_height = self.settings.value("win_height", 640, type=int)
        self.resize(max(720, saved_width), max(500, saved_height))

        saved_sizes = self.settings.value("splitter_sizes")
        if saved_sizes:
            try:
                sizes = [int(s) for s in saved_sizes]
                if len(sizes) == 2 and sum(sizes) > 0:
                    self.splitter.setSizes(sizes)
            except Exception:
                self.splitter.setSizes([665, 275])
        else:
            self.splitter.setSizes([665, 275])

        self.chk_split.setChecked(
            self.settings.value("sentence_split", False, type=bool)
        )

        saved_mode = self.settings.value("work_mode", "manual", type=str)
        mode_idx = self.combo_mode.findData(saved_mode)
        if mode_idx >= 0:
            self.combo_mode.setCurrentIndex(mode_idx)
        else:
            self.combo_mode.setCurrentIndex(0)

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

        saved_voice_name = self.settings.value("voice_name", "")
        if saved_voice_name:
            idx = self.combo_voice.findText(saved_voice_name, Qt.MatchStartsWith)
            if idx >= 0:
                self.combo_voice.setCurrentIndex(idx)
        else:
            self.combo_voice.setCurrentIndex(
                self.settings.value("voice_idx", 0, type=int)
            )

        saved_male_name = self.settings.value("male_voice_name", "am_michael")
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
        self.on_font_size_changed(font_size)

    def save_user_preferences(self):
        if not self.isMinimized() and not self.isMaximized():
            self.settings.setValue("win_width", self.width())
            self.settings.setValue("win_height", self.height())
        if hasattr(self, "splitter"):
            self.settings.setValue("splitter_sizes", self.splitter.sizes())
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
        self.settings.sync()


# =========================================================================
# 15. Application Entry Point
# =========================================================================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app_font = QFont()
    app_font.setFamilies(["Consolas", "Microsoft YaHei UI"])
    app_font.setPointSize(10)
    app.setFont(app_font)

    kernel32 = ctypes.windll.kernel32
    global_mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = kernel32.GetLastError()
    ERROR_ALREADY_EXISTS = 183

    if last_error == ERROR_ALREADY_EXISTS:
        if global_mutex:
            kernel32.CloseHandle(global_mutex)
            global_mutex = None

        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if socket.waitForConnected(800):
            socket.write(b"ACTIVATE")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
        sys.exit(0)

    player = CyberTtsPlayer()
    app.aboutToQuit.connect(player.cleanup_resources)

    if player.chk_always_visible.isChecked():
        player.show()
    else:
        player.tray_icon.showMessage(
            "Kokoro TTS Ready",
            "Running in background. Click tray icon anytime to show window.",
            QSystemTrayIcon.Information,
            2500,
        )

    sys.exit(app.exec_())