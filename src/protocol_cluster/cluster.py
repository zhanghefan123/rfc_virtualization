"""基于 unique_protocols 前缀归并协议簇。

规则见 config/protocol_cluster.yaml 的 rules 段：
  1. 有 protocol_acronym：按空格拆首词；连字符拆分需前缀长度达标；keep_intact / force_cluster 可覆盖
  2. 无 protocol_acronym：protocol_name 前缀匹配 acronym 簇（长度与 exclude 受 rules 约束）
  3. 仍未匹配：按 protocol_name 首词成簇
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from protocol_dedup.dedup import pick_layer

DEFAULT_RULES: dict = {
    "hyphen_split_min_prefix_len": 3,
    "name_match_min_cluster_id_len": 3,
    "keep_intact": [],
    "force_cluster": {},
    "name_match_exclude_cluster_ids": [],
}


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少输入: {path}")
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _normalize_rules(rules: dict | None) -> dict:
    merged = {**DEFAULT_RULES, **(rules or {})}
    merged["keep_intact"] = list(merged.get("keep_intact") or [])
    merged["force_cluster"] = dict(merged.get("force_cluster") or {})
    merged["name_match_exclude_cluster_ids"] = list(
        merged.get("name_match_exclude_cluster_ids") or []
    )
    return merged


def _keep_intact_lookup(rules: dict) -> dict[str, str]:
    return {item.casefold(): item for item in rules["keep_intact"]}


def _force_cluster_lookup(rules: dict) -> dict[str, str]:
    return {k.casefold(): v for k, v in rules["force_cluster"].items()}


def acronym_cluster_key(acronym: str, rules: dict | None = None) -> str:
    """从 acronym 提取簇 id。"""
    rules = _normalize_rules(rules)
    acronym = acronym.strip()
    if not acronym:
        return acronym

    intact = _keep_intact_lookup(rules)
    if acronym.casefold() in intact:
        return intact[acronym.casefold()]

    forced = _force_cluster_lookup(rules)
    if acronym.casefold() in forced:
        return forced[acronym.casefold()]

    if " " in acronym:
        return acronym.split(" ", 1)[0].strip()

    min_prefix = int(rules["hyphen_split_min_prefix_len"])
    if "-" in acronym:
        prefix = acronym.split("-", 1)[0].strip()
        if len(prefix) >= min_prefix:
            return prefix

    return acronym


def name_matches_cluster(protocol_name: str, cluster_id: str) -> bool:
    name = protocol_name.strip()
    if not name or not cluster_id:
        return False
    if len(name) < len(cluster_id):
        return False
    if name[: len(cluster_id)].casefold() != cluster_id.casefold():
        return False
    if len(name) == len(cluster_id):
        return True
    return name[len(cluster_id)] in " -_./("


def name_only_cluster_key(protocol_name: str) -> str:
    return protocol_name.strip().split()[0] if protocol_name.strip() else protocol_name


def _member_record(row: dict, *, source: str) -> dict:
    return {
        "protocol_acronym": row.get("protocol_acronym"),
        "protocol_name": row.get("protocol_name"),
        "layer": row.get("layer"),
        "rfc_count": row.get("rfc_count", len(row.get("rfc_numbers", []))),
        "rfc_numbers": row.get("rfc_numbers", []),
        "source": source,
    }


def _finalize_cluster(cluster_id: str, members: list[dict]) -> dict:
    rfc_numbers = sorted({n for m in members for n in m["rfc_numbers"]})
    layer_rows = [{"layer": m["layer"]} for m in members]
    return {
        "cluster_id": cluster_id,
        "layer": pick_layer(layer_rows),
        "member_count": len(members),
        "rfc_count": len(rfc_numbers),
        "rfc_numbers": rfc_numbers,
        "members": members,
    }


def _name_match_cluster_ids(clusters: dict[str, list[dict]], rules: dict) -> list[str]:
    min_len = int(rules["name_match_min_cluster_id_len"])
    excluded = {item.casefold() for item in rules["name_match_exclude_cluster_ids"]}
    eligible = [
        cluster_id
        for cluster_id in clusters
        if len(cluster_id) >= min_len and cluster_id.casefold() not in excluded
    ]
    return sorted(eligible, key=len, reverse=True)


def build_protocol_clusters(
    acronym_rows: list[dict],
    name_rows: list[dict],
    rules: dict | None = None,
) -> list[dict]:
    rules = _normalize_rules(rules)
    clusters: dict[str, list[dict]] = {}

    for row in acronym_rows:
        acronym = (row.get("protocol_acronym") or "").strip()
        if not acronym:
            continue
        cluster_id = acronym_cluster_key(acronym, rules)
        clusters.setdefault(cluster_id, []).append(_member_record(row, source="acronym"))

    match_cluster_ids = _name_match_cluster_ids(clusters, rules)
    unmatched_name: list[dict] = []

    for row in name_rows:
        name = (row.get("protocol_name") or "").strip()
        if not name:
            continue
        matched = False
        for cluster_id in match_cluster_ids:
            if name_matches_cluster(name, cluster_id):
                clusters[cluster_id].append(_member_record(row, source="name"))
                matched = True
                break
        if not matched:
            unmatched_name.append(row)

    for row in unmatched_name:
        name = (row.get("protocol_name") or "").strip()
        cluster_id = name_only_cluster_key(name)
        clusters.setdefault(cluster_id, []).append(_member_record(row, source="name"))

    result = [_finalize_cluster(cid, members) for cid, members in clusters.items()]
    result.sort(key=lambda c: c["cluster_id"].casefold())
    return result


def cluster_stats(clusters: list[dict]) -> dict:
    member_counts = Counter(c["member_count"] for c in clusters)
    return {
        "cluster_count": len(clusters),
        "multi_member_clusters": sum(1 for c in clusters if c["member_count"] > 1),
        "max_member_count": max((c["member_count"] for c in clusters), default=0),
        "members_per_cluster": dict(sorted(member_counts.items())),
    }
