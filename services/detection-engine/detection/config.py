from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    redis_url: str = Field(..., alias="REDIS_URL")
    stream_frames: str = Field("stream:frames", alias="REDIS_STREAM_FRAMES")
    stream_detections: str = Field("stream:detections", alias="REDIS_STREAM_DETECTIONS")

    model_path: str = Field("/models/yolo11n.pt", alias="YOLO_MODEL_PATH")
    device: str = Field("cpu", alias="YOLO_DEVICE")
    half: bool = Field(False, alias="YOLO_HALF")
    conf: float = Field(0.35, alias="DETECTION_CONFIDENCE")
    iou: float = Field(0.5, alias="DETECTION_IOU")
    target_fps: int = Field(4, alias="DETECTION_TARGET_FPS")
    imgsz: int = Field(416, alias="YOLO_IMGSZ")
    torch_threads: int = Field(2, alias="TORCH_NUM_THREADS")

    # We rely on COCO class indices: 0=person, 67=cell phone.
    # In Phase 2+, retrain a custom head for sewing-machine / tray / bundle.
    classes_of_interest: list[int] = [0, 67]


settings = Settings()  # type: ignore[call-arg]
