"""基于 Neo4j 图谱检索并调用大模型（Qwen-Plus）回答问题。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from model.factory import get_text_generation_model
from utils.env_loader import get_env_config

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table
except Exception:  # pragma: no cover
    Console = None  # type: ignore[assignment]
    Markdown = None  # type: ignore[assignment]
    Panel = None  # type: ignore[assignment]
    Rule = None  # type: ignore[assignment]
    Table = None  # type: ignore[assignment]


@dataclass
class Neo4jConfig:
    """Neo4j 连接配置。"""

    uri: str
    username: str
    password: str
    database: str


def _load_graph_database_class() -> Any:
    """延迟导入 neo4j 驱动，避免帮助命令触发第三方噪音。"""
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            from neo4j import GraphDatabase as _GraphDatabase
    except Exception as exc:
        raise RuntimeError(
            "无法导入 neo4j 驱动，请先安装依赖（pip install neo4j），"
            "并检查本地环境兼容性。"
        ) from exc
    return _GraphDatabase


def _load_neo4j_config(database_override: str = "") -> Neo4jConfig:
    """从环境变量加载 Neo4j 配置。"""
    get_env_config()
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


def _extract_keywords(question: str) -> List[str]:
    """从问题中提取关键词。"""
    raw = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_]+", question)
    stop_words = {"什么", "哪些", "如何", "为什么", "怎么", "一下", "请问", "一下子", "关于", "容易", "是否"}
    zh_dict = [
        "大熊猫",
        "熊猫",
        "疾病",
        "治疗",
        "天敌",
        "捕食",
        "敌害",
        "栖息地",
        "栖息",
        "分布",
        "居住",
        "环境",
        "历史",
        "发现",
        "定名",
        "行为",
        "习性",
        "外形",
        "繁殖",
        "食物",
        "气味",
        "气味标记",
        "交流",
        "沟通",
        "声音",
        "叫声",
        "生长",
        "发育",
        "生长发育",
        "周期",
        "月龄",
        "体重",
        "幼仔",
        "亚成年",
        "成年",
        "性成熟",
    ]
    keywords: List[str] = []
    seen: set[str] = set()
    for phrase in zh_dict:
        if phrase in question and phrase not in seen:
            seen.add(phrase)
            keywords.append(phrase)
    for token in raw:
        key = token.strip()
        if len(key) <= 1 or key in stop_words:
            continue
        if key not in seen:
            seen.add(key)
            keywords.append(key)
    return keywords[:10]


def _build_intent_terms(question: str) -> List[str]:
    """根据问题主题扩展意图词，提升检索召回精度。"""
    q = question.lower()
    terms: List[str] = []
    if any(token in q for token in ["疾病", "生病", "病", "健康", "症状", "治疗"]):
        terms.extend(["疾病", "病", "症状", "治疗", "感染", "炎"])
    if any(token in q for token in ["天敌", "为敌", "敌害", "捕食", "威胁"]):
        terms.extend(["天敌", "捕食", "敌害", "威胁", "黄喉貂", "金猫", "豹"])
    if any(token in q for token in ["栖息", "居住", "分布", "哪里", "环境"]):
        terms.extend(["栖息地", "分布", "环境", "生存", "地区"])
    if any(token in q for token in ["历史", "发现", "定名"]):
        terms.extend(["发现", "历史", "定名", "鉴定", "文献"])
    if any(token in q for token in ["行为", "习性", "活动"]):
        terms.extend(["行为", "习性", "活动", "采食", "交配"])
    if any(token in q for token in ["交流", "沟通", "气味", "气味标记", "声音", "叫声"]):
        terms.extend(["交流", "沟通", "气味标记", "声音交流", "叫声", "标记"])
    if any(token in q for token in ["生长", "发育", "生长发育", "周期", "月龄", "幼仔", "亚成年", "成年", "性成熟"]):
        terms.extend(["生长", "发育", "生长发育", "周期", "月龄", "幼仔", "亚成年", "成年", "性成熟", "体重", "恒牙"])
    deduped: List[str] = []
    seen: set[str] = set()
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            deduped.append(term)
    return deduped[:20]


def _detect_intent(question: str) -> str:
    """识别问题意图，用于检索和回答约束。"""
    q = question.lower()
    if any(token in q for token in ["疾病", "生病", "病", "健康", "症状", "治疗"]):
        return "disease"
    if any(token in q for token in ["天敌", "为敌", "敌害", "捕食", "威胁"]):
        return "predator"
    if any(token in q for token in ["朋友", "伴生", "近邻"]):
        return "companion"
    if any(token in q for token in ["栖息", "居住", "分布", "哪里", "环境"]):
        return "habitat"
    if any(token in q for token in ["历史", "发现", "定名"]):
        return "history"
    if any(token in q for token in ["行为", "习性", "活动"]):
        return "behavior"
    if any(token in q for token in ["食物", "吃什么", "进食", "主食"]):
        return "food"
    if any(token in q for token in ["消化", "胃", "肠", "盲肠"]):
        return "digestion"
    if any(token in q for token in ["交流", "沟通", "气味", "气味标记", "声音", "叫声"]):
        return "communication"
    if any(token in q for token in ["生长", "发育", "生长发育", "周期", "月龄", "幼仔", "亚成年", "成年", "性成熟"]):
        return "development"
    return "general"


def _build_intent_predicates(intent: str) -> List[str]:
    """不同意图下优先命中的关系词。"""
    mapping: Dict[str, List[str]] = {
        "disease": ["疾病", "病", "感染", "治疗", "症状"],
        "predator": ["天敌", "捕食", "敌害", "威胁"],
        "companion": ["伴生", "近邻", "朋友", "共栖"],
        "habitat": ["栖息", "分布", "生存环境", "地区", "海拔", "气候"],
        "history": ["发现", "定名", "鉴定", "记载", "历史", "年代"],
        "behavior": ["行为", "习性", "活动", "交配", "育幼", "刻板行为"],
        "food": ["食用", "主食", "进食", "食物", "采食"],
        "digestion": ["消化", "胃", "肠", "盲肠", "消化道", "营养吸收"],
        "communication": ["交流", "沟通", "气味标记", "标记", "声音", "叫声", "发情"],
        "development": ["生长", "发育", "成长", "阶段", "月龄", "体重", "性成熟", "恒牙", "独立生活", "成年"],
    }
    return mapping.get(intent, [])


def _build_topic_filters(question: str, intent: str) -> List[str]:
    """根据问题和意图推断候选 topic，用于先按 topic 缩小检索范围。"""
    q = question.lower()
    topics: List[str] = []
    if intent in {"predator", "companion"}:
        topics.append("生态关系")
    if intent == "disease":
        topics.append("疾病健康")
    if intent == "habitat":
        topics.append("生存环境")
    if intent == "history":
        topics.append("历史背景")
    if intent in {"behavior", "communication"}:
        topics.append("行为习性")
    if intent in {"digestion", "food"}:
        topics.extend(["生理构造", "行为习性"])
    if intent == "development":
        topics.extend(["生理构造", "行为习性", "个体档案"])

    if any(token in q for token in ["生长", "发育", "月龄", "体重", "幼仔", "成年", "性成熟"]):
        topics.extend(["生理构造", "行为习性", "个体档案"])
    if any(token in q for token in ["交流", "气味标记", "叫声", "沟通"]):
        topics.append("行为习性")
    if any(token in q for token in ["天敌", "伴生", "捕食"]):
        topics.append("生态关系")

    deduped: List[str] = []
    seen: set[str] = set()
    for topic in topics:
        if topic and topic not in seen:
            seen.add(topic)
            deduped.append(topic)
    return deduped


def _rank_and_dedup_rows(rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """按(subject,predicate,object,evidence)去重，并按分数降序排序。"""
    by_key: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}
    for row in rows:
        key = (
            str(row.get("subject", "")),
            str(row.get("predicate", "")),
            str(row.get("object", "")),
            str(row.get("evidence", "")),
        )
        score = float(row.get("total_score", 0) or 0)
        existed = by_key.get(key)
        if existed is None or float(existed.get("total_score", 0) or 0) < score:
            by_key[key] = row
    deduped = list(by_key.values())
    deduped.sort(key=lambda x: float(x.get("total_score", 0) or 0), reverse=True)
    return deduped


def _select_candidate_sources(
    session: Any,
    *,
    keywords: List[str],
    intent_terms: List[str],
    topic_filters: List[str],
    top_n: int = 8,
) -> List[str]:
    """
    第一阶段：先按问题关键词/意图词召回候选 source_file。
    这样可以先缩小检索范围，再做关系级精排，减少主题漂移。
    """
    if not keywords and not intent_terms:
        return []

    source_query = """
