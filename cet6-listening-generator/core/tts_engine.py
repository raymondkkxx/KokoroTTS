# -*- coding: utf-8 -*-
import os
import re
import time
import warnings
from typing import Dict

import numpy as np
import torch
from PyQt5.QtCore import QThread, pyqtSignal

from core.settings import (
    DEFAULT_KOKORO_ROOT,
    SAMPLE_RATE,
    TARGET_RMS,
    TRIM_SILENCE_THRESHOLD,
    get_project_root,
)


def auto_detect_espeak(kokoro_root: str = DEFAULT_KOKORO_ROOT):
    base_dir = get_project_root()
    parent_dir = os.path.dirname(base_dir)
    possible_paths = [
        os.path.join(base_dir, "espeak-ng"),
        os.path.join(parent_dir, "espeak-ng"),
        os.path.join(kokoro_root, "espeak-ng"),
        r"C:\Program Files\eSpeak NG",
        r"C:\Program Files (x86)\eSpeak NG",
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

from kokoro import KPipeline  # noqa: E402


def clean_for_kokoro(text: str) -> str:
    """保留 [word](/ipa/) 音标强制发音语法，过滤中文与异常乱读符号"""
    if not text:
        return ""

    phoneme_blocks = []

    def _stash_phoneme(match):
        phoneme_blocks.append(match.group(0))
        return f"__PHONEME_{len(phoneme_blocks) - 1}__"

    protected = re.sub(r"\[[^\]]+\]\(/[^)]+/\)", _stash_phoneme, text)
    protected = re.sub(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]", "", protected)
    protected = re.sub(
        r"[\u3000-\u303f\uff01-\uff0f\uff1a-\uff20\uff3b-\uff40\uff5b-\uff65]",
        " ",
        protected,
    )
    protected = re.sub(r"[/\\`~@#$%^&*()_+=\|<>{}\[\]\t]+", " ", protected)
    protected = re.sub(r"\s+", " ", protected).strip()

    for idx, block in enumerate(phoneme_blocks):
        protected = protected.replace(f"__PHONEME_{idx}__", block)

    return protected


def trim_edge_silence(
    audio: np.ndarray,
    sample_rate: int = SAMPLE_RATE,
    threshold: float = TRIM_SILENCE_THRESHOLD,
    pad_ms: int = 45,
) -> np.ndarray:
    if len(audio) == 0:
        return audio
    active = np.where(np.abs(audio) > threshold)[0]
    if len(active) == 0:
        return audio
    pad_samples = int(sample_rate * (pad_ms / 1000.0))
    start = max(0, active[0] - pad_samples)
    end = min(len(audio), active[-1] + pad_samples)
    return audio[start:end]


def normalize_loudness(
    audio: np.ndarray, target_rms: float = TARGET_RMS
) -> np.ndarray:
    if len(audio) == 0:
        return audio
    rms = float(np.sqrt(np.mean(np.square(audio))))
    if rms < 1e-6:
        return audio
    scaled = audio * (target_rms / rms)
    return np.clip(scaled, -0.95, 0.95).astype(np.float32)


def generate_ding_chime(
    sample_rate: int = SAMPLE_RATE, volume: float = 0.18
) -> np.ndarray:
    duration = 0.45
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    wave_sig = 0.75 * np.sin(2 * np.pi * 880.0 * t) + 0.25 * np.sin(
        2 * np.pi * 1318.51 * t
    )
    attack_samples = int(sample_rate * 0.015)
    envelope = np.exp(-6.5 * t)
    envelope[:attack_samples] *= np.linspace(0.0, 1.0, attack_samples)

    chime = (wave_sig * envelope * volume).astype(np.float32)
    tail_pad = np.zeros(int(sample_rate * 0.15), dtype=np.float32)
    return np.concatenate([chime, tail_pad])


def make_silence(seconds: float, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    return np.zeros(int(sample_rate * max(0.0, seconds)), dtype=np.float32)


class ExamSynthThread(QThread):
    """后台多音色考场音频合成线程"""

    progress_updated = pyqtSignal(int, str)
    synth_finished = pyqtSignal(object, float)  # (audio_np, elapsed_sec)
    synth_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.pipelines: Dict[str, KPipeline] = {}
        self.current_device = None
        self.is_cancelled = False

        self.exam_data: dict = {}
        self.params: dict = {}

    def start_task(self, exam_data: dict, params: dict):
        self.is_cancelled = False
        self.exam_data = exam_data
        self.params = params
        self.start()

    def cancel(self):
        self.is_cancelled = True

    def _resolve_device(self) -> str:
        req = self.params.get("device", "auto")
        if req == "auto":
            return "cuda" if torch.cuda.is_available() else "cpu"
        return req

    def _get_pipeline(self, lang_code: str, device: str) -> KPipeline:
        if self.current_device != device and self.pipelines:
            self.pipelines.clear()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        self.current_device = device
        if lang_code not in self.pipelines:
            self.progress_updated.emit(
                5, f"正在加载 Kokoro 声学模型 ({device.upper()})..."
            )
            self.pipelines[lang_code] = KPipeline(
                lang_code=lang_code,
                repo_id="hexgrad/Kokoro-82M",
                device=device,
            )
        return self.pipelines[lang_code]

    def _resolve_voice_path(self, voice_str: str) -> str:
        voice_key = voice_str.split(" ")[0].strip()
        base_dir = get_project_root()
        parent_dir = os.path.dirname(base_dir)
        candidates = [
            os.path.join(base_dir, "voices", f"{voice_key}.pt"),
            os.path.join(parent_dir, "voices", f"{voice_key}.pt"),
            os.path.join(DEFAULT_KOKORO_ROOT, "voices", f"{voice_key}.pt"),
            os.path.join("voices", f"{voice_key}.pt"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
        return voice_key

    def _synth_single_line(
        self, text: str, voice_str: str, speed: float, device: str
    ) -> np.ndarray:
        cleaned = clean_for_kokoro(text)
        if not cleaned:
            return np.zeros(0, dtype=np.float32)

        voice_key = voice_str.split(" ")[0].strip()
        lang_code = voice_key[0] if voice_key else "a"
        if lang_code not in ("a", "b", "z", "j", "e", "f", "i", "h", "p"):
            lang_code = "a"

        pipeline = self._get_pipeline(lang_code, device)
        actual_voice = self._resolve_voice_path(voice_str)

        chunks = []
        for _, _, audio in pipeline(cleaned, voice=actual_voice, speed=speed):
            if self.is_cancelled:
                return np.zeros(0, dtype=np.float32)
            if isinstance(audio, torch.Tensor):
                arr = audio.detach().cpu().numpy()
            else:
                arr = np.asarray(audio)
            chunks.append(arr.astype(np.float32))

        if not chunks:
            return np.zeros(0, dtype=np.float32)

        raw_audio = np.concatenate(chunks)
        trimmed = trim_edge_silence(raw_audio)
        return normalize_loudness(trimmed)

    def run(self):
        start_time = time.time()
        try:
            device = self._resolve_device()
            dialogue = self.exam_data.get("dialogue", [])
            questions = self.exam_data.get("questions", [])
            total_steps = max(1, len(dialogue) + len(questions))
            done_steps = 0

            voice_male = self.params.get("voice_male", "am_michael")
            voice_female = self.params.get("voice_female", "af_heart")
            voice_examiner = self.params.get("voice_examiner", "am_eric")
            speed = float(self.params.get("speech_rate", 1.0))
            turn_gap = float(self.params.get("turn_gap_sec", 0.25))
            section_gap = float(self.params.get("section_gap_sec", 1.2))
            pre_q_sec = float(self.params.get("pre_question_read_sec", 8.0))
            post_q_sec = float(self.params.get("post_question_answer_sec", 8.0))
            enable_ding = bool(self.params.get("enable_ding_chime", True))
            ding_vol = float(self.params.get("ding_volume", 0.18))

            timeline = []

            # 1. 合成双人长对话
            for idx, turn in enumerate(dialogue):
                if self.is_cancelled:
                    return
                role = str(turn.get("role", "M")).strip().upper()
                voice = voice_male if role == "M" else voice_female
                spoken = turn.get("spoken_text") or turn.get("display_text", "")

                done_steps += 1
                pct = int(10 + (done_steps / total_steps) * 80)
                self.progress_updated.emit(
                    pct, f"正在合成对话第 {idx + 1}/{len(dialogue)} 轮 ({role})..."
                )

                seg = self._synth_single_line(spoken, voice, speed, device)
                if len(seg) > 0:
                    timeline.append(seg)
                    if idx < len(dialogue) - 1:
                        timeline.append(make_silence(turn_gap))

            if self.is_cancelled:
                return
            timeline.append(make_silence(section_gap))

            # 2. 合成考官读题与静音窗口
            ding_audio = (
                generate_ding_chime(volume=ding_vol) if enable_ding else None
            )
            for idx, q in enumerate(questions):
                if self.is_cancelled:
                    return
                done_steps += 1
                pct = int(10 + (done_steps / total_steps) * 80)
                self.progress_updated.emit(
                    pct, f"正在合成考官读题 Question {q['id']} ({idx + 1}/{len(questions)})..."
                )

                timeline.append(make_silence(pre_q_sec))
                if ding_audio is not None:
                    timeline.append(ding_audio)

                q_spoken = q.get("spoken_text") or q.get("display_text", "")
                prompt_text = f"Question {q['id']}. {q_spoken}"
                q_seg = self._synth_single_line(
                    prompt_text, voice_examiner, speed, device
                )
                if len(q_seg) > 0:
                    timeline.append(q_seg)

                timeline.append(make_silence(post_q_sec))

            if self.is_cancelled or not timeline:
                return

            self.progress_updated.emit(95, "正在混音并导出考场文件包...")
            full_audio = np.concatenate(timeline)
            elapsed = time.time() - start_time
            self.synth_finished.emit(full_audio, elapsed)

        except Exception as e:
            err_str = str(e)
            if "[Errno 2]" in err_str or "espeak" in err_str.lower():
                self.synth_error.emit(
                    "未找到 eSpeak NG 语音音素引擎！\n请确认 espeak-ng 位于 KokoroTTS 根目录或系统环境变量中。"
                )
            else:
                self.synth_error.emit(err_str)