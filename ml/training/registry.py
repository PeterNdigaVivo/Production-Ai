"""Register and promote trained models in MLflow's Model Registry.

The existing train_detection.py logs runs to MLflow. This adds the missing link
the architecture docs describe but didn't implement: registering a trained model
and promoting it through stages (Staging -> Production), so the detection service
can pull the current Production model via YOLO_MODEL_PATH.

Flow:
  train_detection.py  -> logs run + artifacts
  register_model()    -> registers the run's model under a name, returns version
  promote_model()     -> moves a version to Staging/Production (with optional
                         archival of the previously-Production version)

This keeps model lifecycle reproducible and auditable: every Production model
traces back to a run, its params, metrics, and the dataset it trained on.
"""
from __future__ import annotations

import argparse

import mlflow
from mlflow.tracking import MlflowClient


def register_model(run_id: str, artifact_path: str, name: str) -> str:
    """Register a model logged under a run; returns the new version number."""
    uri = f"runs:/{run_id}/{artifact_path}"
    mv = mlflow.register_model(model_uri=uri, name=name)
    return mv.version


def promote_model(name: str, version: str, stage: str = "Production",
                  archive_existing: bool = True) -> None:
    """Promote a model version to a stage, optionally archiving the incumbent."""
    if stage not in ("Staging", "Production", "Archived"):
        raise ValueError(f"invalid stage: {stage}")
    client = MlflowClient()
    client.transition_model_version_stage(
        name=name, version=version, stage=stage,
        archive_existing_versions=archive_existing,
    )


def current_production_uri(name: str) -> str | None:
    """The model URI currently in Production for `name`, or None."""
    client = MlflowClient()
    for mv in client.get_latest_versions(name, stages=["Production"]):
        return mv.source
    return None


def main():
    ap = argparse.ArgumentParser(description="Register / promote a model")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register")
    r.add_argument("--run-id", required=True)
    r.add_argument("--artifact-path", default="weights/best.pt")
    r.add_argument("--name", default="garment-detector")

    p = sub.add_parser("promote")
    p.add_argument("--name", default="garment-detector")
    p.add_argument("--version", required=True)
    p.add_argument("--stage", default="Production")
    p.add_argument("--no-archive", action="store_true")

    c = sub.add_parser("current")
    c.add_argument("--name", default="garment-detector")

    args = ap.parse_args()
    if args.cmd == "register":
        print(register_model(args.run_id, args.artifact_path, args.name))
    elif args.cmd == "promote":
        promote_model(args.name, args.version, args.stage, not args.no_archive)
        print(f"promoted {args.name} v{args.version} -> {args.stage}")
    elif args.cmd == "current":
        print(current_production_uri(args.name) or "(none in Production)")


if __name__ == "__main__":
    main()
