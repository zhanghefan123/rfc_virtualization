"""从 protocol_clusters.jsonl 与 graphs 生成 ECharts 可视化 HTML。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

SRC_ROOT = Path(__file__).resolve().parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from protocol_dedup.dedup import LAYER_ORDER, normalize_layer
from protocol_graph.metrics import measure_all_buckets

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "visualization.yaml"

LAYER_COLORS = {
    "link": "#91cc75",
    "network": "#5470c6",
    "transport": "#fac858",
    "session": "#ee6666",
    "presentation": "#73c0de",
    "application": "#fc8452",
    "other": "#9a60b4",
}
LAYER_LABELS = {
    "link": "链路层",
    "network": "网络层",
    "transport": "传输层",
    "session": "会话层",
    "presentation": "表示层",
    "application": "应用层",
    "other": "其他",
}


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


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


def load_rank_metrics(config: dict) -> list[dict]:
    graphs_root = PROJECT_ROOT / config["input"]["graphs_directory"]
    meta_path = graphs_root / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"缺少 {meta_path}，请先运行 protocol_graph export")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    weighted = bool(config.get("metrics", {}).get("weighted_adjacency", False))
    return measure_all_buckets(graphs_root, meta["buckets"], weighted=weighted)


def count_clusters_by_layer(clusters: list[dict]) -> tuple[dict[str, int], int, int]:
    counts = {layer: 0 for layer in LAYER_ORDER}
    all_rfcs: set[int] = set()
    for cluster in clusters:
        counts[normalize_layer(cluster.get("layer"))] += 1
        all_rfcs.update(cluster.get("rfc_numbers", []))
    return counts, len(clusters), len(all_rfcs)


def prepare_cluster_bar(clusters: list[dict], top_n: int = 0) -> list[dict]:
    ordered = sorted(clusters, key=lambda c: (-c["rfc_count"], c["cluster_id"].casefold()))
    if top_n > 0:
        ordered = ordered[:top_n]
    return [
        {
            "cluster_id": c["cluster_id"],
            "rfc_count": c["rfc_count"],
            "layer": normalize_layer(c.get("layer")),
            "color": LAYER_COLORS.get(normalize_layer(c.get("layer")), LAYER_COLORS["other"]),
        }
        for c in ordered
    ]


def render_html(
    *,
    counts: dict[str, int],
    cluster_total: int,
    rfc_total: int,
    cluster_bar: list[dict],
    rank_rows: list[dict],
) -> str:
    pie_data = [
        {
            "name": LAYER_LABELS[layer],
            "value": counts[layer],
            "itemStyle": {"color": LAYER_COLORS[layer]},
        }
        for layer in LAYER_ORDER
        if counts[layer] > 0
    ]
    pie_json = json.dumps(pie_data, ensure_ascii=False)
    bar_categories = json.dumps([item["cluster_id"] for item in cluster_bar], ensure_ascii=False)
    bar_values = json.dumps([item["rfc_count"] for item in cluster_bar], ensure_ascii=False)
    bar_colors = json.dumps([item["color"] for item in cluster_bar], ensure_ascii=False)
    bar_count = len(cluster_bar)
    initial_window = min(30, bar_count)

    rank_labels = json.dumps([r["bucket"] for r in rank_rows], ensure_ascii=False)
    rank_values = json.dumps([r["rank"] for r in rank_rows])
    rank_ratios = json.dumps([round(r["rank_ratio"] * 100, 2) for r in rank_rows])
    node_counts = json.dumps([r["node_count"] for r in rank_rows])
    lap_rank_values = json.dumps([r["laplacian_rank"] for r in rank_rows])
    lap_expected = json.dumps([r["laplacian_expected_rank"] for r in rank_rows])
    lap_components = json.dumps([r["connected_components"] for r in rank_rows])
    spec_gap_values = json.dumps([r["adjacency_spectral_gap"] for r in rank_rows])
    spec_lambda1 = json.dumps([r["adjacency_lambda1"] for r in rank_rows])
    spec_lambda2 = json.dumps([r["adjacency_lambda2"] for r in rank_rows])
    lap_spec_gap = json.dumps([r["laplacian_spectral_gap"] for r in rank_rows])
    lap_mu1 = json.dumps([r["laplacian_mu1"] for r in rank_rows])
    lap_mu2 = json.dumps([r["laplacian_mu2"] for r in rank_rows])
    vn_entropy = json.dumps([r["laplacian_vn_entropy"] for r in rank_rows])
    vn_entropy_ratio = json.dumps([round(r["laplacian_vn_entropy_ratio"] * 100, 4) for r in rank_rows])

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>RFC 协议分布</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #f5f7fa; }}
    #header {{ padding: 16px 24px; background: #fff; border-bottom: 1px solid #e8e8e8; }}
    #header h1 {{ margin: 0 0 8px; font-size: 20px; }}
    #header p {{ margin: 0; color: #666; font-size: 14px; }}
    #charts-top {{ display: flex; gap: 12px; padding: 12px 12px 0; box-sizing: border-box; }}
    #charts-bottom {{ padding: 12px; box-sizing: border-box; }}
    .metrics-row {{
      display: flex;
      gap: 12px;
      margin-top: 12px;
    }}
    .metrics-row:first-child {{ margin-top: 0; }}
    .panel {{
      flex: 1;
      min-width: 0;
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      padding: 12px 8px 4px;
    }}
    .panel-half {{
      flex: 1;
      min-width: 0;
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      padding: 12px 8px 4px;
    }}
    .panel h2, .panel-half h2, .panel-full h2 {{
      margin: 0 0 8px 12px;
      font-size: 16px;
      font-weight: 600;
      color: #333;
    }}
    .panel-full {{
      margin-top: 12px;
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      padding: 12px 8px 4px;
    }}
    .panel-desc {{
      margin: 0 0 8px 12px;
      color: #666;
      font-size: 13px;
    }}
    .chart {{ width: 100%; height: 640px; }}
    .chart-metric {{ width: 100%; height: 360px; }}
    .chart-full {{ width: 100%; height: 420px; }}
    @media (max-width: 1100px) {{
      #charts-top {{ flex-direction: column; }}
      .metrics-row {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <div id="header">
    <h1>RFC 协议分布</h1>
    <p>数据来源 protocol_clusters.jsonl：{cluster_total} 个协议簇，{rfc_total} 篇 RFC（去重）</p>
  </div>
  <div id="charts-top">
    <div class="panel">
      <h2>协议簇层分布（饼图）</h2>
      <div id="chart-pie" class="chart"></div>
    </div>
    <div class="panel">
      <h2>协议簇 RFC 数量（柱状图）</h2>
      <div id="chart-bar" class="chart"></div>
    </div>
  </div>
  <div id="charts-bottom">
    <div class="metrics-row">
      <div class="panel-half">
        <h2>邻接矩阵秩</h2>
        <p class="panel-desc">秩 ≤ N；rank/N 越高 → 协议引用模式越不可替代</p>
        <div id="chart-rank" class="chart-metric"></div>
      </div>
      <div class="panel-half">
        <h2>拉普拉斯矩阵秩</h2>
        <p class="panel-desc">L = D − A（对称化）；秩 = N − 连通分量数，全连通时为 N−1</p>
        <div id="chart-laplacian" class="chart-metric"></div>
      </div>
    </div>
    <div class="metrics-row">
      <div class="panel-half">
        <h2>邻接矩阵谱间隙</h2>
        <p class="panel-desc">Δ = λ₁ − λ₂；反映扩张性，Δ 越大故障传播越快</p>
        <div id="chart-spectral-gap" class="chart-metric"></div>
      </div>
      <div class="panel-half">
        <h2>拉普拉斯矩阵谱间隙</h2>
        <p class="panel-desc">Δ = μ_{{k+1}} − μ_{{k}}（k 为连通分量数）；μ_{{k}}≈0 时 Δ≈μ_{{k+1}}，仅绘 Δ</p>
        <div id="chart-laplacian-gap" class="chart-metric"></div>
      </div>
    </div>
    <div class="panel-full">
      <h2>von Neumann 熵（拉普拉斯矩阵）</h2>
      <p class="panel-desc">ρ = L/Tr(L)，S = −Σ λᵢ ln λᵢ；反映协议栈结构复杂度，S/N 越大越复杂</p>
      <div id="chart-vn-entropy" class="chart-full"></div>
    </div>
  </div>
  <script>
    const pieData = {pie_json};
    const barCategories = {bar_categories};
    const barValues = {bar_values};
    const barColors = {bar_colors};
    const barCount = {bar_count};
    const initialWindow = {initial_window};
    const rankLabels = {rank_labels};
    const rankValues = {rank_values};
    const rankRatios = {rank_ratios};
    const nodeCounts = {node_counts};
    const lapRankValues = {lap_rank_values};
    const lapExpected = {lap_expected};
    const lapComponents = {lap_components};
    const specGapValues = {spec_gap_values};
    const specLambda1 = {spec_lambda1};
    const specLambda2 = {spec_lambda2};
    const lapSpecGap = {lap_spec_gap};
    const lapMu1 = {lap_mu1};
    const lapMu2 = {lap_mu2};
    const vnEntropy = {vn_entropy};
    const vnEntropyRatio = {vn_entropy_ratio};

    const pieChart = echarts.init(document.getElementById('chart-pie'));
    pieChart.setOption({{
      tooltip: {{
        trigger: 'item',
        formatter: '{{b}}<br/>协议簇: {{c}} ({{d}}%)'
      }},
      legend: {{
        orient: 'vertical',
        left: 'left',
        top: 'middle'
      }},
      series: [{{
        type: 'pie',
        radius: ['36%', '66%'],
        center: ['58%', '50%'],
        avoidLabelOverlap: true,
        itemStyle: {{
          borderRadius: 6,
          borderColor: '#fff',
          borderWidth: 2
        }},
        label: {{
          show: true,
          formatter: '{{b}}\\n{{c}} ({{d}}%)'
        }},
        emphasis: {{
          label: {{ show: true, fontSize: 14, fontWeight: 'bold' }},
          itemStyle: {{ shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.2)' }}
        }},
        data: pieData
      }}]
    }});

    const barChart = echarts.init(document.getElementById('chart-bar'));
    barChart.setOption({{
      tooltip: {{
        trigger: 'axis',
        axisPointer: {{ type: 'shadow' }},
        formatter: params => {{
          const p = params[0];
          return `${{p.name}}<br/>RFC 数量: ${{p.value}}`;
        }}
      }},
      grid: {{
        left: 120,
        right: 24,
        top: 16,
        bottom: barCount > initialWindow ? 72 : 24
      }},
      dataZoom: barCount > initialWindow ? [
        {{
          type: 'slider',
          yAxisIndex: 0,
          start: 0,
          end: Math.round(initialWindow / barCount * 100),
          width: 16,
          right: 8
        }},
        {{
          type: 'inside',
          yAxisIndex: 0,
          start: 0,
          end: Math.round(initialWindow / barCount * 100)
        }}
      ] : [],
      xAxis: {{
        type: 'value',
        name: 'RFC 数量',
        nameLocation: 'middle',
        nameGap: 28
      }},
      yAxis: {{
        type: 'category',
        data: barCategories,
        inverse: true,
        axisLabel: {{
          width: 100,
          overflow: 'truncate'
        }}
      }},
      series: [{{
        type: 'bar',
        data: barValues.map((v, i) => ({{
          value: v,
          itemStyle: {{ color: barColors[i] }}
        }})),
        label: {{
          show: true,
          position: 'right',
          formatter: '{{c}}'
        }}
      }}]
    }});

    const rankChart = echarts.init(document.getElementById('chart-rank'));
    rankChart.setOption({{
      tooltip: {{
        trigger: 'axis',
        formatter: params => {{
          const i = params[0].dataIndex;
          return `${{rankLabels[i]}}<br/>`
            + `N（协议簇）: ${{nodeCounts[i]}}<br/>`
            + `秩 rank: ${{rankValues[i]}}<br/>`
            + `rank/N: ${{rankRatios[i].toFixed(2)}}%`;
        }}
      }},
      legend: {{ data: ['秩 rank', 'rank/N (%)'], top: 8 }},
      grid: {{ left: 64, right: 64, top: 48, bottom: 56 }},
      xAxis: {{
        type: 'category',
        data: rankLabels,
        axisLabel: {{ rotate: 20 }}
      }},
      yAxis: [
        {{ type: 'value', name: '秩 rank', minInterval: 1 }},
        {{ type: 'value', name: 'rank/N (%)', min: 0, max: 100, splitLine: {{ show: false }} }}
      ],
      series: [
        {{
          name: '秩 rank',
          type: 'line',
          data: rankValues,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#5470c6' }}
        }},
        {{
          name: 'rank/N (%)',
          type: 'line',
          yAxisIndex: 1,
          data: rankRatios,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#91cc75' }}
        }}
      ]
    }});

    const lapChart = echarts.init(document.getElementById('chart-laplacian'));
    lapChart.setOption({{
      tooltip: {{
        trigger: 'axis',
        formatter: params => {{
          const i = params[0].dataIndex;
          return `${{rankLabels[i]}}<br/>`
            + `N（协议簇）: ${{nodeCounts[i]}}<br/>`
            + `L 秩: ${{lapRankValues[i]}}<br/>`
            + `N−1（全连通）: ${{lapExpected[i]}}<br/>`
            + `连通分量: ${{lapComponents[i]}}`;
        }}
      }},
      legend: {{ data: ['L 秩', 'N−1（全连通）', '连通分量'], top: 8 }},
      grid: {{ left: 64, right: 64, top: 48, bottom: 56 }},
      xAxis: {{
        type: 'category',
        data: rankLabels,
        axisLabel: {{ rotate: 20 }}
      }},
      yAxis: [
        {{ type: 'value', name: '秩', minInterval: 1 }},
        {{ type: 'value', name: '连通分量', minInterval: 1, splitLine: {{ show: false }} }}
      ],
      series: [
        {{
          name: 'L 秩',
          type: 'line',
          data: lapRankValues,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#5470c6' }}
        }},
        {{
          name: 'N−1（全连通）',
          type: 'line',
          data: lapExpected,
          smooth: true,
          symbolSize: 6,
          lineStyle: {{ width: 2, type: 'dashed' }},
          itemStyle: {{ color: '#999' }}
        }},
        {{
          name: '连通分量',
          type: 'line',
          yAxisIndex: 1,
          data: lapComponents,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#ee6666' }}
        }}
      ]
    }});

    const specChart = echarts.init(document.getElementById('chart-spectral-gap'));
    specChart.setOption({{
      tooltip: {{
        trigger: 'axis',
        formatter: params => {{
          const i = params[0].dataIndex;
          return `${{rankLabels[i]}}<br/>`
            + `N（协议簇）: ${{nodeCounts[i]}}<br/>`
            + `λ₁: ${{specLambda1[i].toFixed(4)}}<br/>`
            + `λ₂: ${{specLambda2[i].toFixed(4)}}<br/>`
            + `Δ = λ₁ − λ₂: ${{specGapValues[i].toFixed(4)}}`;
        }}
      }},
      legend: {{ data: ['谱间隙 Δ', 'λ₁', 'λ₂'], top: 8 }},
      grid: {{ left: 64, right: 64, top: 48, bottom: 56 }},
      xAxis: {{
        type: 'category',
        data: rankLabels,
        axisLabel: {{ rotate: 20 }}
      }},
      yAxis: {{ type: 'value', name: '特征值 / Δ' }},
      series: [
        {{
          name: '谱间隙 Δ',
          type: 'line',
          data: specGapValues,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#5470c6' }}
        }},
        {{
          name: 'λ₁',
          type: 'line',
          data: specLambda1,
          smooth: true,
          symbolSize: 6,
          lineStyle: {{ width: 2, type: 'dashed' }},
          itemStyle: {{ color: '#91cc75' }}
        }},
        {{
          name: 'λ₂',
          type: 'line',
          data: specLambda2,
          smooth: true,
          symbolSize: 6,
          lineStyle: {{ width: 2, type: 'dashed' }},
          itemStyle: {{ color: '#ee6666' }}
        }}
      ]
    }});

    const lapSpecChart = echarts.init(document.getElementById('chart-laplacian-gap'));
    lapSpecChart.setOption({{
      tooltip: {{
        trigger: 'axis',
        formatter: params => {{
          const i = params[0].dataIndex;
          return `${{rankLabels[i]}}<br/>`
            + `N（协议簇）: ${{nodeCounts[i]}}<br/>`
            + `连通分量 k: ${{lapComponents[i]}}<br/>`
            + `μ_k ≈ ${{lapMu1[i].toFixed(4)}}<br/>`
            + `μ_{{k+1}}: ${{lapMu2[i].toFixed(4)}}<br/>`
            + `Δ = μ_{{k+1}} − μ_k: ${{lapSpecGap[i].toFixed(4)}}<br/>`
            + `Δ 越小 → 结构越脆弱`;
        }}
      }},
      legend: {{ data: ['谱间隙 Δ', '连通分量 k'], top: 8 }},
      grid: {{ left: 64, right: 64, top: 48, bottom: 56 }},
      xAxis: {{
        type: 'category',
        data: rankLabels,
        axisLabel: {{ rotate: 20 }}
      }},
      yAxis: [
        {{ type: 'value', name: 'Δ', min: 0 }},
        {{ type: 'value', name: '连通分量 k', minInterval: 1, splitLine: {{ show: false }} }}
      ],
      series: [
        {{
          name: '谱间隙 Δ',
          type: 'line',
          data: lapSpecGap,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#5470c6' }}
        }},
        {{
          name: '连通分量 k',
          type: 'line',
          yAxisIndex: 1,
          data: lapComponents,
          smooth: true,
          symbolSize: 6,
          lineStyle: {{ width: 2, type: 'dashed' }},
          itemStyle: {{ color: '#ee6666' }}
        }}
      ]
    }});

    const vnChart = echarts.init(document.getElementById('chart-vn-entropy'));
    vnChart.setOption({{
      tooltip: {{
        trigger: 'axis',
        formatter: params => {{
          const i = params[0].dataIndex;
          return `${{rankLabels[i]}}<br/>`
            + `N（协议簇）: ${{nodeCounts[i]}}<br/>`
            + `S: ${{vnEntropy[i].toFixed(4)}}<br/>`
            + `S/N: ${{(vnEntropyRatio[i]).toFixed(4)}}%`;
        }}
      }},
      legend: {{ data: ['von Neumann 熵 S', 'S/N (%)'], top: 8 }},
      grid: {{ left: 64, right: 64, top: 48, bottom: 56 }},
      xAxis: {{
        type: 'category',
        data: rankLabels,
        axisLabel: {{ rotate: 20 }}
      }},
      yAxis: [
        {{ type: 'value', name: '熵 S', min: 0 }},
        {{ type: 'value', name: 'S/N (%)', min: 0, splitLine: {{ show: false }} }}
      ],
      series: [
        {{
          name: 'von Neumann 熵 S',
          type: 'line',
          data: vnEntropy,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#5470c6' }}
        }},
        {{
          name: 'S/N (%)',
          type: 'line',
          yAxisIndex: 1,
          data: vnEntropyRatio,
          smooth: true,
          symbolSize: 8,
          lineStyle: {{ width: 3 }},
          itemStyle: {{ color: '#91cc75' }}
        }}
      ]
    }});

    function resizeCharts() {{
      pieChart.resize();
      barChart.resize();
      rankChart.resize();
      lapChart.resize();
      specChart.resize();
      lapSpecChart.resize();
      vnChart.resize();
    }}
    window.addEventListener('resize', resizeCharts);
  </script>
</body>
</html>
"""


