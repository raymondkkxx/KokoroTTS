import os

# Resolve project root directory (parent of kokoro_studio/)
APP_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICES_DIR = os.path.join(APP_ROOT_DIR, "voices")
ESPEAK_LOCAL_DIR = os.path.join(APP_ROOT_DIR, "espeak-ng")
TRAY_ICON_PATH = os.path.join(APP_ROOT_DIR, "tts.png")

# Audio & Window Constants
SAMPLE_RATE = 24000
EDGE_MARGIN = 12
CORNER_MARGIN = 22
HTTP_HOST = "127.0.0.1"
HTTP_PORT = 54322
DEFAULT_EXPORT_ARTIST = "Raymondkkxx"

SINGLE_INSTANCE_KEY = "KokoroTTSPlayer_SingleInstance_Server"
MUTEX_NAME = "Local\\KokoroTTSPlayer_SingleInstance_Mutex"

# Unified Design System Tokens (Crisp Zinc + Rose Red + Dark Orange)
FONT_STACK = '"Consolas", "Microsoft YaHei UI", monospace'
PALETTE_BLACK = "#18181B"
PALETTE_BLACK_SUB = "#52525B"
PALETTE_ROSE = "#E11D48"
PALETTE_ROSE_DEEP = "#BE123C"
PALETTE_ORANGE = "#C2410C"
PALETTE_ORANGE_DEEP = "#9A3412"

