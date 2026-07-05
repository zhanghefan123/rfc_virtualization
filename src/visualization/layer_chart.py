"""从 protocol_clusters.jsonl 生成 ECharts 可视化 HTML。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

SRC_ROOT = Path(__file__).resolve().parent.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from protocol_dedup.dedup import LAYER_ORDER, normalize_layer

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


def count_clusters_by_layer(clusters: list[dict]) -> tuple[dict[str, int], int, int]:
    """按层统计协议簇数量，并汇总 RFC 总数（去重）。"""
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
    #charts {{ display: flex; gap: 12px; padding: 12px; box-sizing: border-box; }}
    .panel {{
      flex: 1;
      min-width: 0;
      background: #fff;
      border-radius: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,0.08);
      padding: 12px 8px 4px;
    }}
    .panel h2 {{
      margin: 0 0 8px 12px;
      font-size: 16px;
      font-weight: 600;
      color: #333;
    }}
    .chart {{ width: 100%; height: 640px; }}
    @media (max-width: 1100px) {{
      #charts {{ flex-direction: column; }}
    }}
  </style>
</head>
<body>
  <div id="header">
    <h1>RFC 协议分布</h1>
    <p>数据来源 protocol_clusters.jsonl：{cluster_total} 个协议簇，{rfc_total} 篇 RFC（去重）</p>
  </div>
  <div id="charts">
    <div class="panel">
      <h2>协议簇层分布（饼图）</h2>
      <div id="chart-pie" class="chart"></div>
    </div>
    <div class="panel">
      <h2>协议簇 RFC 数量（柱状图）</h2>
      <div id="chart-bar" class="chart"></div>
    </div>
  </div>
  <script>
    const pieData = {pie_json};
    const barCategories = {bar_categories};
    const barValues = {bar_values};
    const barColors = {bar_colors};
    const barCount = {bar_count};
    const initialWindow = {initial_window};

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

    function resizeCharts() {{
      pieChart.resize();
      barChart.resize();
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

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_html(
            counts=counts,
            cluster_total=cluster_total,
            rfc_total=rfc_total,
            cluster_bar=cluster_bar,
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
    out_path = generate_layer_chart(config)

    print(f"协议簇: {cluster_total}（{clusters_path.name}）")
    print(f"关联 RFC: {rfc_total}（去重）")
    for layer in LAYER_ORDER:
        print(f"  {LAYER_LABELS[layer]}: {counts[layer]}")
    print(f"柱状图展示 {len(cluster_bar)} 个簇")
    print(f"图表已生成 → {out_path}")


if __name__ == "__main__":
    main()