def generate_layer_chart(config: dict | None = None) -> Path:
    config = config or load_config()
    clusters_path = PROJECT_ROOT / config["input"]["protocol_clusters_jsonl"]
    out_path = PROJECT_ROOT / config["output"]["layer_chart_html"]
    top_n = int(config.get("chart", {}).get("cluster_bar_top_n", 0))

    cluster_rows = load_jsonl(clusters_path)
    counts, cluster_total, rfc_total = count_clusters_by_layer(cluster_rows)
    cluster_bar = prepare_cluster_bar(cluster_rows, top_n=top_n)
    rank_rows = load_rank_metrics(config)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_html(
            counts=counts,
            cluster_total=cluster_total,
            rfc_total=rfc_total,
            cluster_bar=cluster_bar,
            rank_rows=rank_rows,
        ),
        encoding="utf-8",
    )
    return out_path


def main() -> None:
    config = load_config()
    clusters_path = PROJECT_ROOT / config["input"]["protocol_clusters_jsonl"]
    cluster_rows = load_jsonl(clusters_path)
    counts, cluster_total, rfc_total = count_clusters_by_layer(cluster_rows)
    top_n = int(config.get("chart", {}).get("cluster_bar_top_n", 0))
    cluster_bar = prepare_cluster_bar(cluster_rows, top_n=top_n)
    rank_rows = load_rank_metrics(config)
    out_path = generate_layer_chart(config)

    print(f"协议簇: {cluster_total}（{clusters_path.name}）")
    print(f"关联 RFC: {rfc_total}（去重）")
    for layer in LAYER_ORDER:
        print(f"  {LAYER_LABELS[layer]}: {counts[layer]}")
    print(f"柱状图展示 {len(cluster_bar)} 个簇")
    print("邻接矩阵秩：")
    for row in rank_rows:
        print(
            f"  {row['bucket']}: N={row['node_count']}, "
            f"rank={row['rank']}, rank/N={row['rank_ratio']:.4f}"
        )
    print("拉普拉斯矩阵秩：")
    for row in rank_rows:
        print(
            f"  {row['bucket']}: L_rank={row['laplacian_rank']}, "
            f"N-1={row['laplacian_expected_rank']}, "
            f"components={row['connected_components']}"
        )
    print("邻接矩阵谱间隙：")
    for row in rank_rows:
        print(
            f"  {row['bucket']}: lambda1={row['adjacency_lambda1']:.4f}, "
            f"lambda2={row['adjacency_lambda2']:.4f}, "
            f"gap={row['adjacency_spectral_gap']:.4f}"
        )
    print("拉普拉斯矩阵谱间隙：")
    for row in rank_rows:
        print(
            f"  {row['bucket']}: mu_k={row['laplacian_mu1']:.4f}, "
            f"mu_k+1={row['laplacian_mu2']:.4f}, "
            f"gap={row['laplacian_spectral_gap']:.4f}, "
            f"components={row['connected_components']}"
        )
    print("von Neumann 熵：")
    for row in rank_rows:
        print(
            f"  {row['bucket']}: S={row['laplacian_vn_entropy']:.4f}, "
            f"S/N={row['laplacian_vn_entropy_ratio']:.6f}"
        )
    print(f"图表已生成 → {out_path}")


if __name__ == "__main__":
    main()
