# -*- coding: utf-8 -*-
import os
from PyQt5.QtCore import QSettings

APP_NAME = "CET-6 Listening Studio"
APP_ORG = "KokoroTTS"
APP_SETTINGS_KEY = "CET6ListeningStudioV1"

DEFAULT_KOKORO_ROOT = r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\KokoroTTS"

SAMPLE_RATE = 24000
TARGET_RMS = 0.085
TRIM_SILENCE_THRESHOLD = 0.005

DEFAULT_CONFIG = {
    "device": "auto",
    "voice_male": "am_michael",
    "voice_female": "af_heart",
    "voice_examiner": "am_eric",
    "speech_rate": 1.0,
    "turn_gap_sec": 0.25,
    "section_gap_sec": 1.2,
    "pre_question_read_sec": 8.0,
    "post_question_answer_sec": 8.0,
    "enable_ding_chime": True,
    "ding_volume": 0.18,
    "always_on_top": False,
}

PRESET_VOICES = [
    "am_michael (US Michael - 饱满美音男声)",
    "am_eric (US Eric - 沉稳考官/男声)",
    "am_adam (US Adam - 深沉男声)",
    "am_fenrir (US Fenrir - 活力男声)",
    "am_liam (US Liam - 青年男声)",
    "am_onyx (US Onyx - 权威男声)",
    "af_heart (US Heart - 自然美音女声)",
    "af_bella (US Bella - 清晰女声)",
    "af_sarah (US Sarah - 播音女声)",
    "af_nicole (US Nicole - 柔和女声)",
    "af_nova (US Nova - 明亮女声)",
    "af_kore (US Kore - 冷静女声)",
    "bf_emma (UK Emma - 优雅英音女声)",
    "bf_alice (UK Alice - 清晰英音女声)",
    "bm_george (UK George - 经典英音男声)",
    "bm_lewis (UK Lewis - 播音英音男声)",
]

DEFAULT_AI_PROMPT_TEMPLATE = """你是一位资深的大学英语六级（CET-6）听力命题专家。请根据我提供的【参考材料】，创作一套高仿六级听力 Section A（长对话）的平行模拟题，并严格以单个 JSON 代码块输出。

### 一、 命题与语言规范
1. **题材迁移与防雷同**：保留参考材料的核心场景（如校园科研、职场项目、环保科技、社会现象等）与出题逻辑，但**必须更换具体事件、人物姓名与核心语料**，严禁照搬原句。
2. **体量与风格对标**：
   - 总词数严格控制在 **250–280 词**，一男（`M`）一女（`F`）交替对话，共 **8–10 个话轮**。
   - 语体对标六级广播美音真题：包含自然的口语转折（`Well...`、`Actually,`、`I mean,`）、信息密度适中，包含明确的同义替换考点与干扰项。
3. **题目设计**：命制 **4 道选择题**（覆盖主旨、细节推理、态度或后续行动），每题 `A/B/C/D` 四个选项长度与句式结构保持平行对称。

### 二、 Kokoro-TTS 专用语音轨（`spoken_text`）强制规则
为了保证本地 TTS 零误读，每句话和每个题干必须同时提供 `display_text`（正常拼写）和 `spoken_text`（纯语音拼写），`spoken_text` 必须严格遵守以下规则：
1. **数字与符号全展开**：将所有阿拉伯数字、年份、时间、百分比、货币、小数全部拼写成纯英文单词（例如：`2026` -> `twenty twenty-six`；`$45,000` -> `forty-five thousand dollars`；`90%` -> `ninety percent`；`3.5` -> `three point five`）。
2. **缩略词处理**：逐字母念的缩写用空格隔开大写字母（如 `LED` -> `L E D`，`GPA` -> `G P A`）；连读成词的缩写展开为全称或使用 `[原词](/音标/)` 语法。
3. **标点驱动韵律**：禁止使用 `<break>`、`[sigh]` 等无效标签，禁止使用结巴连字符（如 `W-what`）；用省略号 `...` 表示犹豫停顿，用 `--` 表示自然换气转折。

### 三、 输出格式（严禁输出 JSON 以外的任何解释文字）
\x60\x60\x60json
{
  "title": "CET-6 Section A Simulation — [英文话题副标题]",
  "dialogue": [
    {
      "role": "M",
      "display_text": "正常拼写台词...",
      "spoken_text": "纯英文单词展开后的语音专用台词..."
    },
    {
      "role": "F",
      "display_text": "正常拼写台词...",
      "spoken_text": "纯英文单词展开后的语音专用台词..."
    }
  ],
  "questions": [
    {
      "id": 1,
      "display_text": "What does the man say about...?",
      "spoken_text": "What does the man say about...?",
      "options": {
        "A": "选项 A 文本",
        "B": "选项 B 文本",
        "C": "选项 C 文本",
        "D": "选项 D 文本"
      },
      "answer": "B",
      "explanation": "简明中文解析与原文同义替换定位"
    }
  ]
}
\x60\x60\x60

【参考材料】：
（在此处粘贴你的六级真题对话或文章）"""

AI_PROMPT_TEMPLATE = DEFAULT_AI_PROMPT_TEMPLATE


def get_project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_default_output_dir() -> str:
    return os.path.join(get_project_root(), "output")


def get_tray_icon_path() -> str:
    return os.path.join(get_project_root(), ".icon", "cet.png")


def create_qsettings() -> QSettings:
    return QSettings(APP_ORG, APP_SETTINGS_KEY)


def get_ai_prompt() -> str:
    """读取用户自定义提示词，若未修改过则返回默认模板"""
    settings = create_qsettings()
    saved = settings.value("custom_ai_prompt", "", type=str)
    return saved.strip() if saved and saved.strip() else DEFAULT_AI_PROMPT_TEMPLATE


def save_ai_prompt(prompt_text: str):
    """保存用户自定义出题提示词"""
    settings = create_qsettings()
    settings.setValue("custom_ai_prompt", prompt_text.strip())
    settings.sync()


def reset_ai_prompt() -> str:
    """恢复默认出题提示词"""
    settings = create_qsettings()
    settings.remove("custom_ai_prompt")
    settings.sync()
    return DEFAULT_AI_PROMPT_TEMPLATE