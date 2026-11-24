import io
import os
import re
import shutil
import struct
import subprocess
import wave

import numpy as np

from kokoro_studio.config import APP_ROOT_DIR, DEFAULT_EXPORT_ARTIST, SAMPLE_RATE, TRAY_ICON_PATH
from kokoro_studio.text_engine import sanitize_export_stem, sanitize_text


def resolve_unique_mp3_path(directory: str, stem: str) -> str:
    """
    Build target MP3 path in `directory` from `stem`, appending (1), (2), ...
    automatically when a file with the same name already exists.
    """
    target_dir = directory if directory else APP_ROOT_DIR
    os.makedirs(target_dir, exist_ok=True)

    safe_stem = sanitize_export_stem(stem, fallback="kokoro_speech")
    candidate = os.path.join(target_dir, f"{safe_stem}.mp3")
    if not os.path.exists(candidate):
        return candidate

    counter = 1
    while True:
        numbered = os.path.join(target_dir, f"{safe_stem} ({counter}).mp3")
        if not os.path.exists(numbered):
            return numbered
        counter += 1


def compute_export_loop_count(duration_sec: float) -> int:
    """
    Determine export repeat count based on single-pass audio duration:
      - < 5s  -> 3 loops
      - < 20s -> 2 loops
      - >= 20s -> 1 loop
    """
    if duration_sec < 5.0:
        return 3
    if duration_sec < 20.0:
        return 2
    return 1


def format_lrc_timestamp(seconds: float) -> str:
    """Format seconds into standard [mm:ss.xx] LRC tag without 60.00s overflow."""
    total_cs = max(0, int(round(seconds * 100.0)))
    minutes, rem_cs = divmod(total_cs, 6000)
    sec_float = rem_cs / 100.0
    return f"[{minutes:02d}:{sec_float:05.2f}]"


def build_looped_audio_and_lyrics(
    audio: np.ndarray,
    timeline: list,
    fallback_text: str = "",
    sample_rate: int = SAMPLE_RATE,
    title: str = "Kokoro Speech",
    artist: str = DEFAULT_EXPORT_ARTIST,
    album: str = "KokoroTTS",
) -> tuple:
    """
    Expand audio and synchronized clean-text lyrics (both LRC string and ID3 SYLT tuples).
    Returns (looped_audio: np.ndarray, lrc_text: str, sylt_entries: list, loop_count: int).
    """
    if audio is None or len(audio) == 0:
        raise ValueError("No audio data available to export.")

    single_duration = len(audio) / float(sample_rate)
    loop_count = compute_export_loop_count(single_duration)

    looped_audio = np.tile(audio, loop_count) if loop_count > 1 else audio

    lrc_lines = [
        f"[ti:{title}]",
        f"[ar:{artist}]",
        f"[al:{album}]",
        "[by:KokoroTTS Studio]",
    ]
    sylt_entries = []
    last_ts = -1.0

    for loop_idx in range(loop_count):
        time_offset = loop_idx * single_duration
        if timeline:
            for seg in timeline:
                clean_line = (seg.get("clean_text") or seg.get("tts_text") or "").strip()
                clean_line = re.sub(r"\s+", " ", clean_line)
                if not clean_line:
                    continue
                ts_sec = max(last_ts, time_offset + float(seg.get("start_sec", 0.0)))
                last_ts = ts_sec
                lrc_lines.append(f"{format_lrc_timestamp(ts_sec)}{clean_line}")
                sylt_entries.append((clean_line + "\n", max(0, int(round(ts_sec * 1000.0)))))
        elif fallback_text.strip():
            clean_fallback = re.sub(r"\s+", " ", fallback_text.strip())
            ts_sec = max(last_ts, time_offset)
            last_ts = ts_sec
            lrc_lines.append(f"{format_lrc_timestamp(ts_sec)}{clean_fallback}")
            sylt_entries.append((clean_fallback + "\n", max(0, int(round(ts_sec * 1000.0)))))

    return looped_audio, "\n".join(lrc_lines), sylt_entries, loop_count


def build_looped_audio_and_lrc(
    audio: np.ndarray,
    timeline: list,
    fallback_text: str = "",
    sample_rate: int = SAMPLE_RATE,
    title: str = "Kokoro Speech",
    artist: str = DEFAULT_EXPORT_ARTIST,
    album: str = "KokoroTTS",
) -> tuple:
    """Backward-compatible 3-tuple wrapper returning (looped_audio, lrc_text, loop_count)."""
    looped_audio, lrc_text, _, loop_count = build_looped_audio_and_lyrics(
        audio=audio,
        timeline=timeline,
        fallback_text=fallback_text,
        sample_rate=sample_rate,
        title=title,
        artist=artist,
        album=album,
    )
    return looped_audio, lrc_text, loop_count


def _float_to_int16_bytes(audio: np.ndarray) -> bytes:
    scaled = np.clip(audio, -1.0, 1.0) * 32767.0
    return scaled.astype(np.int16).tobytes()


