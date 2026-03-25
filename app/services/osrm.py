"""
このファイルは、OSRM (Open Source Routing Machine) を利用して、複数地点間の移動時間
や実際のルート情報を取得するためのサービス層のコードである。主な役割は、アプリケーションから直接OSRM
のAPIを扱うのではなく、その通信処理やレスポンスの整形、エラーハンドリングを一括して管理し、使いやすい
形で提供することにある。get_distance_matrix 関数では、与えられた複数の座標に対して全組み
合わせの移動時間（秒）を2次元配列として取得し、到達不可能な経路（null）については大きなペ
ナルティ値に置き換えることで、後続の最適化アルゴリズムなどで扱いやすくしている。一方、
get_route_geometry 関数では、指定された順序の座標列に従った実際の道路経路を取得し、
地図ライブラリ（例：Leaflet）でそのまま描画できるように座標の順序（lon, lat から lat, lon）
を変換して返す。また、徒歩・車・自転車といった移動手段ごとに異なるOSRMエンドポイントを切り替える仕組
みを持ち、非同期HTTPクライアント（httpx）を用いることで効率的な通信を実現している。
"""

from typing import cast

import httpx

from app.config import settings


def _base_url(profile: str) -> str:
    urls = {
        "foot": settings.osrm_foot_url,
        "car": settings.osrm_car_url,
        "bicycle": settings.osrm_bicycle_url,
    }
    if profile not in urls:
        raise ValueError(
            f"Unknown OSRM profile: {profile!r}. Must be foot, car, or bicycle."
        )
    return urls[profile]


async def get_distance_matrix(
    coordinates: list[list[float]],
    profile: str = "foot",
) -> list[list[float]]:
    """
    Fetch a travel time matrix from OSRM for the given coordinates.

    Args:
        coordinates: Sequence of (longitude, latitude) pairs. OSRM uses lon,lat order.
        profile: Transport mode — "foot", "car", or "bicycle".

    Returns:
        2D list where result[i][j] is travel time in seconds from coordinates[i] to coordinates[j].
        Unreachable pairs are replaced with a large penalty value.

    Raises:
        httpx.HTTPStatusError: On HTTP errors (e.g. 429 rate limit).
        ValueError: On OSRM error responses.
    """
    if not coordinates:
        return []

    if len(coordinates) == 1:
        return [[0]]

    coord_str = ";".join(f"{lon},{lat}" for lon, lat in coordinates)
    url = f"{_base_url(profile)}/table/v1/{profile}/{coord_str}?annotations=duration"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    if data.get("code") != "Ok":
        msg = data.get("message", "Unknown OSRM error")
        raise ValueError(f"OSRM error: {msg}")

    raw_durations = data.get("durations")
    if raw_durations is None:
        raise ValueError("OSRM response missing 'durations' field")
    durations = cast(list[list[float | None]], raw_durations)

    max_duration = 0.0
    for row in durations:
        for val in row:
            if val is not None:
                max_duration = max(max_duration, val)

    penalty = max(max_duration * 2, 999999)

    result: list[list[float]] = []
    for row in durations:
        result.append([(val if val is not None else penalty) for val in row])

    return result


async def get_route_geometry(
    coordinates: list[list[float]],
    profile: str = "foot",
) -> list[list[float]]:
    """
    Fetch actual path geometry for an ordered sequence of coordinates.

    Args:
        coordinates: Sequence of (longitude, latitude) pairs in visit order.
        profile: Transport mode — "foot", "car", or "bicycle".

    Returns:
        List of [lat, lon] pairs (Leaflet order) tracing the road-snapped path.
    """
    if len(coordinates) < 2:
        return []

    coord_str = ";".join(f"{lon},{lat}" for lon, lat in coordinates)
    url = f"{_base_url(profile)}/route/v1/{profile}/{coord_str}?overview=full&geometries=geojson"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        data = response.json()

    if data.get("code") != "Ok":
        msg = data.get("message", "Unknown OSRM error")
        raise ValueError(f"OSRM error: {msg}")

    routes = data.get("routes", [])
    if not routes:
        raise ValueError("OSRM returned no routes")

    geojson_coords = cast(list[list[float]], routes[0]["geometry"]["coordinates"])
    return [[lat, lon] for lon, lat in geojson_coords]
