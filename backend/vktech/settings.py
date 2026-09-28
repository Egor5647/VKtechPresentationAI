from __future__ import annotations
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env", override=False)


def config(name):
    return yaml.safe_load((ROOT / "config" / name).read_text())


def data_dir():
    path = Path(os.environ.get("DATA_DIR", ROOT / "data")).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_path(relative):
    path = (data_dir() / relative).resolve()
    if not path.is_relative_to(data_dir()):
        raise ValueError("Invalid artifact path")
    return path