def _find_ffmpeg_executable() -> str:
    local_candidates = [
        os.path.join(APP_ROOT_DIR, "ffmpeg.exe"),
        os.path.join(APP_ROOT_DIR, "ffmpeg", "ffmpeg.exe"),
    ]
    for path in local_candidates:
        if os.path.isfile(path):
            return path

    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass

    return ""


def encode_pcm_to_mp3_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    """
    Encode mono float32 PCM audio into MP3 bytes using available backend:
      1. lameenc (self-contained Python wheel)
      2. ffmpeg (local/system/imageio-ffmpeg)
      3. soundfile (libsndfile >= 1.1.0 MP3 support)
    """
    pcm_bytes = _float_to_int16_bytes(audio)

    # Backend 1: lameenc
    try:
        import lameenc

        encoder = lameenc.Encoder()
        encoder.set_bit_rate(192)
        encoder.set_in_sample_rate(sample_rate)
        encoder.set_channels(1)
        encoder.set_quality(2)
        mp3_data = encoder.encode(pcm_bytes) + encoder.flush()
        if mp3_data:
            return bytes(mp3_data)
    except ImportError:
        pass

    # Backend 2: ffmpeg
    ffmpeg_exe = _find_ffmpeg_executable()
    if ffmpeg_exe:
        wav_buf = io.BytesIO()
        with wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            [
                ffmpeg_exe,
                "-y",
                "-f",
                "wav",
                "-i",
                "pipe:0",
                "-codec:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-f",
                "mp3",
                "pipe:1",
            ],
            input=wav_buf.getvalue(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout

    # Backend 3: soundfile
    try:
        import soundfile as sf

        mp3_buf = io.BytesIO()
        sf.write(mp3_buf, np.clip(audio, -1.0, 1.0), sample_rate, format="MP3")
        data = mp3_buf.getvalue()
        if data:
            return data
    except Exception:
        pass

    raise RuntimeError(
        "No MP3 encoder backend found.\n"
        "Please install 'lameenc' (pip install lameenc) or place ffmpeg.exe in the application folder."
    )


def _prepare_cover_jpeg(cover_path: str) -> tuple:
    """
    Convert cover image (including RGBA PNG) into opaque RGB JPEG bytes
    for reliable cover rendering on mobile music players.
    """
    if not cover_path or not os.path.isfile(cover_path):
        return b"", "image/jpeg"

    try:
        from PIL import Image

        with Image.open(cover_path) as img:
            if img.mode in ("RGBA", "LA", "P"):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                rgb_img = bg
            else:
                rgb_img = img.convert("RGB")

            buf = io.BytesIO()
            rgb_img.save(buf, format="JPEG", quality=92)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        try:
            with open(cover_path, "rb") as img_f:
                raw = img_f.read()
            mime = (
                "image/jpeg"
                if cover_path.lower().endswith((".jpg", ".jpeg"))
                else "image/png"
            )
            return raw, mime
        except Exception:
            return b"", "image/jpeg"


def _synchsafe_int(size: int) -> bytes:
    return bytes(
        [
            (size >> 21) & 0x7F,
            (size >> 14) & 0x7F,
            (size >> 7) & 0x7F,
            size & 0x7F,
        ]
    )


def _build_id3v24_frame(frame_id: str, payload: bytes) -> bytes:
    """Build an ID3v2.4 frame (uses synchsafe 28-bit integer for frame payload size)."""
    return frame_id.encode("ascii") + _synchsafe_int(len(payload)) + b"\x00\x00" + payload


def _build_id3v24_tag_bytes(
    title: str,
    artist: str,
    album: str,
    lyrics_lrc: str,
    sylt_entries: list = None,
    cover_bytes: bytes = b"",
    cover_mime: str = "image/jpeg",
) -> bytes:
    """Construct an ID3v2.4 tag with UTF-8 text, USLT, TXXX:LYRICS, SYLT, and APIC cover."""
    frames = []

    if title:
        frames.append(_build_id3v24_frame("TIT2", b"\x03" + title.encode("utf-8")))

    if artist:
        frames.append(_build_id3v24_frame("TPE1", b"\x03" + artist.encode("utf-8")))

    if album:
        frames.append(_build_id3v24_frame("TALB", b"\x03" + album.encode("utf-8")))

    if lyrics_lrc:
        # USLT (UTF-8, ISO-639-2 'eng', null-terminated empty desc)
        uslt_payload = b"\x03eng\x00" + lyrics_lrc.encode("utf-8")
        frames.append(_build_id3v24_frame("USLT", uslt_payload))

        # TXXX:LYRICS (UTF-8 embedded LRC extension frame)
        txxx_payload = b"\x03LYRICS\x00" + lyrics_lrc.encode("utf-8")
        frames.append(_build_id3v24_frame("TXXX", txxx_payload))

    if sylt_entries:
        # SYLT (encoding=0x03 UTF-8, lang='eng', format=0x02 ms, type=0x01 lyrics, desc='')
        sylt_chunks = [b"\x03eng\x02\x01\x00"]
        for line_text, ts_ms in sylt_entries:
            sylt_chunks.append(line_text.encode("utf-8") + b"\x00" + struct.pack(">I", ts_ms))
        frames.append(_build_id3v24_frame("SYLT", b"".join(sylt_chunks)))

    if cover_bytes:
        apic_payload = (
            b"\x00"
            + cover_mime.encode("ascii")
            + b"\x00\x03\x00"
            + cover_bytes
        )
        frames.append(_build_id3v24_frame("APIC", apic_payload))

    body = b"".join(frames)
    header = b"ID3\x04\x00\x00" + _synchsafe_int(len(body))
    return header + body


def write_mp3_with_netease_metadata(
    file_path: str,
    mp3_bytes: bytes,
    title: str,
    artist: str = DEFAULT_EXPORT_ARTIST,
    album: str = "KokoroTTS",
    lyrics_lrc: str = "",
    sylt_entries: list = None,
    cover_path: str = TRAY_ICON_PATH,
):
    """Write single MP3 file with ID3v2.4 UTF-8 USLT, TXXX:LYRICS, SYLT, and Cover Art."""
    cover_bytes, cover_mime = _prepare_cover_jpeg(cover_path)

    # Prefer mutagen if available; otherwise write ID3v2.4 binary header directly
    try:
        from mutagen.id3 import APIC, ID3, SYLT, TALB, TIT2, TPE1, TXXX, USLT, ID3NoHeaderError

        with open(file_path, "wb") as f:
            f.write(mp3_bytes)

        try:
            tags = ID3(file_path)
        except ID3NoHeaderError:
            tags = ID3()

        tags.delall("TIT2")
        tags.delall("TPE1")
        tags.delall("TALB")
        tags.delall("USLT")
        tags.delall("SYLT")
        tags.delall("TXXX")
        tags.delall("APIC")

        if title:
            tags.add(TIT2(encoding=3, text=title))
        if artist:
            tags.add(TPE1(encoding=3, text=artist))
        if album:
            tags.add(TALB(encoding=3, text=album))
        if lyrics_lrc:
            tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics_lrc))
            tags.add(TXXX(encoding=3, desc="LYRICS", text=lyrics_lrc))
        if sylt_entries:
            tags.add(
                SYLT(
                    encoding=3,
                    lang="eng",
                    format=2,  # 2 = absolute milliseconds
                    type=1,    # 1 = lyrics
                    desc="",
                    text=sylt_entries,
                )
            )
        if cover_bytes:
            tags.add(
                APIC(
                    encoding=3,
                    mime=cover_mime,
                    type=3,
                    desc="Cover",
                    data=cover_bytes,
                )
            )
        tags.save(file_path, v2_version=4)
        return
    except ImportError:
        pass

    id3_header = _build_id3v24_tag_bytes(
        title=title,
        artist=artist,
        album=album,
        lyrics_lrc=lyrics_lrc,
        sylt_entries=sylt_entries,
        cover_bytes=cover_bytes,
        cover_mime=cover_mime,
    )
    with open(file_path, "wb") as f:
        f.write(id3_header)
        f.write(mp3_bytes)


