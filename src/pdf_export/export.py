"""用 Playwright 将 ECharts HTML 导出为 PDF，保留桌面版式。

浏览器自带打印易在 flex 布局、图表未渲染完成时版式错乱；此处固定视口宽度、
等待 ECharts 绘制后再按页面实际高度生成单页长 PDF。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "pdf_export.yaml"

# 宽视口下保持并排；打印时避免图表块被分页截断
PRINT_CSS = """
@media print {
  #charts-top, .metrics-row {
    display: flex !important;
    flex-direction: row !important;
    flex-wrap: nowrap !important;
  }
  .panel, .panel-half, .panel-full {
    break-inside: avoid;
    page-break-inside: avoid;
  }
}
"""


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def export_html_to_pdf(
    html_path: Path,
    pdf_path: Path,
    *,
    viewport_width: int = 1600,
    wait_ms: int = 3000,
    print_background: bool = True,
    margin_mm: float = 10,
) -> Path:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise SystemExit(
            "缺少 playwright：pip install playwright && playwright install chromium"
        ) from e

    html_path = html_path.resolve()
    if not html_path.is_file():
        raise FileNotFoundError(f"缺少 HTML: {html_path}")

    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    margin = f"{margin_mm}mm"

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": viewport_width, "height": 900})
        page.goto(html_path.as_uri(), wait_until="networkidle", timeout=120_000)
        page.add_style_tag(content=PRINT_CSS)
        page.evaluate("() => { if (typeof resizeCharts === 'function') resizeCharts(); }")
        page.wait_for_timeout(wait_ms)
        height = page.evaluate("() => Math.ceil(document.documentElement.scrollHeight)")
        page.pdf(
            path=str(pdf_path),
            width=f"{viewport_width}px",
            height=f"{height}px",
            print_background=print_background,
            margin={"top": margin, "bottom": margin, "left": margin, "right": margin},
        )
        browser.close()

    return pdf_path


def main() -> None:
    parser = argparse.ArgumentParser(description="将可视化 HTML 导出为 PDF")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH, help="配置文件路径")
    parser.add_argument("--html", type=Path, help="覆盖 config 中的 input.html")
    parser.add_argument("--pdf", type=Path, help="覆盖 config 中的 output.pdf")
    args = parser.parse_args()

    config = load_config(args.config)
    html_path = PROJECT_ROOT / (args.html or config["input"]["html"])
    pdf_path = PROJECT_ROOT / (args.pdf or config["output"]["pdf"])
    render = config.get("render", {})

    out = export_html_to_pdf(
        html_path,
        pdf_path,
        viewport_width=int(render.get("viewport_width", 1600)),
        wait_ms=int(render.get("wait_ms", 3000)),
        print_background=bool(render.get("print_background", True)),
        margin_mm=float(render.get("margin_mm", 10)),
    )
    print(f"PDF 已生成 → {out}")


if __name__ == "__main__":
    main()
