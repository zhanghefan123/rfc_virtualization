"""导出按不同策略去重后的唯一协议 jsonl。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

SRC_ROOT = Path(__file__).resolve().parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from protocol_dedup.dedup import build_unique_protocols, build_unique_protocols_by_name, load_classification_rows

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "protocol_dedup.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def export_unique_protocols_jsonl(config: dict | None = None) -> Path:
    config = config or load_config()
    in_path = PROJECT_ROOT / config["input"]["classification_jsonl"]
    out_path = PROJECT_ROOT / config["output"]["unique_protocols_jsonl"]
    write_jsonl(out_path, build_unique_protocols(load_classification_rows(in_path)))
    return out_path


def export_unique_protocols_by_name_jsonl(config: dict | None = None) -> Path:
    config = config or load_config()
    in_path = PROJECT_ROOT / config["input"]["classification_jsonl"]
    out_path = PROJECT_ROOT / config["output"]["unique_protocols_by_name_jsonl"]
    write_jsonl(out_path, build_unique_protocols_by_name(load_classification_rows(in_path)))
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 LLM 分类去重后的唯一协议 jsonl")
    parser.add_argument(
        "--by-name",
        action="store_true",
        help="无 acronym 时按 protocol_name 去重（默认按 protocol_acronym）",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"配置文件（默认 {CONFIG_PATH.name}）",
    )
    args = parser.parse_args()
    config = load_config(args.config)

    if args.by_name:
        out_path = export_unique_protocols_by_name_jsonl(config)
        label = "按 name"
    else:
        out_path = export_unique_protocols_jsonl(config)
        label = "按 acronym"

    count = sum(1 for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip())
    print(f"唯一协议（{label}）{count} 条 → {out_path}")


if __name__ == "__main__":
    main()
