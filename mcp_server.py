#!/usr/bin/env python3
"""MCP 服务：让 Polaris 查询沫沫的手机活动数据。"""

import sqlite3
import os
import json
from datetime import datetime, timezone, timedelta

from fastmcp import FastMCP

CST = timezone(timedelta(hours=8))
DB_PATH = os.environ.get("DATA_DIR", "/tmp") + "/activity.db"

mcp = FastMCP("沫沫手机活动", description="查询沫沫的手机使用记录")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@mcp.tool()
def query_recent_activity(limit: int = 20) -> str:
    """查询最近的手机使用记录。返回最近打开的app和时间。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT app_name, opened_at FROM phone_activity ORDER BY opened_at DESC LIMIT ?",
        (limit,)
    ).fetchall()
    conn.close()
    if not rows:
        return "暂无记录"
    result = [{"app": r["app_name"], "time": r["opened_at"]} for r in rows]
    return json.dumps(result, ensure_ascii=False)

@mcp.tool()
def query_activity_summary() -> str:
    """查询手机活动摘要：最后活跃时间、最近用过的app、总记录数。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT app_name, opened_at FROM phone_activity ORDER BY opened_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    if not rows:
        return "暂无活动记录"
    last_active = rows[0]["opened_at"]
    recent_apps = list(dict.fromkeys(r["app_name"] for r in rows[:10]))
    return json.dumps({
        "last_active": last_active,
        "recent_apps": recent_apps,
        "count": len(rows)
    }, ensure_ascii=False)

@mcp.tool()
def query_app_usage(app_name: str) -> str:
    """查询某个特定app的使用记录。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT app_name, opened_at FROM phone_activity WHERE app_name LIKE ? ORDER BY opened_at DESC LIMIT 20",
        (f"%{app_name}%",)
    ).fetchall()
    conn.close()
    if not rows:
        return f"没有找到 {app_name} 的使用记录"
    result = [{"app": r["app_name"], "time": r["opened_at"]} for r in rows]
    return json.dumps(result, ensure_ascii=False)

if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8081)