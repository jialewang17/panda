"""按日计划将百度百科熊猫星图分卷抽取并增量写入 Neo4j（栏目：熊猫资料）。

默认：每天处理 1 个待办分卷（星图4.md → 22.md）。
整卷约 25 只档案，抽取前会拆成小分片，避免模型输出长度截断。
写入不加 --clear，避免清空既有栏目。

用法：
    python scripts/ingest_starmap_daily.py
    python scripts/ingest_starmap_daily.py --status
    python scripts/ingest_starmap_daily.py --dry-run
    python scripts/ingest_starmap_daily.py --day 1 --retry-failed
    python scripts/ingest_starmap_daily.py --chunk-size 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = ROOT / "data" / "curated" / "starmap_ingest_plan.json"
PROGRESS_PATH = ROOT / "data" / "curated" / "starmap_ingest_progress.json"
CHUNK_DIR = ROOT / "sandbox" / "starmap_ingest_chunks"
CATEGORY = "熊猫资料"
DEFAULT_CHUNK_SIZE = 5


def _safe_print(*args: Any) -> None:
    text = " ".join(str(a) for a in args)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", "replace"))


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_plan() -> Dict[str, Any]:
    if not PLAN_PATH.exists():
        raise FileNotFoundError(f"缺少计划文件: {PLAN_PATH}")
    return _load_json(PLAN_PATH)


def _load_progress() -> Dict[str, Any]:
    data = _load_json(PROGRESS_PATH)
    if not data:
        data = {"updated_at": "", "runs": [], "file_status": {}}
    data.setdefault("runs", [])
    data.setdefault("file_status", {})
    return data


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _split_panda_entries(md_text: str) -> List[str]:
    text = (md_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    parts = re.split(r"(?m)(?=^\d+\.\s*大熊猫)", text)
    return [p.strip() for p in parts if p.strip()]


def _chunk_list(items: List[str], size: int) -> List[List[str]]:
    n = max(1, int(size))
    return [items[i : i + n] for i in range(0, len(items), n)]


def _prepare_chunk_dir(md_path: Path, *, chunk_size: int) -> Tuple[Path, int]:
    entries = _split_panda_entries(md_path.read_text(encoding="utf-8"))
    if not entries:
        raise RuntimeError(f"未能从文件解析出熊猫条目: {_rel(md_path)}")
    out_dir = CHUNK_DIR / md_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.md"):
        old.unlink()
    chunks = _chunk_list(entries, chunk_size)
    for i, chunk in enumerate(chunks, start=1):
        (out_dir / f"part_{i:02d}.md").write_text(
            "\n\n".join(chunk).rstrip() + "\n",
            encoding="utf-8",
        )
    _safe_print(
        f"拆分 {_rel(md_path)} -> {len(entries)} 只 / {len(chunks)} 个分片（每片<={chunk_size}）"
    )
    return out_dir, len(chunks)


def _merge_item_status(plan: Dict[str, Any], progress: Dict[str, Any]) -> List[Dict[str, Any]]:
    file_status = progress.get("file_status") or {}
    items: List[Dict[str, Any]] = []
    for raw in plan.get("items") or []:
        item = dict(raw)
        key = str(item.get("file", "")).replace("\\", "/")
        st = file_status.get(key) or {}
        if st.get("status"):
            item["status"] = st["status"]
            item["done_at"] = st.get("done_at", "")
            item["result_json"] = st.get("result_json", "")
            item["error"] = st.get("error", "")
        else:
            item.setdefault("status", "pending")
        items.append(item)
    return items


def _print_status(items: List[Dict[str, Any]]) -> None:
    pending = [x for x in items if x.get("status") == "pending"]
    done = [x for x in items if x.get("status") == "done"]
    failed = [x for x in items if x.get("status") == "failed"]
    _safe_print(
        f"计划进度：done={len(done)} pending={len(pending)} failed={len(failed)} total={len(items)}"
    )
    for item in items:
        mark = {"done": "[x]", "failed": "[!]", "pending": "[ ]"}.get(
            str(item.get("status")), "[?]"
        )
        line = f"  {mark} Day{item.get('day')} {item.get('date')}  {item.get('file')}"
        if item.get("result_json"):
            line += f"  -> {item.get('result_json')}"
        if item.get("error"):
            err = str(item.get("error")).replace("\n", " ")[:180]
            line += f"  ERR={err}"
        _safe_print(line)


def _select_items(
    items: List[Dict[str, Any]],
    *,
    batch_size: int,
    day: int = 0,
    file_path: str = "",
    retry_failed: bool = False,
) -> List[Dict[str, Any]]:
    if file_path:
        key = file_path.replace("\\", "/")
        picked = [x for x in items if str(x.get("file", "")).replace("\\", "/") == key]
        if not picked:
            raise ValueError(f"计划中找不到文件: {file_path}")
        return picked
    if day > 0:
        picked = [x for x in items if int(x.get("day") or 0) == day]
        if not picked:
            raise ValueError(f"计划中找不到 Day{day}")
        return picked

    selected: List[Dict[str, Any]] = []
    for item in items:
        status = str(item.get("status") or "pending")
        if status == "pending" or (retry_failed and status == "failed"):
            selected.append(item)
        if len(selected) >= max(1, batch_size):
            break
    return selected


def _run_cmd(cmd: List[str], *, dry_run: bool) -> subprocess.CompletedProcess[str]:
    _safe_print(">", " ".join(cmd))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    # Windows 默认控制台常为 GBK：强制子进程 UTF-8，避免路径中文被替换成 \ufffd
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _extract_docs_dir(docs_rel: str, *, concurrency: int, dry_run: bool) -> str:
    cmd = [
        sys.executable,
        "-m",
        "tools.panda_history_extractor",
        "--docs-dir",
        docs_rel,
        "--category",
        CATEGORY,
        "--max-concurrency",
        str(max(1, concurrency)),
        "--retry-failed-times",
        "1",
    ]
    proc = _run_cmd(cmd, dry_run=dry_run)
    if dry_run:
        return f"(dry-run) data/wiki/<run>/{Path(docs_rel).name}.json"
    if proc.returncode != 0:
        raise RuntimeError(
            f"抽取失败 code={proc.returncode}; "
            f"stdout={proc.stdout[-1200:]}; stderr={proc.stderr[-800:]}"
        )
    # 成功时也可能有部分失败；只要产出主 json 就继续写库
    result_path = ""
    for line in (proc.stdout or "").splitlines():
        if line.strip().startswith("result_file_path:"):
            candidate = line.split(":", 1)[1].strip()
            # 过滤控制台编码损坏的路径（含替换字符）
            if candidate and "\ufffd" not in candidate and Path(candidate).is_file():
                result_path = candidate
                break
    if not result_path:
        candidates = sorted(
            (ROOT / "data" / "wiki").glob("**/panda_extract_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for cand in candidates[:20]:
            name = cand.name
            if "checkpoint" in name or name.endswith("_nodes.json") or name.endswith(
                "_rels.json"
            ):
                continue
            if "batch_dir" in cand.parent.name or "single_file" in cand.parent.name:
                result_path = str(cand.resolve())
                break
        if not result_path and candidates:
            result_path = str(candidates[0].resolve())
    if not result_path:
        raise RuntimeError("抽取完成但未解析到 result_file_path")
    # 统一为相对项目根的 POSIX 路径，避免 Windows 绝对路径拼接问题
    try:
        result_rel = _rel(Path(result_path))
    except Exception:
        result_rel = result_path
    _safe_print(f"result_json: {result_rel}")
    if proc.stdout:
        _safe_print(proc.stdout[-1000:])
    return result_rel


def _write_neo4j(result_json: str, *, dry_run: bool) -> None:
    cmd = [
        sys.executable,
        "-m",
        "tools.neo4j_graph_writer",
        "--result-json",
        result_json,
    ]
    proc = _run_cmd(cmd, dry_run=dry_run)
    if dry_run:
        return
    if proc.returncode != 0:
        raise RuntimeError(
            f"写入 Neo4j 失败 code={proc.returncode}; "
            f"stdout={proc.stdout[-1200:]}; stderr={proc.stderr[-800:]}"
        )
    if proc.stdout:
        _safe_print(proc.stdout[-1000:])


def _mark_progress(
    progress: Dict[str, Any],
    *,
    file_key: str,
    status: str,
    result_json: str = "",
    error: str = "",
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    progress["updated_at"] = now
    progress["file_status"][file_key] = {
        "status": status,
        "done_at": now if status == "done" else "",
        "result_json": result_json,
        "error": error[:500],
    }
    progress["runs"].append(
        {
            "at": now,
            "file": file_key,
            "status": status,
            "result_json": result_json,
            "error": error[:500],
        }
    )


def run_batch(
    *,
    batch_size: int = 1,
    day: int = 0,
    file_path: str = "",
    concurrency: int = 3,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    dry_run: bool = False,
    retry_failed: bool = False,
) -> int:
    plan = _load_plan()
    progress = _load_progress()
    items = _merge_item_status(plan, progress)
    selected = _select_items(
        items,
        batch_size=batch_size,
        day=day,
        file_path=file_path,
        retry_failed=retry_failed,
    )
    if not selected:
        _safe_print("没有待处理分卷（计划已完成）。")
        _print_status(items)
        return 0

    _safe_print(f"本次将处理 {len(selected)} 个分卷（category={CATEGORY}，增量写入）：")
    for item in selected:
        _safe_print(f"  - Day{item.get('day')} {item.get('file')}")

    failures = 0
    for item in selected:
        file_key = str(item.get("file", "")).replace("\\", "/")
        md_path = ROOT / file_key
        if not md_path.exists():
            msg = f"文件不存在: {file_key}"
            _safe_print(msg)
            if not dry_run:
                _mark_progress(progress, file_key=file_key, status="failed", error=msg)
                _save_json(PROGRESS_PATH, progress)
            failures += 1
            continue
        try:
            chunk_dir, chunk_count = _prepare_chunk_dir(md_path, chunk_size=chunk_size)
            docs_rel = _rel(chunk_dir)
            if dry_run:
                _safe_print(f"(dry-run) 将抽取 {docs_rel}（{chunk_count} 个 md）并增量写入 Neo4j")
            else:
                result_json = _extract_docs_dir(
                    docs_rel, concurrency=concurrency, dry_run=False
                )
                try:
                    result_for_writer = _rel(Path(result_json))
                except Exception:
                    result_for_writer = result_json
                _write_neo4j(result_for_writer, dry_run=False)
                _mark_progress(
                    progress,
                    file_key=file_key,
                    status="done",
                    result_json=result_for_writer,
                )
                _save_json(PROGRESS_PATH, progress)
            _safe_print(f"完成: {file_key}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            err = f"{type(exc).__name__}: {exc}"
            _safe_print(f"失败: {file_key} -> {err}")
            if not dry_run:
                _mark_progress(progress, file_key=file_key, status="failed", error=err)
                _save_json(PROGRESS_PATH, progress)

    items = _merge_item_status(plan, _load_progress() if not dry_run else progress)
    _print_status(items)
    return 1 if failures else 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="百度百科熊猫星图分卷按日入库（熊猫资料）")
    parser.add_argument("--status", action="store_true", help="仅查看进度")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    parser.add_argument("--batch-size", type=int, default=1, help="一次处理几个 pending，默认 1")
    parser.add_argument("--day", type=int, default=0, help="指定计划 Day 编号")
    parser.add_argument("--file", default="", help="指定单个 md 相对路径")
    parser.add_argument("--concurrency", type=int, default=3, help="抽取并发，默认 3")
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"每个分片几只熊猫，默认 {DEFAULT_CHUNK_SIZE}",
    )
    parser.add_argument("--retry-failed", action="store_true", help="重试 failed 分卷")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    plan = _load_plan()
    progress = _load_progress()
    items = _merge_item_status(plan, progress)
    if args.status:
        _print_status(items)
        return 0
    return run_batch(
        batch_size=max(1, int(args.batch_size)),
        day=int(args.day or 0),
        file_path=str(args.file or ""),
        concurrency=max(1, int(args.concurrency)),
        chunk_size=max(1, int(args.chunk_size)),
        dry_run=bool(args.dry_run),
        retry_failed=bool(args.retry_failed),
    )


if __name__ == "__main__":
    raise SystemExit(main())
