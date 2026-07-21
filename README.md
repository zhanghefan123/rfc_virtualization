# RFC Virtualization

从 IETF RFC 爬取、LLM 协议分类、去重归簇，到引用关系图与可视化的完整流水线。

## 环境准备

建议使用独立 conda / venv。例如：

```powershell
conda activate plot
# 或：conda create -n plot python=3.11 -y && conda activate plot
```

在项目根目录安装依赖：

```powershell
cd C:\zhf_projects\rfc_virtualization
pip install -r requirements.txt
pip install numpy
```

> 图指标（秩、谱间隙、熵）依赖 `numpy`，目前未写入 `requirements.txt`，需单独安装。

可选：导出 PDF 时再装 Playwright：

```powershell
pip install playwright
playwright install chromium
```

所有路径均相对于**项目根目录**；配置在 `config/*.yaml`。

LLM 步骤需要 DeepSeek API Key（环境变量或项目根 `.env`）：

```powershell
# PowerShell 临时设置
$env:DEEPSEEK_API_KEY = "你的密钥"

# 或在项目根创建 .env：
# DEEPSEEK_API_KEY=你的密钥
```

---

## 流水线概览

按顺序执行（后一步依赖前一步产物）：

| 步骤 | 脚本 | 配置 | 主要产物 |
|------|------|------|----------|
| 1. 爬取 RFC | `src/crawl_rfcs/crawl_rfcs.py` | `config/crawler.yaml` | `data/` |
| 2. LLM 协议分类 | `src/llm/llm_protocol_audit.py` | `config/llm_audit.yaml` | `outputs/llm_protocol_classification.jsonl` |
| 3. 协议去重 | `src/protocol_dedup/export.py` | `config/protocol_dedup.yaml` | `outputs/unique_protocols*.jsonl` |
| 4. 协议归簇 | `src/protocol_cluster/export.py` | `config/protocol_cluster.yaml` | `outputs/protocol_clusters.jsonl` |
| 5. 引用关系图 | `src/protocol_graph/export.py` | `config/protocol_graph.yaml` | `outputs/graphs/` |
| 6. 可视化 HTML | `src/visualization/layer_chart.py` | `config/visualization.yaml` | `outputs/layer_protocol_chart.html` |
| 7. （可选）PDF | `src/pdf_export/export.py` | `config/pdf_export.yaml` | `outputs/layer_protocol_chart.pdf` |

---

## 逐步运行

以下命令均在**项目根目录**执行。

### 1. 爬取 RFC

默认按 `config/crawler.yaml` 下载 `json`、`relations`、`txt`（可断点续跑，`skip_existing: true`）。

```powershell
python src/crawl_rfcs/crawl_rfcs.py
```

只下部分格式：

```powershell
python src/crawl_rfcs/crawl_rfcs.py --formats json,relations
python src/crawl_rfcs/crawl_rfcs.py --formats txt
```

产物：

- `data/rfc_manifest.csv` — RFC 清单
- `data/documents/json/`、`relations/`、`txt/`
- `data/errors.log`（若有失败）

> 全量爬取耗时长、体积大；`data/` 默认被 git 忽略。

### 2. LLM 协议分类

依赖：`data/rfc_manifest.csv`、`data/documents/json`（及可选 txt）、`DEEPSEEK_API_KEY`。

```powershell
python src/llm/llm_protocol_audit.py
```

可在 `config/llm_audit.yaml` 调整：

- `input.limit` — 处理条数上限
- `run.dry_run` — `true` 时不写盘，仅试跑
- `run.skip_existing` — 跳过已分类 RFC（可续跑）
- `run.batch_size` / `concurrency` — 吞吐与限流

产物：`outputs/llm_protocol_classification.jsonl`

### 3. 协议去重

需跑**两次**（归簇同时需要 acronym 与 name 两份结果）：

```powershell
python src/protocol_dedup/export.py
python src/protocol_dedup/export.py --by-name
```

产物：

- `outputs/unique_protocols.jsonl`（按 acronym）
- `outputs/unique_protocols_by_name.jsonl`（无 acronym 时按 name）

### 4. 协议归簇

```powershell
python src/protocol_cluster/export.py
```

归簇规则见 `config/protocol_cluster.yaml` 的 `rules`。

产物：`outputs/protocol_clusters.jsonl`

### 5. 协议簇引用关系图

依赖：`protocol_clusters.jsonl`、`data/documents/relations/`、`data/rfc_manifest.csv`。

```powershell
python src/protocol_graph/export.py
```

可在 `config/protocol_graph.yaml` 调整年段：

- `graph.year_span` — 年段跨度（当前为 10）
- `graph.year_start` / `year_end`
- `graph.cumulative` — `true` 为累积年段，`false` 为分段快照

产物：`outputs/graphs/`（各年段 `nodes.jsonl` / `edges.jsonl`，以及 `meta.json`、`full/`）

### 6. 生成可视化

依赖：`protocol_clusters.jsonl` 与 `outputs/graphs/`。

```powershell
python src/visualization/layer_chart.py
```

产物：`outputs/layer_protocol_chart.html`  
用浏览器打开即可查看饼图、柱状图、各层时间序列及图指标曲线。

### 7.（可选）导出 PDF

先完成步骤 6，并已安装 Playwright + Chromium：

```powershell
python src/pdf_export/export.py
```

指定输入输出：

```powershell
python src/pdf_export/export.py --html outputs/layer_protocol_chart.html --pdf outputs/layer_protocol_chart.pdf
```

---

## 一键顺序（已有 API Key 时）

```powershell
cd C:\zhf_projects\rfc_virtualization
conda activate plot

python src/crawl_rfcs/crawl_rfcs.py
python src/llm/llm_protocol_audit.py
python src/protocol_dedup/export.py
python src/protocol_dedup/export.py --by-name
python src/protocol_cluster/export.py
python src/protocol_graph/export.py
python src/visualization/layer_chart.py
# 可选：
# python src/pdf_export/export.py
```

若本地已有 `data/` 与 `outputs/` 中间产物，可从断点对应步骤继续，不必从头爬取。

---

## 配置速查

| 文件 | 作用 |
|------|------|
| `config/crawler.yaml` | 下载格式、并发、输出目录 |
| `config/llm_audit.yaml` | 模型、批次、分类输出路径 |
| `config/protocol_dedup.yaml` | 去重输入输出 |
| `config/protocol_cluster.yaml` | 归簇规则与输出 |
| `config/protocol_graph.yaml` | 年段、引用类型、图输出目录 |
| `config/visualization.yaml` | 图表输入输出、柱状图 Top-N |
| `config/pdf_export.yaml` | HTML→PDF 视口与边距 |

多数脚本支持 `--config` 指定其它配置文件。

---

## 常见问题

**LLM 报缺少 API Key**  
设置 `DEEPSEEK_API_KEY`，或在项目根 `.env` 写入该变量。

**可视化报缺少 `outputs/graphs/meta.json`**  
先跑步骤 5（`protocol_graph/export.py`）。

**可视化报 `ModuleNotFoundError: numpy`**  
执行 `pip install numpy`。

**PDF 报缺少 playwright**  
`pip install playwright && playwright install chromium`。

**爬虫太慢 / 被限流**  
调低 `config/crawler.yaml` 中的 `download.concurrency`，或增大 `request_delay_seconds`。
