"""Fine-tune YOLOv11 on garment-line bboxes and log to MLflow."""
from __future__ import annotations
import argparse
import mlflow


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True, help="Ultralytics dataset YAML")
    p.add_argument("--model", default="yolo11n.pt")
    p.add_argument("--epochs", type=int, default=100)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--experiment", default="detection")
    args = p.parse_args()

    from ultralytics import YOLO

    mlflow.set_experiment(args.experiment)
    with mlflow.start_run():
        mlflow.log_params({
            "model": args.model, "epochs": args.epochs, "imgsz": args.imgsz, "data": args.data,
        })
        m = YOLO(args.model)
        results = m.train(data=args.data, epochs=args.epochs, imgsz=args.imgsz, plots=True)
        if results and getattr(results, "results_dict", None):
            for k, v in results.results_dict.items():
                if isinstance(v, (int, float)):
                    mlflow.log_metric(k, float(v))
        mlflow.log_artifact(str(results.save_dir))


if __name__ == "__main__":
    main()
