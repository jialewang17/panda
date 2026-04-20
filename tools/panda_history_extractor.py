"""熊猫知识抽取工具（并发 + 结构化输出 + Neo4j 导出）。"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import re
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from model.factory import get_element_extraction_model
from utils.path import ensure_task_dirs, get_project_root
from utils.task_context import get_task_id

DEFAULT_PANDA_DOCS_DIR = "docs/熊猫知识"
DEFAULT_MD_PATH = "docs/熊猫知识/【熊猫知识】大熊猫历史 - 成都大熊猫繁育研究基地.md"
PANDA_CANONICAL_NAME = "大熊猫"
PANDA_ALIASES = {"熊猫", "猫熊", "黑白熊", "白熊", "花熊", "食铁兽"}
TOPIC_CHOICES = ["生态关系", "生理构造", "疾病健康", "行为习性", "历史背景", "生存环境", "个体档案", "其他"]


def _configure_warning_filters() -> None:
    """过滤第三方库的已知噪音告警，保留真实异常和关键告警。"""
    warnings.filterwarnings(
        "ignore",
        message=r"Pydantic serializer warnings:.*",
        category=UserWarning,
        module=r"pydantic\.main",
    )


class TopicResult(BaseModel):
    topic: Literal["生态关系", "生理构造", "疾病健康", "行为习性", "历史背景", "生存环境", "个体档案", "其他"] = "其他"
    reason: str = ""


class RelationTriple(BaseModel):
    subject: str = Field(description="实体1")
    predicate: str = Field(description="关系")
    object: str = Field(description="实体2")
    object_type: Literal["Species", "Habitat", "Biology", "Behavior", "Disease", "Treatment", "Person", "Place", "Alias", "Other"] = "Other"
    evidence: str = ""


class ExtractionSchema(BaseModel):
    topic: str = "其他"
    times: List[str] = Field(default_factory=list)
    person_entities: List[str] = Field(default_factory=list)
    place_entities: List[str] = Field(default_factory=list)
    panda_aliases: List[str] = Field(default_factory=list)
    companion_species: List[str] = Field(default_factory=list)
    predator_species: List[str] = Field(default_factory=list)
    habitats: List[str] = Field(default_factory=list)
    biology_terms: List[str] = Field(default_factory=list)
    behavior_terms: List[str] = Field(default_factory=list)
    disease_terms: List[str] = Field(default_factory=list)
    treatment_terms: List[str] = Field(default_factory=list)
    lifecycle_stages: List[str] = Field(default_factory=list)
    reproduction_terms: List[str] = Field(default_factory=list)
    communication_terms: List[str] = Field(default_factory=list)
    threat_factors: List[str] = Field(default_factory=list)
    food_items: List[str] = Field(default_factory=list)
    food_parts: List[str] = Field(default_factory=list)
    population_metrics: List[str] = Field(default_factory=list)
    taxonomy_terms: List[str] = Field(default_factory=list)
    relation_triples: List[RelationTriple] = Field(default_factory=list)


def _strip_markdown_noise(raw_text: str) -> str:
    text = raw_text
    if text.startswith("---"):
        end_idx = text.find("\n---", 3)
        if end_idx != -1:
            text = text[end_idx + 4 :]
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", text)
    text = text.replace("返回顶部", "")
    return text.strip()


def _clean_label(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _slugify(value: str) -> str:
    """将文本转为安全文件名片段（保留中文，提升可读性）。"""
    cleaned = _clean_label(value)
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", cleaned)
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned[:60] if cleaned else "unknown"


def _as_list(values: List[str]) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_label(str(value))
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def _canonical_panda_name(name: str, alias_map: Dict[str, str]) -> str:
    cleaned = _clean_label(name)
    if cleaned in alias_map:
        return alias_map[cleaned]
    if cleaned in PANDA_ALIASES:
        return PANDA_CANONICAL_NAME
    return cleaned


def _normalize_rel_type(predicate: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", predicate.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized.upper() if normalized else "RELATED_TO"


def _build_topic_prompt(text: str) -> str:
    return f"""
你是熊猫知识分类助手。请判断文章主题类别：
可选值：{"、".join(TOPIC_CHOICES)}

文章正文：
{text}
""".strip()


def _build_extraction_prompt(topic: str, text: str) -> str:
    topic_rules: Dict[str, str] = {
        "生态关系": "重点抽取伴生动物、天敌、共同栖息环境及其关系。",
        "生理构造": "重点抽取解剖/生理结构和与生理相关行为。",
        "疾病健康": "重点抽取疾病、治疗方式、生理指标及关系。",
        "行为习性": "重点抽取行为习性、行为发生环境及关系。",
        "历史背景": "重点抽取时间、人物、地点、熊猫曾用名及关系。",
        "生存环境": "重点抽取地理环境、植被、海拔、气候特征及关系。",
        "个体档案": "重点抽取个体名、时间、地点和关键事件关系。",
        "其他": "尽可能完整抽取实体与主谓宾关系。",
    }
    rules = topic_rules.get(topic, topic_rules["其他"])
    return f"""
