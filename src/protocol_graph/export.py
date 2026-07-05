"""导出分年段协议簇引用关系图到 outputs/graphs。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import yaml

SRC_ROOT = Path(__file__).resolve().parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from protocol_graph.graph import (
    aggregate_edges_for_bucket,
    build_bucket_edges,
    build_bucket_nodes,
    build_cluster_index,
    build_year_buckets,
    clusters_published_in_range,
    edge_cumulative_buckets,
    extract_edge_events,
    load_jsonl,
    load_rfc_years,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "protocol_graph.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_protocol_graphs(config: dict | None = None) -> Path:
    config = config or load_config()
    graph_cfg = config["graph"]

    clusters_path = PROJECT_ROOT / config["input"]["protocol_clusters_jsonl"]
    relations_dir = PROJECT_ROOT / config["input"]["relations_dir"]
    manifest_path = PROJECT_ROOT / config["input"]["manifest"]
    out_root = PROJECT_ROOT / config["output"]["directory"]

    year_span = int(graph_cfg["year_span"])
    year_start = int(graph_cfg["year_start"])
    year_end = int(graph_cfg["year_end"])
    cumulative = bool(graph_cfg.get("cumulative", True))
    reference_types = set(graph_cfg.get("reference_types") or [])
    include_self_loops = bool(graph_cfg.get("include_self_loops", False))

    clusters = load_jsonl(clusters_path)
    rfc_to_cluster, cluster_by_id = build_cluster_index(clusters)
    rfc_years = load_rfc_years(manifest_path)
    buckets = build_year_buckets(year_start, year_end, year_span, cumulative=cumulative)
    events = extract_edge_events(
        rfc_to_cluster=rfc_to_cluster,
        rfc_years=rfc_years,
        relations_dir=relations_dir,
        reference_types=reference_types,
        include_self_loops=include_self_loops,
        year_start=year_start,
        year_end=year_end,
    )

    bucket_stats: list[dict] = []
    bucket_labels = {label for _, _, label in buckets}
    if out_root.is_dir():
        for child in out_root.iterdir():
            if child.is_dir() and child.name not in bucket_labels and child.name != "full":
                shutil.rmtree(child)

    for start, end, label in buckets:
        pub_lo = year_start if cumulative else start
        published = clusters_published_in_range(rfc_to_cluster, rfc_years, pub_lo, end)
        edge_map, active_clusters = aggregate_edges_for_bucket(
            events,
            range_start=start,
            range_end=end,
            cumulative=cumulative,
            global_year_start=year_start,
        )
        nodes = build_bucket_nodes(
            bucket_label=label,
            bucket_range=(start, end),
            cumulative_through=(pub_lo, end),
            active_cluster_ids=active_clusters,
            published_rfcs=published,
            cluster_by_id=cluster_by_id,
            cumulative=cumulative,
        )
        edges = build_bucket_edges(
            edge_map,
            label,
            cumulative=cumulative,
            cumulative_end=end,
        )
        bucket_dir = out_root / label
        write_jsonl(bucket_dir / "nodes.jsonl", nodes)
        write_jsonl(bucket_dir / "edges.jsonl", edges)
        bucket_stats.append(
            {
                "bucket": label,
                "start": start,
                "end": end,
                "cumulative_through": f"{pub_lo}-{end}" if cumulative else f"{start}-{end}",
                "node_count": len(nodes),
                "edge_count": len(edges),
                "edge_weight": sum(e["weight"] for e in edges),
            }
        )

    full_edge_map, _ = aggregate_edges_for_bucket(
        events,
        range_start=year_start,
        range_end=year_end,
        cumulative=True,
        global_year_start=year_start,
    )
    full_nodes = [
        {
            "cluster_id": cluster_id,
            "layer": cluster.get("layer"),
            "rfc_numbers": cluster.get("rfc_numbers", []),
            "rfc_count": cluster.get("rfc_count", len(cluster.get("rfc_numbers", []))),
            "member_count": cluster.get("member_count"),
        }
        for cluster_id, cluster in sorted(cluster_by_id.items(), key=lambda x: x[0].casefold())
    ]
    full_edges = []
    for (source, target), agg in sorted(full_edge_map.items(), key=lambda x: (-x[1].weight, x[0][0], x[0][1])):
        row = agg.to_dict()
        full_edges.append(
            {
                "source": source,
                "target": target,
                "cumulative": True,
                "year_buckets": edge_cumulative_buckets(
                    row["citation_years"], buckets, year_start, cumulative=cumulative
                ),
                **row,
            }
        )

    full_dir = out_root / "full"
    write_jsonl(full_dir / "nodes.jsonl", full_nodes)
    write_jsonl(full_dir / "edges.jsonl", full_edges)

    write_json(
        out_root / "meta.json",
        {
            "year_span": year_span,
            "year_start": year_start,
            "year_end": year_end,
            "cumulative": cumulative,
            "buckets": [label for _, _, label in buckets],
            "reference_types": sorted(reference_types),
            "include_self_loops": include_self_loops,
            "cluster_count": len(cluster_by_id),
            "protocol_rfc_count": len(rfc_to_cluster),
            "bucket_stats": bucket_stats,
            "full_edge_count": len(full_edges),
            "full_edge_weight": sum(e["weight"] for e in full_edges),
        },
    )
    return out_root


def main() -> None:
    parser = argparse.ArgumentParser(description="生成协议簇分年段引用关系图")
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"配置文件（默认 {CONFIG_PATH.name}）",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    out_root = export_protocol_graphs(config)
    meta = json.loads((out_root / "meta.json").read_text(encoding="utf-8"))
    mode = "累积" if meta["cumulative"] else "分段"
    print(f"模式: {mode}，年段跨度 {meta['year_span']}，区间 {meta['year_start']}-{meta['year_end']}")
    print(f"协议簇: {meta['cluster_count']}，协议 RFC: {meta['protocol_rfc_count']}")
    print(f"全量边: {meta['full_edge_count']} 条，权重合计 {meta['full_edge_weight']}")
    for stat in meta["bucket_stats"]:
        print(
            f"  {stat['bucket']}（{stat['cumulative_through']}）: "
            f"节点 {stat['node_count']}，边 {stat['edge_count']}（权重 {stat['edge_weight']}）"
        )
    print(f"输出目录 → {out_root}")


if __name__ == "__main__":
    main()
