"""基于三栏目文档自动出题，调用 neo4j_qa 评测并输出查漏补缺清单。

用法示例：
  python scripts/kb_doc_eval.py generate --categories 熊猫资料 --max-questions 40
  python scripts/kb_doc_eval.py run --dataset data/eval/kb_auto.jsonl
  python scripts/kb_doc_eval.py all --categories 熊猫资料 --max-questions 20
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.env_loader import get_env_config

get_env_config()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from model.factory import get_text_generation_model  # noqa: E402
from tools.neo4j_qa import neo4j_qa  # noqa: E402
from tools.qa_metrics import answer_groundedness  # noqa: E402

CATEGORY_KNOWLEDGE = "熊猫知识"
CATEGORY_RUMOR = "熊猫谣言"
CATEGORY_PROFILE = "熊猫资料"

CATEGORY_DIR_MAP: Dict[str, str] = {
    CATEGORY_KNOWLEDGE: "docs/熊猫知识",
    CATEGORY_RUMOR: "docs/熊猫谣言",
    CATEGORY_PROFILE: "docs/熊猫资料",
}

FACT_TYPE_PREDICATES: Dict[str, List[str]] = {
    "父母": ["父亲为", "母亲为", "父母"],
    "昵称": ["昵称为", "又名", "乳名为", "外号为", "认养名为"],
    "认养": ["被认养于", "终生认养于", "认养名为"],
    "子女": ["育有", "产下", "诞下"],
    "组合": ["属于组合", "成员为"],
    "现居": ["现居地", "栖息于"],
    "迁居": ["迁至", "赴", "返回", "旅居于"],
    "去世": ["逝世于", "去世年份", "死亡时间"],
    "谱系": ["谱系号", "性别为", "出生于", "出生时间"],
    "分类": ["分类属于", "属于"],
    "天敌": ["天敌", "捕食"],
    "食性": ["主食", "食用"],
    "保护": ["措施", "保护"],
    "疾病": ["疾病", "症状", "治疗"],
    "繁殖": ["繁殖", "妊娠", "育幼"],
    "分布": ["分布于", "栖息于"],
    "行为": ["行为", "习性"],
    "其他": [],
}


@dataclass
class DocChunk:
    """文档切块，用于出题。"""

    category: str
    source_file: str
    doc_path: str
    chunk_id: str
    title: str
    text: str
    max_questions: int = 2


@dataclass
class EvalSample:
    """评测样本（兼容 qa_evaluator JSONL）。"""

    sample_id: str
    question: str
    gold_answer: str
    gold_sources: List[str]
    category: str = ""
    fact_type: str = "其他"
    doc_path: str = ""
    chunk_id: str = ""
    evidence_span: str = ""


def _safe_mean(values: Iterable[float]) -> float:
    value_list = list(values)
    if not value_list:
        return 0.0
    return float(statistics.fmean(value_list))


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip().lower()


def _char_f1(prediction: str, reference: str) -> float:
    pred = _normalize_text(prediction)
    ref = _normalize_text(reference)
    if not pred and not ref:
        return 1.0
    if not pred or not ref:
        return 0.0
    pred_counts: Dict[str, int] = {}
    ref_counts: Dict[str, int] = {}
    for ch in pred:
        pred_counts[ch] = pred_counts.get(ch, 0) + 1
    for ch in ref:
        ref_counts[ch] = ref_counts.get(ch, 0) + 1
    overlap = sum(min(c, ref_counts.get(ch, 0)) for ch, c in pred_counts.items())
    if overlap == 0:
        return 0.0
    precision = overlap / max(1, len(pred))
    recall = overlap / max(1, len(ref))
    return 2 * precision * recall / max(1e-12, precision + recall)


def _exact_match(prediction: str, reference: str) -> float:
    return 1.0 if _normalize_text(prediction) == _normalize_text(reference) else 0.0


def _source_hit(predicted: Sequence[str], gold: Sequence[str], *, top_n: int) -> float:
    if not gold:
        return 0.0
    pred_norm = {_normalize_text(Path(s).name) for s in predicted[: max(1, top_n)] if s}
    gold_norm = {_normalize_text(Path(s).name) for s in gold if s}
    if not pred_norm or not gold_norm:
        return 0.0
    return 1.0 if pred_norm.intersection(gold_norm) else 0.0


def _parse_categories(raw: str) -> List[str]:
    if not raw.strip():
        return list(CATEGORY_DIR_MAP.keys())
    parts = [p.strip() for p in re.split(r"[,，\s]+", raw) if p.strip()]
    resolved: List[str] = []
    for part in parts:
        aliases = {
            "知识": CATEGORY_KNOWLEDGE,
            "knowledge": CATEGORY_KNOWLEDGE,
            CATEGORY_KNOWLEDGE: CATEGORY_KNOWLEDGE,
            "谣言": CATEGORY_RUMOR,
            "rumor": CATEGORY_RUMOR,
            CATEGORY_RUMOR: CATEGORY_RUMOR,
            "资料": CATEGORY_PROFILE,
            "profile": CATEGORY_PROFILE,
            CATEGORY_PROFILE: CATEGORY_PROFILE,
        }
        cat = aliases.get(part)
        if not cat:
            raise ValueError(f"不支持的栏目: {part}")
        if cat not in resolved:
            resolved.append(cat)
    return resolved


def _rel_doc_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _chunk_profile(path: Path, category: str) -> List[DocChunk]:
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(\d+)\.\s*", text)
    chunks: List[DocChunk] = []
    i = 1
    while i < len(parts) - 1:
        body = parts[i + 1]
        title = body.split("\n", 1)[0].strip()
        content = body.split("\n", 1)[1].strip() if "\n" in body else ""
        if len(content) < 40:
            i += 2
            continue
        short = title[3:] if title.startswith("大熊猫") else title
        short = re.sub(r"[（(].*?[)）]", "", short).strip() or title
        chunks.append(
            DocChunk(
                category=category,
                source_file=path.name,
                doc_path=_rel_doc_path(path),
                chunk_id=f"{path.stem}#{short}",
                title=short,
                text=f"{title}\n{content}"[:2500],
                max_questions=3,
            )
        )
        i += 2
    if not chunks and text.strip():
        chunks.append(
            DocChunk(
                category=category,
                source_file=path.name,
                doc_path=_rel_doc_path(path),
                chunk_id=f"{path.stem}#all",
                title=path.stem,
                text=text[:3000],
                max_questions=3,
            )
        )
    return chunks


def _chunk_rumor(path: Path, category: str) -> List[DocChunk]:
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    chunks: List[DocChunk] = []
    for idx, section in enumerate(sections[1:], start=1):
        lines = section.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        # 只采信正确结论，避免把谣言标题当事实
        m = re.search(r"\*\*正确结论[（(]辟谣[)）]?\*\*[：:]?\s*(.*)", body, re.S)
        conclusion = m.group(1).strip() if m else body
        conclusion = re.sub(r"(?m)^##+.*", "", conclusion).strip()
        if len(conclusion) < 30:
            continue
        chunks.append(
            DocChunk(
                category=category,
                source_file=path.name,
                doc_path=_rel_doc_path(path),
                chunk_id=f"{path.stem}#s{idx}",
                title=title[:80],
                text=f"主题：{title}\n正确结论：\n{conclusion}"[:2500],
                max_questions=2,
            )
        )
    return chunks


def _chunk_knowledge(path: Path, category: str) -> List[DocChunk]:
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^#{1,3}\s+", text)
    chunks: List[DocChunk] = []
    if len(sections) > 1:
        for idx, section in enumerate(sections[1:], start=1):
            lines = section.strip().splitlines()
            if not lines:
                continue
            title = lines[0].strip()
            body = "\n".join(lines[1:]).strip()
            if len(body) < 60:
                continue
            chunks.append(
                DocChunk(
                    category=category,
                    source_file=path.name,
                    doc_path=_rel_doc_path(path),
                    chunk_id=f"{path.stem}#h{idx}",
                    title=title[:80],
                    text=f"{title}\n{body}"[:2500],
                    max_questions=2,
                )
            )
    if not chunks:
        # 按空行分段
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if len(p.strip()) >= 80]
        for idx, para in enumerate(paras[:12], start=1):
            chunks.append(
                DocChunk(
                    category=category,
                    source_file=path.name,
                    doc_path=_rel_doc_path(path),
                    chunk_id=f"{path.stem}#p{idx}",
                    title=f"{path.stem}-{idx}",
                    text=para[:2500],
                    max_questions=2,
                )
            )
    return chunks


def _resolve_category_dir(docs_root: Path, category: str) -> Path:
    """解析栏目文档目录：支持 --docs-root docs 或项目根。"""
    rel = CATEGORY_DIR_MAP[category]
    candidates = [
        docs_root / Path(rel).name,
        docs_root / rel,
        PROJECT_ROOT / rel,
    ]
    for path in candidates:
        if path.exists() and path.is_dir():
            return path.resolve()
    return candidates[0].resolve()


def _collect_chunks(categories: Sequence[str], docs_root: Path) -> List[DocChunk]:
    chunks: List[DocChunk] = []
    for category in categories:
        dir_path = _resolve_category_dir(docs_root, category)
        if not dir_path.exists():
            print(f"[warn] 目录不存在，跳过: {dir_path}")
            continue
        files = sorted(dir_path.glob("*.md"))
        print(f"[scan] {category}: {dir_path} ({len(files)} files)")
        for path in files:
            if category == CATEGORY_PROFILE:
                chunks.extend(_chunk_profile(path, category))
            elif category == CATEGORY_RUMOR:
                chunks.extend(_chunk_rumor(path, category))
            else:
                chunks.extend(_chunk_knowledge(path, category))
    return chunks


def _extract_json_array(text: str) -> List[Dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start < 0 or end <= start:
        # 兼容单对象
        obj_start = cleaned.find("{")
        obj_end = cleaned.rfind("}")
        if obj_start >= 0 and obj_end > obj_start:
            return [json.loads(cleaned[obj_start : obj_end + 1])]
        raise ValueError(f"无法解析 JSON 数组: {cleaned[:200]}")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("出题结果不是数组")
    return [item for item in data if isinstance(item, dict)]


def _profile_fact_hint(category: str) -> str:
    if category == CATEGORY_PROFILE:
        return (
            "优先覆盖：父母、昵称/乳名/外号、认养、子女、组合、现居/迁居、谱系号、去世。"
            "fact_type 取值：父母/昵称/认养/子女/组合/现居/迁居/去世/谱系/其他。"
        )
    if category == CATEGORY_RUMOR:
        return (
            "只根据「正确结论」出题，禁止把谣言说法当作正确答案。"
            "fact_type 取值：分类/天敌/食性/保护/繁殖/其他。"
        )
    return "fact_type 取值：行为/分布/疾病/食性/繁殖/保护/其他。"


def _generate_questions_for_chunk(llm: Any, chunk: DocChunk) -> List[EvalSample]:
    n = max(1, chunk.max_questions)
    prompt = f"""
你是知识库评测出题助手。请仅基于给定原文，生成 {n} 条可核对的事实问答题。

硬性规则：
1. 问题和答案必须能被原文直接支撑，禁止杜撰。
2. gold_answer 尽量短（一句话或关键实体/数值）。
3. 输出严格 JSON 数组，每项字段：question, gold_answer, fact_type, evidence_span。
4. evidence_span 从原文摘一句短证据。
5. {_profile_fact_hint(chunk.category)}

栏目：{chunk.category}
来源：{chunk.source_file}
主题：{chunk.title}

原文：
{chunk.text}
""".strip()
    response = llm.invoke(
        [
            SystemMessage(content="你只输出合法 JSON 数组，不要 Markdown 解释。"),
            HumanMessage(content=prompt),
        ]
    )
    content = response.content
    if isinstance(content, list):
        text = "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content
        )
    else:
        text = str(content)
    items = _extract_json_array(text)
    samples: List[EvalSample] = []
    for idx, item in enumerate(items[:n], start=1):
        question = str(item.get("question", "")).strip()
        gold = str(item.get("gold_answer", "")).strip()
        if not question or not gold:
            continue
        fact_type = str(item.get("fact_type", "其他")).strip() or "其他"
        evidence_span = str(item.get("evidence_span", "")).strip()
        samples.append(
            EvalSample(
                sample_id=f"{chunk.chunk_id}__q{idx}",
                question=question,
                gold_answer=gold,
                gold_sources=[chunk.source_file],
                category=chunk.category,
                fact_type=fact_type,
                doc_path=chunk.doc_path,
                chunk_id=chunk.chunk_id,
                evidence_span=evidence_span,
            )
        )
    return samples


def _write_jsonl(path: Path, samples: Sequence[EvalSample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        for sample in samples:
            fp.write(json.dumps(asdict(sample), ensure_ascii=False) + "\n")


def _load_jsonl(path: Path) -> List[EvalSample]:
    if not path.exists():
        raise FileNotFoundError(f"评测集不存在: {path}")
    records: List[EvalSample] = []
    with path.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            raw = line.strip()
            if not raw:
                continue
            obj = json.loads(raw)
            question = str(obj.get("question", "")).strip()
            gold = str(obj.get("gold_answer", "")).strip()
            if not question or not gold:
                raise ValueError(f"第 {line_no} 行缺少 question/gold_answer")
            records.append(
                EvalSample(
                    sample_id=str(obj.get("sample_id") or obj.get("id") or f"line_{line_no}"),
                    question=question,
                    gold_answer=gold,
                    gold_sources=[str(x).strip() for x in obj.get("gold_sources", []) if str(x).strip()],
                    category=str(obj.get("category", "")).strip(),
                    fact_type=str(obj.get("fact_type", "其他")).strip() or "其他",
                    doc_path=str(obj.get("doc_path", "")).strip(),
                    chunk_id=str(obj.get("chunk_id", "")).strip(),
                    evidence_span=str(obj.get("evidence_span", "")).strip(),
                )
            )
    if not records:
        raise ValueError(f"评测集为空: {path}")
    return records


def _call_neo4j_qa(
    *,
    question: str,
    category: str,
    top_k: int,
    database: str,
    persona: str,
    strict_mode: bool,
) -> Dict[str, Any]:
    fn = getattr(neo4j_qa, "func", None)
    payload = {
        "question": question,
        "top_k": top_k,
        "database": database,
        "strict_mode": strict_mode,
        "persona": persona,
        "category": category or "",
    }
    if callable(fn):
        raw = fn(**payload)
    else:
        raw = neo4j_qa.invoke(payload)
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _gap_label(
    *,
    rows_count: int,
    char_f1: float,
    answer_mode: str,
    predicted_answer: str,
    gold_answer: str,
) -> str:
    answer_l = predicted_answer or ""
    no_evidence_cues = ("暂无依据", "知识库中未", "未收录", "检索0条", "没有找到", "暂无足够依据")
    gold_compact = re.sub(r"\s+", "", gold_answer or "")
    ans_compact = re.sub(r"\s+", "", answer_l)
    # 从 gold 抽关键实体/数字，判断是否真正命中
    key_tokens = re.findall(r"\d{2,4}(?:[-–—－]\d{2,4})?|[\u4e00-\u9fff]{2,8}", gold_compact)
    stop = {"母亲是", "父亲是", "母亲为", "父亲为", "分别", "哪些", "什么", "以及", "因为", "如何"}
    key_tokens = [t for t in key_tokens if t not in stop and len(t) >= 2][:8]
    entity_hits = 0
    if key_tokens:
        for t in key_tokens:
            t2 = t.replace("–", "-").replace("—", "-")
            if t in ans_compact or t2 in ans_compact:
                entity_hits += 1
        entity_cover = entity_hits / max(1, len(key_tokens))
    else:
        entity_cover = 1.0 if gold_compact and gold_compact in ans_compact else 0.0

    if rows_count <= 0:
        return "no_hit"
    if any(cue in answer_l for cue in no_evidence_cues) and entity_cover < 0.5:
        return "no_hit"
    if answer_mode == "llm_fallback":
        return "hallucination_risk"
    if entity_cover >= 0.5 or (gold_compact and gold_compact in ans_compact):
        return "ok"
    if entity_cover > 0 or 0.15 <= char_f1 < 0.55:
        return "partial"
    if rows_count > 0 and entity_cover == 0 and len(answer_l) > 40:
        return "hallucination_risk"
    return "partial"


def _suggest_predicates(fact_type: str) -> List[str]:
    return list(FACT_TYPE_PREDICATES.get(fact_type, [])) or list(FACT_TYPE_PREDICATES["其他"])


def _llm_judge_optional(
    *,
    question: str,
    gold_answer: str,
    predicted_answer: str,
    evidences: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    llm = get_text_generation_model()
    evidence_lines = []
    for idx, item in enumerate(list(evidences)[:8], start=1):
        evidence_lines.append(
            f"{idx}. {item.get('subject', '')}-[{item.get('predicate', '')}]->{item.get('object', '')}"
        )
    prompt = (
        "按以下标准打分并只输出 JSON：\n"
        "- correctness: 0~5\n"
        "- groundedness: 0~5\n"
        "- reason: 一句话\n\n"
        f"question: {question}\n"
        f"gold_answer: {gold_answer}\n"
        f"predicted_answer: {predicted_answer}\n"
        f"evidences:\n" + ("\n".join(evidence_lines) or "(无)") + "\n"
        '仅输出：{"correctness": number, "groundedness": number, "reason": "..."}'
    )
    response = llm.invoke(
        [
            SystemMessage(content="你是严格的问答评测器，只输出合法 JSON。"),
            HumanMessage(content=prompt),
        ]
    )
    text = str(response.content)
    start, end = text.find("{"), text.rfind("}")
    parsed = json.loads(text[start : end + 1])
    return {
        "correctness": max(0.0, min(5.0, float(parsed.get("correctness", 0)))),
        "groundedness": max(0.0, min(5.0, float(parsed.get("groundedness", 0)))),
        "reason": str(parsed.get("reason", "")).strip(),
    }


def _evaluate_one(
    sample: EvalSample,
    *,
    qa_top_k: int,
    hit_at_k: int,
    database: str,
    persona: str,
    strict_mode: bool,
    use_llm_judge: bool,
) -> Dict[str, Any]:
    qa_result = _call_neo4j_qa(
        question=sample.question,
        category=sample.category,
        top_k=qa_top_k,
        database=database,
        persona=persona,
        strict_mode=strict_mode,
    )
    predicted = str(qa_result.get("answer", "") or "")
    evidences = qa_result.get("evidences", [])
    if not isinstance(evidences, list):
        evidences = []
    retrieved_sources = [
        str(item.get("source_file", "")).strip()
        for item in evidences
        if isinstance(item, dict) and str(item.get("source_file", "")).strip()
    ]
    rows_count = int(qa_result.get("rows_count", 0) or 0)
    char_f1 = _char_f1(predicted, sample.gold_answer)
    exact = _exact_match(predicted, sample.gold_answer)
    hit = _source_hit(retrieved_sources, sample.gold_sources, top_n=hit_at_k)
    groundedness = answer_groundedness(predicted, evidences)
    answer_mode = str(qa_result.get("answer_mode", "") or "")
    gap = _gap_label(
        rows_count=rows_count,
        char_f1=char_f1,
        answer_mode=answer_mode,
        predicted_answer=predicted,
        gold_answer=sample.gold_answer,
    )
    payload: Dict[str, Any] = {
        "id": sample.sample_id,
        "question": sample.question,
        "gold_answer": sample.gold_answer,
        "predicted_answer": predicted,
        "category": sample.category,
        "fact_type": sample.fact_type,
        "doc_path": sample.doc_path,
        "chunk_id": sample.chunk_id,
        "evidence_span": sample.evidence_span,
        "exact_match": round(exact, 6),
        "char_f1": round(char_f1, 6),
        f"hit@{hit_at_k}": round(hit, 6),
        "groundedness": round(groundedness, 6),
        "rows_count": rows_count,
        "answer_mode": answer_mode,
        "gap_label": gap,
        "suggested_predicates": _suggest_predicates(sample.fact_type),
        "gold_sources": sample.gold_sources,
        "retrieved_sources_top10": retrieved_sources[:10],
        "online_quality": qa_result.get("quality", {}),
        "ok": bool(qa_result.get("ok")),
        "error": qa_result.get("error", ""),
    }
    if use_llm_judge:
        try:
            scores = _llm_judge_optional(
                question=sample.question,
                gold_answer=sample.gold_answer,
                predicted_answer=predicted,
                evidences=evidences,
            )
            payload["llm_correctness_0_5"] = round(float(scores["correctness"]), 4)
            payload["llm_groundedness_0_5"] = round(float(scores["groundedness"]), 4)
            payload["llm_judge_reason"] = scores["reason"]
        except Exception as exc:  # noqa: BLE001
            payload["llm_judge_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def _build_summary(details: Sequence[Dict[str, Any]], *, hit_at_k: int) -> Dict[str, Any]:
    metric_hit = f"hit@{hit_at_k}"
    labels = [str(item.get("gap_label", "")) for item in details]
    by_label: Dict[str, int] = {}
    for label in labels:
        by_label[label] = by_label.get(label, 0) + 1
    by_category: Dict[str, int] = {}
    for item in details:
        cat = str(item.get("category", "") or "未指定")
        by_category[cat] = by_category.get(cat, 0) + 1
    summary: Dict[str, Any] = {
        "sample_count": len(details),
        "exact_match_avg": round(_safe_mean(float(item.get("exact_match", 0.0)) for item in details), 6),
        "char_f1_avg": round(_safe_mean(float(item.get("char_f1", 0.0)) for item in details), 6),
        f"{metric_hit}_avg": round(_safe_mean(float(item.get(metric_hit, 0.0)) for item in details), 6),
        "groundedness_avg": round(_safe_mean(float(item.get("groundedness", 0.0)) for item in details), 6),
        "ok_rate": round(_safe_mean(1.0 if item.get("gap_label") == "ok" else 0.0 for item in details), 6),
        "no_hit_rate": round(_safe_mean(1.0 if item.get("gap_label") == "no_hit" else 0.0 for item in details), 6),
        "gap_label_counts": by_label,
        "category_counts": by_category,
    }
    llm_c = [
        float(item["llm_correctness_0_5"])
        for item in details
        if "llm_correctness_0_5" in item and not math.isnan(float(item["llm_correctness_0_5"]))
    ]
    if llm_c:
        summary["llm_correctness_avg_0_5"] = round(_safe_mean(llm_c), 6)
    return summary


def _export_gaps_and_focus(
    details: Sequence[Dict[str, Any]],
    *,
    gaps_path: Path,
    focus_path: Path,
) -> tuple[int, int]:
    gaps = [item for item in details if item.get("gap_label") not in {"ok", ""}]
    gaps_path.parent.mkdir(parents=True, exist_ok=True)
    with gaps_path.open("w", encoding="utf-8") as fp:
        for item in gaps:
            gap_row = {
                "id": item.get("id"),
                "category": item.get("category"),
                "doc_path": item.get("doc_path"),
                "fact_type": item.get("fact_type"),
                "gap_label": item.get("gap_label"),
                "question": item.get("question"),
                "gold_answer": item.get("gold_answer"),
                "predicted_answer": item.get("predicted_answer"),
                "evidence_span": item.get("evidence_span"),
                "suggested_predicates": item.get("suggested_predicates", []),
                "rows_count": item.get("rows_count"),
                "char_f1": item.get("char_f1"),
                "补缺建议": (
                    f"原文可答「{item.get('gold_answer', '')}」，"
                    f"系统缺口={item.get('gap_label')}；"
                    f"建议补抽谓词 {','.join(item.get('suggested_predicates') or []) or '相关事实'}。"
                ),
            }
            fp.write(json.dumps(gap_row, ensure_ascii=False) + "\n")

    # focus_questions：按 doc_path 聚合缺口问题
    grouped: Dict[str, List[str]] = {}
    for item in gaps:
        doc_path = str(item.get("doc_path", "")).strip()
        question = str(item.get("question", "")).strip()
        if not doc_path or not question:
            continue
        grouped.setdefault(doc_path, [])
        if question not in grouped[doc_path]:
            grouped[doc_path].append(question)
    focus_payload = {
        "groups": [
            {"doc_path": doc_path, "questions": questions}
            for doc_path, questions in sorted(grouped.items())
        ]
    }
    focus_path.parent.mkdir(parents=True, exist_ok=True)
    focus_path.write_text(json.dumps(focus_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(gaps), len(focus_payload["groups"])


def _chunk_id_from_sample(obj: Dict[str, Any]) -> str:
    """从评测样本提取 chunk_id（优先字段，否则从 sample_id 解析）。"""
    chunk_id = str(obj.get("chunk_id", "")).strip()
    if chunk_id:
        return chunk_id
    sample_id = str(obj.get("sample_id") or obj.get("id") or "").strip()
    if "__q" in sample_id:
        return sample_id.rsplit("__q", 1)[0]
    return sample_id


def _load_tested_chunk_ids(dataset_globs: Sequence[str]) -> set[str]:
    """从历史评测 JSONL 收集已测 chunk_id。"""
    tested: set[str] = set()
    paths: List[Path] = []
    for pattern in dataset_globs:
        pattern = str(pattern).strip().replace("\\", "/")
        if not pattern:
            continue
        if any(ch in pattern for ch in "*?[]"):
            paths.extend(sorted(PROJECT_ROOT.glob(pattern)))
        else:
            paths.append((PROJECT_ROOT / pattern).resolve())
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            chunk_id = _chunk_id_from_sample(obj)
            if chunk_id:
                tested.add(chunk_id)
    return tested


def _interleave_chunks_by_category(
    chunks: Sequence[DocChunk],
    *,
    seed: int = 42,
) -> List[DocChunk]:
    """按栏目轮询切块，避免 max_questions 被单一栏目占满。"""
    import random

    buckets: Dict[str, List[DocChunk]] = {}
    for chunk in chunks:
        buckets.setdefault(chunk.category, []).append(chunk)
    ordered: List[DocChunk] = []
    rng = random.Random(int(seed))
    for cat in list(buckets):
        rng.shuffle(buckets[cat])
    while any(buckets.values()):
        for cat in list(buckets.keys()):
            if buckets[cat]:
                ordered.append(buckets[cat].pop(0))
            if not buckets[cat]:
                buckets.pop(cat, None)
    return ordered


def cmd_generate(args: argparse.Namespace) -> Path:
    categories = _parse_categories(args.categories)
    docs_root = (PROJECT_ROOT / args.docs_root).resolve()
    chunks = _collect_chunks(categories, docs_root)
    if not chunks:
        raise RuntimeError("未收集到任何文档切块，请检查 docs 目录")

    seed = int(getattr(args, "seed", 42) or 42)
    skip_tested = bool(getattr(args, "skip_tested", False))
    tested_ids: set[str] = set()
    if skip_tested:
        globs = [
            g.strip()
            for g in str(getattr(args, "tested_datasets", "") or "").split(",")
            if g.strip()
        ]
        if not globs:
            globs = ["data/eval/kb_auto*.jsonl"]
        tested_ids = _load_tested_chunk_ids(globs)
        before = len(chunks)
        chunks = [c for c in chunks if c.chunk_id not in tested_ids]
        print(
            f"[generate] skip_tested=1 tested_chunks={len(tested_ids)} "
            f"remain={len(chunks)}/{before} datasets={globs}"
        )
        if not chunks:
            raise RuntimeError("跳过已测 chunk 后无剩余切块，请扩大文档或关闭 --skip-tested")

    chunks = _interleave_chunks_by_category(chunks, seed=seed)

    # 轮询切块直到达到 max_questions；每块只抽 1 题以扩大文档覆盖
    max_questions = max(1, int(args.max_questions))
    per_chunk = max(1, int(getattr(args, "per_chunk", 1) or 1))
    llm = get_text_generation_model()
    samples: List[EvalSample] = []
    print(
        f"[generate] categories={categories} chunks={len(chunks)} "
        f"max_questions={max_questions} per_chunk={per_chunk} seed={seed}"
    )
    for idx, chunk in enumerate(chunks, start=1):
        if len(samples) >= max_questions:
            break
        remain = max_questions - len(samples)
        chunk.max_questions = min(per_chunk, remain, chunk.max_questions)
        try:
            got = _generate_questions_for_chunk(llm, chunk)
            samples.extend(got)
            print(
                f"  [{idx}/{len(chunks)}] {chunk.category} {chunk.chunk_id} -> +{len(got)} "
                f"(total={len(samples)})"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [{idx}/{len(chunks)}] FAIL {chunk.chunk_id}: {type(exc).__name__}: {exc}")

    if not samples:
        raise RuntimeError("出题失败：未生成任何样本")

    if args.output.strip():
        out_path = (PROJECT_ROOT / args.output).resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = PROJECT_ROOT / "data" / "eval" / f"kb_auto_{stamp}.jsonl"
    _write_jsonl(out_path, samples[:max_questions])
    cat_counts: Dict[str, int] = {}
    for sample in samples[:max_questions]:
        cat_counts[sample.category] = cat_counts.get(sample.category, 0) + 1
    print(f"[generate] wrote {min(len(samples), max_questions)} samples -> {out_path}")
    print(f"[generate] category_counts={cat_counts}")
    return out_path


def cmd_run(args: argparse.Namespace, dataset_path: Optional[Path] = None) -> Dict[str, Any]:
    path = dataset_path or (PROJECT_ROOT / args.dataset).resolve()
    samples = _load_jsonl(path)
    if args.limit and int(args.limit) > 0:
        samples = samples[: int(args.limit)]

    hit_at_k = max(1, int(args.hit_at_k))
    details: List[Dict[str, Any]] = []
    print(f"[run] dataset={path} samples={len(samples)}")
    for idx, sample in enumerate(samples, start=1):
        print(f"  [{idx}/{len(samples)}] {sample.category} | {sample.question[:40]}")
        try:
            details.append(
                _evaluate_one(
                    sample,
                    qa_top_k=max(1, int(args.qa_top_k)),
                    hit_at_k=hit_at_k,
                    database=str(args.database or ""),
                    persona=str(args.persona or "educator"),
                    strict_mode=not bool(args.no_strict),
                    use_llm_judge=bool(args.use_llm_judge),
                )
            )
        except Exception as exc:  # noqa: BLE001
            details.append(
                {
                    "id": sample.sample_id,
                    "question": sample.question,
                    "gold_answer": sample.gold_answer,
                    "predicted_answer": "",
                    "category": sample.category,
                    "fact_type": sample.fact_type,
                    "doc_path": sample.doc_path,
                    "gap_label": "no_hit",
                    "char_f1": 0.0,
                    "exact_match": 0.0,
                    f"hit@{hit_at_k}": 0.0,
                    "groundedness": 0.0,
                    "rows_count": 0,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "suggested_predicates": _suggest_predicates(sample.fact_type),
                }
            )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = (PROJECT_ROOT / (args.output_dir or "reports")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"kb_eval_{stamp}.json"
    gaps_path = output_dir / f"kb_gaps_{stamp}.jsonl"
    focus_path = output_dir / f"kb_focus_questions_{stamp}.json"

    summary = _build_summary(details, hit_at_k=hit_at_k)
    gap_count, focus_groups = _export_gaps_and_focus(details, gaps_path=gaps_path, focus_path=focus_path)
    report = {
        "timestamp": datetime.now().isoformat(),
        "dataset": str(path),
        "summary": summary,
        "details": details,
        "artifacts": {
            "report": str(report_path),
            "gaps": str(gaps_path),
            "focus_questions": str(focus_path),
            "gap_count": gap_count,
            "focus_groups": focus_groups,
        },
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== KB Doc Eval Summary ===")
    print(f"样本数: {summary.get('sample_count', 0)}")
    print(f"Char F1: {summary.get('char_f1_avg', 0):.4f}")
    print(f"Hit@{hit_at_k}: {summary.get(f'hit@{hit_at_k}_avg', 0):.4f}")
    print(f"OK Rate: {summary.get('ok_rate', 0):.4f}")
    print(f"No-Hit Rate: {summary.get('no_hit_rate', 0):.4f}")
    print(f"gap_label_counts: {summary.get('gap_label_counts', {})}")
    print(f"report: {report_path}")
    print(f"gaps: {gaps_path} ({gap_count})")
    print(f"focus_questions: {focus_path} ({focus_groups} groups)")
    return report


def cmd_all(args: argparse.Namespace) -> Dict[str, Any]:
    dataset = cmd_generate(args)
    return cmd_run(args, dataset_path=dataset)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="基于 docs 三栏目自动出题，并用 neo4j_qa 评估知识库缺口。"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common_gen(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--docs-root",
            default="docs",
            help="文档根目录（相对项目根），默认 docs。",
        )
        p.add_argument(
            "--categories",
            default="熊猫知识,熊猫谣言,熊猫资料",
            help="逗号分隔栏目：熊猫知识/熊猫谣言/熊猫资料",
        )
        p.add_argument("--max-questions", type=int, default=60, help="最多生成多少题。")
        p.add_argument(
            "--per-chunk",
            type=int,
            default=1,
            help="每个文档切块最多出几题（默认1，便于扩大覆盖面挖缺口）。",
        )
        p.add_argument(
            "--output",
            default="",
            help="generate 输出 JSONL；默认 data/eval/kb_auto_<timestamp>.jsonl",
        )
        p.add_argument(
            "--seed",
            type=int,
            default=42,
            help="切块打散随机种子（换 seed 可改变选题顺序）。",
        )
        p.add_argument(
            "--skip-tested",
            action="store_true",
            help="跳过历史评测集中已出现过的 chunk_id，优先挖未测块。",
        )
        p.add_argument(
            "--tested-datasets",
            default="data/eval/kb_auto*.jsonl",
            help="已测数据集 glob，逗号分隔；配合 --skip-tested 使用。",
        )

    def add_common_run(p: argparse.ArgumentParser) -> None:
        p.add_argument("--qa-top-k", type=int, default=20)
        p.add_argument("--hit-at-k", type=int, default=5)
        p.add_argument("--database", default="")
        p.add_argument("--persona", default="educator", choices=["educator", "kid"])
        p.add_argument("--no-strict", action="store_true", help="关闭 strict_mode。")
        p.add_argument("--use-llm-judge", action="store_true", help="启用 LLM 语义裁判。")
        p.add_argument("--limit", type=int, default=0, help="只评测前 N 条，0 表示全部。")
        p.add_argument("--output-dir", default="reports", help="报告输出目录。")

    p_gen = sub.add_parser("generate", help="从文档自动出题，写出 JSONL 评测集。")
    add_common_gen(p_gen)

    p_run = sub.add_parser("run", help="对已有 JSONL 评测集调用 neo4j_qa 并输出缺口报告。")
    p_run.add_argument("--dataset", required=True, help="JSONL 评测集路径。")
    add_common_run(p_run)

    p_all = sub.add_parser("all", help="出题 + 评测一条龙。")
    add_common_gen(p_all)
    add_common_run(p_all)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.command == "generate":
            cmd_generate(args)
        elif args.command == "run":
            cmd_run(args)
        elif args.command == "all":
            cmd_all(args)
        else:
            parser.error(f"未知子命令: {args.command}")
            return 2
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
