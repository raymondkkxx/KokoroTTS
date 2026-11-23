import ctypes
import os
import sys

# Ensure project root directory is in sys.path (required for Windows embedded Python)
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# =========================================================================
# 1. Core Priority: PyTorch & NumPy MUST be imported before any Qt modules
# =========================================================================
import numpy as np  # noqa: F401
import torch  # noqa: F401

# =========================================================================
# 2. Detect eSpeak NG environment before importing Kokoro pipeline
# =========================================================================
from kokoro_studio.win32_utils import auto_detect_espeak

auto_detect_espeak()

# =========================================================================
# 3. GUI & Application Bootstrap
# =========================================================================
from PyQt5.QtGui import QFont
from PyQt5.QtNetwork import QLocalSocket
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

from kokoro_studio.config import MUTEX_NAME, SINGLE_INSTANCE_KEY
from kokoro_studio.main_window import CyberTtsPlayer


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app_font = QFont()
    app_font.setFamilies(["Consolas", "Microsoft YaHei UI"])
    app_font.setPointSize(10)
    app.setFont(app_font)

    # Single-instance guard via Win32 Named Mutex + QLocalSocket IPC
    kernel32 = ctypes.windll.kernel32
    global_mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    last_error = kernel32.GetLastError()
    ERROR_ALREADY_EXISTS = 183

    if last_error == ERROR_ALREADY_EXISTS:
        if global_mutex:
            kernel32.CloseHandle(global_mutex)

        socket = QLocalSocket()
        socket.connectToServer(SINGLE_INSTANCE_KEY)
        if socket.waitForConnected(800):
            socket.write(b"ACTIVATE")
            socket.waitForBytesWritten(1000)
            socket.disconnectFromServer()
        sys.exit(0)

    player = CyberTtsPlayer(mutex_handle=global_mutex)
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


if __name__ == "__main__":
    main()