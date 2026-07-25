"""将熊猫抽取结果写入 Neo4j（含 Aura）并提供 CLI 入口。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from langchain_core.tools import tool

from utils.env_loader import get_env_config
from utils.path import get_project_root

PANDA_CANONICAL_NAME = "大熊猫"
ALLOWED_OBJECT_TYPES = {
    "Species",
    "Habitat",
    "Biology",
    "Behavior",
    "Disease",
    "Treatment",
    "Person",
    "Place",
    "Alias",
    "Other",
}
OBJECT_TYPE_LABEL_MAP = {
    "Species": "物种",
    "Habitat": "栖息地",
    "Biology": "生理",
    "Behavior": "行为",
    "Disease": "疾病",
    "Treatment": "治疗",
    "Person": "人物",
    "Place": "地点",
    "Alias": "别名",
    "Other": "其他",
}


@dataclass
class Neo4jConfig:
    """Neo4j 连接配置。"""

    uri: str
    username: str
    password: str
    database: str


def _normalize_rel_type(predicate: str) -> str:
    normalized = re.sub(r"\s+", "_", predicate.strip())
    normalized = normalized.replace("`", "")
    normalized = re.sub(r"[^\w\u4e00-\u9fff_]+", "_", normalized, flags=re.UNICODE)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized if normalized else "相关"


def _normalize_label(label: str, default: str = "Entity") -> str:
    cleaned = re.sub(r"\s+", "", label.strip())
    cleaned = cleaned.replace("`", "")
    cleaned = re.sub(r"[^\w\u4e00-\u9fff_]+", "", cleaned, flags=re.UNICODE)
    if not cleaned:
        return default
    if cleaned[0].isdigit() and default:
        cleaned = f"{default}_{cleaned}"
    return cleaned


def _load_neo4j_config(database_override: str = "") -> Neo4jConfig:
    """从环境变量加载 Neo4j 配置。"""
    get_env_config()  # 触发 .env 加载

    uri = (os.getenv("NEO4J_URI") or "").strip()
    username = (os.getenv("NEO4J_USERNAME") or "").strip()
    password = (os.getenv("NEO4J_PASSWORD") or "").strip()
    database = (database_override or os.getenv("NEO4J_DATABASE") or "neo4j").strip()

    missing: List[str] = []
    if not uri:
        missing.append("NEO4J_URI")
    if not username:
        missing.append("NEO4J_USERNAME")
    if not password:
        missing.append("NEO4J_PASSWORD")
    if missing:
        raise ValueError(f"缺少 Neo4j 环境变量: {', '.join(missing)}")
    return Neo4jConfig(uri=uri, username=username, password=password, database=database)


def _resolve_result_json(result_json: str, run_output_dir: str) -> Path:
    """解析结果 JSON 文件路径。"""
    project_root = get_project_root()
    if result_json.strip():
        path = (project_root / result_json).resolve()
        if not path.exists():
            raise FileNotFoundError(f"结果 JSON 不存在: {path}")
        return path
    if run_output_dir.strip():
        run_dir = (project_root / run_output_dir).resolve()
        if not run_dir.exists() or not run_dir.is_dir():
            raise NotADirectoryError(f"运行目录不存在: {run_dir}")
        json_files = sorted(run_dir.glob("*.json"))
        if len(json_files) != 1:
            raise FileNotFoundError(f"运行目录下期望恰好 1 个 JSON，实际 {len(json_files)} 个: {run_dir}")
        return json_files[0]
    raise ValueError("请提供 result_json 或 run_output_dir 其中之一。")


def _load_triples(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """从抽取结果 JSON 中加载三元组列表。"""
    triples: List[Dict[str, str]] = []
    payload_category = str(payload.get("category", "")).strip()
    for file_item in payload.get("files", []):
        extracted = file_item.get("extracted", {})
        source_file = str(file_item.get("source_file", ""))
        topic = str(extracted.get("topic", file_item.get("topic", ""))).strip()
        category = str(file_item.get("category", payload_category)).strip()
        for triple in extracted.get("relation_triples", []):
            if not isinstance(triple, dict):
                continue
            subject = str(triple.get("subject", "")).strip()
            predicate = str(triple.get("predicate", "")).strip()
            obj = str(triple.get("object", "")).strip()
            if not subject or not predicate or not obj:
                continue
            object_type = str(triple.get("object_type", "Other")).strip() or "Other"
            if object_type not in ALLOWED_OBJECT_TYPES:
                object_type = "Other"
            evidence = str(triple.get("evidence", "")).strip()
            triples.append(
                {
                    "subject": subject,
                    "predicate": predicate,
                    "object": obj,
                    "object_type": object_type,
                    "evidence": evidence,
                    "source_file": source_file,
                    "topic": topic,
                    "category": category,
                }
            )
    return triples


def _iter_triples_for_write(triples: Iterable[Dict[str, str]]) -> Iterable[Dict[str, str]]:
    """归一化三元组，生成可写入 Neo4j 的条目。"""
    for triple in triples:
        subject = triple["subject"]
        obj = triple["object"]
        predicate = triple["predicate"]
        object_type = triple["object_type"]
        yield {
            "subject": subject,
            "subject_label": "熊猫类" if subject == PANDA_CANONICAL_NAME else "实体",
            "object": obj,
            "object_label": _normalize_label(OBJECT_TYPE_LABEL_MAP.get(object_type, "其他"), default="实体"),
            "rel_type": _normalize_rel_type(predicate),
            "predicate": predicate,
            "evidence": triple["evidence"],
            "source_file": triple["source_file"],
            "topic": triple.get("topic", ""),
            "category": triple.get("category", ""),
        }


def _clear_graph(driver: Any, database: str) -> None:
    with driver.session(database=database) as session:
        session.run("MATCH (n) DETACH DELETE n")


def _clear_category_relations(driver: Any, database: str, category: str) -> int:
    """仅删除指定栏目的关系，保留其他栏目图谱。"""
    with driver.session(database=database) as session:
        result = session.run(
            "MATCH ()-[r]->() WHERE coalesce(r.category, '') = $category "
            "WITH r DELETE r RETURN count(*) AS deleted",
            category=category,
        )
        record = result.single()
        return int(record["deleted"]) if record else 0


def _load_graph_database_class() -> Any:
    """延迟导入 neo4j 驱动，避免 CLI --help 时出现第三方导入噪音。"""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            from neo4j import GraphDatabase as _GraphDatabase
    except Exception as exc:
        raise RuntimeError(
            "无法导入 neo4j 驱动，请先安装依赖（pip install neo4j），"
            "并检查本地 numpy/pandas 兼容性。"
        ) from exc
    return _GraphDatabase


def _write_graph(driver: Any, database: str, triples: List[Dict[str, str]]) -> Dict[str, int]:
    node_ids: set[str] = set()
    rel_count = 0
    with driver.session(database=database) as session:
        for item in _iter_triples_for_write(triples):
            node_ids.add(item["subject"])
            node_ids.add(item["object"])
            subject_label = item["subject_label"].replace("`", "``")
            object_label = item["object_label"].replace("`", "``")
            rel_type = item["rel_type"].replace("`", "``")
            query = (
                f"MERGE (a:`{subject_label}` {{id:$subject}}) "
                "SET a.name=$subject, "
                "a.evidence_list = CASE "
                "WHEN $evidence = '' THEN coalesce(a.evidence_list, []) "
                "WHEN $evidence IN coalesce(a.evidence_list, []) THEN coalesce(a.evidence_list, []) "
                "ELSE coalesce(a.evidence_list, []) + $evidence END, "
                "a.source_files = CASE "
                "WHEN $source_file = '' THEN coalesce(a.source_files, []) "
                "WHEN $source_file IN coalesce(a.source_files, []) THEN coalesce(a.source_files, []) "
                "ELSE coalesce(a.source_files, []) + $source_file END "
                f"MERGE (b:`{object_label}` {{id:$obj}}) "
                "SET b.name=$obj, "
                "b.evidence_list = CASE "
                "WHEN $evidence = '' THEN coalesce(b.evidence_list, []) "
                "WHEN $evidence IN coalesce(b.evidence_list, []) THEN coalesce(b.evidence_list, []) "
                "ELSE coalesce(b.evidence_list, []) + $evidence END, "
                "b.source_files = CASE "
                "WHEN $source_file = '' THEN coalesce(b.source_files, []) "
                "WHEN $source_file IN coalesce(b.source_files, []) THEN coalesce(b.source_files, []) "
                "ELSE coalesce(b.source_files, []) + $source_file END "
                f"MERGE (a)-[r:`{rel_type}`]->(b) "
                "SET r.predicate=$predicate, "
                "r.evidence=$evidence, "
                "r.source_file=$source_file, "
                "r.topic=$topic, "
                "r.category=$category, "
                "r.last_updated=datetime()"
            )
            session.run(
                query,
                subject=item["subject"],
                obj=item["object"],
                predicate=item["predicate"],
                evidence=item["evidence"],
                source_file=item["source_file"],
                topic=item.get("topic", ""),
                category=item.get("category", ""),
            )
            rel_count += 1
    return {"nodes_merged": len(node_ids), "relations_merged": rel_count}


@tool
def neo4j_graph_writer(
    result_json: str = "",
    run_output_dir: str = "",
    database: str = "",
    clear_before_write: bool = False,
    clear_category: str = "",
    dry_run: bool = False,
) -> str:
    """
    描述：将 panda_history_extractor 生成的结果 JSON 写入 Neo4j（支持 Aura）。
    输入：
    - result_json：结果 JSON 路径（相对项目根），优先于 run_output_dir。
    - run_output_dir：运行目录路径（相对项目根），目录下应仅有一个结果 JSON。
    - database：Neo4j 数据库名，默认取 NEO4J_DATABASE 或 neo4j。
    - clear_before_write：写入前是否清空图谱。
    - clear_category：写入前仅删除该栏目关系（如 熊猫谣言），优先于全量清空以外的局部替换。
    - dry_run：仅解析和统计，不执行写入。
    输出：JSON 字符串，包含写入统计与目标文件。
    """
    result: Dict[str, Any] = {
        "ok": False,
        "result_json_path": "",
        "triples_count": 0,
        "nodes_merged": 0,
        "relations_merged": 0,
        "database": "",
        "dry_run": dry_run,
        "clear_before_write": clear_before_write,
        "clear_category": clear_category,
        "category_relations_deleted": 0,
    }
    try:
        json_path = _resolve_result_json(result_json=result_json, run_output_dir=run_output_dir)
        result["result_json_path"] = str(json_path)
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        triples = _load_triples(payload)
        result["triples_count"] = len(triples)

        config = _load_neo4j_config(database_override=database)
        result["database"] = config.database
        result["neo4j_uri"] = config.uri

        if dry_run:
            result["ok"] = True
            return json.dumps(result, ensure_ascii=False)

        graph_database = _load_graph_database_class()
        driver = graph_database.driver(config.uri, auth=(config.username, config.password))
        try:
            driver.verify_connectivity()
            if clear_before_write:
                _clear_graph(driver=driver, database=config.database)
            elif clear_category.strip():
                deleted = _clear_category_relations(
                    driver=driver,
                    database=config.database,
                    category=clear_category.strip(),
                )
                result["category_relations_deleted"] = deleted
            write_stats = _write_graph(driver=driver, database=config.database, triples=triples)
            result.update(write_stats)
            result["ok"] = True
        finally:
            driver.close()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return json.dumps(result, ensure_ascii=False)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将熊猫知识抽取结果写入 Neo4j Aura。")
    parser.add_argument(
        "--result-json",
        default="",
        help="抽取结果 JSON 路径（相对项目根），优先于 --run-output-dir。",
    )
    parser.add_argument(
        "--run-output-dir",
        default="",
        help="抽取运行结果目录（相对项目根），目录下需有唯一 JSON 文件。",
    )
    parser.add_argument(
        "--database",
        default="",
        help="Neo4j 数据库名，默认读取 NEO4J_DATABASE 或 neo4j。",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="写入前清空数据库内现有图谱。",
    )
    parser.add_argument(
        "--clear-category",
        default="",
        help="写入前仅删除指定栏目关系（如 熊猫谣言），保留其他栏目。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅解析并统计，不执行写入。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出完整 JSON；默认输出摘要。",
    )
    return parser


def main() -> int:
    parser = _build_cli_parser()
    args = parser.parse_args()
    result_json = neo4j_graph_writer.invoke(
        {
            "result_json": args.result_json,
            "run_output_dir": args.run_output_dir,
            "database": args.database,
            "clear_before_write": args.clear,
            "clear_category": args.clear_category,
            "dry_run": args.dry_run,
        }
    )
    try:
        parsed = json.loads(result_json)
    except Exception:
        print(result_json)
        return 1

    if args.verbose:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    else:
        print("=== neo4j_graph_writer ===")
        print(f"status: {'OK' if parsed.get('ok') else 'ERROR'}")
        print(f"database: {parsed.get('database', '')}")
        print(f"triples_count: {parsed.get('triples_count', 0)}")
        print(f"nodes_merged: {parsed.get('nodes_merged', 0)}")
        print(f"relations_merged: {parsed.get('relations_merged', 0)}")
        if parsed.get("clear_category"):
            print(f"clear_category: {parsed.get('clear_category')}")
            print(f"category_relations_deleted: {parsed.get('category_relations_deleted', 0)}")
        print(f"result_json_path: {parsed.get('result_json_path', '')}")
        if parsed.get("error"):
            print(f"error: {parsed['error']}")
    return 0 if parsed.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