UNWIND $terms AS t
MATCH ()-[r]->()
WITH t, r,
  (
    CASE WHEN size($topic_filters) > 0 AND coalesce(r.topic, '') IN $topic_filters THEN 8 ELSE 0 END +
    CASE WHEN coalesce(r.source_file, '') CONTAINS t THEN 6 ELSE 0 END +
    CASE WHEN coalesce(r.predicate, '') CONTAINS t THEN 3 ELSE 0 END +
    CASE WHEN coalesce(endNode(r).id, '') CONTAINS t THEN 3 ELSE 0 END +
    CASE WHEN coalesce(r.evidence, '') CONTAINS t THEN 1 ELSE 0 END
  ) AS s
WHERE s > 0
WITH coalesce(r.source_file, '') AS source_file, sum(s) AS score
WHERE source_file <> ''
RETURN source_file, score
ORDER BY score DESC
LIMIT $top_n
"""
    terms = keywords + [term for term in intent_terms if term not in keywords]
    rows = list(session.run(source_query, terms=terms[:20], topic_filters=topic_filters, top_n=max(1, top_n)))
    return [str(r["source_file"]).strip() for r in rows if str(r["source_file"]).strip()]


def _fetch_knowledge(question: str, top_k: int, database: str) -> List[Dict[str, str]]:
    """从 Neo4j 检索相关三元组。"""
    config = _load_neo4j_config(database_override=database)
    graph_database = _load_graph_database_class()
    driver = graph_database.driver(config.uri, auth=(config.username, config.password))
    keywords = _extract_keywords(question)
    intent = _detect_intent(question)
    intent_terms = _build_intent_terms(question)
    intent_predicates = _build_intent_predicates(intent)
    topic_filters = _build_topic_filters(question, intent)
    if "大熊猫" not in keywords:
        keywords.append("大熊猫")
    require_intent = len(intent_terms) > 0

    query = """
