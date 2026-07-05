#!/usr/bin/env python3
"""
从 RFC Editor 官方索引爬取全部 IETF RFC 文档。

整体流程（对应 main() 中的四个步骤）：
  1. 读取 config/crawler.yaml（可用命令行 --formats 覆盖下载格式）
  2. 下载 / 复用 rfc-index.xml，解析出全部 RFC 元数据
  3. 使用 httpx 异步并发下载各 RFC 的 doc.json 和/或 rfc-editor 正文 txt
  4. 写出清单 CSV 与错误日志

下载格式（可多选，命令行优先于 config）：
  --formats json       仅 Datatracker doc.json
  --formats relations  仅文档关系 JSON（Datatracker relateddocument API）
  --formats txt        仅 RFC Editor 正文 .txt
  --formats json,relations,txt

注意：本脚本默认不自动运行；请在安装依赖后手动执行。
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import aiofiles
import httpx
import pandas as pd
import yaml
from lxml import etree
from tqdm.asyncio import tqdm_asyncio

# ---------------------------------------------------------------------------
# 路径与命名空间
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "crawler.yaml"

# rfc-index.xml 使用的 XML 命名空间（解析时必须带上）
RFC_INDEX_NS = "https://www.rfc-editor.org/rfc-index"
NSMAP = {"rfc": RFC_INDEX_NS}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class RfcMeta:
    """从 rfc-index.xml 解析出的一条 RFC 元数据。"""

    rfc_number: int
    doc_id: str
    title: str
    authors: str
    year: str | None
    month: str | None
    available_formats: list[str] = field(default_factory=list)


@dataclass
class DownloadTask:
    """一次具体的文件下载任务（一篇 RFC 的一种格式）。"""

    rfc_number: int
    doc_id: str
    title: str
    file_format: str
    url: str
    output_path: Path
    required: bool


@dataclass
class DownloadResult:
    """单个下载任务的结果，用于更新清单与错误日志。"""

    rfc_number: int
    file_format: str
    output_path: Path
    success: bool
    skipped: bool = False
    error_message: str | None = None


# ---------------------------------------------------------------------------
# 配置与格式选择
# ---------------------------------------------------------------------------

SUPPORTED_FORMATS = ("json", "relations", "txt")

# Datatracker relateddocument API 中属于「引用」的关系类型
REFERENCE_RELATION_SLUGS = frozenset({"refnorm", "refinfo", "refold", "refunk"})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="从 RFC Editor 索引批量下载 RFC 文档（json / txt / 两者）",
    )
    parser.add_argument(
        "--formats",
        metavar="FORMAT",
        help=(
            "下载格式，逗号分隔：json、relations、txt 或其组合。"
            "未指定时使用 config.yaml 中的 download.formats"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=f"配置文件路径（默认: {CONFIG_PATH.name}）",
    )
    return parser.parse_args(argv)


def normalize_format_names(raw_formats: list[str]) -> list[str]:
    """去重、小写、校验格式名，保持 json → relations → txt 顺序。"""
    order = {name: index for index, name in enumerate(SUPPORTED_FORMATS)}
    normalized: list[str] = []
    for fmt in raw_formats:
        name = fmt.strip().lower()
        if not name:
            continue
        if name not in SUPPORTED_FORMATS:
            raise ValueError(f"不支持的格式: {fmt!r}，可选: {', '.join(SUPPORTED_FORMATS)}")
        if name not in normalized:
            normalized.append(name)
    if not normalized:
        raise ValueError("至少需要指定一种下载格式（json / relations / txt）")
    return sorted(normalized, key=lambda item: order[item])


def parse_formats_arg(formats_arg: str | None) -> list[str] | None:
    if formats_arg is None:
        return None
    return normalize_format_names(formats_arg.split(","))


def resolve_selected_formats(config: dict, formats_arg: str | None) -> list[str]:
    """命令行 --formats 优先，否则读取 config.download.formats。"""
    cli_formats = parse_formats_arg(formats_arg)
    if cli_formats is not None:
        return cli_formats

    config_formats = config["download"].get("formats") or []
    return normalize_format_names(config_formats)


def apply_format_selection(config: dict, selected_formats: list[str]) -> dict:
    """返回注入了选定格式列表的配置副本。"""
    updated = copy.deepcopy(config)
    updated["download"]["formats"] = selected_formats
    return updated


def format_source(config: dict, fmt: str) -> dict:
    """读取某格式的 URL 模板等设置，兼容旧版单一 url_template。"""
    dl_cfg = config["download"]
    sources = dl_cfg.get("format_sources") or {}
    if fmt in sources:
        return sources[fmt]

    if fmt == "json" and "url_template" in dl_cfg:
        return {"url_template": dl_cfg["url_template"]}

    raise KeyError(
        f"未配置格式 {fmt!r} 的下载源，请在 config.download.format_sources.{fmt} 中设置 url_template"
    )


def is_optional_format(config: dict, fmt: str) -> bool:
    dl_cfg = config["download"]
    optional = {name.lower() for name in dl_cfg.get("optional_formats", [])}
    source = format_source(config, fmt)
    return fmt in optional or bool(source.get("optional", False))


def load_config(path: Path = CONFIG_PATH) -> dict:
    """从 YAML 配置文件加载全部运行参数。"""
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def output_dir(config: dict) -> Path:
    """返回输出根目录（自动创建）。"""
    path = PROJECT_ROOT / config["output"]["directory"]
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 第 1 步：获取 RFC 官方索引
# ---------------------------------------------------------------------------


async def fetch_index_xml(config: dict, client: httpx.AsyncClient) -> bytes:
    """
    下载 rfc-index.xml，或在 reuse_cached_index=true 时直接读取本地缓存。

    索引文件包含全部 RFC 的编号、标题、作者等信息，是后续批量下载的「目录」。
    """
    index_cfg = config["index"]
    cache_path = output_dir(config) / index_cfg["cache_file"]

    if index_cfg.get("reuse_cached_index", True) and cache_path.is_file():
        return cache_path.read_bytes()

    response = await client.get(index_cfg["url"])
    response.raise_for_status()

    cache_path.write_bytes(response.content)
    return response.content


def parse_rfc_index(xml_bytes: bytes) -> list[RfcMeta]:
    """
    用 lxml 解析 rfc-index.xml，提取全部 <rfc-entry> 节点。

    相比手写字符串解析，lxml 对大型 XML 更快、更稳健。
    """
    root = etree.fromstring(xml_bytes)
    entries: list[RfcMeta] = []

    for entry in root.findall("rfc:rfc-entry", namespaces=NSMAP):
        doc_id = _text(entry, "rfc:doc-id")
        if not doc_id or not doc_id.upper().startswith("RFC"):
            continue

        # doc-id 形如 "RFC3339" → 编号 3339
        rfc_number = int(doc_id[3:])

        authors = [
            author.findtext("rfc:name", namespaces=NSMAP, default="").strip()
            for author in entry.findall("rfc:author", namespaces=NSMAP)
        ]
        available_formats = [
            fmt.text.strip().lower()
            for fmt in entry.findall("rfc:format/rfc:file-format", namespaces=NSMAP)
            if fmt.text
        ]

        entries.append(
            RfcMeta(
                rfc_number=rfc_number,
                doc_id=doc_id,
                title=_text(entry, "rfc:title") or "",
                authors="; ".join(a for a in authors if a),
                year=_text(entry, "rfc:date/rfc:year"),
                month=_text(entry, "rfc:date/rfc:month"),
                available_formats=available_formats,
            )
        )

    entries.sort(key=lambda item: item.rfc_number)
    if not entries:
        raise RuntimeError("索引解析结果为空，请检查 rfc-index.xml 结构是否变化")

    return entries


def _text(parent: etree._Element, xpath: str) -> str | None:
    """安全读取带命名空间的子节点文本。"""
    value = parent.findtext(xpath, namespaces=NSMAP)
    return value.strip() if value else None


# ---------------------------------------------------------------------------
# 第 2 步：构建下载任务列表
# ---------------------------------------------------------------------------


def build_download_tasks(config: dict, rfc_list: list[RfcMeta]) -> list[DownloadTask]:
    """
    根据选定的 formats，为每篇 RFC 生成若干 DownloadTask。

    例如 formats=[json, txt] 且共 9800 篇 RFC → 约 19600 个下载任务。
    """
    selected_formats = normalize_format_names(config["download"]["formats"])
    docs_root = output_dir(config) / config["output"]["documents_subdirectory"]

    tasks: list[DownloadTask] = []
    for meta in rfc_list:
        for fmt in selected_formats:
            source = format_source(config, fmt)
            if fmt == "relations":
                url = ""
            else:
                url = source["url_template"].format(number=meta.rfc_number, format=fmt)
            tasks.append(
                DownloadTask(
                    rfc_number=meta.rfc_number,
                    doc_id=meta.doc_id,
                    title=meta.title,
                    file_format=fmt,
                    url=url,
                    output_path=docs_root / fmt / f"rfc{meta.rfc_number}.json"
                    if fmt in ("json", "relations")
                    else docs_root / fmt / f"rfc{meta.rfc_number}.{fmt}",
                    required=not is_optional_format(config, fmt),
                )
            )

    return tasks


# ---------------------------------------------------------------------------
# 文档关系（Datatracker relateddocument API）
# ---------------------------------------------------------------------------


def relations_api_base(config: dict) -> str:
    return format_source(config, "relations")["api_base"].rstrip("/")


def resolve_api_url(url: str, config: dict) -> str:
    """Datatracker 分页 meta.next 常为相对路径 /api/v1/...，需补全为绝对 URL。"""
    if url.startswith(("http://", "https://")):
        return url
    origin = relations_api_base(config).rsplit("/api/", 1)[0]
    if url.startswith("/"):
        return origin + url
    return urljoin(relations_api_base(config) + "/", url)


def doc_name_from_uri(resource_uri: str) -> str:
    return resource_uri.rstrip("/").split("/")[-1]


def json_has_embedded_references(path: Path) -> bool:
    """旧版爬虫会把 HTML 解析的 references 写入 doc.json，需清理。"""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return "references" in data


def relations_missing_referenced_by(path: Path) -> bool:
    """旧版 relations 缺少 referenced_by 字段，需补爬。"""
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True
    return "referenced_by" not in data


def should_fetch_task(task: DownloadTask, config: dict) -> bool:
    if not task.output_path.is_file():
        return True
    if not config["download"].get("skip_existing", True):
        return True
    if task.file_format == "json" and json_has_embedded_references(task.output_path):
        return True
    if task.file_format == "relations" and relations_missing_referenced_by(task.output_path):
        return True
    return False


def cached_download_result(task: DownloadTask) -> DownloadResult:
    return DownloadResult(
        rfc_number=task.rfc_number,
        file_format=task.file_format,
        output_path=task.output_path,
        success=True,
        skipped=True,
    )


def partition_tasks(
    tasks: list[DownloadTask], config: dict
) -> tuple[list[DownloadTask], list[DownloadResult]]:
    pending: list[DownloadTask] = []
    cached: list[DownloadResult] = []
    for task in tasks:
        if should_fetch_task(task, config):
            pending.append(task)
        else:
            cached.append(cached_download_result(task))
    return pending, cached


async def fetch_api_objects(
    client: httpx.AsyncClient,
    url: str,
    config: dict,
) -> list[dict]:
    """分页拉取 Datatracker API 列表端点。"""
    items: list[dict] = []
    while url:
        await apply_request_delay(config)
        response = await fetch_with_retry(client, url, config)
        response.raise_for_status()
        payload = response.json()
        items.extend(payload.get("objects", []))
        next_url = payload.get("meta", {}).get("next")
        url = resolve_api_url(next_url, config) if next_url else None
    return items


async def load_relationship_slugs(client: httpx.AsyncClient, config: dict) -> dict[str, str]:
    base = relations_api_base(config)
    url = f"{base}/name/docrelationshipname/?limit=500"
    objects = await fetch_api_objects(client, url, config)
    return {obj["resource_uri"]: obj["slug"] for obj in objects}


def build_relations_payload(
    rfc_number: int,
    outbound: list[dict],
    inbound: list[dict],
    slug_map: dict[str, str],
    include_references: bool = True,
) -> dict:
    name = f"rfc{rfc_number}"
    obsoletes: list[str] = []
    updates: list[str] = []
    references: list[dict] = []
    obsoleted_by: list[str] = []
    updated_by: list[str] = []
    referenced_by: list[dict] = []

    for rel in outbound:
        slug = slug_map.get(rel["relationship"], "")
        other = doc_name_from_uri(rel["target"])
        if slug == "obs":
            obsoletes.append(other)
        elif slug == "updates":
            updates.append(other)
        elif include_references and slug in REFERENCE_RELATION_SLUGS:
            references.append(
                {
                    "document": other,
                    "label": rel.get("originaltargetaliasname") or other,
                    "reference_type": slug,
                }
            )

    for rel in inbound:
        slug = slug_map.get(rel["relationship"], "")
        other = doc_name_from_uri(rel["source"])
        if slug == "obs":
            obsoleted_by.append(other)
        elif slug == "updates":
            updated_by.append(other)
        elif include_references and slug in REFERENCE_RELATION_SLUGS:
            referenced_by.append(
                {
                    "document": other,
                    "label": rel.get("originaltargetaliasname") or other,
                    "reference_type": slug,
                }
            )

    payload = {
        "rfc_number": rfc_number,
        "name": name,
        "obsoletes": sorted(set(obsoletes)),
        "obsoleted_by": sorted(set(obsoleted_by)),
        "updates": sorted(set(updates)),
        "updated_by": sorted(set(updated_by)),
    }
    if include_references:
        payload["references"] = references
        payload["referenced_by"] = referenced_by
    return payload


async def fetch_relations(
    client: httpx.AsyncClient,
    rfc_number: int,
    config: dict,
    slug_map: dict[str, str],
) -> dict:
    base = relations_api_base(config)
    name = f"rfc{rfc_number}"
    rel_cfg = format_source(config, "relations")
    include_references = bool(rel_cfg.get("include_references", True))
    outbound = await fetch_api_objects(
        client,
        f"{base}/doc/relateddocument/?source__name={name}&limit=200",
        config,
    )
    inbound = await fetch_api_objects(
        client,
        f"{base}/doc/relateddocument/?target__name={name}&limit=200",
        config,
    )
    return build_relations_payload(
        rfc_number, outbound, inbound, slug_map, include_references=include_references
    )


async def write_json_document(path: Path, payload: dict) -> None:
    """将合并后的文档写入 json 文件（格式化缩进，便于阅读）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)


