"""用 DeepSeek API 批量让 LLM 判断 RFC 是否定义协议及其协议层。"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import re
import sys
from pathlib import Path

import httpx
import yaml
from tqdm.asyncio import tqdm_asyncio

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "llm_audit.yaml"

LAYERS = ("link", "network", "transport", "session", "presentation", "application", "other", "none")

SYSTEM_PROMPT = """你是 IETF RFC 分析助手。根据每条 RFC 的编号、标题和摘要片段，判断该文档是否**定义**了一个通信协议（而非仅讨论、评论、实现说明或管理文档）。

协议层（layer）使用简化 OSI 分类：
- link: 链路层（以太网、PPP 等）
- network: 网络层（IP、ICMP、路由协议等）
- transport: 传输层（TCP、UDP、SCTP 等）
- session: 会话层
- presentation: 表示层
- application: 应用层（HTTP、SMTP、DNS 等）
- other: 跨层或难以归入上述层（如隧道、安全框架）
- none: 未定义协议

仅输出 JSON，不要 markdown 包裹：
{"results":[{"rfc_number":1,"defines_protocol":false,"protocol_name":null,"protocol_acronym":null,"layer":"none","confidence":0.9,"rationale":"简短理由"}]}
"""


def load_dotenv(path: Path | None = None) -> None:
    """从项目根 .env 加载变量（不覆盖已有环境变量）。"""
    path = path or PROJECT_ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_first_rfcs(manifest: Path, limit: int) -> list[dict]:
    rows: list[dict] = []
    with manifest.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    rows.sort(key=lambda r: int(r["rfc_number"]))
    return rows[:limit]


def load_cached_rfc_numbers(path: Path) -> set[int]:
    """从已有 jsonl 输出中读取已分类的 RFC 编号。"""
    if not path.is_file():
        return set()
    cached: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            cached.add(int(json.loads(line)["rfc_number"]))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return cached


def _abstract_snippet(txt_dir: Path, rfc_number: int, max_len: int) -> str:
    path = txt_dir / f"rfc{rfc_number}.txt"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"ABSTRACT\s*\n(.*?)(?:\n\n[A-Z][A-Z \n]{3,}|\Z)", text, re.S)
    chunk = m.group(1) if m else text
    chunk = re.sub(r"\s+", " ", chunk).strip()
    return chunk[:max_len]


def enrich_rfc(row: dict, *, json_dir: Path, txt_dir: Path, abstract_max_len: int) -> dict:
    n = int(row["rfc_number"])
    meta: dict = {"rfc_number": n, "title": row["title"], "abstract": ""}
    jpath = json_dir / f"rfc{n}.json"
    if jpath.is_file():
        data = json.loads(jpath.read_text(encoding="utf-8"))
        meta["abstract"] = (data.get("abstract") or "").strip()
        meta["std_level"] = data.get("std_level")
    snippet = _abstract_snippet(txt_dir, n, abstract_max_len)
    if snippet and not meta["abstract"]:
        meta["abstract"] = snippet
    elif snippet and len(meta["abstract"]) < 80:
        meta["abstract"] = f"{meta['abstract']} {snippet}".strip()[:abstract_max_len]
    return meta


def batch_items(items: list[dict], batch_size: int) -> list[list[dict]]:
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def build_user_prompt(batch: list[dict]) -> str:
    lines = ["待分析的 RFC："]
    for item in batch:
        lines.append(
            f"- RFC {item['rfc_number']}: {item['title']}"
            + (f"\n  摘要: {item['abstract']}" if item.get("abstract") else "")
        )
    lines.append(f"\n请为以上 {len(batch)} 条 RFC 各输出一条 results 记录，rfc_number 必须与输入一致。")
    return "\n".join(lines)


def build_batch_prompt(batch: list[dict]) -> str:
    """dry-run 预览用：system + user 合并展示。"""
    return SYSTEM_PROMPT + "\n\n" + build_user_prompt(batch)


def parse_llm_json(text: str) -> list[dict]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    results = data.get("results", data if isinstance(data, list) else [])
    if not isinstance(results, list):
        raise ValueError("LLM response missing results array")
    return results


def validate_result(row: dict) -> dict:
    layer = (row.get("layer") or "none").lower()
    if layer not in LAYERS:
        layer = "other"
    return {
        "rfc_number": int(row["rfc_number"]),
        "defines_protocol": bool(row.get("defines_protocol")),
        "protocol_name": row.get("protocol_name"),
        "protocol_acronym": row.get("protocol_acronym"),
        "layer": layer,
        "confidence": float(row.get("confidence", 0.5)),
        "rationale": (row.get("rationale") or "").strip(),
    }


async def call_deepseek_batch(
    client: httpx.AsyncClient,
    batch: list[dict],
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout: float,
    temperature: float,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(batch)},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
    }
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = await client.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPStatusError as err:
        body = err.response.text[:500]
        raise RuntimeError(f"DeepSeek API HTTP {err.response.status_code}: {body}") from err
    except httpx.HTTPError as err:
        raise RuntimeError(f"DeepSeek API 请求失败: {err}") from err

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as err:
        raise RuntimeError(f"DeepSeek 响应格式异常: {data!r}") from err


def resolve_api_key(config: dict) -> str:
    env_name = config["api"].get("api_key_env", "DEEPSEEK_API_KEY")
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise RuntimeError(f"未设置 {env_name} 环境变量")
    return key


def append_results(out_path: Path, rows: list[dict]) -> None:
    with out_path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sort_jsonl_results(path: Path) -> int:
    """按 rfc_number 重写 jsonl；重复编号保留最后一条。"""
    if not path.is_file():
        return 0
    rows: dict[int, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            rows[int(row["rfc_number"])] = row
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    if not rows:
        return 0
    path.write_text(
        "".join(json.dumps(rows[n], ensure_ascii=False) + "\n" for n in sorted(rows)),
        encoding="utf-8",
    )
    return len(rows)


async def process_batch(
    batch_index: int,
    batch: list[dict],
    *,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    write_lock: asyncio.Lock,
    out_path: Path,
    model: str,
    api_key: str,
    base_url: str,
    timeout: float,
    temperature: float,
    request_delay: float,
) -> list[dict]:
    async with semaphore:
        if request_delay > 0:
            await asyncio.sleep(request_delay)
        try:
            raw = await call_deepseek_batch(
                client,
                batch,
                model=model,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                temperature=temperature,
            )
            parsed = [validate_result(r) for r in parse_llm_json(raw)]
            expected = {item["rfc_number"] for item in batch}
            got = {r["rfc_number"] for r in parsed}
            if expected != got:
                print(
                    f"  警告: 批次 {batch_index} RFC 集合不匹配 expected={sorted(expected)} got={sorted(got)}",
                    file=sys.stderr,
                )
            async with write_lock:
                append_results(out_path, parsed)
            return parsed
        except Exception as exc:  # noqa: BLE001
            lo, hi = batch[0]["rfc_number"], batch[-1]["rfc_number"]
            print(f"批次 {batch_index} 失败 RFC {lo}–{hi}: {exc}", file=sys.stderr)
            return []


async def async_run_audit(config: dict) -> list[dict]:
    inp = config["input"]
    api_cfg = config["api"]
    run_cfg = config["run"]

    manifest = PROJECT_ROOT / inp["manifest"]
    json_dir = PROJECT_ROOT / inp["json_dir"]
    txt_dir = PROJECT_ROOT / inp["txt_dir"]
    out_path = PROJECT_ROOT / config["output"]["classification_jsonl"]
    limit = inp["limit"]
    batch_size = run_cfg["batch_size"]
    concurrency = run_cfg.get("concurrency", 4)
    request_delay = float(run_cfg.get("request_delay_seconds", 0))
    skip_existing = run_cfg.get("skip_existing", True)
    dry_run = run_cfg.get("dry_run", False)
    abstract_max_len = inp.get("abstract_max_len", 800)

    if not manifest.is_file():
        raise FileNotFoundError(f"缺少清单: {manifest}")

    manifest_rows = load_first_rfcs(manifest, limit)
    cached = load_cached_rfc_numbers(out_path) if skip_existing else set()
    pending_rows = [r for r in manifest_rows if int(r["rfc_number"]) not in cached]

    print(
        f"清单 {len(manifest_rows)} 条，缓存命中 {len(cached)} 条，待处理 {len(pending_rows)} 条",
        flush=True,
    )

    if not pending_rows:
        print("无待处理 RFC，跳过 LLM 调用。")
        return []

    rfcs = [
        enrich_rfc(r, json_dir=json_dir, txt_dir=txt_dir, abstract_max_len=abstract_max_len)
        for r in pending_rows
    ]
    batches = batch_items(rfcs, batch_size)

    if dry_run:
        for idx, batch in enumerate(batches, 1):
            print(f"[batch {idx}/{len(batches)}] RFC {batch[0]['rfc_number']}–{batch[-1]['rfc_number']}", flush=True)
            prompt = build_batch_prompt(batch)
            print("--- prompt preview ---")
            print(prompt[:1200] + ("..." if len(prompt) > 1200 else ""))
            print("--- end preview ---\n")
        return []

    out_path.parent.mkdir(parents=True, exist_ok=True)
    key = resolve_api_key(config)
    model = api_cfg["model"]
    base_url = api_cfg["base_url"]
    timeout = float(api_cfg.get("timeout_seconds", 120))
    temperature = float(api_cfg.get("temperature", 0.2))

    semaphore = asyncio.Semaphore(concurrency)
    write_lock = asyncio.Lock()
    limits = httpx.Limits(max_connections=concurrency + 2, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:
        coroutines = [
            process_batch(
                idx,
                batch,
                client=client,
                semaphore=semaphore,
                write_lock=write_lock,
                out_path=out_path,
                model=model,
                api_key=key,
                base_url=base_url,
                timeout=timeout,
                temperature=temperature,
                request_delay=request_delay,
            )
            for idx, batch in enumerate(batches, 1)
        ]
        batch_results = await tqdm_asyncio.gather(*coroutines, desc="LLM 协议分类", unit="batch")

    return [row for batch in batch_results for row in batch]


def run_audit(config: dict) -> list[dict]:
    return asyncio.run(async_run_audit(config))


def main() -> None:
    config = load_config()
    dry_run = config["run"].get("dry_run", False)
    out_path = PROJECT_ROOT / config["output"]["classification_jsonl"]
    results = run_audit(config)
    if not dry_run:
        total = sort_jsonl_results(out_path)
        print(f"完成，本次新处理 {len(results)} 条，共 {total} 条已排序 → {out_path}")


if __name__ == "__main__":
    main()