PRESET_VOICES = [
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


def get_available_voices() -> list:
    """Return built-in voices merged with any custom .pt files in root voices/ folder."""
    voices = list(PRESET_VOICES)
    known_keys = {v.split(" ")[0] for v in voices}
    if os.path.exists(VOICES_DIR):
        for fname in sorted(os.listdir(VOICES_DIR)):
            if fname.endswith(".pt"):
                v_key = fname[:-3]
                if v_key not in known_keys:
                    voices.append(f"{v_key} (Custom / Local)")
                    known_keys.add(v_key)
    return voices


def build_text_edit_stylesheet(font_size: int, transparency: int = 30) -> str:
    t = clamp_transparency(transparency)
    opacity_pct = 100 - t
    is_dark = t > 0
    if not is_dark:
        bg = "#FFFFFF"
        bd = "1px solid #E4E4E7"
        fg = PALETTE_BLACK
    else:
        alpha = max(14, int(round(235 * opacity_pct / 100.0)))
        edge = max(16, min(48, int(alpha * 0.22)))
        bg = f"rgba(24, 26, 32, {alpha})"
        bd = f"1px solid rgba(255, 255, 255, {edge})"
        fg = "#F4F4F5"

    return f"""
        QTextEdit {{
            background-color: {bg};
            border: {bd};
            border-radius: 12px;
            color: {fg};
            font-family: {FONT_STACK};
            font-size: {font_size}px;
            padding: 14px;
            selection-background-color: rgba(225, 29, 72, 36);
            selection-color: {PALETTE_ROSE_DEEP};
        }}
        QTextEdit:focus {{
            border: 1.5px solid {PALETTE_ROSE};
        }}
    """


def clamp_transparency(val: int) -> int:
    try:
        v = int(val)
    except Exception:
        v = 30
    return max(0, min(95, v))


def build_main_stylesheet(transparency: int = 30, is_setting_open: bool = False) -> str:
    """
    Generate adaptive stylesheet:
      - 0% transparency: Crisp White & Zinc Light Mode (#FFFFFF / #F4F4F5)
      - 1%~95% transparency: Crystal-Clear Dark Black-Gray Glass Mode (rgba(18, 20, 24, alpha))
    """
    t = clamp_transparency(transparency)
    opacity_pct = 100 - t
    is_dark = t > 0

    if not is_dark:
        container_bg = "#F4F4F5"
        container_bd = "1px solid #E4E4E7"
        panel_bg = "#FFFFFF"
        panel_bd = "1px solid #E4E4E7"
        ctrl_bg = "#F4F4F5"
        ctrl_hover = "#E4E4E7"
        text_main = PALETTE_BLACK
        text_sub = PALETTE_BLACK_SUB
        stats_fg = PALETTE_ORANGE
        card_bg = "#F4F4F5"
        card_hover_bg = "rgba(225, 29, 72, 18)"
        card_idx_bg = "rgba(24, 24, 27, 16)"
        badge_bg = PALETTE_BLACK
        badge_fg = "#FFFFFF"
        scroll_col = "rgba(24, 24, 27, 65)"
        groove_bg = "rgba(24, 24, 27, 36)"
        handle_bg = "#FFFFFF"
        chk_bg = "#F4F4F5"
        chk_bd = "#D4D4D8"
        popup_bg = "#FAFAFA"
        popup_bd = "1px solid #D4D4D8"
        setting_panel_bg = "#FAFAFA"
        setting_panel_bd = "1px solid #E4E4E7"
        input_bd = "1px solid #E4E4E7"
    else:
        alpha_main = max(16, int(round(255 * opacity_pct / 100.0)))
        alpha_panel = max(14, int(round(235 * opacity_pct / 100.0)))
        alpha_ctrl = max(16, min(38, int(alpha_main * 0.16)))
        alpha_hover = max(26, min(56, int(alpha_main * 0.25)))
        edge_alpha = max(18, min(52, int(alpha_main * 0.22)))

        container_bg = f"rgba(18, 20, 24, {alpha_main})"
        container_bd = f"1px solid rgba(255, 255, 255, {edge_alpha})"
        panel_bg = f"rgba(24, 26, 32, {alpha_panel})"
        panel_bd = f"1px solid rgba(255, 255, 255, {max(14, int(edge_alpha * 0.85))})"
        ctrl_bg = f"rgba(255, 255, 255, {alpha_ctrl})"
        ctrl_hover = f"rgba(255, 255, 255, {alpha_hover})"
        text_main = "#F4F4F5"
        text_sub = "#A1A1AA"
        stats_fg = "#FB923C"
        card_bg = f"rgba(255, 255, 255, {alpha_ctrl})"
        card_hover_bg = "rgba(244, 63, 94, 38)"
        card_idx_bg = "rgba(255, 255, 255, 24)"
        badge_bg = "rgba(255, 255, 255, 32)"
        badge_fg = "#F4F4F5"
        scroll_col = "rgba(255, 255, 255, 58)"
        groove_bg = "rgba(255, 255, 255, 34)"
        handle_bg = "#18181B"
        chk_bg = f"rgba(255, 255, 255, {alpha_ctrl})"
        chk_bd = "rgba(255, 255, 255, 65)"
        popup_bg = "#181A1F"
        popup_bd = "1px solid rgba(255, 255, 255, 36)"
        setting_panel_bg = f"rgba(255, 255, 255, {max(15, int(alpha_ctrl * 1.1))})"
        setting_panel_bd = f"1px solid rgba(255, 255, 255, {edge_alpha})"
        input_bd = f"1px solid rgba(255, 255, 255, {max(14, int(edge_alpha * 0.7))})"

    setting_btn_bg = (
        ("rgba(244, 63, 94, 42)" if is_dark else "rgba(225, 29, 72, 24)")
        if is_setting_open
        else "transparent"
    )
    setting_btn_fg = (
        ("#FB7185" if is_dark else PALETTE_ROSE) if is_setting_open else text_sub
    )

    return f"""
QWidget#mainContainer {{
    background-color: {container_bg};
    border: {container_bd};
    border-radius: 16px;
    font-family: {FONT_STACK};
}}
QLabel {{
    color: {text_sub};
    font-family: {FONT_STACK};
    font-size: 13px;
}}
QLabel#lblHighlightStats {{
    color: {stats_fg};
    font-size: 12.5px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLabel#lblTimeStatus {{
    color: {text_sub};
    font-size: 12.5px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLabel#lblCoverThumb {{
    background-color: {ctrl_bg};
    border: {input_bd};
    border-radius: 5px;
}}
QLabel#lblExportInfo {{
    background-color: {ctrl_bg};
    border: {input_bd};
    border-radius: 7px;
    color: {text_main};
    padding: 4px 8px;
    font-size: 12px;
    font-family: {FONT_STACK};
}}
QLabel#lblExportExt {{
    color: {text_sub};
    font-size: 12px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLineEdit#editExportName {{
    background-color: {ctrl_bg};
    border: {input_bd};
    border-radius: 7px;
    color: {text_main};
    padding: 4px 8px;
    font-size: 12px;
    font-family: {FONT_STACK};
    selection-background-color: rgba(225, 29, 72, 36);
    selection-color: {PALETTE_ROSE_DEEP};
}}
QLineEdit#editExportName:hover {{
    background-color: {ctrl_hover};
}}
QLineEdit#editExportName:focus {{
    border: 1.5px solid {PALETTE_ROSE};
}}
QFrame#rightPanel, QFrame#drillBarFrame {{
    background-color: {panel_bg};
    border: {panel_bd};
    border-radius: 12px;
    font-family: {FONT_STACK};
}}
QFrame#settingPanel {{
    background-color: {setting_panel_bg};
    border: {setting_panel_bd};
    border-radius: 10px;
    font-family: {FONT_STACK};
}}
QLabel#drillHeaderTitle {{
    color: {text_main};
    font-size: 12px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLabel#drillCountBadge {{
    background-color: {badge_bg};
    color: {badge_fg};
    font-size: 11px;
    font-weight: bold;
    border-radius: 5px;
    padding: 1px 7px;
    font-family: {FONT_STACK};
}}
QFrame#drillCardItem {{
    background-color: {card_bg};
    border: 1px solid transparent;
    border-radius: 8px;
}}
QFrame#drillCardItem:hover {{
    background-color: {card_hover_bg};
    border: 1px solid rgba(225, 29, 72, 75);
}}
QLabel#drillCardIdx {{
    background-color: {card_idx_bg};
    color: {text_sub};
    font-size: 11px;
    font-weight: bold;
    border-radius: 4px;
    padding: 1px 3px;
    font-family: {FONT_STACK};
}}
QLabel#drillCardPhrase {{
    color: {text_main};
    font-size: 13px;
    font-weight: bold;
    font-family: {FONT_STACK};
}}
QLabel#drillCardPlayIcon {{
    color: {PALETTE_ROSE};
    font-size: 12px;
}}
QLabel#drillCardTip {{
    color: {text_sub};
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
    background: {scroll_col};
    border-radius: 2px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: rgba(225, 29, 72, 140);
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QComboBox {{
    background-color: {ctrl_bg};
    border: none;
    border-radius: 7px;
    padding: 4px 10px;
    color: {text_main};
    font-size: 12.5px;
    font-family: {FONT_STACK};
}}
QComboBox:hover {{
    background-color: {ctrl_hover};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {popup_bg};
    color: {text_main};
    selection-background-color: rgba(225, 29, 72, 34);
    selection-color: {PALETTE_ROSE};
    border: {popup_bd};
    border-radius: 8px;
    font-size: 12.5px;
    padding: 4px;
    outline: none;
    font-family: {FONT_STACK};
}}
QSlider::groove:horizontal {{
    border: none;
    height: 5px;
    background: {groove_bg};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {PALETTE_ROSE};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {handle_bg};
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
    color: {text_main};
    font-size: 12.5px;
    spacing: 8px;
    font-family: {FONT_STACK};
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    border-radius: 5px;
    border: 1.5px solid {chk_bd};
    background-color: {chk_bg};
}}
QCheckBox::indicator:hover {{
    border-color: {PALETTE_ROSE};
}}
QCheckBox::indicator:checked {{
    background-color: {PALETTE_ROSE};
    border: 1.5px solid {PALETTE_ROSE};
}}
QSpinBox {{
    background-color: {ctrl_bg};
    border: none;
    border-radius: 7px;
    color: {text_main};
    padding: 4px 8px;
    font-size: 12.5px;
    font-family: {FONT_STACK};
}}
QSpinBox:hover {{
    background-color: {ctrl_hover};
}}
QPushButton {{
    border-radius: 7px;
    font-size: 12.5px;
    font-weight: bold;
    padding: 6px 10px;
    font-family: {FONT_STACK};
}}
QPushButton#btnMiniTool {{
    background-color: {ctrl_bg};
    border: {input_bd};
    border-radius: 7px;
    color: {text_main};
    font-size: 12px;
    font-weight: bold;
    padding: 3px 7px;
}}
QPushButton#btnMiniTool:hover {{
    background-color: {ctrl_hover};
    color: {PALETTE_ROSE};
    border: 1px solid rgba(225, 29, 72, 90);
}}
QPushButton#btnPlay {{
    background-color: {PALETTE_ROSE};
    color: #FFFFFF;
    border: none;
}}
QPushButton#btnPlay:hover {{
    background-color: {PALETTE_ROSE_DEEP};
}}
QPushButton#btnStop {{
    background-color: {"#27272A" if is_dark else PALETTE_BLACK};
    color: #FFFFFF;
    border: none;
}}
QPushButton#btnStop:hover {{
    background-color: #3F3F46;
}}
QPushButton#btnSave {{
    background-color: {ctrl_bg};
    border: none;
    color: {text_main};
}}
QPushButton#btnSave:hover {{
    background-color: {ctrl_hover};
    color: {PALETTE_ROSE};
}}
QPushButton#btnNetease {{
    background-color: {ctrl_bg};
    border: 1px solid rgba(194, 65, 12, 70);
    color: {stats_fg};
}}
QPushButton#btnNetease:hover {{
    background-color: {PALETTE_ORANGE};
    border: 1px solid {PALETTE_ORANGE};
    color: #FFFFFF;
}}
QPushButton#btnTopAction {{
    background-color: transparent;
    color: {text_sub};
    font-size: 12.5px;
    font-weight: bold;
    border-radius: 6px;
    padding: 3px 9px;
    font-family: {FONT_STACK};
}}
QPushButton#btnTopAction:hover {{
    background-color: {ctrl_hover};
    color: {text_main};
}}
QPushButton#btnPhoneticMode, QPushButton#btnAutoIpa {{
    background-color: {ctrl_bg};
    color: {text_sub};
    font-size: 12px;
    font-weight: bold;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 3px 10px;
    font-family: {FONT_STACK};
}}
QPushButton#btnPhoneticMode:hover, QPushButton#btnAutoIpa:hover {{
    background-color: {ctrl_hover};
    color: {text_main};
}}
QPushButton#btnPhoneticMode:checked {{
    background-color: {PALETTE_ROSE};
    color: #FFFFFF;
    border: 1px solid {PALETTE_ROSE};
}}
QPushButton#btnPhoneticMode:checked:hover {{
    background-color: {PALETTE_ROSE_DEEP};
}}
QPushButton#btnAutoIpa:checked {{
    background-color: {PALETTE_ORANGE};
    color: #FFFFFF;
    border: 1px solid {PALETTE_ORANGE};
}}
QPushButton#btnAutoIpa:checked:hover {{
    background-color: {PALETTE_ORANGE_DEEP};
}}
QPushButton#btnSettingToggle {{
    background-color: {setting_btn_bg};
    color: {setting_btn_fg};
    font-size: 12.5px;
    font-weight: bold;
    border-radius: 6px;
    padding: 3px 9px;
    font-family: {FONT_STACK};
}}
QPushButton#btnSettingToggle:hover {{
    background-color: {ctrl_hover};
    color: {PALETTE_ROSE};
}}
QPushButton#quickSpeedBtn {{
    background-color: {panel_bg};
    border: none;
    color: {text_sub};
    border-radius: 6px;
    padding: 4px 9px;
    font-size: 12px;
    font-family: {FONT_STACK};
}}
QPushButton#quickSpeedBtn:hover {{
    background-color: rgba(225, 29, 72, 26);
    color: {PALETTE_ROSE};
}}
QPushButton#btnExitLoop {{
    background-color: rgba(194, 65, 12, 28);
    border: none;
    color: {stats_fg};
    border-radius: 6px;
    padding: 3px 10px;
    font-size: 12px;
}}
QPushButton#btnExitLoop:hover {{
    background-color: {PALETTE_ORANGE};
    color: #FFFFFF;
}}
QMenu {{
    background-color: {popup_bg};
    border: {popup_bd};
    border-radius: 8px;
    padding: 4px;
    color: {text_main};
    font-size: 12.5px;
    font-family: {FONT_STACK};
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background-color: rgba(225, 29, 72, 34);
    color: {PALETTE_ROSE};
}}
QMenu::separator {{
    height: 1px;
    background: #E4E4E7;
    margin: 4px 8px;
}}
"""