"""评估 Neo4j 知识库问答效果的离线工具。"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from model.factory import get_text_generation_model
from tools.qa_metrics import answer_groundedness
from tools.neo4j_qa import neo4j_qa


@dataclass
class EvalSample:
    """单条评测样本。"""

    sample_id: str
    question: str
    gold_answer: str
    gold_sources: List[str]


def _normalize_text(text: str) -> str:
    """归一化文本，用于稳定计算匹配分数。"""
    compact = re.sub(r"\s+", "", text or "")
    return compact.strip().lower()


def _char_f1(prediction: str, reference: str) -> float:
    """按字符级重合度计算 F1。"""
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

    overlap = 0
    for ch, pred_count in pred_counts.items():
        overlap += min(pred_count, ref_counts.get(ch, 0))
    if overlap == 0:
        return 0.0

    precision = overlap / max(1, len(pred))
    recall = overlap / max(1, len(ref))
    return 2 * precision * recall / max(1e-12, precision + recall)


def _exact_match(prediction: str, reference: str) -> float:
    """计算归一化后的完全匹配。"""
    return 1.0 if _normalize_text(prediction) == _normalize_text(reference) else 0.0


def _first_source_hits(
    predicted_sources: Sequence[str],
    gold_sources: Sequence[str],
    *,
    top_n: int,
) -> float:
    """判断 gold source 是否命中 top-n 检索来源。"""
    if not gold_sources:
        return 0.0
    pred_norm = {_normalize_text(s) for s in predicted_sources[: max(1, top_n)] if s}
    gold_norm = {_normalize_text(s) for s in gold_sources if s}
    if not pred_norm or not gold_norm:
        return 0.0
    return 1.0 if pred_norm.intersection(gold_norm) else 0.0


def _safe_mean(values: Iterable[float]) -> float:
    value_list = list(values)
    if not value_list:
        return 0.0
    return float(statistics.fmean(value_list))


def _load_dataset(path: Path) -> List[EvalSample]:
    """读取 JSONL 评测集。"""
    if not path.exists():
        raise FileNotFoundError(f"评测集不存在: {path}")
    records: List[EvalSample] = []
    with path.open("r", encoding="utf-8") as fp:
        for line_no, line in enumerate(fp, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception as exc:
                raise ValueError(f"JSONL 解析失败，第 {line_no} 行: {exc}") from exc

            question = str(obj.get("question", "")).strip()
            gold_answer = str(obj.get("gold_answer", "")).strip()
            sample_id = str(obj.get("id", f"line_{line_no}")).strip() or f"line_{line_no}"
            gold_sources = [str(item).strip() for item in obj.get("gold_sources", []) if str(item).strip()]
            if not question:
                raise ValueError(f"第 {line_no} 行缺少 question")
            if not gold_answer:
                raise ValueError(f"第 {line_no} 行缺少 gold_answer")

            records.append(
                EvalSample(
                    sample_id=sample_id,
                    question=question,
                    gold_answer=gold_answer,
                    gold_sources=gold_sources,
                )
            )
    if not records:
        raise ValueError(f"评测集为空: {path}")
    return records


def _build_llm_judge_prompt(
    *,
    question: str,
    gold_answer: str,
    predicted_answer: str,
    evidences: Sequence[Dict[str, Any]],
) -> str:
    evidence_lines = []
    for idx, item in enumerate(evidences[:8], start=1):
        evidence_lines.append(
            f"{idx}. {item.get('subject', '')} -[{item.get('predicate', '')}]-> {item.get('object', '')} | 证据: {item.get('evidence', '')}"
        )
    evidence_text = "\n".join(evidence_lines) if evidence_lines else "(无检索证据)"
    return (
        "请你作为问答评估器，按以下标准打分并只输出 JSON：\n"
        "- correctness: 0~5，答案与 gold_answer 的一致程度\n"
        "- groundedness: 0~5，答案是否被给定 evidences 支撑\n"
        "- reason: 一句话原因\n\n"
        f"question: {question}\n"
        f"gold_answer: {gold_answer}\n"
        f"predicted_answer: {predicted_answer}\n"
        f"evidences:\n{evidence_text}\n\n"
        '仅输出：{"correctness": number, "groundedness": number, "reason": "..."}'
    )


def _llm_judge(
    *,
    question: str,
    gold_answer: str,
    predicted_answer: str,
    evidences: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    """可选：用模型做语义裁判。"""
    llm = get_text_generation_model()
    prompt = _build_llm_judge_prompt(
        question=question,
        gold_answer=gold_answer,
        predicted_answer=predicted_answer,
        evidences=evidences,
    )
    response = llm.invoke(
        [
            SystemMessage(content="你是严格的问答评测器，只输出合法 JSON。"),
            HumanMessage(content=prompt),
        ]
    )
    text = str(response.content)
    if isinstance(response.content, list):
        text = "\n".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in response.content
        )
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"LLM 评测输出非 JSON: {text}")
    parsed = json.loads(text[start : end + 1])
    correctness = float(parsed.get("correctness", 0))
    groundedness = float(parsed.get("groundedness", 0))
    reason = str(parsed.get("reason", "")).strip()
    return {
        "correctness": max(0.0, min(5.0, correctness)),
        "groundedness": max(0.0, min(5.0, groundedness)),
        "reason": reason,
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
    """评估单条样本。"""
    result_json = neo4j_qa.invoke(
        {
            "question": sample.question,
            "top_k": qa_top_k,
            "database": database,
            "strict_mode": strict_mode,
            "persona": persona,
        }
    )
    qa_result = json.loads(result_json)

    predicted_answer = str(qa_result.get("answer", "") or "")
    evidences = qa_result.get("evidences", [])
    if not isinstance(evidences, list):
        evidences = []
    retrieved_sources = [str(item.get("source_file", "")).strip() for item in evidences if isinstance(item, dict)]
    retrieved_sources = [item for item in retrieved_sources if item]

    exact_match = _exact_match(predicted_answer, sample.gold_answer)
    char_f1 = _char_f1(predicted_answer, sample.gold_answer)
    hit_at_k_score = _first_source_hits(retrieved_sources, sample.gold_sources, top_n=hit_at_k)
    groundedness = answer_groundedness(predicted_answer, evidences)

    payload: Dict[str, Any] = {
        "id": sample.sample_id,
        "question": sample.question,
        "gold_answer": sample.gold_answer,
        "predicted_answer": predicted_answer,
        "exact_match": round(exact_match, 6),
        "char_f1": round(char_f1, 6),
        f"hit@{hit_at_k}": round(hit_at_k_score, 6),
        "groundedness": round(groundedness, 6),
        "gold_sources": sample.gold_sources,
        "retrieved_sources_top10": retrieved_sources[:10],
        "rows_count": int(qa_result.get("rows_count", 0) or 0),
        "online_quality": qa_result.get("quality", {}),
        "ok": bool(qa_result.get("ok")),
        "error": qa_result.get("error", ""),
    }
    if use_llm_judge:
        try:
            llm_scores = _llm_judge(
                question=sample.question,
                gold_answer=sample.gold_answer,
                predicted_answer=predicted_answer,
                evidences=evidences,
            )
            payload["llm_correctness_0_5"] = round(float(llm_scores["correctness"]), 4)
            payload["llm_groundedness_0_5"] = round(float(llm_scores["groundedness"]), 4)
            payload["llm_judge_reason"] = llm_scores["reason"]
        except Exception as exc:
            payload["llm_judge_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def _build_summary(records: Sequence[Dict[str, Any]], *, hit_at_k: int) -> Dict[str, Any]:
    """汇总总体指标。"""
    metric_hit = f"hit@{hit_at_k}"
    summary: Dict[str, Any] = {
        "sample_count": len(records),
        "exact_match_avg": round(_safe_mean(float(item.get("exact_match", 0.0)) for item in records), 6),
        "char_f1_avg": round(_safe_mean(float(item.get("char_f1", 0.0)) for item in records), 6),
        f"{metric_hit}_avg": round(_safe_mean(float(item.get(metric_hit, 0.0)) for item in records), 6),
        "groundedness_avg": round(_safe_mean(float(item.get("groundedness", 0.0)) for item in records), 6),
        "ok_rate": round(_safe_mean(1.0 if item.get("ok") else 0.0 for item in records), 6),
    }

    llm_correctness_values = [
        float(item["llm_correctness_0_5"])
        for item in records
        if "llm_correctness_0_5" in item and not math.isnan(float(item["llm_correctness_0_5"]))
    ]
    llm_groundedness_values = [
        float(item["llm_groundedness_0_5"])
        for item in records
        if "llm_groundedness_0_5" in item and not math.isnan(float(item["llm_groundedness_0_5"]))
    ]
    if llm_correctness_values:
        summary["llm_correctness_avg_0_5"] = round(_safe_mean(llm_correctness_values), 6)
    if llm_groundedness_values:
        summary["llm_groundedness_avg_0_5"] = round(_safe_mean(llm_groundedness_values), 6)
    return summary


def _print_console_report(report: Dict[str, Any], *, hit_at_k: int, show_failures: int) -> None:
    summary = report.get("summary", {})
    metric_hit = f"hit@{hit_at_k}_avg"
    print("=== QA Evaluator Summary ===")
    print(f"样本数: {summary.get('sample_count', 0)}")
    print(f"Exact Match: {summary.get('exact_match_avg', 0):.4f}")
    print(f"Char F1: {summary.get('char_f1_avg', 0):.4f}")
    print(f"Hit@{hit_at_k}: {summary.get(metric_hit, 0):.4f}")
    print(f"Groundedness: {summary.get('groundedness_avg', 0):.4f}")
    print(f"OK Rate: {summary.get('ok_rate', 0):.4f}")
    if "llm_correctness_avg_0_5" in summary:
        print(f"LLM Correctness(0-5): {summary.get('llm_correctness_avg_0_5', 0):.4f}")
    if "llm_groundedness_avg_0_5" in summary:
        print(f"LLM Groundedness(0-5): {summary.get('llm_groundedness_avg_0_5', 0):.4f}")

    if show_failures <= 0:
        return
    details = list(report.get("details", []))
    details.sort(key=lambda item: float(item.get("char_f1", 0.0)))
    print("\n=== Lowest F1 Cases ===")
    for item in details[:show_failures]:
        print(f"- [{item.get('id', '')}] F1={float(item.get('char_f1', 0.0)):.4f} | Q={item.get('question', '')}")
        if item.get("error"):
            print(f"  error: {item['error']}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="评估 Neo4j 知识库问答准确率与检索命中率。")
    parser.add_argument(
        "--dataset",
        required=True,
        help="JSONL 评测集路径。每行需包含 question, gold_answer，可选 id, gold_sources。",
    )
    parser.add_argument(
        "--output",
        default="",
        help="输出报告 JSON 路径。默认写入 reports/qa_eval_<timestamp>.json。",
    )
    parser.add_argument("--qa-top-k", type=int, default=20, help="调用 neo4j_qa 的 top_k 参数。")
    parser.add_argument("--hit-at-k", type=int, default=5, help="来源命中率 Hit@k 的 k。")
    parser.add_argument("--database", default="", help="Neo4j 数据库名，默认读取环境变量。")
    parser.add_argument(
        "--persona",
        default="educator",
        choices=["educator", "kid"],
        help="评测时使用的回答 persona：educator / kid。",
    )
    parser.add_argument(
        "--strict",
        dest="strict_mode",
        action="store_true",
        help="启用严格模式（证据不足时明确返回暂无依据）。默认开启。",
    )
    parser.add_argument("--no-strict", dest="strict_mode", action="store_false", help="关闭严格模式。")
    parser.set_defaults(strict_mode=True)
    parser.add_argument("--limit", type=int, default=0, help="仅评估前 N 条样本，0 表示全部。")
    parser.add_argument(
        "--use-llm-judge",
        action="store_true",
        help="额外启用 LLM 语义裁判（有成本，速度较慢）。",
    )
    parser.add_argument("--show-failures", type=int, default=5, help="终端展示最差样本数量。")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    samples = _load_dataset(dataset_path)
    if args.limit and args.limit > 0:
        samples = samples[: args.limit]

    details: List[Dict[str, Any]] = []
    for idx, sample in enumerate(samples, start=1):
        print(f"[{idx}/{len(samples)}] evaluating: {sample.sample_id}")
        details.append(
            _evaluate_one(
                sample,
                qa_top_k=max(1, args.qa_top_k),
                hit_at_k=max(1, args.hit_at_k),
                database=str(args.database or ""),
                persona=str(args.persona or "educator"),
                strict_mode=bool(args.strict_mode),
                use_llm_judge=bool(args.use_llm_judge),
            )
        )

    report = {
        "meta": {
            "dataset": str(dataset_path),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "qa_top_k": max(1, args.qa_top_k),
            "hit_at_k": max(1, args.hit_at_k),
            "database": str(args.database or ""),
            "persona": str(args.persona or "educator"),
            "strict_mode": bool(args.strict_mode),
            "use_llm_judge": bool(args.use_llm_judge),
        },
        "summary": _build_summary(details, hit_at_k=max(1, args.hit_at_k)),
        "details": details,
    }

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = Path("reports") / f"qa_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    _print_console_report(report, hit_at_k=max(1, args.hit_at_k), show_failures=max(0, args.show_failures))
    print(f"\n评测报告已写入: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
