"""
ui/server.py
大容量動画ファイルを HTML5 video プレイヤーで快適にシーク再生するための
Range リクエスト (HTTP 206 Partial Content) 対応ローカル HTTP サーバー
"""

import os
import re
import mimetypes
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse
import threading

class VideoStreamHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/video":
            file_path = params.get("path", [None])[0]
            if not file_path or not os.path.exists(file_path):
                self.send_error(404, "File Not Found")
                return
            self.serve_video_file(file_path)
        else:
            super().do_GET()

    def serve_video_file(self, path):
        """HTTP Range リクエストを処理して動画をストリーミング"""
        file_size = os.path.getsize(path)
        range_header = self.headers.get("Range")

        content_type, _ = mimetypes.guess_type(path)
        if not content_type:
            content_type = "video/mp4"

        if not range_header:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_size))
            self.end_headers()
            with open(path, "rb") as f:
                self.copyfile(f, self.wfile)
            return

        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if not range_match:
            self.send_error(416, "Requested Range Not Satisfiable")
            return

        start = int(range_match.group(1))
        end = int(range_match.group(2)) if range_match.group(2) else file_size - 1
        end = min(end, file_size - 1)
        length = end - start + 1

        self.send_response(206)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()

        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            chunk_size = 64 * 1024
            while remaining > 0:
                read_bytes = min(remaining, chunk_size)
                data = f.read(read_bytes)
                if not data:
                    break
                try:
                    self.wfile.write(data)
                except (ConnectionResetError, BrokenPipeError):
                    break
                remaining -= len(data)

    def log_message(self, format, *args):
        # ログを静音化
        pass


class LocalServer:
    def __init__(self, host="127.0.0.1", port=0):
        # webディレクトリをドキュメントルートに
        web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "web"))
        handler = lambda *args, **kwargs: VideoStreamHandler(*args, directory=web_dir, **kwargs)
        self.httpd = HTTPServer((host, port), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.httpd.shutdown()