你是熊猫知识图谱抽取助手。文章主题是：{topic}
任务要求：
1. 仅基于原文抽取，不要杜撰。
2. 输出字段必须完整（缺失填空数组）。
3. relation_triples 每条包含 subject/predicate/object/object_type/evidence。
4. object_type 只能是 Species/Habitat/Biology/Behavior/Disease/Treatment/Person/Place/Alias/Other。
5. 优先抽取“可检索、可回答”的事实：时间、数量、范围、因果、条件、阶段、行为机制。
6. evidence 要尽量保留原文关键片段（短句级），不要只写概括。
7. 尽量避免“它们/这种/这里”等代词作为实体；主语优先使用明确名词（如“大熊猫/金丝猴/秦岭山系”等）。

通用覆盖清单（能抽则抽）：
- 分类与称谓：taxonomy_terms、panda_aliases、relation_triples（如 属于/曾用名/定名）
- 种群与分布：population_metrics、place_entities、habitats、relation_triples（如 分布于/历史分布于/数量）
- 生长繁殖：lifecycle_stages、reproduction_terms、relation_triples（如 幼年阶段时间范围/性成熟时间/妊娠期）
- 交流行为：behavior_terms、communication_terms、relation_triples（如 气味标记/咩叫/触发条件）
- 食物消化：food_items、food_parts、biology_terms、relation_triples（如 主食/优选采食部位/消化特征）
- 疾病威胁：disease_terms、treatment_terms、threat_factors、relation_triples（如 由_引起/致危因素/影响因素）

主题规则：{rules}

