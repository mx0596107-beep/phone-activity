#!/usr/bin/env python3
"""手机活动上报 + 偷看屏幕 + 触发发信 + 活动查询。纯标准库，不需要 flask。"""

import os
import glob
import json
import base64
import sqlite3
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

CST = timezone(timedelta(hours=8))
DATA_DIR = os.environ.get("DATA_DIR", "/tmp")
DB_PATH = DATA_DIR + "/activity.db"
PEEK_SECRET = os.environ.get("PEEK_SECRET", "momo0605")
SCREEN_DIR = DATA_DIR + "/screens"
os.makedirs(SCREEN_DIR, exist_ok=True)

EXPECTED_TOKEN = os.environ.get("REPORT_TOKEN", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
ICLOUD_TO = os.environ.get("ICLOUD_TO", "momw_0605@icloud.com")


# ── 数据库 ──────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS phone_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            app_name TEXT NOT NULL,
            opened_at TEXT NOT NULL,
            battery TEXT,
            clipboard TEXT,
            music TEXT,
            location TEXT
        )
    """)
    for col in ("battery", "clipboard", "music", "location"):
        try:
            conn.execute(f"ALTER TABLE phone_activity ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
    return conn


# ── 工具函数 ─────────────────────────────────────────────

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


def send_peek_mail():
    msg = MIMEText("peek the screen", "plain", "utf-8")
    msg["Subject"] = "peek"
    msg["From"] = GMAIL_USER
    msg["To"] = ICLOUD_TO
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.sendmail(GMAIL_USER, [ICLOUD_TO], msg.as_string())


def check_token(headers):
    if not EXPECTED_TOKEN:
        return True
    auth = headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    return token == EXPECTED_TOKEN


# ── Handler ──────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self._send(code, body, "application/json; charset=utf-8")

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length) if length else b""

    # ── GET ──

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        # 健康检查
        if path in ("", "/health", "/ping"):
            self._json(200, {"status": "ok"})

        # 活动记录查询
        elif path == "/activity":
            if not check_token(self.headers):
                self._json(401, {"error": "unauthorized"})
                return
            limit = int(params.get("limit", ["100"])[0])
            app_filter = params.get("app", [None])[0]
            conn = get_db()
            if app_filter:
                rows = conn.execute(
                    """SELECT app_name, opened_at, battery, clipboard, music, location
                       FROM phone_activity WHERE app_name LIKE ?
                       ORDER BY opened_at DESC LIMIT ?""",
                    (f"%{app_filter}%", min(limit, 100))
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT app_name, opened_at, battery, clipboard, music, location
                       FROM phone_activity ORDER BY opened_at DESC LIMIT ?""",
                    (min(limit, 100),)
                ).fetchall()
            conn.close()
            self._json(200, [{
                "app": r["app_name"], "time": r["opened_at"],
                "battery": r["battery"], "clipboard": r["clipboard"],
                "music": r["music"], "location": r["location"],
            } for r in rows])

        # 活动摘要
        elif path == "/activity/summary":
            if not check_token(self.headers):
                self._json(401, {"error": "unauthorized"})
                return
            conn = get_db()
            rows = conn.execute(
                """SELECT app_name, opened_at, battery, clipboard, music, location
                   FROM phone_activity ORDER BY opened_at DESC LIMIT 100"""
            ).fetchall()
            conn.close()
            if not rows:
                self._json(200, {"last_active": None, "recent_apps": [], "count": 0})
                return
            recent_apps = list(dict.fromkeys(r["app_name"] for r in rows[:10]))
            self._json(200, {
                "last_active": rows[0]["opened_at"],
                "recent_apps": recent_apps,
                "count": len(rows),
                "latest_battery": rows[0]["battery"],
                "latest_music": rows[0]["music"],
                "latest_location": rows[0]["location"],
            })

        # 最新截图（原图）
        elif path == "/latest":
            files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
            if not files:
                self._send(404, "还没有截图".encode())
                return
            with open(files[0], "rb") as f:
                self._send(200, f.read(), "image/png")

        # 网页看截图
        elif path == "/view":
            files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
            if not files:
                self._send(404, "还没有截图".encode())
                return
            with open(files[0], "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            html = '<html><body><img src="data:image/png;base64,' + b64 + '"></body></html>'
            self._send(200, html.encode(), "text/html; charset=utf-8")

        # base64 截图 JSON
        elif path == "/latest-b64":
            files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
            if not files:
                self._send(404, "还没有截图".encode())
                return
            with open(files[0], "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            self._json(200, {"image": "data:image/png;base64," + b64})

        # 触发截屏邮件
        elif path == "/peek-trigger":
            secret = params.get("secret", [""])[0]
            if secret != PEEK_SECRET:
                self._send(403, "forbidden".encode())
                return
            try:
                send_peek_mail()
                self._send(200, "邮件已发送，等手机自动截屏".encode())
            except Exception as e:
                self._send(500, ("发信失败: " + str(e)).encode())

        else:
            self._send(404, "not found".encode())

    # ── POST ──

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        # 活动上报
        if path == "/report":
            if not check_token(self.headers):
                self._json(401, {"error": "unauthorized"})
                return
            raw = self._read_body()
            ctype = self.headers.get("Content-Type", "")

            if "json" in ctype:
                try:
                    data = json.loads(raw)
                except Exception:
                    data = {}
                app_name = data.get("app_name") or data.get("app") or "unknown"
                battery = data.get("battery") or data.get("电池电量")
                clipboard = data.get("clipboard") or data.get("剪贴板")
                music = data.get("music")
                location = data.get("location") or data.get("定位")
            else:
                app_name = raw.decode().strip() or "unknown"
                battery = clipboard = music = location = None

            now = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
            conn = get_db()
            conn.execute(
                """INSERT INTO phone_activity
                   (app_name, opened_at, battery, clipboard, music, location)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (app_name, now, battery, clipboard, music, location),
            )
            conn.execute("""
                DELETE FROM phone_activity
                WHERE id NOT IN (
                    SELECT id FROM phone_activity ORDER BY opened_at DESC LIMIT 100
                )
            """)
            conn.commit()
            conn.close()
            self._json(200, {"status": "ok", "recorded": app_name, "time": now})

        # 截图上传
        elif path == "/peek":
            secret = params.get("secret", [""])[0]
            if secret != PEEK_SECRET:
                self._send(403, "forbidden".encode())
                return
            import cgi
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

        else:
            self._send(404, "not found".encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print("server started on 8080", flush=True)
    server.serve_forever()