UNWIND $keywords AS kw
MATCH (s)-[r]->(o)
WITH s, r, o, kw,
  (
    CASE WHEN coalesce(r.predicate, '') CONTAINS kw THEN 6 ELSE 0 END +
    CASE WHEN coalesce(o.id, '') CONTAINS kw THEN 4 ELSE 0 END +
    CASE WHEN coalesce(s.id, '') CONTAINS kw THEN 3 ELSE 0 END +
    CASE WHEN coalesce(r.evidence, '') CONTAINS kw THEN 2 ELSE 0 END
  ) AS kw_score
WHERE kw_score > 0
WITH s, r, o, sum(kw_score) AS score
WHERE (size($candidate_sources) = 0) OR coalesce(r.source_file, '') IN $candidate_sources
WITH s, r, o, score,
  reduce(intent_score = 0, t IN $intent_terms |
    intent_score +
    CASE
      WHEN coalesce(r.predicate, '') CONTAINS t OR coalesce(o.id, '') CONTAINS t OR coalesce(r.evidence, '') CONTAINS t
      THEN 2 ELSE 0
    END
  ) AS intent_score
WITH s, r, o, score, intent_score,
  CASE
    WHEN size($topic_filters) = 0 THEN 0
    WHEN coalesce(r.topic, '') IN $topic_filters THEN 6
    ELSE 0
  END AS topic_bias,
  reduce(pred_score = 0, p IN $intent_predicates |
    pred_score + CASE WHEN coalesce(r.predicate, '') CONTAINS p THEN 3 ELSE 0 END
  ) AS predicate_bias
