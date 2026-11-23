from http.server import BaseHTTPRequestHandler, HTTPServer

from PyQt5.QtCore import QThread

from kokoro_studio.config import HTTP_HOST, HTTP_PORT


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
            if hasattr(self.server, "mode_change_signal") and self.server.mode_change_signal:
                self.server.mode_change_signal.emit(mode_str)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"MODE_UPDATED")
        elif self.path in ("/transparency", "/opacity"):
            val_str = post_data.strip()
            prefix = "T:" if self.path == "/transparency" else "O:"
            if hasattr(self.server, "opacity_change_signal") and self.server.opacity_change_signal:
                self.server.opacity_change_signal.emit(prefix + val_str)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"TRANSPARENCY_UPDATED")
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/mode":
            mode_val = getattr(self.server, "current_mode_val", "2")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(mode_val.encode("utf-8"))
        elif self.path == "/transparency":
            t_val = getattr(self.server, "current_transparency_val", "30")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(t_val.encode("utf-8"))
        elif self.path == "/opacity":
            op_val = getattr(self.server, "current_opacity_val", "70")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(op_val.encode("utf-8"))
        elif self.path == "/ping":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


class HttpServerThread(QThread):

    def __init__(self, text_signal, mode_signal=None, opacity_signal=None):
        super().__init__()
        self.text_signal = text_signal
        self.mode_signal = mode_signal
        self.opacity_signal = opacity_signal
        self.server = None
        self.init_mode_val = "2"
        self.init_transparency_val = 30

    def run(self):
        try:
            HTTPServer.allow_reuse_address = True
            self.server = HTTPServer((HTTP_HOST, HTTP_PORT), RequestHandler)
            self.server.text_received_signal = self.text_signal
            self.server.mode_change_signal = self.mode_signal
            self.server.opacity_change_signal = self.opacity_signal
            self.server.current_mode_val = str(self.init_mode_val)
            t = max(0, min(95, int(self.init_transparency_val)))
            self.server.current_transparency_val = str(t)
            self.server.current_opacity_val = str(100 - t)
            self.server.serve_forever()
        except Exception as e:
            print("HTTP Server Notice:", e)

    def set_current_mode(self, mode_str: str):
        self.init_mode_val = str(mode_str)
        if self.server:
            self.server.current_mode_val = str(mode_str)

    def set_current_transparency(self, transparency_pct: int):
        t = max(0, min(95, int(transparency_pct)))
        self.init_transparency_val = t
        if self.server:
            self.server.current_transparency_val = str(t)
            self.server.current_opacity_val = str(100 - t)

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass