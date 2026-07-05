"""LLM 分类结果中的协议去重。

去重键（protocol_key，用于全量统计）：
  1. protocol_acronym（大小写不敏感）
  2. protocol_name（大小写不敏感）
  3. rfc:{rfc_number}

导出策略：
  - build_unique_protocols：仅有 acronym 的记录，按 acronym 去重
  - build_unique_protocols_by_name：无 acronym 的记录，按 protocol_name 去重
  - count_protocols_by_layer：defines_protocol 记录，按 protocol_key 去重
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

LAYER_ORDER = ("link", "network", "transport", "session", "presentation", "application", "other")


def load_classification_rows(path: Path) -> list[dict]:
    if not path.is_file():
        raise FileNotFoundError(f"缺少分类结果: {path}")
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def protocol_key(row: dict) -> str:
    acronym = (row.get("protocol_acronym") or "").strip()
    if acronym:
        return acronym.upper()
    name = (row.get("protocol_name") or "").strip()
    if name:
        return name.casefold()
    return f"rfc:{row['rfc_number']}"


def normalize_layer(layer: str | None) -> str:
    layer = (layer or "other").lower()
    return layer if layer in LAYER_ORDER else "other"


def pick_layer(members: list[dict]) -> str:
    layer_counts = Counter(normalize_layer(r.get("layer")) for r in members)
    top_count = layer_counts.most_common(1)[0][1]
    candidates = {layer for layer, count in layer_counts.items() if count == top_count}
    for layer in LAYER_ORDER:
        if layer in candidates:
            return layer
    return "other"


def merge_protocol_group(members: list[dict]) -> dict:
    members.sort(key=lambda r: (-float(r.get("confidence", 0)), int(r["rfc_number"])))
    best = members[0]
    rfc_numbers = sorted({int(r["rfc_number"]) for r in members})
    return {
        "protocol_acronym": best.get("protocol_acronym"),
        "protocol_name": best.get("protocol_name"),
        "layer": pick_layer(members),
        "rfc_count": len(rfc_numbers),
        "rfc_numbers": rfc_numbers,
    }


def build_unique_protocols(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if not row.get("defines_protocol"):
            continue
        acronym = (row.get("protocol_acronym") or "").strip()
        if not acronym:
            continue
        groups.setdefault(acronym.upper(), []).append(row)

    unique = [merge_protocol_group(members) for members in groups.values()]
    unique.sort(key=lambda r: (r.get("protocol_acronym") or "").upper())
    return unique


def build_unique_protocols_by_name(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if not row.get("defines_protocol"):
            continue
        if (row.get("protocol_acronym") or "").strip():
            continue
        name = (row.get("protocol_name") or "").strip()
        if not name:
            continue
        groups.setdefault(name.casefold(), []).append(row)

    unique = [merge_protocol_group(members) for members in groups.values()]
    unique.sort(key=lambda r: (r.get("protocol_name") or "").casefold())
    return unique


def count_protocols_by_layer(rows: list[dict]) -> tuple[dict[str, int], int]:
    groups: dict[str, list[dict]] = {}
    rfc_protocol_count = 0
    for row in rows:
        if not row.get("defines_protocol"):
            continue
        rfc_protocol_count += 1
        groups.setdefault(protocol_key(row), []).append(row)

    counts = {layer: 0 for layer in LAYER_ORDER}
    for members in groups.values():
        counts[pick_layer(members)] += 1
    return counts, rfc_protocol_count
