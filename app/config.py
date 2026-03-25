"""
このファイルは、アプリケーション全体で使用される設定値を環境変数または .env ファイルから読み込み、
一元管理するための構成定義を提供するものである。Pydantic の BaseSettings を利用するこ
とで、OSRMの各プロファイル（徒歩・車・自転車）のエンドポイントURLやGoogle Places APIキー、
データベースの保存パスといった設定を型安全に扱えるようになっている。また、環境変数が未設定の場合
にはデフォルト値が使用されるため、ローカル開発環境でも容易に動作させることができる。.env ファイル
の読み込み設定も含まれており、環境ごとの設定差分を柔軟に管理できる設計となっている。このファイル
により、コード内にハードコードされた設定を排除し、可搬性と保守性を高めている。
"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    osrm_foot_url: str = "http://localhost:5000"
    osrm_car_url: str = "http://localhost:5001"
    osrm_bicycle_url: str = "http://localhost:5002"

    google_places_api_key: str = ""

    database_path: str = "./data/pathfinder.db"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
