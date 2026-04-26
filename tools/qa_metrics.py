"""QA 质量评估通用指标。"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence


def answer_groundedness(answer: str, evidences: Sequence[Dict[str, Any]]) -> float:
    """
    粗略估计“回答是否引用了证据”。

    规则：
    - 命中证据短片段 +0.7
    - 命中关系词 predicate +0.3
    每条证据最多贡献 1.0，总体取平均。
    """
    answer_text = answer or ""
    if not answer_text or not evidences:
        return 0.0

    scores: List[float] = []
    for item in evidences:
        evidence = str(item.get("evidence", "")).strip()
        predicate = str(item.get("predicate", "")).strip()
        piece_score = 0.0
        if evidence:
            head = evidence[:20]
            tail = evidence[-20:] if len(evidence) > 24 else evidence
            if head and head in answer_text:
                piece_score += 0.7
            elif tail and tail in answer_text:
                piece_score += 0.7
        if predicate and predicate in answer_text:
            piece_score += 0.3
        scores.append(min(1.0, piece_score))
    if not scores:
        return 0.0
    return float(sum(scores) / len(scores))


def score_to_level(score_0_100: float) -> str:
    """将 0-100 分映射为可读等级。"""
    if score_0_100 >= 85:
        return "excellent"
    if score_0_100 >= 70:
        return "good"
    if score_0_100 >= 50:
        return "fair"
    return "poor"


def build_online_quality(
    *,
    answer: str,
    evidences: Sequence[Dict[str, Any]],
    supporting_sources_count: int,
    strict_mode: bool,
    no_evidence_phrase: str = "知识库暂无足够依据。",
) -> Dict[str, Any]:
    """
    构建单次问答质量评估。

    这是“在线无标注评估”，不依赖 gold answer。
    """
    evidence_count = len(evidences)
    if evidence_count <= 0:
        fallback_score = 90.0 if strict_mode and no_evidence_phrase in (answer or "") else 20.0
        return {
            "mode": "online_no_gold",
            "score_0_100": round(fallback_score, 2),
            "level": score_to_level(fallback_score),
            "groundedness": 0.0,
            "evidence_count": 0,
            "supporting_sources_count": 0,
            "retrieval_strength": 0.0,
            "notes": "无检索证据；若严格模式下明确拒答，则评分更高。",
        }

    groundedness = answer_groundedness(answer, evidences)
    source_support_ratio = min(1.0, supporting_sources_count / max(1, evidence_count))
    avg_total_score = sum(float(item.get("total_score", 0) or 0.0) for item in evidences) / max(1, evidence_count)
    retrieval_strength = min(1.0, avg_total_score / 12.0)

    score = 100.0 * (0.55 * groundedness + 0.25 * source_support_ratio + 0.20 * retrieval_strength)
    score = max(0.0, min(100.0, score))

    return {
        "mode": "online_no_gold",
        "score_0_100": round(score, 2),
        "level": score_to_level(score),
        "groundedness": round(float(groundedness), 6),
        "evidence_count": evidence_count,
        "supporting_sources_count": int(supporting_sources_count),
        "retrieval_strength": round(float(retrieval_strength), 6),
        "notes": "基于证据命中、来源支撑和检索强度的在线估计分。",
    }
