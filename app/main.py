"""
このファイルは、FastAPI を用いてアプリケーション全体のエントリーポイントを定義し、
APIサーバーの初期化と基本的なルーティングを設定する役割を持つ。アプリケーション
起動時には lifespan コンテキストマネージャを通じてデータベースの初期化
（init_db）が実行され、必要なテーブルが自動的に準備される仕組みになっている。
また、/health エンドポイントを提供することでサービスの稼働状態を簡易的に確認
できるようにしている。さらに、ビルド済みのフロントエンド（frontend/dist）
が存在する場合には、それを静的ファイルとしてルートパスにマウントし、バックエンドと
フロントエンドを単一のサーバーで配信できる構成となっている。このように本ファイルは、
アプリケーションの起動処理、ヘルスチェック、およびフロントエンド配信を統合的に管理
する中核的な役割を担っている。
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="PathFinder v2", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.exists(FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="static")
