"""
このファイルは、SQLite を用いたデータベース接続およびスキーマ管理を担当するモジュールであり、
非同期ライブラリである aiosqlite を利用して効率的なデータアクセスを実現している。アプリ
ケーションで使用する主要なテーブル（trips、places、distance_cache）のCREATE文
が定義されており、init_db 関数によってアプリ起動時にこれらのテーブルが存在しない場合のみ
自動的に作成される仕組みになっている。また、データベースファイルの保存ディレクトリが存在しない
場合には事前に作成されるため、初回起動時でもエラーなく動作する。get_db 関数はデータベース
接続を返し、行データを辞書形式で扱えるように row_factory が設定されている。このように本
ファイルは、データ永続化の基盤としてスキーマ定義、接続管理、および初期化処理を一括して担っている。
"""

import os

import aiosqlite

from app.config import settings

CREATE_TRIPS = """
CREATE TABLE IF NOT EXISTS trips (
    id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    start_lat REAL NOT NULL,
    start_lon REAL NOT NULL,
    end_lat REAL NOT NULL,
    end_lon REAL NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    date TEXT NOT NULL,
    transport_mode TEXT NOT NULL DEFAULT 'foot',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

CREATE_PLACES = """
CREATE TABLE IF NOT EXISTS places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trip_id TEXT NOT NULL REFERENCES trips(id),
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    category TEXT,
    priority TEXT NOT NULL DEFAULT 'want',
    estimated_duration_min INTEGER,
    opening_hours TEXT,
    opening_hours_source TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    arrived_at TEXT,
    departed_at TEXT,
    created_at TEXT NOT NULL
);
"""

CREATE_DISTANCE_CACHE = """
CREATE TABLE IF NOT EXISTS distance_cache (
    trip_id TEXT NOT NULL REFERENCES trips(id),
    from_place_id INTEGER NOT NULL,
    to_place_id INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    PRIMARY KEY (trip_id, from_place_id, to_place_id)
);
"""


async def get_db() -> aiosqlite.Connection:
    """Open and return a database connection with row_factory set."""
    db = await aiosqlite.connect(settings.database_path)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    os.makedirs(os.path.dirname(os.path.abspath(settings.database_path)), exist_ok=True)
    async with aiosqlite.connect(settings.database_path) as db:
        await db.execute(CREATE_TRIPS)
        await db.execute(CREATE_PLACES)
        await db.execute(CREATE_DISTANCE_CACHE)
        await db.commit()
