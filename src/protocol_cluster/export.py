"""导出协议簇 jsonl。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

SRC_ROOT = Path(__file__).resolve().parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from protocol_cluster.cluster import build_protocol_clusters, cluster_stats, load_jsonl

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "protocol_cluster.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def export_protocol_clusters_jsonl(config: dict | None = None) -> Path:
    config = config or load_config()
    acronym_path = PROJECT_ROOT / config["input"]["unique_protocols_jsonl"]
    name_path = PROJECT_ROOT / config["input"]["unique_protocols_by_name_jsonl"]
    out_path = PROJECT_ROOT / config["output"]["protocol_clusters_jsonl"]

    clusters = build_protocol_clusters(
        load_jsonl(acronym_path),
        load_jsonl(name_path),
        rules=config.get("rules"),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in clusters),
        encoding="utf-8",
    )
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="合并 unique_protocols 为协议簇 jsonl")
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"配置文件（默认 {CONFIG_PATH.name}）",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    out_path = export_protocol_clusters_jsonl(config)

    clusters = load_jsonl(out_path)
    stats = cluster_stats(clusters)
    print(f"协议簇 {stats['cluster_count']} 条 → {out_path}")
    print(f"  多成员簇: {stats['multi_member_clusters']}")
    print(f"  最大成员数: {stats['max_member_count']}")


if __name__ == "__main__":
    main()
