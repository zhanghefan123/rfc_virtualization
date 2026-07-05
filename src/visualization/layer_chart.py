"""从 LLM 分类 jsonl 生成各协议层数量 ECharts 饼图（HTML）。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "visualization.yaml"

LAYER_ORDER = ("link", "network", "transport", "session", "presentation", "application", "other")
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
    """去重键：优先 protocol_acronym，其次 protocol_name，否则按 RFC 编号。"""
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
    """同一协议多条记录时，取出现最多的 layer。"""
    layer_counts = Counter(normalize_layer(r.get("layer")) for r in members)
    top_count = layer_counts.most_common(1)[0][1]
    candidates = {layer for layer, count in layer_counts.items() if count == top_count}
    for layer in LAYER_ORDER:
        if layer in candidates:
            return layer
    return "other"


def count_protocols_by_layer(rows: list[dict]) -> tuple[dict[str, int], int]:
    """按 protocol_acronym 去重后统计各层唯一协议数；返回 (各层计数, RFC 定义协议篇数)。"""
    groups: dict[str, list[dict]] = {}
    rfc_protocol_count = 0
    for row in rows:
        if not row.get("defines_protocol"):
            continue
        rfc_protocol_count += 1
        key = protocol_key(row)
        groups.setdefault(key, []).append(row)

    counts = {layer: 0 for layer in LAYER_ORDER}
    for members in groups.values():
        counts[pick_layer(members)] += 1
    return counts, rfc_protocol_count


def render_html(
    *,
    counts: dict[str, int],
    classified_total: int,
    protocol_total: int,
    rfc_protocol_count: int,
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
    chart_data = json.dumps(pie_data, ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>RFC 协议层分布</title>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, sans-serif; background: #f5f7fa; }}
    #header {{ padding: 16px 24px; background: #fff; border-bottom: 1px solid #e8e8e8; }}
    #header h1 {{ margin: 0 0 8px; font-size: 20px; }}
    #header p {{ margin: 0; color: #666; font-size: 14px; }}
    #chart {{ width: 100%; height: 600px; }}
  </style>
</head>
<body>
  <div id="header">
    <h1>RFC 协议层分布</h1>
    <p>已分类 {classified_total} 篇 RFC，其中 {rfc_protocol_count} 篇定义协议；
    按 protocol_acronym 去重后唯一协议 {protocol_total} 个</p>
  </div>
  <div id="chart"></div>
  <script>
    const data = {chart_data};
    const chart = echarts.init(document.getElementById('chart'));
    chart.setOption({{
      tooltip: {{
        trigger: 'item',
        formatter: '{{b}}<br/>数量: {{c}} ({{d}}%)'
      }},
      legend: {{
        orient: 'vertical',
        left: 'left',
        top: 'middle'
      }},
      series: [{{
        type: 'pie',
        radius: ['38%', '68%'],
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
          label: {{ show: true, fontSize: 16, fontWeight: 'bold' }},
          itemStyle: {{ shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.2)' }}
        }},
        data
      }}]
    }});
    window.addEventListener('resize', () => chart.resize());
  </script>
</body>
</html>
"""


def generate_layer_chart(config: dict | None = None) -> Path:
    config = config or load_config()
    in_path = PROJECT_ROOT / config["input"]["classification_jsonl"]
    out_path = PROJECT_ROOT / config["output"]["layer_chart_html"]

    rows = load_classification_rows(in_path)
    counts, rfc_protocol_count = count_protocols_by_layer(rows)
    protocol_total = sum(counts.values())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_html(
            counts=counts,
            classified_total=len(rows),
            protocol_total=protocol_total,
            rfc_protocol_count=rfc_protocol_count,
        ),
        encoding="utf-8",
    )
    return out_path


def main() -> None:
    config = load_config()
    out_path = generate_layer_chart(config)
    in_path = PROJECT_ROOT / config["input"]["classification_jsonl"]
    rows = load_classification_rows(in_path)
    counts, rfc_protocol_count = count_protocols_by_layer(rows)
    protocol_total = sum(counts.values())
    print(f"已分类 RFC: {len(rows)}")
    print(f"定义协议 RFC: {rfc_protocol_count}")
    print(f"去重后唯一协议: {protocol_total}")
    for layer in LAYER_ORDER:
        print(f"  {LAYER_LABELS[layer]}: {counts[layer]}")
    print(f"图表已生成 → {out_path}")


if __name__ == "__main__":
    main()
