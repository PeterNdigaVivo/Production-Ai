from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = Field(..., alias="REDIS_URL")
    stream_frames: str = Field("stream:frames", alias="REDIS_STREAM_FRAMES")
    backend_url: str = Field("http://backend:8000", alias="BACKEND_URL")
    internal_service_token: str | None = Field(None, alias="INTERNAL_SERVICE_TOKEN")
    target_fps: int = Field(8, alias="INGEST_TARGET_FPS")
    hw_accel: str = Field("auto", alias="INGEST_HW_ACCEL")
    transport: str = Field("tcp", alias="INGEST_TRANSPORT")
    # Maximum bytes per frame published to Redis (downsampled JPEGs).
    max_frame_bytes: int = 200_000
    # Maximum stream length per camera (frames). Redis trims to this with MAXLEN ~.
    stream_maxlen: int = 600  # ~75s at 8fps

    settings_singleton: bool = True


settings = Settings()  # type: ignore[call-arg]
