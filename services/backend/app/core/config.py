from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_name: str = "production-ai"
    environment: str = Field("development", alias="ENVIRONMENT")
    log_level: str = "INFO"

    # DB
    database_url: str = Field(..., alias="DATABASE_URL")

    # Redis
    redis_url: str = Field(..., alias="REDIS_URL")
    stream_frames: str = Field("stream:frames", alias="REDIS_STREAM_FRAMES")
    stream_detections: str = Field("stream:detections", alias="REDIS_STREAM_DETECTIONS")
    stream_tracks: str = Field("stream:tracks", alias="REDIS_STREAM_TRACKS")
    stream_events: str = Field("stream:events", alias="REDIS_STREAM_EVENTS")

    # Auth
    jwt_secret: str = Field(..., alias="JWT_SECRET")
    jwt_algorithm: str = Field("HS256", alias="JWT_ALGORITHM")
    jwt_access_ttl: int = Field(900, alias="JWT_ACCESS_TTL_SECONDS")
    jwt_refresh_ttl: int = Field(2_592_000, alias="JWT_REFRESH_TTL_SECONDS")

    # Shared secret for service-to-service `/_internal` endpoints. Required
    # when ingestion/tracking talk to the backend. Never expose externally.
    internal_service_token: str | None = Field(None, alias="INTERNAL_SERVICE_TOKEN")

    # Dev convenience: when ENVIRONMENT == "development" the backend will
    # idempotently create this admin user on startup. LOCAL ONLY.
    # Dev convenience: when ENVIRONMENT == "development" AND seed_dev_admin is
    # explicitly enabled, the backend creates this admin on startup. Requiring an
    # explicit opt-in (not just the env string) prevents an accidentally-misset
    # ENVIRONMENT from creating a well-known superuser in a real deploy (Finding 9).
    seed_dev_admin: bool = Field(False, alias="SEED_DEV_ADMIN")
    dev_admin_email: str = Field("admin@local", alias="DEV_ADMIN_EMAIL")
    dev_admin_password: str = Field("admin", alias="DEV_ADMIN_PASSWORD")

    # CORS
    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
