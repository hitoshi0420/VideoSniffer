"""SQLite 数据库操作 — 下载历史持久化"""

import asyncio
import aiosqlite
from pathlib import Path
from typing import Optional
from config.settings import CONFIG_DIR

DB_PATH = CONFIG_DIR / "history.db"

INIT_SQL = """
CREATE TABLE IF NOT EXISTS download_history (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT DEFAULT '',
    video_type TEXT DEFAULT 'direct',
    status TEXT DEFAULT 'waiting',
    progress REAL DEFAULT 0.0,
    downloaded_bytes INTEGER DEFAULT 0,
    total_bytes INTEGER DEFAULT 0,
    speed REAL DEFAULT 0.0,
    quality TEXT DEFAULT '',
    duration REAL DEFAULT 0.0,
    save_path TEXT DEFAULT '',
    error_msg TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    completed_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


async def init_db():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.executescript(INIT_SQL)
        await db.commit()


async def save_task(task_dict: dict):
    await init_db()
    sql = """INSERT OR REPLACE INTO download_history
        (id, url, title, video_type, status, progress, downloaded_bytes,
         total_bytes, speed, quality, duration, save_path, error_msg,
         created_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute(sql, (
            task_dict["id"], task_dict["url"], task_dict["title"],
            task_dict["video_type"], task_dict["status"], task_dict["progress"],
            task_dict["downloaded_bytes"], task_dict["total_bytes"],
            task_dict["speed"], task_dict["quality"], task_dict["duration"],
            task_dict["save_path"], task_dict["error_msg"],
            task_dict["created_at"], task_dict["completed_at"],
        ))
        await db.commit()


async def get_all_tasks() -> list[dict]:
    await init_db()
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM download_history ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_task(task_id: str) -> Optional[dict]:
    await init_db()
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM download_history WHERE id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def delete_task(task_id: str):
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM download_history WHERE id = ?", (task_id,))
        await db.commit()


async def clear_history():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        await db.execute("DELETE FROM download_history")
        await db.commit()