def export_netease_mp3_file(
    file_path: str,
    audio: np.ndarray,
    timeline: list,
    visible_text: str = "",
    phonetic_mode: bool = False,
    artist: str = DEFAULT_EXPORT_ARTIST,
    album: str = "KokoroTTS",
    cover_path: str = TRAY_ICON_PATH,
) -> int:
    """
    Single-file MP3 export pipeline:
    loop audio & synced lyrics -> encode MP3 -> write ID3v2.4 UTF-8 (USLT + TXXX:LYRICS + SYLT).
    Returns the applied loop count (1, 2, or 3).
    """
    fallback_clean = sanitize_text(visible_text, phonetic_mode=phonetic_mode)
    title = os.path.splitext(os.path.basename(file_path))[0] or "Kokoro Speech"
    looped_audio, lrc_text, sylt_entries, loop_count = build_looped_audio_and_lyrics(
        audio=audio,
        timeline=timeline,
        fallback_text=fallback_clean,
        sample_rate=SAMPLE_RATE,
        title=title,
        artist=artist,
        album=album,
    )
    mp3_bytes = encode_pcm_to_mp3_bytes(looped_audio, sample_rate=SAMPLE_RATE)
    write_mp3_with_netease_metadata(
        file_path=file_path,
        mp3_bytes=mp3_bytes,
        title=title,
        artist=artist,
        album=album,
        lyrics_lrc=lrc_text,
        sylt_entries=sylt_entries,
        cover_path=cover_path,
    )
    return loop_count