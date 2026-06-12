from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = Field(..., alias="REDIS_URL")
    stream_frames: str = Field("stream:frames", alias="REDIS_STREAM_FRAMES")
    stream_detections: str = Field("stream:detections", alias="REDIS_STREAM_DETECTIONS")

    model_path: str = Field("/models/yolo11n.pt", alias="YOLO_MODEL_PATH")
    device: str = Field("cuda:0", alias="YOLO_DEVICE")
    half: bool = Field(True, alias="YOLO_HALF")
    conf: float = Field(0.35, alias="DETECTION_CONFIDENCE")
    iou: float = Field(0.5, alias="DETECTION_IOU")
    target_fps: int = Field(8, alias="DETECTION_TARGET_FPS")
    imgsz: int = 640

    # We rely on COCO class indices: 0=person, 67=cell phone.
    # In Phase 2+, retrain a custom head for sewing-machine / tray / bundle.
    classes_of_interest: list[int] = [0, 67]


settings = Settings()  # type: ignore[call-arg]
