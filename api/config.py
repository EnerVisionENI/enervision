from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Postgres — mêmes noms de variables que infra/postgres/.env, réutilisé tel quel.
    postgres_user: str = "ev_admin"
    postgres_password: str = "changeme"
    postgres_db: str = "ev_monitoring"
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    database_url: str | None = None  # si défini, prend le pas sur les champs postgres_*

    jwt_secret_key: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # API Mock IoT — même variable et même défaut que etl/collect.py et etl/alerts.py.
    api_base: str = "http://10.105.200.45:8000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
