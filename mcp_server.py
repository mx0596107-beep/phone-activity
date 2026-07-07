#!/usr/bin/env python3
"""手机活动上报 + 偷看屏幕。用 Python 标准库做 HTTP，不依赖 fastmcp 路由。"""

import sqlite3
import os
import glob
import cgi
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CST = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("DATA_DIR", "/tmp")
DB_PATH = DATA_DIR + "/activity.db"
PEEK_SECRET = os.environ.get("PEEK_SECRET", "momo0605")
SCREEN_DIR = DATA_DIR + "/screens"
os.makedirs(SCREEN_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS phone_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL,
            opened_at TEXT NOT NULL
        )
    """)
    return conn


def save_screenshot(data: bytes):
    name = datetime.now(CST).strftime("%Y%m%d_%H%M%S_%f") + ".png"
    with open(os.path.join(SCREEN_DIR, name), "wb") as f:
        f.write(data)
    files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
    for old in files[5:]:
        try:
            os.remove(old)
        except OSError:
            pass
    return name


def report_activity(app_name: str):
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db()
    conn.execute("INSERT INTO phone_activity (app_name, opened_at) VALUES (?, ?)", (app_name, now))
    conn.execute("""
        DELETE FROM phone_activity WHERE id NOT IN (
            SELECT id FROM phone_activity ORDER BY opened_at DESC LIMIT 100
        )
    """)
    conn.commit()
    conn.close()
    return now


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/health":
            self._send(200, "ok".encode())
        elif path == "/latest":
            files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
            if not files:
                self._send(404, "还没有截图".encode())
                return
            with open(files[0], "rb") as f:
                self._send(200, f.read(), "image/png")
        else:
            self._send(404, "not found".encode())

    def do_POST(self):
        path = self.path.split("?")[0]
        qs = self.path.split("?")[1] if "?" in self.path else ""
        if path == "/peek":
            if f"secret={PEEK_SECRET}" not in qs:
                self._send(403, "forbidden".encode())
                return
            ctype = self.headers.get("Content-Type", "")
            form = cgi.FieldStorage(
                fp=self.rfile, headers=self.headers,
                environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
            )
            if "image" not in form:
                self._send(400, "no image".encode())
                return
            data = form["image"].file.read()
            name = save_screenshot(data)
            self._send(200, ("saved: " + name).encode())
        elif path == "/report":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            app_name = body.strip() or "unknown"
            now = report_activity(app_name)
            self._send(200, ("已记录: " + app_name + " @ " + now).encode())
        else:
            self._send(404, "not found".encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print("server started on 8080", flush=True)
    server.serve_forever()
