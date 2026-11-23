import os
import threading

from kokoro import KPipeline
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
import torch

from kokoro_studio.config import SAMPLE_RATE, VOICES_DIR
from kokoro_studio.text_engine import clean_annotated_for_tts

VALID_LANG_CODES = ["a", "b", "z", "j", "e", "f", "i", "h", "p"]


def resolve_voice_path(voice_key: str) -> str:
    """Resolve local .pt voice weight path if present in root voices/ folder."""
    local_voice_path = os.path.join(VOICES_DIR, f"{voice_key}.pt")
    if not os.path.exists(local_voice_path):
        local_voice_path = os.path.join("voices", f"{voice_key}.pt")
    return local_voice_path if os.path.exists(local_voice_path) else voice_key


def synthesize_chunk(
    pipeline, text: str, voice: str, speed: float, phonetic_mode: bool = False
):
    """Run KPipeline either from converted Kokoro IPA tokens or standard G2P text."""
    if phonetic_mode:
        if hasattr(pipeline, "generate_from_tokens"):
            return pipeline.generate_from_tokens(
                tokens=text, voice=voice, speed=speed
            )
        return pipeline(f"[p](/{text}/)", voice=voice, speed=speed)
    return pipeline(text, voice=voice, speed=speed)


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


class TtsSynthesizerThread(QThread):
    synth_finished = pyqtSignal(object)  # emits {"audio": np.ndarray, "timeline": list}
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
        self.phonetic_mode = False

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
        phonetic_mode=False,
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
        self.phonetic_mode = phonetic_mode
        self.start()

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        try:
            f_key = self.female_voice.split(" ")[0]
            m_key = self.male_voice.split(" ")[0]

            target_lang = (
                f_key[0] if len(f_key) > 0 and f_key[0] in VALID_LANG_CODES else "a"
            )

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
                        generator = synthesize_chunk(
                            pipeline,
                            seg["tts_text"],
                            voice=voice_to_use,
                            speed=pass_speed,
                            phonetic_mode=self.phonetic_mode,
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
        self.phonetic_mode = False

    def play_phrase(
        self,
        phrase: str,
        voice: str,
        speed: float = 0.85,
        device: str = "cpu",
        phonetic_mode: bool = False,
    ):
        self.phonetic_mode = phonetic_mode
        self.phrase = clean_annotated_for_tts(phrase, phonetic_mode=phonetic_mode)
        self.voice = voice
        self.speed = speed
        self.device = device
        if self.phrase:
            self.start()

    def run(self):
        try:
            v_key = self.voice.split(" ")[0]
            target_lang = (
                v_key[0] if len(v_key) > 0 and v_key[0] in VALID_LANG_CODES else "a"
            )
            actual_voice = resolve_voice_path(v_key)

            chunks = []
            with shared_kokoro.lock:
                pipeline = shared_kokoro.get_pipeline(target_lang, self.device)
                generator = synthesize_chunk(
                    pipeline,
                    self.phrase,
                    voice=actual_voice,
                    speed=self.speed,
                    phonetic_mode=self.phonetic_mode,
                )
                for _, _, audio in generator:
                    if isinstance(audio, torch.Tensor):
                        chunks.append(audio.detach().cpu().numpy().astype(np.float32))
                    else:
                        chunks.append(np.asarray(audio, dtype=np.float32))

            if chunks:
                self.drill_ready.emit(np.concatenate(chunks))
        except Exception:
            pass