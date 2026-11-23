import ctypes
from ctypes import wintypes
import os
import warnings

from kokoro_studio.config import ESPEAK_LOCAL_DIR, HTTP_PORT


def auto_detect_espeak():
    """Locate eSpeak NG binary directory and inject into PATH / PHONEMIZER_ESPEAK_PATH."""
    possible_paths = [
        r"C:\Program Files\eSpeak NG",
        r"C:\Program Files (x86)\eSpeak NG",
        ESPEAK_LOCAL_DIR,
        os.path.expanduser(r"~\miniconda3\Library\bin"),
        os.path.expanduser(r"~\anaconda3\Library\bin"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            if p not in os.environ.get("PATH", ""):
                os.environ["PATH"] = p + ";" + os.environ.get("PATH", "")
            os.environ["PHONEMIZER_ESPEAK_PATH"] = p
            break

    warnings.filterwarnings("ignore")


def dismiss_external_osd():
    """Clear lingering ToolTip OSD windows from external scripts or AHK."""
    port_str = str(HTTP_PORT)
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
                    if port_str in title.value or "Kokoro TTS" in title.value:
                        ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
                        ctypes.windll.user32.ShowWindow(hwnd, 0)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    except Exception:
        pass


def bring_window_to_foreground(hwnd: int):
    """Force Win32 HWND to restore, elevate topmost briefly, and acquire focus."""
    if not hwnd:
        return
    try:
        ctypes.windll.user32.ShowWindow(hwnd, 9)
        ctypes.windll.user32.SetWindowPos(
            hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040
        )
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def register_taskbar_created_msg() -> int:
    """Register Windows TaskbarCreated broadcast message for Explorer crash recovery."""
    try:
        return ctypes.windll.user32.RegisterWindowMessageW("TaskbarCreated")
    except Exception:
        return 0