"""统一编号 docs/熊猫知识 文件，并迁移 Neo4j 溯源字段。

目标：
1. 将混乱文件名改为 熊猫知识_001_标题.md 形式，便于后续递增新增
2. 不重抽、不删图谱关系，仅改写 r.source_file / n.source_files 中的路径
3. 落盘旧→新映射，保证历史路径可还原

用法：
    python scripts/renumber_panda_knowledge_docs.py --dry-run
    python scripts/renumber_panda_knowledge_docs.py --apply
    python scripts/renumber_panda_knowledge_docs.py --apply --skip-neo4j
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs" / "熊猫知识"
MAP_PATH = ROOT / "data" / "curated" / "panda_knowledge_source_map.json"
CATEGORY = "熊猫知识"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class RenameItem:
    """单个文件的改名与溯源映射。"""

    index: int
    old_name: str
    new_name: str
    old_rel: str
    new_rel: str
    title: str


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(
            msg.encode("utf-8", errors="replace").decode("ascii", errors="replace"),
            flush=True,
        )


def clean_title(filename: str) -> str:
    """从旧文件名提炼稳定短标题。"""
    stem = Path(filename).stem
    stem = re.sub(r"^\[\d{12}\]", "", stem)
    stem = re.sub(r"^【熊猫知识】", "", stem)
    stem = re.sub(r"\s*-\s*成都大熊猫繁育研究基地$", "", stem)
    stem = stem.strip(" -_\t")
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)
    stem = re.sub(r"\s+", "", stem)
    return stem or "未命名"


def list_knowledge_docs() -> List[Path]:
    """按当前目录排序列出待编号 md（跳过映射/临时文件）。"""
    skip = {"_curated_gap_facts.md", "README.md"}
    files = [
        p
        for p in DOCS_DIR.glob("*.md")
        if p.is_file() and p.name not in skip and not p.name.startswith("熊猫知识_")
    ]
    # 已编号文件也纳入排序，避免重复执行时乱序；仅处理未编号的
    return sorted(files, key=lambda p: p.name)


def list_already_numbered() -> List[Path]:
    return sorted(
        [p for p in DOCS_DIR.glob("熊猫知识_*.md") if p.is_file()],
        key=lambda p: p.name,
    )


def build_rename_plan(files: Sequence[Path]) -> List[RenameItem]:
    """生成编号方案：001 起，保持当前文件名排序稳定。"""
    items: List[RenameItem] = []
    used_new: set[str] = set()
    for idx, path in enumerate(files, start=1):
        title = clean_title(path.name)
        new_name = f"熊猫知识_{idx:03d}_{title}.md"
        # 极端情况下标题撞车，追加序号
        if new_name in used_new or (DOCS_DIR / new_name).exists():
            new_name = f"熊猫知识_{idx:03d}_{title}_{idx:03d}.md"
        used_new.add(new_name)
        items.append(
            RenameItem(
                index=idx,
                old_name=path.name,
                new_name=new_name,
                old_rel=f"docs/熊猫知识/{path.name}",
                new_rel=f"docs/熊猫知识/{new_name}",
                title=title,
            )
        )
    return items


def write_mapping(items: Sequence[RenameItem], *, next_id: int) -> None:
    payload = {
        "version": "1.0",
        "category": CATEGORY,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "docs_dir": "docs/熊猫知识",
        "naming_rule": "熊猫知识_{NNN}_{标题}.md",
        "next_id": next_id,
        "note": "旧文件名/路径 -> 新文件名/路径；Neo4j 已迁移 source_file 后仍保留本表供历史溯源。",
        "items": [asdict(x) for x in items],
        "by_old_name": {x.old_name: x.new_name for x in items},
        "by_new_name": {x.new_name: x.old_name for x in items},
    }
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def rename_files(items: Sequence[RenameItem], *, dry_run: bool) -> None:
    """两阶段改名，避免 Windows 上大小写/同名冲突。"""
    # phase1: -> .renaming.tmp
    temps: List[Tuple[Path, Path, Path]] = []
    for item in items:
        src = DOCS_DIR / item.old_name
        dst = DOCS_DIR / item.new_name
        if not src.exists():
            raise FileNotFoundError(f"缺少源文件: {src}")
        if dst.exists() and dst.resolve() != src.resolve():
            raise FileExistsError(f"目标已存在: {dst}")
        tmp = DOCS_DIR / f".__renaming_{item.index:03d}__.tmp.md"
        temps.append((src, tmp, dst))
        _safe_print(f"{'DRY ' if dry_run else ''}{item.old_name} -> {item.new_name}")
        if not dry_run:
            src.rename(tmp)

    if dry_run:
        return

    for _, tmp, dst in temps:
        tmp.rename(dst)


def _abs_doc(rel_or_name: str) -> str:
    text = rel_or_name.replace("\\", "/")
    if text.startswith("docs/"):
        return str((ROOT / text).resolve())
    return str((DOCS_DIR / Path(text).name).resolve())


def migrate_neo4j(items: Sequence[RenameItem], *, dry_run: bool) -> Dict[str, int]:
    """把关系/节点上的 source_file(s) 从旧绝对路径改到新绝对路径。"""
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(ROOT / ".env")
    uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL")
    user = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME") or "neo4j"
    password = os.getenv("NEO4J_PASSWORD")
    database = os.getenv("NEO4J_DATABASE") or "neo4j"
    if not uri or not password:
        raise RuntimeError("缺少 Neo4j 连接配置")

    stats = {"rels_updated": 0, "nodes_updated": 0}
    if dry_run:
        _safe_print(f"(dry-run) 将迁移 {len(items)} 个源文件的 Neo4j 溯源路径")
        return stats

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            for item in items:
                old_abs = _abs_doc(item.old_rel)
                new_abs = _abs_doc(item.new_rel)
                old_name = item.old_name

                rel_res = session.run(
                    """
                    MATCH ()-[r]->()
                    WHERE coalesce(r.category, '') = $category
                      AND (
                        r.source_file = $old_abs
                        OR r.source_file ENDS WITH $old_name
                      )
                    WITH r, r.source_file AS before
                    SET r.source_file = $new_abs,
                        r.source_file_prev = before
                    RETURN count(r) AS c
                    """,
                    category=CATEGORY,
                    old_abs=old_abs,
                    old_name=old_name,
                    new_abs=new_abs,
                ).single()
                stats["rels_updated"] += int((rel_res or {}).get("c") or 0)

                node_res = session.run(
                    """
                    MATCH (n)
                    WHERE any(
                      x IN coalesce(n.source_files, [])
                      WHERE x = $old_abs OR x ENDS WITH $old_name
                    )
                    SET n.source_files = [
                      x IN coalesce(n.source_files, []) |
                      CASE
                        WHEN x = $old_abs OR x ENDS WITH $old_name THEN $new_abs
                        ELSE x
                      END
                    ]
                    RETURN count(n) AS c
                    """,
                    old_abs=old_abs,
                    old_name=old_name,
                    new_abs=new_abs,
                ).single()
                stats["nodes_updated"] += int((node_res or {}).get("c") or 0)

                _safe_print(
                    f"Neo4j {old_name} -> {item.new_name} "
                    f"rels={int((rel_res or {}).get('c') or 0)} "
                    f"nodes={int((node_res or {}).get('c') or 0)}"
                )
    finally:
        driver.close()
    return stats


def rewrite_text_refs(items: Sequence[RenameItem], paths: Sequence[Path]) -> int:
    """把仓库内关键 JSON/MD 引用里的旧文件名替换为新文件名。"""
    pairs = [(it.old_name, it.new_name) for it in items]
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    changed = 0
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        original = text
        for old, new in pairs:
            if old in text:
                text = text.replace(old, new)
        if text != original:
            path.write_text(text, encoding="utf-8")
            changed += 1
            _safe_print(f"updated refs: {path.relative_to(ROOT)}")
    return changed


def default_ref_targets() -> List[Path]:
    targets: List[Path] = [
        ROOT / "data" / "curated" / "kb_gap_facts_v1.json",
        ROOT / "docs" / "panda_detailed_questions.json",
        ROOT / "docs" / "panda_gap_focus_questions.json",
        ROOT / "README.md",
    ]
    targets.extend(sorted((ROOT / "reports").glob("kb_focus_questions_*.json")))
    targets.extend(sorted((ROOT / "reports").glob("kb_gaps_*.jsonl")))
    targets.extend(sorted((ROOT / "reports").glob("kb_eval_*.json")))
    targets.extend(sorted((ROOT / "reports").glob("kb_gapfind_summary_*.md")))
    return [p for p in targets if p.exists()]


def verify_neo4j(items: Sequence[RenameItem]) -> None:
    from dotenv import load_dotenv
    from neo4j import GraphDatabase

    load_dotenv(ROOT / ".env")
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL"),
        auth=(
            os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME") or "neo4j",
            os.getenv("NEO4J_PASSWORD"),
        ),
    )
    database = os.getenv("NEO4J_DATABASE") or "neo4j"
    old_left = 0
    new_ok = 0
    with driver.session(database=database) as session:
        for item in items:
            c_old = session.run(
                """
                MATCH ()-[r]->()
                WHERE coalesce(r.category,'')=$category AND r.source_file ENDS WITH $old_name
                RETURN count(r) AS c
                """,
                category=CATEGORY,
                old_name=item.old_name,
            ).single()["c"]
            c_new = session.run(
                """
                MATCH ()-[r]->()
                WHERE coalesce(r.category,'')=$category AND r.source_file ENDS WITH $new_name
                RETURN count(r) AS c
                """,
                category=CATEGORY,
                new_name=item.new_name,
            ).single()["c"]
            old_left += int(c_old)
            if int(c_new) > 0:
                new_ok += 1
    driver.close()
    _safe_print(f"verify: files_with_new_source={new_ok}/{len(items)} old_path_rels_left={old_left}")


def write_readme_note(next_id: int) -> None:
    note = DOCS_DIR / "README_编号说明.md"
    text = f"""# 熊猫知识文档编号说明

