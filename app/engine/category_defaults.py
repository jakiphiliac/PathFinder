CATEGORY_DEFAULTS = {
    "museum": 90,
    "gallery": 60,
    "temple": 45,
    "church": 30,
    "castle": 90,
    "monument": 15,
    "landmark": 10,
    "park": 60,
    "garden": 45,
    "cafe": 30,
    "restaurant": 75,
    "bar": 60,
    "shop": 40,
    "market": 60,
    "theater": 120,
    "zoo": 180,
    "beach": 120,
    "viewpoint": 15,
    "other": 45,
}


def get_duration_minutes(category: str | None, override: int | None = None) -> int:
    if override is not None:
        return override
    return CATEGORY_DEFAULTS.get(category or "other", 45)