async def write_text_document(path: Path, content: str) -> None:
    """将 RFC 正文写入 txt 文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(path, "w", encoding="utf-8", newline="") as f:
        await f.write(content)


async def apply_request_delay(config: dict) -> None:
    """在发出 HTTP 请求前等待配置的间隔，降低触发限流的概率。"""
    request_delay = config["download"].get("request_delay_seconds", 0)
    if request_delay > 0:
        await asyncio.sleep(request_delay)


# ---------------------------------------------------------------------------
# 第 3 步：异步并发下载
# ---------------------------------------------------------------------------


def make_http_client(config: dict) -> httpx.AsyncClient:
    """创建带连接池限制的 httpx 异步客户端。"""
    http_cfg = config["http"]
    dl_cfg = config["download"]
    limits = httpx.Limits(
        max_connections=http_cfg.get("max_connections", dl_cfg["concurrency"] + 4),
        max_keepalive_connections=http_cfg.get("max_keepalive_connections", dl_cfg["concurrency"]),
    )
    timeout = httpx.Timeout(dl_cfg["timeout_seconds"])
    headers = {"User-Agent": http_cfg["user_agent"]}
    return httpx.AsyncClient(limits=limits, timeout=timeout, headers=headers, follow_redirects=True)


def _backoff_seconds(config: dict, attempt: int, response: httpx.Response | None) -> float:
    """
    计算退避等待时间。

    优先使用服务端返回的 Retry-After；否则按指数退避递增。
    """
    dl_cfg = config["download"]
    base_delay = dl_cfg.get("retry_base_seconds", 2.0)
    max_delay = dl_cfg.get("retry_max_seconds", 60.0)

    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), max_delay)

    return min(base_delay * (2**attempt), max_delay)


async def fetch_with_retry(client: httpx.AsyncClient, url: str, config: dict) -> httpx.Response:
    """
    带重试的 HTTP GET。

    会重试以下情况：
      - 网络超时 / 连接错误
      - 429 Too Many Requests（Datatracker 限流）
      - 503 Service Unavailable
    """
    max_retries = config["download"]["max_retries"]
    last_exc: Exception | None = None
    response: httpx.Response | None = None

    for attempt in range(max_retries):
        try:
            response = await client.get(url)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            last_exc = exc
            await asyncio.sleep(_backoff_seconds(config, attempt, None))
            continue

        if response.status_code not in (429, 503):
            return response

        await asyncio.sleep(_backoff_seconds(config, attempt, response))

    if last_exc is not None:
        raise last_exc
    assert response is not None
    return response


async def download_json_task(
    client: httpx.AsyncClient,
    task: DownloadTask,
    config: dict,
) -> DownloadResult:
    """下载 Datatracker doc.json（不含 references）。"""
    await apply_request_delay(config)
    response = await fetch_with_retry(client, task.url, config)

    if response.status_code == 404 and not task.required:
        return DownloadResult(
            rfc_number=task.rfc_number,
            file_format=task.file_format,
            output_path=task.output_path,
            success=True,
            skipped=True,
            error_message="404 not found (optional format)",
        )

    response.raise_for_status()
    doc = response.json()
    doc.pop("references", None)
    await write_json_document(task.output_path, doc)

    return DownloadResult(
        rfc_number=task.rfc_number,
        file_format=task.file_format,
        output_path=task.output_path,
        success=True,
        skipped=False,
    )


async def cleanup_json_references_task(task: DownloadTask) -> DownloadResult:
    """从已有 doc.json 中移除旧版合并的 references 字段。"""
    doc = json.loads(task.output_path.read_text(encoding="utf-8"))
    doc.pop("references", None)
    await write_json_document(task.output_path, doc)
    return DownloadResult(
        rfc_number=task.rfc_number,
        file_format=task.file_format,
        output_path=task.output_path,
        success=True,
        skipped=True,
    )


async def download_relations_task(
    client: httpx.AsyncClient,
    task: DownloadTask,
    config: dict,
    slug_map: dict[str, str],
) -> DownloadResult:
    """通过 Datatracker API 拉取 obsoletes / updates / references / referenced_by 等关系。"""
    payload = await fetch_relations(client, task.rfc_number, config, slug_map)
    await write_json_document(task.output_path, payload)

    return DownloadResult(
        rfc_number=task.rfc_number,
        file_format=task.file_format,
        output_path=task.output_path,
        success=True,
        skipped=False,
    )


async def download_txt_task(
    client: httpx.AsyncClient,
    task: DownloadTask,
    config: dict,
) -> DownloadResult:
    """下载 RFC Editor 正文 .txt。"""
    await apply_request_delay(config)
    response = await fetch_with_retry(client, task.url, config)

    if response.status_code == 404 and not task.required:
        return DownloadResult(
            rfc_number=task.rfc_number,
            file_format=task.file_format,
            output_path=task.output_path,
            success=True,
            skipped=True,
            error_message="404 not found (optional format)",
        )

    response.raise_for_status()
    await write_text_document(task.output_path, response.text)

    return DownloadResult(
        rfc_number=task.rfc_number,
        file_format=task.file_format,
        output_path=task.output_path,
        success=True,
        skipped=False,
    )


async def download_one(
    client: httpx.AsyncClient,
    task: DownloadTask,
    semaphore: asyncio.Semaphore,
    config: dict,
    slug_map: dict[str, str],
) -> DownloadResult:
    """
    下载单个 RFC 文件。

    - json：Datatracker doc.json
    - relations：obsoletes / obsoleted_by / updates / updated_by / references / referenced_by
    - txt：RFC Editor 正文纯文本
    - skip_existing=true 时，本地已有完整文件则跳过
    """
    async with semaphore:
        file_existed = task.output_path.is_file()
        if not should_fetch_task(task, config):
            return DownloadResult(
                rfc_number=task.rfc_number,
                file_format=task.file_format,
                output_path=task.output_path,
                success=True,
                skipped=True,
            )

        if (
            task.file_format == "json"
            and file_existed
            and json_has_embedded_references(task.output_path)
        ):
            try:
                return await cleanup_json_references_task(task)
            except Exception as exc:  # noqa: BLE001
                return DownloadResult(
                    rfc_number=task.rfc_number,
                    file_format=task.file_format,
                    output_path=task.output_path,
                    success=False,
                    error_message=str(exc),
                )

        try:
            if task.file_format == "json":
                return await download_json_task(client, task, config)
            if task.file_format == "relations":
                return await download_relations_task(client, task, config, slug_map)
            if task.file_format == "txt":
                return await download_txt_task(client, task, config)

            raise ValueError(f"不支持的下载格式: {task.file_format}")

        except Exception as exc:  # noqa: BLE001 — 需收集所有失败以便写 errors.log
            return DownloadResult(
                rfc_number=task.rfc_number,
                file_format=task.file_format,
                output_path=task.output_path,
                success=False,
                error_message=str(exc),
            )


async def download_all(
    client: httpx.AsyncClient,
    tasks: list[DownloadTask],
    config: dict,
) -> list[DownloadResult]:
    """并发执行全部下载任务，并用 tqdm 显示进度条。"""
    concurrency = config["download"]["concurrency"]
    semaphore = asyncio.Semaphore(concurrency)
    slug_map: dict[str, str] = {}
    if "relations" in normalize_format_names(config["download"]["formats"]):
        slug_map = await load_relationship_slugs(client, config)

    coroutines = [download_one(client, task, semaphore, config, slug_map) for task in tasks]

    # tqdm_asyncio.gather 会在进度条中显示已完成 / 总数
    results: list[DownloadResult] = await tqdm_asyncio.gather(
        *coroutines,
        desc="下载 RFC 文档",
        unit="file",
    )
    return results


# ---------------------------------------------------------------------------
# 第 4 步：写出清单与错误日志
# ---------------------------------------------------------------------------


def save_manifest(
    config: dict,
    rfc_list: list[RfcMeta],
    results: list[DownloadResult],
) -> Path:
    """
    将 RFC 元数据与下载结果合并，写出 manifest CSV。

    每行一篇 RFC，download_status 列概括该 RFC 各格式的下载情况。
    """
    target_formats = normalize_format_names(config["download"]["formats"])

    result_map: dict[tuple[int, str], DownloadResult] = {
        (r.rfc_number, r.file_format): r for r in results
    }

    rows: list[dict] = []
    for meta in rfc_list:
        format_statuses: list[str] = []
        downloaded_paths: list[str] = []

        for fmt in target_formats:
            result = result_map.get((meta.rfc_number, fmt))
            if result is None:
                continue
            if result.skipped and result.output_path.is_file():
                format_statuses.append(f"{fmt}:cached")
            elif result.skipped:
                format_statuses.append(f"{fmt}:skipped")
            elif result.success:
                format_statuses.append(f"{fmt}:ok")
                downloaded_paths.append(str(result.output_path.relative_to(output_dir(config))))
            else:
                format_statuses.append(f"{fmt}:failed")

        rows.append(
            {
                "rfc_number": meta.rfc_number,
                "doc_id": meta.doc_id,
                "title": meta.title,
                "authors": meta.authors,
                "year": meta.year,
                "month": meta.month,
                "index_formats": ",".join(meta.available_formats),
                "download_status": "; ".join(format_statuses),
                "local_files": "; ".join(downloaded_paths),
            }
        )

    manifest_path = output_dir(config) / config["output"]["manifest_csv"]
    pd.DataFrame(rows).to_csv(manifest_path, index=False, encoding="utf-8")
    return manifest_path


def save_errors_log(config: dict, failures: list[DownloadResult]) -> Path | None:
    """把失败的下载任务写入 errors.log，便于后续补爬。"""
    if not failures:
        return None

    log_path = output_dir(config) / config["output"]["errors_log"]
    lines = [
        f"generated_at={datetime.now(timezone.utc).isoformat()}",
        f"failure_count={len(failures)}",
        "",
    ]
    for item in failures:
        lines.append(
            f"rfc{item.rfc_number}.{item.file_format}\t{item.output_path}\t{item.error_message}"
        )

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return log_path


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def report_failures(failures: list[DownloadResult], errors_path: Path) -> None:
    """仅在存在失败任务时向 stderr 输出错误信息，避免干扰 tqdm 进度条。"""
    print(f"下载失败 {len(failures)} 个文件，详情已写入: {errors_path}", file=sys.stderr)
    for item in failures:
        print(
            f"  rfc{item.rfc_number}.{item.file_format}: {item.error_message}",
            file=sys.stderr,
        )


async def async_main(config: dict, selected_formats: list[str]) -> None:
    config = apply_format_selection(config, selected_formats)
    print(f"下载格式: {', '.join(selected_formats)}")

    index_cfg = config["index"]
    cache_path = output_dir(config) / index_cfg["cache_file"]
    use_local_index = index_cfg.get("reuse_cached_index", True) and cache_path.is_file()

    if use_local_index:
        xml_bytes = cache_path.read_bytes()
    else:
        async with make_http_client(config) as client:
            xml_bytes = await fetch_index_xml(config, client)

    rfc_list = parse_rfc_index(xml_bytes)
    tasks = build_download_tasks(config, rfc_list)
    pending_tasks, cached_results = partition_tasks(tasks, config)

    if not pending_tasks:
        print(f"本地缓存已齐全（{len(cached_results)}/{len(tasks)} 个文件），跳过下载")
        manifest_path = save_manifest(config, rfc_list, cached_results)
        print(f"清单已写入: {manifest_path}")
        return

    by_format: dict[str, int] = {}
    for task in pending_tasks:
        by_format[task.file_format] = by_format.get(task.file_format, 0) + 1
    breakdown = ", ".join(f"{fmt} {count}" for fmt, count in sorted(by_format.items()))
    print(
        f"本地已有 {len(cached_results)}/{len(tasks)}，"
        f"待下载 {len(pending_tasks)}（{breakdown}）"
    )

    async with make_http_client(config) as client:
        downloaded_results = await download_all(client, pending_tasks, config)

    results = cached_results + downloaded_results
    manifest_path = save_manifest(config, rfc_list, results)
    print(f"清单已写入: {manifest_path}")

    failures = [r for r in downloaded_results if not r.success]
    if failures:
        errors_path = save_errors_log(config, failures)
        if errors_path is not None:
            report_failures(failures, errors_path)
    else:
        print("全部下载任务完成，无失败记录。")


def main(argv: list[str] | None = None) -> None:
    """同步入口，内部运行 asyncio 事件循环。"""
    args = parse_args(argv)
    config = load_config(args.config)
    selected_formats = resolve_selected_formats(config, args.formats)
    asyncio.run(async_main(config, selected_formats))


if __name__ == "__main__":
    main()
