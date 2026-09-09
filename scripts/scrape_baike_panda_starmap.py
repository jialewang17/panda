"""爬取百度百科「知名大熊猫」星图中的熊猫姓名与文字简介。

数据来源：星图 collectinfo 接口（仅文字 summary，不含图片）。
保存为 Markdown：首个文件 10 条，其后每个文件 25 条。

用法：
    python scripts/scrape_baike_panda_starmap.py
    python scripts/scrape_baike_panda_starmap.py --out-dir docs/熊猫资料 --limit 50
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_NODE_ID = "82a3cad5ed220114658fe747"
DEFAULT_LEMMA_ID = "55861456"
DEFAULT_STARMAP_URL = (
    "https://baike.baidu.com/starmap/view"
    "?fromModule=starMap_recommend"
    f"&lemmaId={DEFAULT_LEMMA_ID}"
    "&lemmaTitle=%E5%A4%A7%E7%86%8A%E7%8C%AB%E4%B8%AB%E4%B8%AB"
    f"&nodeId={DEFAULT_NODE_ID}"
    "&starMapFrom=lemma_starMap"
)
COLLECTINFO_API = "https://baike.baidu.com/starmap/api/collectinfo"
PAGE_SIZE = 50
FIRST_FILE_SIZE = 10
LATER_FILE_SIZE = 25


@dataclass
class PandaEntry:
    """单只大熊猫词条文字信息。"""

    lemma_id: int
    title: str
    summary: str
    lemma_desc: str = ""


def _http_get_json(url: str, *, timeout: float = 30.0, retries: int = 3) -> Dict[str, Any]:
    """GET JSON，带简单重试。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Referer": DEFAULT_STARMAP_URL,
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
    }
    last_exc: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            payload = json.loads(raw)
            if int(payload.get("errno", -1)) != 0:
                raise RuntimeError(
                    f"API errno={payload.get('errno')} errmsg={payload.get('errmsg')}"
                )
            return payload
        except (urllib.error.URLError, TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(1.2 * attempt)
                continue
            raise RuntimeError(f"请求失败: {url} ({type(exc).__name__}: {exc})") from exc
    raise RuntimeError(f"请求失败: {url} ({last_exc})")


def _clean_text(text: str) -> str:
    """清理摘要中的引用角标与多余空白，保留正文。"""
    cleaned = str(text or "")
    # 去掉 [3]、[8-9]、[1-2] 等角标
    cleaned = re.sub(r"\s*\[\d+(?:[-–—]\d+)?(?:\s*[、,，]\s*\d+(?:[-–—]\d+)?)*\]", "", cleaned)
    cleaned = cleaned.replace("\xa0", " ").replace("\u3000", " ")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def fetch_starmap_pandas(
    *,
    lemma_id: str = DEFAULT_LEMMA_ID,
    node_id: str = DEFAULT_NODE_ID,
    sleep_s: float = 0.25,
) -> List[PandaEntry]:
    """分页拉取星图下全部熊猫词条文字。"""
    entries: List[PandaEntry] = []
    seen: set[int] = set()
    pn = 1
    total = None

    while True:
        query = urllib.parse.urlencode(
            {
                "lemmaId": lemma_id,
                "encodeRelId": node_id,
                "pn": pn,
                "rn": PAGE_SIZE,
                "productId": 1,
            }
        )
        url = f"{COLLECTINFO_API}?{query}"
        payload = _http_get_json(url)
        data = payload.get("data") or {}
        if total is None:
            total = int(data.get("total") or 0)
            title = ((data.get("baseInfo") or data.get("nodeInfo") or {}).get("collectTitle"))
            print(f"星图标题: {title or '(未知)'}，共 {total} 条")

        rows = data.get("list") or []
        if not rows:
            break

        for row in rows:
            try:
                lid = int(row.get("lemmaId"))
            except (TypeError, ValueError):
                continue
            if lid in seen:
                continue
            title = str(row.get("lemmaTitle") or "").strip()
            summary = _clean_text(str(row.get("summary") or ""))
            if not title or not summary:
                continue
            seen.add(lid)
            entries.append(
                PandaEntry(
                    lemma_id=lid,
                    title=title,
                    summary=summary,
                    lemma_desc=_clean_text(str(row.get("lemmaDesc") or "")),
                )
            )

        print(f"  已拉取第 {pn} 页，累计有效 {len(entries)} 条")
        if total is not None and pn * PAGE_SIZE >= total:
            break
        if len(rows) < PAGE_SIZE:
            break
        pn += 1
        time.sleep(max(0.0, sleep_s))

    return entries


def _chunk_sizes(total: int, first: int = FIRST_FILE_SIZE, later: int = LATER_FILE_SIZE) -> List[int]:
    """生成分文件条数：首文件 first，其后 later。"""
    if total <= 0:
        return []
    sizes = [min(first, total)]
    remain = total - sizes[0]
    while remain > 0:
        take = min(later, remain)
        sizes.append(take)
        remain -= take
    return sizes


def iter_file_chunks(entries: List[PandaEntry]) -> Iterable[tuple[int, List[PandaEntry]]]:
    """按约定分块：(文件序号从1开始, 条目列表)。"""
    offset = 0
    for idx, size in enumerate(_chunk_sizes(len(entries)), start=1):
        yield idx, entries[offset : offset + size]
        offset += size


def format_markdown_chunk(chunk: List[PandaEntry], *, start_index: int) -> str:
    """格式化为与现有熊猫资料类似的 Markdown。"""
    parts: List[str] = []
    for i, item in enumerate(chunk):
        n = start_index + i
        title = item.title if item.title.startswith("大熊猫") else f"大熊猫{item.title}"
        parts.append(f"{n}.{title}")
        parts.append("")
        parts.append(item.summary)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def save_markdown_files(
    entries: List[PandaEntry],
    out_dir: Path,
    *,
    stem: str = "百度百科熊猫星图",
) -> List[Path]:
    """写入分卷 Markdown，返回写出路径列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    cursor = 1
    for file_idx, chunk in iter_file_chunks(entries):
        path = out_dir / f"{stem}{file_idx}.md"
        text = format_markdown_chunk(chunk, start_index=cursor)
        path.write_text(text, encoding="utf-8")
        written.append(path)
        print(f"写入 {path} （{len(chunk)} 条，序号 {cursor}-{cursor + len(chunk) - 1}）")
        cursor += len(chunk)
    return written


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="爬取百度百科知名大熊猫星图文字简介")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "docs" / "熊猫资料",
        help="Markdown 输出目录（默认 docs/熊猫资料）",
    )
    parser.add_argument("--lemma-id", default=DEFAULT_LEMMA_ID, help="星图关联 lemmaId")
    parser.add_argument("--node-id", default=DEFAULT_NODE_ID, help="星图 nodeId / encodeRelId")
    parser.add_argument("--limit", type=int, default=0, help="仅保存前 N 条（0=全部）")
    parser.add_argument("--sleep", type=float, default=0.25, help="分页请求间隔秒")
    parser.add_argument(
        "--dump-json",
        type=Path,
        default=None,
        help="可选：同时保存原始条目 JSON",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    print(f"星图页面: {DEFAULT_STARMAP_URL}")
    entries = fetch_starmap_pandas(
        lemma_id=str(args.lemma_id),
        node_id=str(args.node_id),
        sleep_s=float(args.sleep),
    )
    if args.limit and args.limit > 0:
        entries = entries[: int(args.limit)]
        print(f"按 --limit 截断为 {len(entries)} 条")

    if not entries:
        print("未获取到有效条目")
        return 1

    if args.dump_json:
        args.dump_json.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "lemmaId": e.lemma_id,
                "title": e.title,
                "lemmaDesc": e.lemma_desc,
                "summary": e.summary,
            }
            for e in entries
        ]
        args.dump_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON 已写入 {args.dump_json}")

    paths = save_markdown_files(entries, Path(args.out_dir))
    print(f"完成：共 {len(entries)} 条，{len(paths)} 个 Markdown 文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
