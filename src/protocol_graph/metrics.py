"""协议簇引用图指标：邻接矩阵秩等。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from protocol_graph.graph import load_jsonl


def build_adjacency_matrix(
    node_ids: list[str],
    edges: list[dict],
    *,
    weighted: bool = False,
) -> np.ndarray:
    index = {node_id: i for i, node_id in enumerate(node_ids)}
    n = len(node_ids)
    matrix = np.zeros((n, n), dtype=np.float64)
    for edge in edges:
        src = edge.get("source")
        tgt = edge.get("target")
        if src not in index or tgt not in index:
            continue
        value = float(edge.get("weight", 1)) if weighted else 1.0
        matrix[index[src], index[tgt]] += value
    return matrix


def symmetrized_adjacency(adjacency: np.ndarray) -> np.ndarray:
    """有向邻接矩阵对称化（任一方向有边即视为无向边）。"""
    binary = (adjacency > 0).astype(np.float64)
    return np.maximum(binary, binary.T)


def laplacian_matrix(adjacency: np.ndarray) -> np.ndarray:
    undirected = symmetrized_adjacency(adjacency)
    degrees = np.diag(undirected.sum(axis=1))
    return degrees - undirected


def adjacency_spectral_gap(adjacency: np.ndarray) -> dict:
    """邻接矩阵谱间隙 Δ = λ₁ − λ₂（特征值按实部降序）。"""
    n = adjacency.shape[0]
    if n < 2:
        return {
            "adjacency_spectral_gap": 0.0,
            "adjacency_lambda1": 0.0,
            "adjacency_lambda2": 0.0,
        }
    eigvals = np.linalg.eigvals(adjacency)
    ordered = sorted(eigvals, key=lambda z: (z.real, z.imag), reverse=True)
    l1, l2 = ordered[0], ordered[1]
    return {
        "adjacency_spectral_gap": round(float((l1 - l2).real), 6),
        "adjacency_lambda1": round(float(l1.real), 6),
        "adjacency_lambda2": round(float(l2.real), 6),
    }


def laplacian_spectral_gap(adjacency: np.ndarray, *, connected_components: int) -> dict:
    """拉普拉斯谱间隙 Δ = μ_{k+1} − μ_k（μ 升序，k 为连通分量数；全连通时 k=1，Δ=μ₂−μ₁）。"""
    n = adjacency.shape[0]
    if n < 2:
        return {
            "laplacian_spectral_gap": 0.0,
            "laplacian_mu1": 0.0,
            "laplacian_mu2": 0.0,
        }
    eigs = np.sort(np.linalg.eigvalsh(laplacian_matrix(adjacency)))
    k = min(max(connected_components, 1), n - 1)
    mu_k = float(eigs[k - 1])
    mu_k1 = float(eigs[k])
    return {
        "laplacian_spectral_gap": round(max(mu_k1 - mu_k, 0.0), 6),
        "laplacian_mu1": round(mu_k, 6),
        "laplacian_mu2": round(mu_k1, 6),
    }


def laplacian_von_neumann_entropy(adjacency: np.ndarray) -> dict:
    """von Neumann 图熵：ρ = L/Tr(L)，S = −Σ λᵢ ln λᵢ（λᵢ 为 ρ 的特征值）。"""
    n = adjacency.shape[0]
    if n == 0:
        return {"laplacian_vn_entropy": 0.0, "laplacian_vn_entropy_ratio": 0.0}
    laplacian = laplacian_matrix(adjacency)
    trace = float(np.trace(laplacian))
    if trace <= 0:
        return {"laplacian_vn_entropy": 0.0, "laplacian_vn_entropy_ratio": 0.0}
    probs = np.linalg.eigvalsh(laplacian) / trace
    probs = probs[probs > 1e-15]
    entropy = float(-np.sum(probs * np.log(probs)))
    return {
        "laplacian_vn_entropy": round(entropy, 6),
        "laplacian_vn_entropy_ratio": round(entropy / n, 6),
    }


def graph_bucket_metrics(
    nodes: list[dict],
    edges: list[dict],
    *,
    weighted: bool = False,
) -> dict:
    node_ids = sorted({n["cluster_id"] for n in nodes}, key=str.casefold)
    n = len(node_ids)
    if n == 0:
        return {
            "node_count": 0,
            "edge_count": len(edges),
            "adjacency_rank": 0,
            "adjacency_rank_ratio": 0.0,
            "laplacian_rank": 0,
            "connected_components": 0,
            "laplacian_expected_rank": 0,
            "adjacency_spectral_gap": 0.0,
            "adjacency_lambda1": 0.0,
            "adjacency_lambda2": 0.0,
            "laplacian_spectral_gap": 0.0,
            "laplacian_mu1": 0.0,
            "laplacian_mu2": 0.0,
            "laplacian_vn_entropy": 0.0,
            "laplacian_vn_entropy_ratio": 0.0,
        }

    adjacency = build_adjacency_matrix(node_ids, edges, weighted=weighted)
    adj_rank = int(np.linalg.matrix_rank(adjacency))
    lap_rank = int(np.linalg.matrix_rank(laplacian_matrix(adjacency)))
    components = n - lap_rank
    gap_stats = adjacency_spectral_gap(adjacency)
    lap_gap_stats = laplacian_spectral_gap(adjacency, connected_components=components)
    vn_stats = laplacian_von_neumann_entropy(adjacency)
    return {
        "node_count": n,
        "edge_count": len(edges),
        "adjacency_rank": adj_rank,
        "adjacency_rank_ratio": round(adj_rank / n, 6),
        "laplacian_rank": lap_rank,
        "connected_components": components,
        "laplacian_expected_rank": max(n - 1, 0),
        **gap_stats,
        **lap_gap_stats,
        **vn_stats,
    }


def adjacency_matrix_rank(
    nodes: list[dict],
    edges: list[dict],
    *,
    weighted: bool = False,
) -> dict:
    metrics = graph_bucket_metrics(nodes, edges, weighted=weighted)
    return {
        "node_count": metrics["node_count"],
        "edge_count": metrics["edge_count"],
        "rank": metrics["adjacency_rank"],
        "rank_ratio": metrics["adjacency_rank_ratio"],
    }


def measure_bucket_rank(bucket_dir: Path, *, weighted: bool = False) -> dict:
    nodes = load_jsonl(bucket_dir / "nodes.jsonl")
    edges = load_jsonl(bucket_dir / "edges.jsonl")
    bucket = bucket_dir.name
    if nodes and nodes[0].get("bucket"):
        bucket = nodes[0]["bucket"]
    result = graph_bucket_metrics(nodes, edges, weighted=weighted)
    # 兼容旧字段名
    result["rank"] = result["adjacency_rank"]
    result["rank_ratio"] = result["adjacency_rank_ratio"]
    result["bucket"] = bucket
    return result


def measure_all_buckets(graphs_root: Path, buckets: list[str], *, weighted: bool = False) -> list[dict]:
    rows: list[dict] = []
    for label in buckets:
        stats = measure_bucket_rank(graphs_root / label, weighted=weighted)
        stats["bucket"] = label
        rows.append(stats)
    return rows