文章正文：
{text}
""".strip()


def _infer_topic_from_filename(file_path: Path) -> str:
    """根据文件名做轻量主题兜底，减少 topic 误判。"""
    name = file_path.name
    mapping: List[Tuple[List[str], str]] = [
        (["天敌", "伴生", "朋友"], "生态关系"),
        (["外形", "消化", "进食", "食物"], "生理构造"),
        (["疾病"], "疾病健康"),
        (["行为", "气味标记"], "行为习性"),
        (["历史", "认识"], "历史背景"),
        (["生存环境", "分布", "致危"], "生存环境"),
        (["育幼", "生长发育", "繁殖"], "个体档案"),
    ]
    for keys, topic in mapping:
        if any(key in name for key in keys):
            return topic
    return "其他"


_ENTITY_STOPWORDS = {
    "它们",
    "它",
    "其",
    "该物种",
    "这种",
    "这些",
    "这个",
    "这一",
    "这里",
    "那里",
    "其中",
    "我们",
}


_TOPIC_PREDICATE_WHITELIST: Dict[str, set[str]] = {
    "生态关系": {
        "伴生动物",
        "天敌",
        "捕食对象",
        "共同栖息环境",
        "提供警戒支持",
        "互动对象",
    },
    "生理构造": {
        "具有",
        "生理功能",
        "消化特征",
        "皮肤厚度分布特征",
        "食用",
        "主食",
        "优选采食部位",
        "采食（季节性）",
        "垂直迁移至",
    },
    "疾病健康": {
        "疾病",
        "由...引起",
        "症状",
        "影响因素",
        "用于减少",
        "促进表现",
    },
    "行为习性": {
        "具有行为",
        "行为策略",
        "触发条件",
        "在...环境中发生",
        "包含子行为",
        "属于子行为",
        "气味标记",
        "声音交流",
        "分泌",
    },
    "历史背景": {
        "发现者",
        "发现时间",
        "首次标本采集时间",
        "科学鉴定者",
        "定名",
        "曾用名",
        "标本运送地",
        "鉴定机构所在地",
        "记载于",
    },
    "生存环境": {
        "分布于",
        "历史分布于",
        "海拔范围",
        "气候特征",
        "地形类型",
        "栖息于",
        "影响因素",
        "致危因素",
        "位于",
    },
    "个体档案": {
        "幼年阶段时间范围",
        "亚成年阶段时间范围",
        "成年阶段起始时间",
        "独立生活起始时间",
        "性成熟时间",
        "妊娠期范围",
        "出生时间",
        "出生体重范围",
        "体重范围",
        "体长范围",
        "开始长出恒牙时间",
        "繁殖模式",
    },
}

_PREDICATE_TEMPLATES: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(属于|属|分类为)$"), "属于"),
    (re.compile(r"^(伴生|近邻|朋友)$"), "伴生动物"),
    (re.compile(r"^(天敌|敌害|为敌)$"), "天敌"),
    (re.compile(r"^(捕食|袭击对象|捕食对象)$"), "捕食对象"),
    (re.compile(r"^(共同栖息|同域共栖|共同栖息环境)$"), "共同栖息环境"),
    (re.compile(r"^(栖息于|栖息在|生活在)$"), "栖息于"),
    (re.compile(r"^(分布于|分布在)$"), "分布于"),
    (re.compile(r"^(历史分布于|曾分布于)$"), "历史分布于"),
    (re.compile(r"^(海拔范围|海拔)$"), "海拔范围"),
    (re.compile(r"^(气候特征|气候)$"), "气候特征"),
    (re.compile(r"^(地形类型|地形)$"), "地形类型"),
    (re.compile(r"^(致危因素|威胁因素|危险因素)$"), "致危因素"),
    (re.compile(r"^(食用|采食|吃)$"), "食用"),
    (re.compile(r"^(主食)$"), "主食"),
    (re.compile(r"^(优选采食部位|优先采食部位)$"), "优选采食部位"),
    (re.compile(r"^(采食（?季节性）?|季节性采食)$"), "采食（季节性）"),
    (re.compile(r"^(垂直迁移至|垂直迁移)$"), "垂直迁移至"),
    (re.compile(r"^(消化特征|消化特点)$"), "消化特征"),
    (re.compile(r"^(由.*引起|病因)$"), "由...引起"),
    (re.compile(r"^(症状|表现症状)$"), "症状"),
    (re.compile(r"^(具有行为|行为特点|行为)$"), "具有行为"),
    (re.compile(r"^(行为策略)$"), "行为策略"),
    (re.compile(r"^(触发条件)$"), "触发条件"),
    (re.compile(r"^(在.*环境中发生)$"), "在...环境中发生"),
    (re.compile(r"^(包含子行为)$"), "包含子行为"),
    (re.compile(r"^(属于子行为)$"), "属于子行为"),
    (re.compile(r"^(气味标记|标记)$"), "气味标记"),
    (re.compile(r"^(声音交流|叫声交流|咩叫)$"), "声音交流"),
    (re.compile(r"^(分泌)$"), "分泌"),
    (re.compile(r"^(发现者)$"), "发现者"),
    (re.compile(r"^(发现时间)$"), "发现时间"),
    (re.compile(r"^(首次标本采集时间)$"), "首次标本采集时间"),
    (re.compile(r"^(科学鉴定者|鉴定者)$"), "科学鉴定者"),
    (re.compile(r"^(定名|命名)$"), "定名"),
    (re.compile(r"^(曾用名|别名)$"), "曾用名"),
    (re.compile(r"^(标本运送地)$"), "标本运送地"),
    (re.compile(r"^(鉴定机构所在地)$"), "鉴定机构所在地"),
    (re.compile(r"^(记载于|记载者)$"), "记载于"),
    (re.compile(r"^(幼年阶段时间范围|幼年阶段)$"), "幼年阶段时间范围"),
    (re.compile(r"^(亚成年阶段时间范围|亚成年阶段)$"), "亚成年阶段时间范围"),
    (re.compile(r"^(成年阶段起始时间|成年阶段)$"), "成年阶段起始时间"),
    (re.compile(r"^(独立生活起始时间|独立生活时间)$"), "独立生活起始时间"),
    (re.compile(r"^(性成熟时间|性成熟)$"), "性成熟时间"),
    (re.compile(r"^(妊娠期范围|妊娠期)$"), "妊娠期范围"),
    (re.compile(r"^(出生时间)$"), "出生时间"),
    (re.compile(r"^(出生体重范围|出生体重)$"), "出生体重范围"),
    (re.compile(r"^(体重范围|体重)$"), "体重范围"),
    (re.compile(r"^(体长范围|体长)$"), "体长范围"),
    (re.compile(r"^(开始长出恒牙时间|长出恒牙时间)$"), "开始长出恒牙时间"),
    (re.compile(r"^(繁殖模式)$"), "繁殖模式"),
]


def _is_meaningful_entity(token: str) -> bool:
    cleaned = _clean_label(token)
    if not cleaned:
        return False
    if cleaned in _ENTITY_STOPWORDS:
        return False
    if len(cleaned) == 1 and cleaned not in {"熊", "猫"}:
        return False
    return True


def _canonicalize_predicate(topic: str, predicate: str, evidence: str) -> Optional[str]:
    """将谓词归一到主题模板；对噪音谓词返回 None。"""
    cleaned = _clean_label(predicate).strip("，。；：,.;: ")
    if not cleaned:
        return None
    canonical = cleaned
    for pattern, mapped in _PREDICATE_TEMPLATES:
        if pattern.search(cleaned):
            canonical = mapped
            break

    whitelist = _TOPIC_PREDICATE_WHITELIST.get(topic, set())
    if canonical in whitelist:
        return canonical

    # 非白名单关系走宽松兜底：只保留证据清晰、且谓词不是极短噪音词的条目
    if len(canonical) >= 3 and len(_clean_label(evidence)) >= 8:
        return canonical
    return None


async def _extract_one_markdown_v2_async(
    file_path: Path,
    topic_chain: Any,
    extraction_chain_factory: Any,
    semaphore: asyncio.Semaphore,
) -> Dict[str, Any]:
    async with semaphore:
        raw_text = file_path.read_text(encoding="utf-8")
        markdown_text = _strip_markdown_noise(raw_text)

        topic_result: TopicResult = await topic_chain.ainvoke([HumanMessage(content=_build_topic_prompt(markdown_text))])
        topic = topic_result.topic if topic_result.topic in TOPIC_CHOICES else "其他"
        filename_topic = _infer_topic_from_filename(file_path)
        if topic == "其他" and filename_topic != "其他":
            topic = filename_topic

        extraction_chain = extraction_chain_factory()
        extraction_result: ExtractionSchema = await extraction_chain.ainvoke(
            [HumanMessage(content=_build_extraction_prompt(topic, markdown_text))]
        )

        extracted: Dict[str, Any] = {
            "topic": topic,
            "times": _as_list(extraction_result.times),
            "person_entities": _as_list(extraction_result.person_entities),
            "place_entities": _as_list(extraction_result.place_entities),
            "panda_aliases": _as_list(extraction_result.panda_aliases),
            "companion_species": _as_list(extraction_result.companion_species),
            "predator_species": _as_list(extraction_result.predator_species),
            "habitats": _as_list(extraction_result.habitats),
            "biology_terms": _as_list(extraction_result.biology_terms),
            "behavior_terms": _as_list(extraction_result.behavior_terms),
            "disease_terms": _as_list(extraction_result.disease_terms),
            "treatment_terms": _as_list(extraction_result.treatment_terms),
            "lifecycle_stages": _as_list(extraction_result.lifecycle_stages),
            "reproduction_terms": _as_list(extraction_result.reproduction_terms),
            "communication_terms": _as_list(extraction_result.communication_terms),
            "threat_factors": _as_list(extraction_result.threat_factors),
            "food_items": _as_list(extraction_result.food_items),
            "food_parts": _as_list(extraction_result.food_parts),
            "population_metrics": _as_list(extraction_result.population_metrics),
            "taxonomy_terms": _as_list(extraction_result.taxonomy_terms),
            "relation_triples": [triple.model_dump() for triple in extraction_result.relation_triples],
        }

        legacy = {
            "时间": extracted["times"],
            "人物": extracted["person_entities"],
            "地点": extracted["place_entities"],
            "熊猫曾用名": extracted["panda_aliases"],
        }
        return {"source_file": str(file_path), "topic": topic, "extracted": extracted, "legacy": legacy}


def _build_alias_map(files: List[Dict[str, Any]]) -> Dict[str, str]:
    alias_map: Dict[str, str] = {PANDA_CANONICAL_NAME: PANDA_CANONICAL_NAME}
    for alias in PANDA_ALIASES:
        alias_map[alias] = PANDA_CANONICAL_NAME
    for item in files:
        extracted = item.get("extracted", {})
        for alias in extracted.get("panda_aliases", []):
            cleaned = _clean_label(str(alias))
            if cleaned:
                alias_map[cleaned] = PANDA_CANONICAL_NAME
    return alias_map


def _normalize_triples(files: List[Dict[str, Any]], alias_map: Dict[str, str]) -> None:
    for item in files:
        topic = str(item.get("topic", "其他")).strip() or "其他"
        triples = item.get("extracted", {}).get("relation_triples", [])
        if not isinstance(triples, list):
            continue
        deduped: List[Dict[str, Any]] = []
        seen: set[Tuple[str, str, str, str]] = set()
        for triple in triples:
            if not isinstance(triple, dict):
                continue
            subject = _clean_label(str(triple.get("subject", "")))
            raw_predicate = _clean_label(str(triple.get("predicate", ""))).strip("，。；：,.;: ")
            obj = _clean_label(str(triple.get("object", "")))
            evidence = _clean_label(str(triple.get("evidence", "")))
            if not _is_meaningful_entity(subject) or not _is_meaningful_entity(obj):
                continue
            predicate = _canonicalize_predicate(topic, raw_predicate, evidence)
            if not predicate:
                continue
            if subject:
                triple["subject"] = _canonical_panda_name(subject, alias_map)
            if obj:
                triple["object"] = _canonical_panda_name(obj, alias_map)
            triple["predicate"] = predicate
            triple["evidence"] = evidence
            dedupe_key = (
                str(triple.get("subject", "")),
                str(triple.get("predicate", "")),
                str(triple.get("object", "")),
                str(triple.get("evidence", "")),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(triple)
        item.get("extracted", {})["relation_triples"] = deduped


def _build_summary(files: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    keys = [
        "times",
        "person_entities",
        "place_entities",
        "panda_aliases",
        "companion_species",
        "predator_species",
        "habitats",
        "biology_terms",
        "behavior_terms",
        "disease_terms",
        "treatment_terms",
        "lifecycle_stages",
        "reproduction_terms",
        "communication_terms",
        "threat_factors",
        "food_items",
        "food_parts",
        "population_metrics",
        "taxonomy_terms",
    ]
    sets: Dict[str, set[str]] = {k: set() for k in keys}
    for item in files:
        extracted = item.get("extracted", {})
        for key in keys:
            values = extracted.get(key, [])
            if isinstance(values, list):
                sets[key].update(str(v).strip() for v in values if str(v).strip())
    return {key: sorted(values) for key, values in sets.items()}


def _build_output_stem(
    *,
    mode: str,
    source_file: str,
    source_dir: str,
    files_count: int,
    timestamp: datetime,
) -> str:
    """构建统一输出命名主干。"""
    ts = timestamp.strftime("%Y%m%d_%H%M%S")
    if mode == "single_file":
        raw_source = Path(source_file).stem or "single"
        source = _slugify(raw_source)
    else:
        raw_source = Path(source_dir).name or "batch"
        source = _slugify(raw_source)
    return f"panda_extract_{ts}_{mode}_{source}_n{files_count}"


def _generate_neo4j_exports(files: List[Dict[str, Any]], output_dir: Path, output_stem: str) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes_csv_path = output_dir / f"{output_stem}_nodes.csv"
    rels_csv_path = output_dir / f"{output_stem}_rels.csv"
    cypher_path = output_dir / f"{output_stem}_graph.cypher"

    nodes: set[Tuple[str, str]] = set()
    rels: List[Tuple[str, str, str, str, str]] = []
    cypher_lines: List[str] = ["// Auto-generated by panda_history_extractor"]

    for item in files:
        source_file = item.get("source_file", "")
        triples = item.get("extracted", {}).get("relation_triples", [])
        for triple in triples:
            if not isinstance(triple, dict):
                continue
            subject = _clean_label(str(triple.get("subject", "")))
            predicate = _clean_label(str(triple.get("predicate", "")))
            obj = _clean_label(str(triple.get("object", "")))
            object_type = _clean_label(str(triple.get("object_type", ""))) or "Entity"
            evidence = _clean_label(str(triple.get("evidence", "")))
            if not subject or not predicate or not obj:
                continue

            subject_label = "PandaKind" if subject == PANDA_CANONICAL_NAME else "Entity"
            obj_label = object_type if object_type else "Entity"
            rel_type = _normalize_rel_type(predicate)

            nodes.add((subject, subject_label))
            nodes.add((obj, obj_label))
            rels.append((subject, obj, rel_type, source_file, evidence))

            cypher_lines.append(f"MERGE (a:{subject_label} {{id:'{subject}'}}) SET a.name='{subject}';")
            cypher_lines.append(f"MERGE (b:{obj_label} {{id:'{obj}'}}) SET b.name='{obj}';")
            cypher_lines.append(
                "MATCH (a {id:'" + subject + "'}), (b {id:'" + obj + "'}) "
                + "MERGE (a)-[r:" + rel_type + "]->(b) "
                + "SET r.source_file='" + source_file.replace("'", "\\'") + "', "
                + "r.evidence='" + evidence.replace("'", "\\'") + "', "
                + "r.last_updated='" + datetime.now().isoformat() + "';"
            )

    with nodes_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id:ID", "name", ":LABEL"])
        for node_id, label in sorted(nodes):
            writer.writerow([node_id, node_id, label])

    with rels_csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([":START_ID", ":END_ID", ":TYPE", "source_file", "evidence"])
        for row in rels:
            writer.writerow(row)

    cypher_path.write_text("\n".join(cypher_lines) + "\n", encoding="utf-8")
    return {
        "nodes_csv_path": str(nodes_csv_path),
        "rels_csv_path": str(rels_csv_path),
        "cypher_path": str(cypher_path),
    }


async def _run_async_pipeline(
    md_files: List[Path],
    llm: Any,
    max_concurrency: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    topic_chain = llm.with_structured_output(TopicResult)
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    def extraction_chain_factory() -> Any:
        return llm.with_structured_output(ExtractionSchema)

    tasks = [
        _extract_one_markdown_v2_async(
            file_path=file_path,
            topic_chain=topic_chain,
            extraction_chain_factory=extraction_chain_factory,
            semaphore=semaphore,
        )
        for file_path in md_files
    ]
    gathered = await asyncio.gather(*tasks, return_exceptions=True)
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for file_path, item in zip(md_files, gathered):
        if isinstance(item, Exception):
            errors.append(
                {
                    "source_file": str(file_path),
                    "error": f"{type(item).__name__}: {item}",
                }
            )
        else:
            results.append(item)
    return results, errors


def _checkpoint_file_path(run_output_dir: Path) -> Path:
    return run_output_dir / "extract_checkpoint.json"


def _save_checkpoint(
    checkpoint_path: Path,
    results_by_source: Dict[str, Dict[str, Any]],
    errors_by_source: Dict[str, str],
) -> None:
    checkpoint = {
        "updated_at": datetime.now().isoformat(),
        "results_by_source": results_by_source,
        "errors_by_source": errors_by_source,
    }
    checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_checkpoint(run_output_dir: Path) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    checkpoint_path = _checkpoint_file_path(run_output_dir)
    if not checkpoint_path.exists():
        return {}, {}
    raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    results = raw.get("results_by_source", {})
    errors = raw.get("errors_by_source", {})
    if not isinstance(results, dict) or not isinstance(errors, dict):
        return {}, {}
    clean_results: Dict[str, Dict[str, Any]] = {}
    clean_errors: Dict[str, str] = {}
    for key, value in results.items():
        if isinstance(key, str) and isinstance(value, dict):
            clean_results[key] = value
    for key, value in errors.items():
        if isinstance(key, str) and isinstance(value, str):
            clean_errors[key] = value
    return clean_results, clean_errors


async def _run_attempt_with_progress(
    md_files: List[Path],
    llm: Any,
    max_concurrency: int,
    attempt_index: int,
    max_attempts: int,
    total_files: int,
    results_by_source: Dict[str, Dict[str, Any]],
    errors_by_source: Dict[str, str],
    checkpoint_path: Path,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> List[Path]:
    topic_chain = llm.with_structured_output(TopicResult)
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    def extraction_chain_factory() -> Any:
        return llm.with_structured_output(ExtractionSchema)

    async def _run_one(file_path: Path) -> Tuple[Path, Optional[Dict[str, Any]], Optional[str]]:
        try:
            item = await _extract_one_markdown_v2_async(
                file_path=file_path,
                topic_chain=topic_chain,
                extraction_chain_factory=extraction_chain_factory,
                semaphore=semaphore,
            )
            return file_path, item, None
        except Exception as exc:
            return file_path, None, f"{type(exc).__name__}: {exc}"

    tasks: List[asyncio.Task[Tuple[Path, Optional[Dict[str, Any]], Optional[str]]]] = []
    for file_path in md_files:
        tasks.append(
            asyncio.create_task(
                _run_one(file_path)
            )
        )

    failed_paths: List[Path] = []
    done_in_attempt = 0
    total_in_attempt = len(md_files)
    for task in asyncio.as_completed(tasks):
        file_path, item, error = await task
        if item is not None:
            source = str(item.get("source_file", "")).strip() or str(file_path)
            results_by_source[source] = item
            errors_by_source.pop(source, None)
            status = "SUCCESS"
        else:
            source = str(file_path)
            errors_by_source[source] = error or "UnknownError: unknown extraction error"
            failed_paths.append(file_path)
            status = "FAILED"

        done_in_attempt += 1
        _save_checkpoint(checkpoint_path, results_by_source=results_by_source, errors_by_source=errors_by_source)
        if progress_callback:
            done_total = len(results_by_source)
            failed_total = len(errors_by_source)
            progress_callback(
                f"[Attempt {attempt_index}/{max_attempts}] {done_in_attempt}/{total_in_attempt} "
                f"{status} | done_total={done_total}/{total_files} failed_total={failed_total} | {file_path.name}"
            )
    return failed_paths


async def _run_with_retry_and_resume_async(
    md_files: List[Path],
    llm: Any,
    max_concurrency: int,
    retry_failed_times: int,
    run_output_dir: Path,
    resume_enabled: bool,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """执行抽取，支持失败重试、断点续传和进度回调。"""
    checkpoint_path = _checkpoint_file_path(run_output_dir)
    results_by_source: Dict[str, Dict[str, Any]] = {}
    errors_by_source: Dict[str, str] = {}
    if resume_enabled:
        results_by_source, errors_by_source = _load_checkpoint(run_output_dir)

    target_sources = {str(path) for path in md_files}
    results_by_source = {k: v for k, v in results_by_source.items() if k in target_sources}
    errors_by_source = {k: v for k, v in errors_by_source.items() if k in target_sources}

    pending_files = [path for path in md_files if str(path) not in results_by_source]
    if progress_callback and resume_enabled:
        progress_callback(
            f"[Resume] loaded checkpoint: done={len(results_by_source)} "
            f"failed={len(errors_by_source)} pending={len(pending_files)}"
        )

    attempt = 0
    max_attempts = max(1, retry_failed_times + 1)
    total_files = len(md_files)

    while pending_files and attempt < max_attempts:
        attempt += 1
        failed_paths = await _run_attempt_with_progress(
            pending_files,
            llm,
            max_concurrency=max_concurrency,
            attempt_index=attempt,
            max_attempts=max_attempts,
            total_files=total_files,
            results_by_source=results_by_source,
            errors_by_source=errors_by_source,
            checkpoint_path=checkpoint_path,
            progress_callback=progress_callback,
        )
        pending_files = failed_paths

    final_results = [results_by_source[str(file_path)] for file_path in md_files if str(file_path) in results_by_source]
    final_errors = [{"source_file": source, "error": err} for source, err in errors_by_source.items() if source in target_sources]
    _save_checkpoint(checkpoint_path, results_by_source=results_by_source, errors_by_source=errors_by_source)
    return final_results, final_errors


@tool
def panda_history_extractor(
    md_path: str = "",
    docs_dir: str = DEFAULT_PANDA_DOCS_DIR,
    max_concurrency: int = 5,
    export_neo4j: bool = True,
    retry_failed_times: int = 1,
    resume: bool = True,
    show_progress: bool = True,
    resume_run_output_dir: str = "",
) -> str:
    """
    描述：主题驱动抽取熊猫知识，支持并发处理、Pydantic 结构化输出，并可导出 Neo4j CSV/Cypher。
    输入：
    - md_path：单文件路径（相对项目根），有值时仅抽该文件。
    - docs_dir：批量目录（相对项目根），md_path 为空时扫描该目录全部 .md。
    - max_concurrency：批量并发数，默认 5。
    - export_neo4j：是否生成 Neo4j 导入文件，默认 true。
    - retry_failed_times：失败文件重试次数，默认 1（即最多尝试 2 轮）。
    - resume：是否启用断点续传，默认 true。
    - show_progress：是否输出处理进度，默认 true。
    - resume_run_output_dir：指定已有 run_output_dir 进行续跑；为空则自动新建。
    输出：JSON 字符串，包含抽取结果与导出文件路径。
    """
    result_data: Dict[str, Any] = {
        "mode": "",
        "source_file": "",
        "source_dir": "",
        "files_count": 0,
        "files": [],
        "alias_map": {},
        "summary": {},
        "neo4j_exports": {},
        "file_errors": [],
        "run_output_dir": "",
        "result_file_path": "",
        "timestamp": datetime.now().isoformat(),
    }

    try:
        _configure_warning_filters()
        project_root = get_project_root()
        llm = get_element_extraction_model()

        md_files: List[Path] = []
        if md_path.strip():
            file_path = (project_root / md_path).resolve()
            if not file_path.exists() or file_path.suffix.lower() != ".md":
                raise FileNotFoundError(f"Markdown 文件不存在或后缀不是 .md: {file_path}")
            md_files = [file_path]
            result_data["mode"] = "single_file"
            result_data["source_file"] = str(file_path)
        else:
            dir_path = (project_root / docs_dir).resolve()
            if not dir_path.exists() or not dir_path.is_dir():
                raise NotADirectoryError(f"目录不存在: {dir_path}")
            md_files = sorted(dir_path.glob("*.md"))
            if not md_files:
                raise FileNotFoundError(f"目录下未找到 .md 文件: {dir_path}")
            result_data["mode"] = "batch_dir"
            result_data["source_dir"] = str(dir_path)

        task_id = get_task_id()
        output_dir = ensure_task_dirs(task_id) if task_id else (project_root / "data" / "wiki")
        if resume_run_output_dir.strip():
            run_output_dir = (project_root / resume_run_output_dir).resolve()
            run_output_dir.mkdir(parents=True, exist_ok=True)
            output_stem = run_output_dir.name
        else:
            now = datetime.now()
            output_stem = _build_output_stem(
                mode=result_data["mode"],
                source_file=result_data["source_file"],
                source_dir=result_data["source_dir"],
                files_count=len(md_files),
                timestamp=now,
            )
            run_output_dir = output_dir / output_stem
            run_output_dir.mkdir(parents=True, exist_ok=True)

        result_data["run_output_dir"] = str(run_output_dir)
        output_file = run_output_dir / f"{output_stem}.json"
        result_data["result_file_path"] = str(output_file)

        progress_callback: Optional[Callable[[str], None]] = print if show_progress else None
        files, file_errors = asyncio.run(_run_with_retry_and_resume_async(
            md_files=md_files,
            llm=llm,
            max_concurrency=max_concurrency,
            retry_failed_times=retry_failed_times,
            run_output_dir=run_output_dir,
            resume_enabled=resume,
            progress_callback=progress_callback,
        ))

        result_data["file_errors"] = file_errors
        if not files:
            raise RuntimeError("全部文件抽取失败，请检查 file_errors")

        result_data["files"] = files
        result_data["files_count"] = len(files)
        alias_map = _build_alias_map(files)
        _normalize_triples(files, alias_map)
        result_data["alias_map"] = alias_map
        result_data["summary"] = _build_summary(files)

        if export_neo4j:
            result_data["neo4j_exports"] = _generate_neo4j_exports(files, run_output_dir, output_stem)

        output_file.write_text(json.dumps(result_data, ensure_ascii=False, indent=2), encoding="utf-8")

    except Exception as exc:
        result_data["error"] = f"{type(exc).__name__}: {exc}"

    return json.dumps(result_data, ensure_ascii=False)


def _build_cli_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(
        description="主题驱动抽取熊猫知识，并可导出 Neo4j CSV/Cypher 文件。"
    )
    parser.add_argument(
        "--md-path",
        default="",
        help="单文件路径（相对项目根），有值时仅处理该文件。",
    )
    parser.add_argument(
        "--docs-dir",
        default=DEFAULT_PANDA_DOCS_DIR,
        help=f"批量目录（相对项目根），默认：{DEFAULT_PANDA_DOCS_DIR}",
    )
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=5,
        help="批量并发数，默认 5。",
    )
    parser.add_argument(
        "--export-neo4j",
        dest="export_neo4j",
        action="store_true",
        help="启用 Neo4j 导出（默认启用）。",
    )
    parser.add_argument(
        "--no-export-neo4j",
        dest="export_neo4j",
        action="store_false",
        help="禁用 Neo4j 导出。",
    )
    parser.set_defaults(export_neo4j=True)
    parser.add_argument(
        "--retry-failed-times",
        type=int,
        default=1,
        help="失败文件重试次数，默认 1（即最多尝试 2 轮）。",
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="禁用断点续传（默认启用）。",
    )
    parser.add_argument(
        "--resume-run-output-dir",
        default="",
        help="指定已有 run_output_dir 继续处理（相对项目根）。",
    )
    parser.add_argument(
        "--no-progress",
        dest="show_progress",
        action="store_false",
        help="禁用实时进度输出（默认启用）。",
    )
    parser.set_defaults(resume=True, show_progress=True)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="打印完整 JSON 结果（默认仅输出摘要）。",
    )
    return parser


def main() -> int:
    """CLI 入口：解析参数并执行抽取。"""
    _configure_warning_filters()
    parser = _build_cli_parser()
    args = parser.parse_args()

    result_json = panda_history_extractor.invoke(
        {
            "md_path": args.md_path,
            "docs_dir": args.docs_dir,
            "max_concurrency": args.max_concurrency,
            "export_neo4j": args.export_neo4j,
            "retry_failed_times": args.retry_failed_times,
            "resume": args.resume,
            "show_progress": args.show_progress,
            "resume_run_output_dir": args.resume_run_output_dir,
        }
    )
    try:
        parsed = json.loads(result_json)
        if args.verbose:
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
        else:
            file_errors = parsed.get("file_errors", [])
            failed_files = [str(item.get("source_file", "")) for item in file_errors if isinstance(item, dict)]
            success_count = int(parsed.get("files_count", 0))
            failed_count = len(failed_files)
            print("=== panda_history_extractor ===")
            print(f"status: {'ERROR' if parsed.get('error') else 'OK'}")
            print(f"mode: {parsed.get('mode', '')}")
            print(f"success_files: {success_count}")
            print(f"failed_files: {failed_count}")
            if failed_files:
                print("failed_file_list:")
                for failed_file in failed_files:
                    print(f"  - {failed_file}")
            print(f"run_output_dir: {parsed.get('run_output_dir', '')}")
            print(f"result_file_path: {parsed.get('result_file_path', '')}")
        return 1 if parsed.get("error") else 0
    except Exception:
        print(result_json)
        return 0


if __name__ == "__main__":
    sys.exit(main())
