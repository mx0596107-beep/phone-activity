#!/usr/bin/env python3
"""手机活动上报 + 偷看屏幕 + 触发发信 + 活动查询。Flask 版合并。"""

import os
import glob
import base64
import sqlite3
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timezone, timedelta

from flask import Flask, request, jsonify, send_file, Response

app = Flask(__name__)

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

def require_token():
    if not EXPECTED_TOKEN:
        return None
    auth = request.headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if token != EXPECTED_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    return None


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


# ── 活动上报 ─────────────────────────────────────────────

@app.route("/report", methods=["POST"])
def report():
    err = require_token()
    if err:
        return err

    # 兼容 JSON 和纯文本两种格式
    ctype = request.content_type or ""
    if "json" in ctype:
        data = request.get_json(silent=True) or {}
        app_name = data.get("app_name") or data.get("app") or "unknown"
        battery = data.get("battery") or data.get("电池电量")
        clipboard = data.get("clipboard") or data.get("剪贴板")
        music = data.get("music")
        location = data.get("location") or data.get("定位")
    else:
        body = request.get_data(as_text=True).strip()
        app_name = body or "unknown"
        battery = None
        clipboard = None
        music = None
        location = None

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
    return jsonify({"status": "ok", "recorded": app_name, "time": now})


# ── 活动查询 ─────────────────────────────────────────────

@app.route("/activity", methods=["GET"])
def activity():
    err = require_token()
    if err:
        return err

    limit = request.args.get("limit", 100, type=int)
    app_filter = request.args.get("app")
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
    return jsonify([{
        "app": r["app_name"],
        "time": r["opened_at"],
        "battery": r["battery"],
        "clipboard": r["clipboard"],
        "music": r["music"],
        "location": r["location"],
    } for r in rows])


@app.route("/activity/summary", methods=["GET"])
def activity_summary():
    err = require_token()
    if err:
        return err

    conn = get_db()
    rows = conn.execute(
        """SELECT app_name, opened_at, battery, clipboard, music, location
           FROM phone_activity ORDER BY opened_at DESC LIMIT 100"""
    ).fetchall()
    conn.close()

    if not rows:
        return jsonify({"last_active": None, "recent_apps": [], "count": 0})

    last_active = rows[0]["opened_at"]
    recent_apps = list(dict.fromkeys(r["app_name"] for r in rows[:10]))
    return jsonify({
        "last_active": last_active,
        "recent_apps": recent_apps,
        "count": len(rows),
        "latest_battery": rows[0]["battery"],
        "latest_music": rows[0]["music"],
        "latest_location": rows[0]["location"],
    })


# ── 截图相关 ─────────────────────────────────────────────

@app.route("/peek", methods=["POST"])
def peek_upload():
    secret = request.args.get("secret", "")
    if secret != PEEK_SECRET:
        return "forbidden", 403
    if "image" not in request.files:
        return "no image", 400
    data = request.files["image"].read()
    name = save_screenshot(data)
    return "saved: " + name


@app.route("/latest", methods=["GET"])
def latest_screenshot():
    files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
    if not files:
        return "还没有截图", 404
    return send_file(files[0], mimetype="image/png")


@app.route("/view", methods=["GET"])
def view_screenshot():
    files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
    if not files:
        return "还没有截图", 404
    with open(files[0], "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    html = '<html><body><img src="data:image/png;base64,' + b64 + '"></body></html>'
    return Response(html, mimetype="text/html")


@app.route("/latest-b64", methods=["GET"])
def latest_b64():
    files = sorted(glob.glob(os.path.join(SCREEN_DIR, "*.png")), reverse=True)
    if not files:
        return "还没有截图", 404
    with open(files[0], "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    return jsonify({"image": "data:image/png;base64," + b64})


@app.route("/peek-trigger", methods=["GET"])
def peek_trigger():
    secret = request.args.get("secret", "")
    if secret != PEEK_SECRET:
        return "forbidden", 403
    try:
        send_peek_mail()
        return "邮件已发送，等手机自动截屏"
    except Exception as e:
        return "发信失败: " + str(e), 500


# ── 健康检查 ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
@app.route("/ping", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
