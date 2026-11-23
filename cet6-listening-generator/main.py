# -*- coding: utf-8 -*-
import ctypes
import os
import sys

# 强制将项目根目录加入模块搜索路径（解决 Windows 便携版 Python 找不到 ui/core 的问题）
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# =========================================================================
# 1. Core Priority: PyTorch & NumPy must be imported before any Qt modules
# =========================================================================
import numpy as np  # noqa: F401
import torch  # noqa: F401

from PyQt5.QtGui import QFont
from PyQt5.QtNetwork import QLocalServer, QLocalSocket
from PyQt5.QtWidgets import QApplication

from ui.main_window import Cet6GeneratorMainWindow

SINGLE_INSTANCE_KEY = "CET6ListeningStudio_SingleInstance_Server"
MUTEX_NAME = "Local\\CET6ListeningStudio_SingleInstance_Mutex"
global_mutex = None


def main():
    global global_mutex

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    app_font = QFont("Consolas", 10)
    app_font.setStyleHint(QFont.Monospace)
    app.setFont(app_font)

    # Windows 单实例互斥锁：重复启动时自动唤醒已有窗口
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        global_mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        last_error = kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
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

    window = Cet6GeneratorMainWindow(single_instance_key=SINGLE_INSTANCE_KEY)

    def _cleanup():
        global global_mutex
        window.cleanup_resources()
        if os.name == "nt" and global_mutex:
            try:
                ctypes.windll.kernel32.CloseHandle(global_mutex)
                global_mutex = None
            except Exception:
                pass

    app.aboutToQuit.connect(_cleanup)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()