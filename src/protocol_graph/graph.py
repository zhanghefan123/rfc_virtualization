"""基于 relations 与 protocol_clusters 构建分年段协议簇引用图。

边方向：引用方簇 → 被引用方簇（source RFC 的 references 指向 target RFC）。
年段：按引用方 RFC 发布年（manifest.year）划分；cumulative=true 时每个年段含 year_start 至该段末年的全部引用。
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少输入: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_rfc_years(manifest: Path) -> dict[int, int]:
    years: dict[int, int] = {}
    with manifest.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            year = row.get("year", "").strip()
            if year.isdigit():
                years[int(row["rfc_number"])] = int(year)
    return years


def parse_rfc_document(document: str) -> int | None:
    doc = (document or "").strip().lower()
    if doc.startswith("rfc") and doc[3:].isdigit():
        return int(doc[3:])
    return None


def build_year_buckets(
    year_start: int,
    year_end: int,
    year_span: int,
    *,
    cumulative: bool = False,
) -> list[tuple[int, int, str]]:
    """返回 (段起始年, 段末年, 输出标签)。累积模式下标签为 {year_start}-{段末年}。"""
    if year_span <= 0:
        raise ValueError("year_span 必须 > 0")
    buckets: list[tuple[int, int, str]] = []
    segment_start = year_start
    while segment_start <= year_end:
        end = min(segment_start + year_span - 1, year_end)
        label = f"{year_start}-{end}" if cumulative else f"{segment_start}-{end}"
        buckets.append((segment_start, end, label))
        segment_start += year_span
    return buckets


@dataclass
class EdgeAgg:
    weight: int = 0
    source_rfc_numbers: set[int] = field(default_factory=set)
    citation_years: set[int] = field(default_factory=set)
    reference_types: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def add(self, source_rfc: int, cite_year: int, ref_type: str) -> None:
        self.weight += 1
        self.source_rfc_numbers.add(source_rfc)
        self.citation_years.add(cite_year)
        self.reference_types[ref_type] += 1

    def to_dict(self) -> dict:
        return {
            "weight": self.weight,
            "source_rfc_numbers": sorted(self.source_rfc_numbers),
            "citation_years": sorted(self.citation_years),
            "reference_types": dict(sorted(self.reference_types.items())),
        }


@dataclass(frozen=True)
class EdgeEvent:
    source: str
    target: str
    source_rfc: int
    cite_year: int
    ref_type: str


def build_cluster_index(clusters: list[dict]) -> tuple[dict[int, str], dict[str, dict]]:
    rfc_to_cluster: dict[int, str] = {}
    cluster_by_id: dict[str, dict] = {}
    for cluster in clusters:
        cluster_id = cluster["cluster_id"]
        cluster_by_id[cluster_id] = cluster
        for rfc in cluster.get("rfc_numbers", []):
            rfc_to_cluster[int(rfc)] = cluster_id
    return rfc_to_cluster, cluster_by_id


def extract_edge_events(
    *,
    rfc_to_cluster: dict[int, str],
    rfc_years: dict[int, int],
    relations_dir: Path,
    reference_types: set[str],
    include_self_loops: bool,
    year_start: int,
    year_end: int,
) -> list[EdgeEvent]:
    events: list[EdgeEvent] = []
    for source_rfc, source_cluster in rfc_to_cluster.items():
        cite_year = rfc_years.get(source_rfc)
        if cite_year is None or cite_year < year_start or cite_year > year_end:
            continue

        rel_path = relations_dir / f"rfc{source_rfc}.json"
        if not rel_path.is_file():
            continue

        data = json.loads(rel_path.read_text(encoding="utf-8"))
        for ref in data.get("references") or []:
            ref_type = ref.get("reference_type") or ""
            if reference_types and ref_type not in reference_types:
                continue
            target_rfc = parse_rfc_document(ref.get("document", ""))
            if target_rfc is None:
                continue
            target_cluster = rfc_to_cluster.get(target_rfc)
            if target_cluster is None:
                continue
            if not include_self_loops and source_cluster == target_cluster:
                continue
            events.append(
                EdgeEvent(source_cluster, target_cluster, source_rfc, cite_year, ref_type)
            )
    return events


def aggregate_edges_for_bucket(
    events: list[EdgeEvent],
    *,
    range_start: int,
    range_end: int,
    cumulative: bool,
    global_year_start: int,
) -> tuple[dict[tuple[str, str], EdgeAgg], set[str]]:
    """cumulative: [global_year_start, range_end]；snapshot: [range_start, range_end]。"""
    lo = global_year_start if cumulative else range_start
    hi = range_end
    edge_map: dict[tuple[str, str], EdgeAgg] = {}
    active_clusters: set[str] = set()
    for ev in events:
        if ev.cite_year < lo or ev.cite_year > hi:
            continue
        key = (ev.source, ev.target)
        if key not in edge_map:
            edge_map[key] = EdgeAgg()
        edge_map[key].add(ev.source_rfc, ev.cite_year, ev.ref_type)
        active_clusters.add(ev.source)
        active_clusters.add(ev.target)
    return edge_map, active_clusters


def clusters_published_in_range(
    rfc_to_cluster: dict[int, str],
    rfc_years: dict[int, int],
    range_start: int,
    range_end: int,
) -> dict[str, list[int]]:
    published: dict[str, list[int]] = defaultdict(list)
    for rfc, cluster_id in rfc_to_cluster.items():
        year = rfc_years.get(rfc)
        if year is not None and range_start <= year <= range_end:
            published[cluster_id].append(rfc)
    return {cid: sorted(set(rfcs)) for cid, rfcs in published.items()}


def build_bucket_nodes(
    *,
    bucket_label: str,
    bucket_range: tuple[int, int],
    cumulative_through: tuple[int, int],
    active_cluster_ids: set[str],
    published_rfcs: dict[str, list[int]],
    cluster_by_id: dict[str, dict],
    cumulative: bool,
) -> list[dict]:
    node_ids = active_cluster_ids | set(published_rfcs)
    nodes: list[dict] = []
    start, end = bucket_range
    cum_start, cum_end = cumulative_through
    for cluster_id in sorted(node_ids, key=str.casefold):
        cluster = cluster_by_id.get(cluster_id, {})
        in_range = published_rfcs.get(cluster_id, [])
        nodes.append(
            {
                "cluster_id": cluster_id,
                "layer": cluster.get("layer"),
                "rfc_numbers_in_bucket": in_range,
                "rfc_count_in_bucket": len(in_range),
                "bucket": bucket_label,
                "bucket_start": start,
                "bucket_end": end,
                "cumulative": cumulative,
                "cumulative_start": cum_start,
                "cumulative_end": cum_end,
            }
        )
    return nodes


def build_bucket_edges(
    edge_map: dict[tuple[str, str], EdgeAgg],
    bucket_label: str,
    *,
    cumulative: bool,
    cumulative_end: int,
) -> list[dict]:
    edges: list[dict] = []
    for (source, target), agg in sorted(edge_map.items(), key=lambda x: (-x[1].weight, x[0][0], x[0][1])):
        row = {
            "source": source,
            "target": target,
            "bucket": bucket_label,
            "cumulative": cumulative,
            "cumulative_end": cumulative_end,
            **agg.to_dict(),
        }
        edges.append(row)
    return edges


def edge_cumulative_buckets(
    citation_years: list[int],
    buckets: list[tuple[int, int, str]],
    global_year_start: int,
    *,
    cumulative: bool,
) -> list[str]:
    labels: list[str] = []
    for seg_start, end, label in buckets:
        lo = global_year_start if cumulative else seg_start
        if any(lo <= year <= end for year in citation_years):
            labels.append(label)
    return labels
