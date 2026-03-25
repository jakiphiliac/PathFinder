"""
このファイルは、Pydantic を用いてAPIのリクエストおよびレスポンスのデータ構造
を定義するモデル群を提供するものであり、アプリケーションにおけるデータの入出力
仕様を明確にする役割を持つ。Trip（旅行計画）、Place（訪問地点）、
Check-in（到着・完了などの状態更新）、Feasibility（訪問可能性評価）
、NextRecommendation（次の訪問候補）といった各ドメインごとにモデルが分かれており、
それぞれ入力用（Create/Update）と出力用（Response）を分離することで安全かつ
柔軟なAPI設計を実現している。また、Literal 型を用いて取り得る値を制限することで、
transport_modeやpriorityなどのフィールドに対してバリデーションが自動的に行われる。
このファイルにより、APIの契約（スキーマ）が明確化され、フロントエンドや他サー
ビスとの連携が容易になるとともに、データの一貫性と信頼性が向上している。
"""

from typing import Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Trip
# ---------------------------------------------------------------------------


class TripCreate(BaseModel):
    city: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    start_time: str  # "09:00"
    end_time: str  # "18:00"
    date: str  # "2026-04-15"
    transport_mode: Literal["foot", "car", "bicycle"] = "foot"


class TripUpdate(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    transport_mode: Literal["foot", "car", "bicycle"] | None = None


class TripResponse(BaseModel):
    id: str
    city: str
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    start_time: str
    end_time: str
    date: str
    transport_mode: str
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Place
# ---------------------------------------------------------------------------


class PlaceAdd(BaseModel):
    name: str
    lat: float
    lon: float
    category: str | None = None
    priority: Literal["must", "want", "if_time"] = "want"
    estimated_duration_min: int | None = None
    opening_hours: str | None = None  # user-supplied override
    opening_hours_source: str | None = None


class PlaceUpdate(BaseModel):
    priority: Literal["must", "want", "if_time"] | None = None
    estimated_duration_min: int | None = None
    opening_hours: str | None = None
    opening_hours_source: str | None = None


class PlaceResponse(BaseModel):
    id: int
    trip_id: str
    name: str
    lat: float
    lon: float
    category: str | None
    priority: str
    estimated_duration_min: int | None
    opening_hours: str | None
    opening_hours_source: str | None
    status: str
    arrived_at: str | None
    departed_at: str | None
    created_at: str


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------


class CheckInRequest(BaseModel):
    place_id: int
    action: Literal["arrived", "done", "skipped"]


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------


class FeasibilityResult(BaseModel):
    place_id: int
    color: Literal["green", "yellow", "red", "gray", "unknown"]
    slack_minutes: float
    closing_urgency_minutes: float | None
    reason: str


# ---------------------------------------------------------------------------
# Next recommendation
# ---------------------------------------------------------------------------


class NextRecommendation(BaseModel):
    place_id: int
    place_name: str
    score: float
    opportunity_cost: int
    travel_minutes: float
    reason: str
