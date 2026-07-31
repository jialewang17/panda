"""将 sandbox 多轮 gap 补录合并为可进仓库的正式抽取结果 JSON。

产出：
1) data/curated/kb_gap_facts_v1.json  —— 可直接喂给 neo4j_graph_writer
2) docs/panda_gap_focus_questions.json —— 可喂给 panda_history_extractor 二次定向抽取
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox"
OUT_JSON = ROOT / "data" / "curated" / "kb_gap_facts_v1.json"
OUT_FOCUS = ROOT / "docs" / "panda_gap_focus_questions.json"

META_QUESTION_CUES = (
    "网址",
    "官方英文",
    "来源网址",
    "哪个机构发布",
    "来源于哪个机构",
    "栏目主要介绍",
)

PREDICATE_OBJECT_TYPE: Dict[str, str] = {
    "出生于": "Place",
    "分布于": "Place",
    "迁至": "Place",
    "旅居于": "Place",
    "出土于": "Place",
    "父亲为": "Person",
    "母亲为": "Person",
    "育有": "Person",
    "蛔虫感染率为": "Disease",
    "疾病": "Disease",
    "冬眠习性为": "Behavior",
    "爬树原因为": "Behavior",
    "野外冲突原因为": "Behavior",
    "野外相遇行为为": "Behavior",
    "野外双胞胎策略为": "Behavior",
    "野外育幼选择为": "Behavior",
    "气味标记方式为": "Behavior",
    "采食部位顺序为": "Biology",
    "一胎产仔数为": "Biology",
    "尾长为": "Biology",
    "皮肤最厚处为": "Biology",
    "共存鸟类为": "Other",
    "保护级别为": "Other",
}


def _rel_doc_path(source_file: str) -> str:
    text = (source_file or "").replace("\\", "/").strip()
    if not text:
        return ""
    marker = "/docs/"
    lower = text.lower()
    idx = lower.rfind("/docs/")
    if idx >= 0:
        return "docs/" + text[idx + len(marker) :]
    # already relative
    if text.startswith("docs/"):
        return text
    name = Path(text).name
    for cat in ("熊猫知识", "熊猫谣言", "熊猫资料"):
        candidate = ROOT / "docs" / cat / name
        if candidate.exists():
            return f"docs/{cat}/{name}"
    return text


def _infer_object_type(predicate: str, obj: str) -> str:
    if predicate in PREDICATE_OBJECT_TYPE:
        return PREDICATE_OBJECT_TYPE[predicate]
    if any(tok in predicate for tok in ("出生", "分布", "迁", "旅居", "出土")):
        return "Place"
    if any(tok in predicate for tok in ("父亲", "母亲", "育有", "父母")):
        return "Person"
    if any(tok in predicate for tok in ("感染", "疾病", "症状")):
        return "Disease"
    if any(tok in predicate for tok in ("行为", "习性", "冲突", "爬树", "冬眠", "标记")):
        return "Behavior"
    if re.search(r"\d", obj or ""):
        return "Other"
    return "Other"


def _infer_topic(category: str, predicate: str, topic: str) -> str:
    raw = (topic or "").strip()
    if raw and raw != "其他":
        return raw
    if category == "熊猫资料":
        return "个体档案"
    if category == "熊猫谣言":
        return "其他"
    if any(tok in predicate for tok in ("父亲", "母亲", "谱系", "出生", "昵称", "育有")):
        return "个体档案"
    if any(tok in predicate for tok in ("冬眠", "爬树", "冲突", "行为", "双胞胎", "富化", "丰容", "标记", "睡眠")):
        return "行为习性"
    if any(tok in predicate for tok in ("数量", "分布", "山系", "威胁", "栖息")):
        return "生存环境"
    if any(tok in predicate for tok in ("尾长", "皮肤", "产仔", "感染", "采食")):
        return "生理构造"
    if any(tok in predicate for tok in ("发现", "得名", "1972", "历史")):
        return "历史背景"
    if any(tok in predicate for tok in ("天敌", "伴生", "保护级别", "鸟类", "邻居")):
        return "生态关系"
    return "其他"


def _iter_upsert_files() -> List[Path]:
    files = sorted(SANDBOX.glob("gap_fact_upsert*.json"))
    return [p for p in files if p.is_file()]


def _load_raw_triples(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("triples", "facts", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def _normalize_triple(raw: Dict[str, Any], *, default_category: str = "熊猫知识") -> Dict[str, str] | None:
    subject = str(raw.get("subject", "")).strip()
    predicate = str(raw.get("predicate", "")).strip()
    obj = str(raw.get("object") or raw.get("obj") or "").strip()
    if not subject or not predicate or not obj:
        return None
    category = str(raw.get("category", "")).strip() or default_category
    evidence = str(raw.get("evidence", "")).strip()
    source_file = _rel_doc_path(str(raw.get("source_file", "")).strip())
    topic = _infer_topic(category, predicate, str(raw.get("topic", "")).strip())
    object_type = _infer_object_type(predicate, obj)
    if not evidence:
        evidence = f"{subject}{predicate}{obj}"
    if not source_file:
        # still keep triple but mark unknown source
        source_file = "docs/熊猫知识/_curated_gap_facts.md"
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "object_type": object_type,
        "evidence": evidence,
        "source_file": source_file,
        "category": category,
        "topic": topic,
    }


def _question_from_triple(triple: Dict[str, str]) -> str:
    s, p, o = triple["subject"], triple["predicate"], triple["object"]
    if p.endswith("为") or p.endswith("是"):
        return f"{s}的{p.rstrip('为是')}是什么？"
    if "原因为" in p or p.endswith("原因"):
        return f"{s}{p.replace('为', '')}是什么？"
    return f"{s}与“{p}”相关的事实是什么？（参考：{o[:20]}）"


def _is_meta_question(question: str) -> bool:
    return any(cue in question for cue in META_QUESTION_CUES)


def _load_report_focus_groups() -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = defaultdict(list)
    for path in sorted((ROOT / "reports").glob("kb_focus_questions_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for group in payload.get("groups", []):
            if not isinstance(group, dict):
                continue
            doc_path = _rel_doc_path(str(group.get("doc_path", "")).strip())
            questions = group.get("questions", [])
            if not doc_path or not isinstance(questions, list):
                continue
            for q in questions:
                text = str(q).strip()
                if text and not _is_meta_question(text) and text not in mapping[doc_path]:
                    mapping[doc_path].append(text)
    return mapping


def build_curated_payload(triples: Iterable[Dict[str, str]]) -> Dict[str, Any]:
    by_source: Dict[Tuple[str, str, str], List[Dict[str, str]]] = defaultdict(list)
    seen: set[Tuple[str, str, str, str]] = set()
    for triple in triples:
        key = (
            triple["subject"],
            triple["predicate"],
            triple["object"],
            triple["source_file"],
        )
        if key in seen:
            continue
        seen.add(key)
        group_key = (triple["source_file"], triple["category"], triple["topic"])
        by_source[group_key].append(
            {
                "subject": triple["subject"],
                "predicate": triple["predicate"],
                "object": triple["object"],
                "object_type": triple["object_type"],
                "evidence": triple["evidence"],
            }
        )

    files: List[Dict[str, Any]] = []
    for (source_file, category, topic), rels in sorted(by_source.items(), key=lambda x: x[0][0]):
        abs_source = str((ROOT / source_file).resolve()) if source_file.startswith("docs/") else source_file
        files.append(
            {
                "source_file": abs_source,
                "source_file_rel": source_file,
                "topic": topic,
                "category": category,
                "extracted": {
                    "topic": topic,
                    "relation_triples": rels,
                },
            }
        )

    return {
        "mode": "curated_gap_facts",
        "description": "多轮挖缺口定向补录沉淀；可直接 neo4j_graph_writer 增量写入。",
        "category": "",
        "rumor_mode": False,
        "files_count": len(files),
        "files": files,
        "summary": {
            "triple_count": sum(len(f["extracted"]["relation_triples"]) for f in files),
            "source_count": len(files),
        },
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "schema_note": "compatible with tools/neo4j_graph_writer._load_triples",
    }


def build_focus_questions(
    triples: List[Dict[str, str]],
    report_groups: Dict[str, List[str]],
) -> Dict[str, Any]:
    groups_map: Dict[str, List[str]] = defaultdict(list)
    for doc, questions in report_groups.items():
        for q in questions:
            if q not in groups_map[doc]:
                groups_map[doc].append(q)

    for triple in triples:
        doc = triple["source_file"]
        if not doc.startswith("docs/"):
            continue
        q = _question_from_triple(triple)
        if _is_meta_question(q):
            continue
        if q not in groups_map[doc]:
            groups_map[doc].append(q)

    groups = [
        {
            "doc_path": doc,
            "doc_title": Path(doc).stem,
            "questions": questions,
        }
        for doc, questions in sorted(groups_map.items())
        if questions
    ]
    return {
        "version": "1.0",
        "description": "挖缺口补录沉淀的定向抽取问题清单（已过滤 URL/栏目元数据题）",
        "groups": groups,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    upsert_files = _iter_upsert_files()
    if not upsert_files:
        raise SystemExit("未找到 sandbox/gap_fact_upsert*.json，无法沉淀")

    normalized: List[Dict[str, str]] = []
    for path in upsert_files:
        for raw in _load_raw_triples(path):
            item = _normalize_triple(raw)
            if item:
                normalized.append(item)

    # dedupe by spo + source
    deduped: List[Dict[str, str]] = []
    seen: set[Tuple[str, str, str, str]] = set()
    for item in normalized:
        key = (item["subject"], item["predicate"], item["object"], item["source_file"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    payload = build_curated_payload(deduped)
    focus = build_focus_questions(deduped, _load_report_focus_groups())

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    OUT_FOCUS.write_text(json.dumps(focus, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"upsert_files={len(upsert_files)}")
    print(f"triples={payload['summary']['triple_count']} sources={payload['summary']['source_count']}")
    print(f"wrote {OUT_JSON}")
    print(f"focus_groups={len(focus['groups'])} -> {OUT_FOCUS}")


if __name__ == "__main__":
    main()