WHERE ((NOT $require_intent) OR intent_score > 0)
  AND ((NOT $require_topic) OR coalesce(r.topic, '') IN $topic_filters)
RETURN
  coalesce(s.id, '') AS subject,
  coalesce(r.predicate, type(r)) AS predicate,
  coalesce(o.id, '') AS object,
  coalesce(r.evidence, '') AS evidence,
  coalesce(r.source_file, '') AS source_file,
  coalesce(r.topic, '') AS topic,
  (score + intent_score + topic_bias + predicate_bias) AS total_score
ORDER BY total_score DESC, size(coalesce(r.evidence, '')) DESC
LIMIT $top_k
"""
    fallback_query = """
MATCH (s)-[r]->(o)
RETURN
  coalesce(s.id, '') AS subject,
  coalesce(r.predicate, type(r)) AS predicate,
  coalesce(o.id, '') AS object,
  coalesce(r.evidence, '') AS evidence,
  coalesce(r.source_file, '') AS source_file,
  coalesce(r.topic, '') AS topic,
  0 AS total_score
LIMIT $top_k
"""
    try:
        with driver.session(database=config.database) as session:
            candidate_sources = _select_candidate_sources(
                session,
                keywords=keywords,
                intent_terms=intent_terms,
                topic_filters=topic_filters,
                top_n=8,
            )
            rows: List[Dict[str, str]] = []
            for require_topic in [bool(topic_filters), False]:
                rows = [
                    dict(record)
                    for record in session.run(
                        query,
                        keywords=keywords,
                        candidate_sources=candidate_sources,
                        intent_terms=intent_terms,
                        intent_predicates=intent_predicates,
                        topic_filters=topic_filters,
                        require_intent=require_intent,
                        require_topic=require_topic,
                        top_k=max(1, top_k),
                    )
                ]
                if rows:
                    break
            if rows:
                return _rank_and_dedup_rows(rows)
            return _rank_and_dedup_rows([dict(record) for record in session.run(fallback_query, top_k=max(1, top_k))])
    finally:
        driver.close()


def _llm_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content)


def _build_context_rows(rows: List[Dict[str, str]]) -> str:
    lines: List[str] = []
    for idx, item in enumerate(rows, start=1):
        lines.append(
            f"{idx}. {item['subject']} -[{item['predicate']}]-> {item['object']} | 证据: {item['evidence']} | 来源: {item['source_file']}"
        )
    return "\n".join(lines)


def _pick_supporting_sources(answer: str, evidences: List[Dict[str, Any]], limit: int = 10) -> List[str]:
    """只返回在回答文本中被实际引用到的来源文档。"""
    answer_text = (answer or "").strip()
    if not answer_text:
        return []

    matched: List[str] = []
    seen: set[str] = set()
    for item in evidences:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_file", "")).strip()
        evidence = str(item.get("evidence", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        if not source or not evidence:
            continue

        # 优先用证据片段匹配；证据较长时使用前后片段降低偶然误命中。
        short_head = evidence[:24]
        short_tail = evidence[-24:] if len(evidence) > 30 else evidence
        evidence_hit = (short_head and short_head in answer_text) or (short_tail and short_tail in answer_text)
        predicate_hit = predicate and predicate in answer_text
        if evidence_hit or predicate_hit:
            if source not in seen:
                seen.add(source)
                matched.append(source)
            if len(matched) >= max(1, limit):
                break
    if matched:
        return matched[: max(1, limit)]

    # 兜底：当回答被模型重写导致无法命中文本片段时，按证据得分回填来源，避免空结果。
    scored_sources: List[Tuple[float, str]] = []
    seen_fallback: set[str] = set()
    for item in evidences:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_file", "")).strip()
        if not source or source in seen_fallback:
            continue
        score = float(item.get("total_score", 0) or 0)
        seen_fallback.add(source)
        scored_sources.append((score, source))
    scored_sources.sort(key=lambda x: x[0], reverse=True)
    return [source for _, source in scored_sources[: max(1, limit)]]


def _persona_instructions(persona: str) -> str:
    """回答语气/角色设定。"""
    persona_map: Dict[str, str] = {
        "default": "语气：清晰、客观、偏科普；适合成年人快速理解。",
        "kid": (
            "语气：面向 6-10 岁小朋友的科普讲解员；用词简单、句子短；"
            "多用类比；避免恐吓性描述；必要时用“我们可以理解为…”帮助理解。"
        ),
        "educator": "语气：耐心、鼓励式；像课堂老师；适当分点；避免堆砌术语。",
        "expert": "语气：专业、克制；术语可保留但需简短解释；结构更紧凑。",
        "story": "语气：轻故事化但仍基于证据；不要编造情节；比喻要克制。",
    }
    return persona_map.get(persona.strip(), persona_map["default"])


def _get_console(*, use_rich: bool) -> Any:
    if use_rich and Console is not None:
        return Console(highlight=False, soft_wrap=True)
    return None


def _print_cli_result(
    *,
    parsed: Dict[str, Any],
    show_sources: bool,
    use_rich: bool,
) -> None:
    console = _get_console(use_rich=use_rich)
    status = "OK" if parsed.get("ok") else "ERROR"
    if console is None:
        print("=== neo4j_qa ===")
        print(f"status: {status}")
        print(f"database: {parsed.get('database', '')}")
        print(f"persona: {parsed.get('persona', '')}")
        print(f"rows_count: {parsed.get('rows_count', 0)}")
        print("answer:")
        print(parsed.get("answer", ""))
        if show_sources:
            evidences = parsed.get("evidences", [])
            if isinstance(evidences, list):
                source_files = _pick_supporting_sources(
                    answer=str(parsed.get("answer", "")),
                    evidences=evidences,
                    limit=10,
                )
                print("supporting_source_files:")
                if source_files:
                    for source in source_files:
                        print(f"  - {source}")
                else:
                    print("  - (no supporting source found in answer text)")
        if parsed.get("error"):
            print(f"error: {parsed['error']}")
        return

    console.print(Rule("neo4j_qa", style="cyan"))
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row("status", status)
    table.add_row("database", str(parsed.get("database", "")))
    table.add_row("persona", str(parsed.get("persona", "")))
    table.add_row("rows_count", str(parsed.get("rows_count", 0)))
    console.print(Panel(table, title="运行信息", border_style="cyan"))

    answer_text = str(parsed.get("answer", "") or "")
    console.print(Panel(Markdown(answer_text), title="回答", border_style="green"))

    if show_sources:
        evidences = parsed.get("evidences", [])
        source_files: List[str] = []
        if isinstance(evidences, list):
            source_files = _pick_supporting_sources(
                answer=str(parsed.get("answer", "")),
                evidences=evidences,
                limit=10,
            )
        if source_files:
            lines = "\n".join(f"- `{path}`" for path in source_files)
            console.print(Panel(Markdown(lines), title="依据来源（文档）", border_style="yellow"))
        else:
            console.print(Panel("(本轮未匹配到可展示的来源文件)", title="依据来源（文档）", border_style="yellow"))

    if parsed.get("error"):
        console.print(Panel(str(parsed["error"]), title="错误", border_style="red"))


def _generate_answer(question: str, rows: List[Dict[str, str]], strict_mode: bool, persona: str) -> str:
    llm = get_text_generation_model()  # 由 config/model.yaml 的 text_generation 配置决定（当前为 qwen-plus）
    context_text = _build_context_rows(rows)
    intent = _detect_intent(question)
    intent_hint = {
        "disease": "优先总结疾病类别、典型病名与症状线索。",
        "predator": "优先回答天敌名单与其威胁对象（幼仔/病弱个体等）。",
        "companion": "优先回答伴生动物与同域共栖关系。",
        "habitat": "优先回答分布区、栖息地类型、地理环境。",
        "history": "优先回答时间线（起源、发现、定名）。",
        "behavior": "优先回答行为特点并按类别归纳。",
        "food": "优先回答食物类型并区分野外/圈养。",
        "digestion": "优先回答消化系统结构、消化相关行为与证据边界。",
        "communication": "优先回答交流方式（气味标记/声音）及其作用场景。",
        "development": "优先回答生长发育阶段（如月龄、体重、独立与性成熟等）并按时间线组织。",
    }.get(intent, "优先回答与问题最相关的事实。")
    system_prompt = (
        "你是熊猫知识库问答助手。你只能基于给定知识作答，不得编造。"
        "回答要简洁清晰，并在末尾列出“依据”要点（引用关系和证据句）。"
        "避免重复证据，优先引用高相关条目。"
        f"{_persona_instructions(persona)}"
    )
    if strict_mode:
        system_prompt += "当证据不足时，必须明确说“知识库暂无足够依据”。"
    user_prompt = (
        f"问题：{question}\n\n"
        f"知识库检索结果（最多 {len(rows)} 条）：\n{context_text}\n\n"
        f"回答偏好：{intent_hint}\n\n"
        "请输出：\n"
        "1) 直接回答\n"
        "2) 依据（2-6条，格式：关系 -> 证据）"
    )
    response = llm.invoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
    return _llm_content_to_text(response.content).strip()


@tool
def neo4j_qa(
    question: str,
    top_k: int = 20,
    database: str = "",
    strict_mode: bool = True,
    persona: str = "default",
) -> str:
    """
    描述：基于 Neo4j 图谱检索知识，并调用 Qwen-Plus 进行带证据回答。
    输入：
    - question：用户问题。
    - top_k：最多检索多少条关系，默认 20。
    - database：Neo4j 数据库名，默认读取 NEO4J_DATABASE 或 neo4j。
    - strict_mode：严格模式；证据不足时明确返回“暂无依据”。
    - persona：回答语气/角色（default/kid/educator/expert/story）。
    输出：JSON 字符串，包含 answer 与 evidences。
    """
    result: Dict[str, Any] = {
        "ok": False,
        "question": question,
        "top_k": max(1, top_k),
        "database": "",
        "persona": persona.strip() or "default",
        "rows_count": 0,
        "answer": "",
        "evidences": [],
    }
    try:
        config = _load_neo4j_config(database_override=database)
        result["database"] = config.database
        rows = _fetch_knowledge(question=question, top_k=max(1, top_k), database=config.database)
        result["rows_count"] = len(rows)
        result["evidences"] = rows
        if not rows:
            result["answer"] = "知识库暂无足够依据。"
            result["ok"] = True
            return json.dumps(result, ensure_ascii=False)
        result["answer"] = _generate_answer(
            question=question,
            rows=rows,
            strict_mode=strict_mode,
            persona=persona,
        )
        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return json.dumps(result, ensure_ascii=False)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="基于 Neo4j 图谱 + Qwen-Plus 进行问答。")
    parser.add_argument(
        "--question",
        default="",
        help="要提问的问题文本。",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=20,
        help="检索关系条数上限，默认 20。",
    )
    parser.add_argument(
        "--database",
        default="",
        help="Neo4j 数据库名，默认读取 NEO4J_DATABASE 或 neo4j。",
    )
    parser.add_argument(
        "--strict",
        dest="strict_mode",
        action="store_true",
        help="严格模式（证据不足时明确返回暂无依据）。默认开启。",
    )
    parser.add_argument(
        "--no-strict",
        dest="strict_mode",
        action="store_false",
        help="关闭严格模式。",
    )
    parser.set_defaults(strict_mode=True)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="进入终端交互问答模式（输入 exit/quit 退出）。",
    )
    parser.add_argument(
        "--show-sources",
        action="store_true",
        help="显示本轮命中的证据来源文件（最多10条，去重）。",
    )
    parser.add_argument(
        "--persona",
        default="default",
        choices=["default", "kid", "educator", "expert", "story"],
        help="回答语气/角色：default/kid/educator/expert/story。",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="禁用 rich 美化输出（纯文本）。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="输出完整 JSON；默认输出摘要。",
    )
    return parser


def _run_one_cli_query(
    question: str,
    top_k: int,
    database: str,
    strict_mode: bool,
    verbose: bool,
    show_sources: bool,
    persona: str,
    use_rich: bool,
) -> bool:
    result_json = neo4j_qa.invoke(
        {
            "question": question,
            "top_k": top_k,
            "database": database,
            "strict_mode": strict_mode,
            "persona": persona,
        }
    )
    try:
        parsed = json.loads(result_json)
    except Exception:
        print(result_json)
        return False

    if verbose:
        print(json.dumps(parsed, ensure_ascii=False, indent=2))
    else:
        _print_cli_result(parsed=parsed, show_sources=show_sources, use_rich=use_rich)
    return bool(parsed.get("ok"))


def main() -> int:
    parser = _build_cli_parser()
    args = parser.parse_args()
    use_rich = (not args.plain) and (Console is not None)
    persona = str(args.persona or "default")
    show_sources = bool(args.show_sources)
    if args.interactive:
        if use_rich and Console is not None and Panel is not None and Markdown is not None:
            console = Console(highlight=False, soft_wrap=True)
            console.print(
                Panel(
                    Markdown(
                        "进入 **Neo4j 问答交互模式**。\n\n"
                        "- 输入问题即可开始\n"
                        "- 输入 `exit` / `quit` 退出\n"
                        "- 输入 `:persona kid` 切换为儿童科普语气（可选：`default/educator/expert/story`）\n"
                        "- 输入 `:sources on` / `:sources off` 切换是否显示来源文档\n"
                        "- 输入 `:help` 查看帮助"
                    ),
                    title="AnyClaw · 熊猫知识库问答",
                    border_style="cyan",
                )
            )
        else:
            print("进入 Neo4j 问答交互模式（输入 exit / quit 退出）")
            print("命令：:persona <default|kid|educator|expert|story>  |  :sources on|off  |  :help")
        while True:
            try:
                question = input("\nquestion> ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n已退出交互模式。")
                return 0
            if not question:
                continue
            if question.lower() in {"exit", "quit"}:
                print("已退出交互模式。")
                return 0
            if question.startswith(":"):
                parts = question.split()
                cmd = parts[0].lower()
                if cmd in {":help", ":h"}:
                    print("命令：")
                    print("  :persona <default|kid|educator|expert|story>")
                    print("  :sources on|off")
                    print("  :help")
                    continue
                if cmd in {":persona", ":p"}:
                    if len(parts) < 2:
                        print(f"当前 persona: {persona}")
                        continue
                    next_persona = parts[1].strip().lower()
                    allowed = {"default", "kid", "educator", "expert", "story"}
                    if next_persona not in allowed:
                        print(f"不支持的 persona: {next_persona}，可选：{', '.join(sorted(allowed))}")
                        continue
                    persona = next_persona
                    print(f"已切换 persona: {persona}")
                    continue
                if cmd in {":sources", ":src"}:
                    if len(parts) < 2:
                        print(f"当前 sources: {'on' if show_sources else 'off'}")
                        continue
                    mode = parts[1].strip().lower()
                    if mode in {"on", "true", "1", "yes"}:
                        show_sources = True
                    elif mode in {"off", "false", "0", "no"}:
                        show_sources = False
                    else:
                        print("用法：:sources on|off")
                        continue
                    print(f"已切换 sources: {'on' if show_sources else 'off'}")
                    continue
                print("未知命令，输入 :help 查看帮助")
                continue
            _run_one_cli_query(
                question=question,
                top_k=args.top_k,
                database=args.database,
                strict_mode=args.strict_mode,
                verbose=args.verbose,
                show_sources=show_sources,
                persona=persona,
                use_rich=use_rich,
            )

    if not args.question.strip():
        parser.error("非交互模式下必须提供 --question，或使用 --interactive")
    ok = _run_one_cli_query(
        question=args.question.strip(),
        top_k=args.top_k,
        database=args.database,
        strict_mode=args.strict_mode,
        verbose=args.verbose,
        show_sources=args.show_sources,
        persona=persona,
        use_rich=use_rich,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
