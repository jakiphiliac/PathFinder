"""Application settings loaded from environment / .env file."""

from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    osrm_foot_url: str = "http://localhost:5000"
    osrm_car_url: str = "http://localhost:5001"
    osrm_bicycle_url: str = "http://localhost:5002"

    google_places_api_key: str = ""

    database_path: str = "./data/pathfinder.db"

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8"
    )


settings = Settings()