- 命名规则：`熊猫知识_{{NNN}}_{{标题}}.md`
- 当前下一个可用编号：**{next_id:03d}**
- 旧→新映射：`data/curated/panda_knowledge_source_map.json`
- 图谱关系字段 `r.source_file` / 节点 `n.source_files` 已迁移到新路径；映射表保留旧名便于历史溯源。

## 新增文件

1. 使用下一个编号，例如 `熊猫知识_{next_id:03d}_你的主题.md`
2. 抽取入库时 `--category 熊猫知识`
3. 入库后把 `next_id` 更新进映射 JSON（或重跑编号脚本的维护逻辑）
"""
    note.write_text(text, encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="统一编号 docs/熊猫知识 并迁移 Neo4j 溯源")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不改文件/库")
    parser.add_argument("--apply", action="store_true", help="执行改名与迁移")
    parser.add_argument("--skip-neo4j", action="store_true", help="只改本地文件，不改 Neo4j")
    parser.add_argument("--skip-refs", action="store_true", help="不改 curated/reports 引用")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.dry_run and not args.apply:
        _safe_print("请指定 --dry-run 或 --apply")
        return 2

    dry_run = bool(args.dry_run) and not bool(args.apply)
    already = list_already_numbered()
    if already and not dry_run:
        _safe_print(
            f"检测到已有 {len(already)} 个编号文件；为避免重复编号，请确认后手动处理。"
        )
        # 若目录已全部是新命名，则仅刷新映射不重命名
        pending = list_knowledge_docs()
        if not pending:
            _safe_print("没有待编号旧文件，退出。")
            return 0
    else:
        pending = list_knowledge_docs()

    if not pending:
        _safe_print("未找到待编号文件")
        return 1

    items = build_rename_plan(pending)
    next_id = len(items) + 1
    _safe_print(f"plan: {len(items)} files, next_id={next_id:03d}")
    for it in items[:5]:
        _safe_print(f"  {it.index:03d}: {it.old_name} -> {it.new_name}")
    if len(items) > 5:
        _safe_print(f"  ... 共 {len(items)} 个")

    if dry_run:
        write_mapping(items, next_id=next_id)
        _safe_print(f"dry-run mapping written: {MAP_PATH.relative_to(ROOT)}")
        if not args.skip_neo4j:
            migrate_neo4j(items, dry_run=True)
        return 0

    write_mapping(items, next_id=next_id)
    rename_files(items, dry_run=False)
    write_readme_note(next_id)

    stats = {"rels_updated": 0, "nodes_updated": 0}
    if not args.skip_neo4j:
        stats = migrate_neo4j(items, dry_run=False)
        verify_neo4j(items)

    refs = 0
    if not args.skip_refs:
        refs = rewrite_text_refs(items, default_ref_targets())

    # 刷新映射中的执行结果
    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    mapping["applied_at"] = datetime.now().isoformat(timespec="seconds")
    mapping["neo4j_stats"] = stats
    mapping["refs_updated_files"] = refs
    MAP_PATH.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    _safe_print(
        f"done. renamed={len(items)} neo4j_rels={stats.get('rels_updated')} "
        f"neo4j_nodes={stats.get('nodes_updated')} refs_files={refs}"
    )
    _safe_print(f"mapping: {MAP_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
