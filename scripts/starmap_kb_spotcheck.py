"""星图/金标准档案随机抽检：每批抽 5 只熊猫 × 默认 10 轮，调用 neo4j_qa 查漏补缺。

从星图 4–22 与 `熊猫资料.md` / `2` / `3` 收集个体条目，每轮随机抽样后
按原文应有字段生成问题，调用知识库作答，输出缺口清单与 focus 问题。

用法：
    python scripts/starmap_kb_spotcheck.py
    python scripts/starmap_kb_spotcheck.py --rounds 10 --batch-size 5 --seed 42
    python scripts/starmap_kb_spotcheck.py --dry-run
    python scripts/starmap_kb_spotcheck.py --rounds 1 --questions-per-panda 2
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.verify_starmap_daily import (  # noqa: E402
    expected_dims_from_text,
    split_panda_entries,
)

CATEGORY = "熊猫资料"
DOCS_DIR = ROOT / "docs" / "熊猫资料"
REPORT_DIR = ROOT / "reports"

SOURCE_FILES: Tuple[str, ...] = tuple(
    [f"百度百科熊猫星图{i}.md" for i in range(4, 23)]
    + ["熊猫资料.md", "熊猫资料2.md", "熊猫资料3.md"]
)

NO_EVIDENCE_CUES = (
    "暂无依据",
    "暂无完整依据",
    "知识库中未",
    "未收录",
    "检索0条",
    "没有找到",
    "暂无足够依据",
    "无法提供",
    "无法确认",
    "未检索到",
)

QUESTION_TEMPLATES: Dict[str, str] = {
    "pedigree": "大熊猫{name}的谱系号是多少？",
    "parents": "大熊猫{name}的父母是谁？",
    "birth_time": "大熊猫{name}是什么时候出生的？",
    "birth_place": "大熊猫{name}出生在哪里？",
    "sex": "大熊猫{name}的性别是什么？",
    "nick": "大熊猫{name}有哪些昵称或别名？",
}


@dataclass
class PandaDoc:
    """文档中的一只大熊猫档案。"""

    name: str
    text: str
    source_file: str
    dims: Set[str] = field(default_factory=set)


@dataclass
class SpotQuestion:
    """针对一只熊猫生成的抽检问题。"""

    panda: str
    dim: str
    question: str
    gold_answer: str
    gold_tokens: List[str]
    source_file: str


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("ascii", errors="replace"), flush=True)


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def load_panda_pool(files: Sequence[str] | None = None) -> List[PandaDoc]:
    """从指定 md 收集个体条目（同名多来源保留多条，抽检时按条目抽样）。"""
    names = list(files) if files else list(SOURCE_FILES)
    pool: List[PandaDoc] = []
    for fname in names:
        path = DOCS_DIR / fname
        if not path.is_file():
            _safe_print(f"跳过缺失文件: {_rel(path)}")
            continue
        for name, text in split_panda_entries(path.read_text(encoding="utf-8")):
            if not name:
                continue
            dims = expected_dims_from_text(text, name=name)
            pool.append(
                PandaDoc(
                    name=name,
                    text=text,
                    source_file=_rel(path),
                    dims=dims,
                )
            )
    return pool


def _extract_pedigree(text: str) -> Optional[str]:
    scan = re.sub(
        r"[\u4e00-\u9fffA-Za-z]{1,12}\s*[（(]谱系号\s*\d+[)）]",
        "",
        text,
    )
    m = re.search(r"谱系号\s*[：:]?\s*(\d+)", scan)
    return m.group(1) if m else None


def _extract_parent(text: str, kind: str) -> Optional[str]:
    if kind == "father":
        pats = [
            r"父亲[为是]\s*[「\"“]?大熊猫\s*([^，,。；;\s」\"”]{1,12})",
            r"父亲[为是]\s*[「\"“]?([^，,。；;\s」\"”]{1,12})",
            r"其父[为是]\s*[「\"“]?([^，,。；;\s」\"”]{1,12})",
            r"爸爸[为是]\s*[「\"“]?([^，,。；;\s」\"”]{1,12})",
        ]
    else:
        pats = [
            r"母亲[为是]\s*[「\"“]?大熊猫\s*([^，,。；;\s」\"”]{1,12})",
            r"母亲[为是]\s*[「\"“]?([^，,。；;\s」\"”]{1,12})",
            r"其母[为是]\s*[「\"“]?([^，,。；;\s」\"”]{1,12})",
            r"妈妈[为是]\s*[「\"“]?([^，,。；;\s」\"”]{1,12})",
        ]
    for pat in pats:
        m = re.search(pat, text)
        if m:
            name = m.group(1).strip("「」\"“”'()（）")
            if name and name not in {"未知", "不详"}:
                return name
    return None


def _extract_birth_time(text: str) -> Optional[str]:
    m = re.search(
        r"(?:出生于|生于|出生时间[为是]?)\s*(\d{4}\s*年(?:\d{1,2}\s*月(?:\d{1,2}\s*日)?)?)",
        text,
    )
    if m:
        return re.sub(r"\s+", "", m.group(1))
    m = re.search(r"(\d{4}\s*年\d{1,2}\s*月\d{1,2}\s*日).{0,12}出生", text)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    m = re.search(r"（(\d{4})\s*年", text)
    return f"{m.group(1)}年" if m else None


def _extract_birth_place(text: str) -> Optional[str]:
    m = re.search(
        r"(?:出生于|出生在|生于)\s*([^\s，,。；;（(]{2,40}?(?:基地|动物园|保护区|中心|产房|公园))",
        text,
    )
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:出生于|出生在|生于)\s*([^\s，,。；;]{2,30})", text)
    return m.group(1).strip() if m else None


def _extract_sex(text: str) -> Optional[str]:
    if re.search(r"雌性", text):
        return "雌性"
    if re.search(r"雄性", text):
        return "雄性"
    return None


def _extract_nick(text: str) -> Optional[str]:
    for pat in (
        r"昵称[为是]?[「\"“]([^」\"”]+)[」\"”]",
        r"又名[「\"“]?([^，,。；;\s」\"”]{1,12})",
        r"乳名[为是]?[「\"“]?([^，,。；;\s」\"”]{1,12})",
        r"外号[为是]?[「\"“]?([^，,。；;\s」\"”]{1,12})",
    ):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None


def build_questions_for_panda(
    panda: PandaDoc,
    *,
    max_questions: int,
    rng: random.Random,
) -> List[SpotQuestion]:
    """按原文维度生成可核对问题（优先硬字段）。"""
    candidates: List[Tuple[str, str, List[str]]] = []
    dims = panda.dims
    name = panda.name
    text = panda.text

    if "pedigree" in dims:
        ped = _extract_pedigree(text)
        if ped:
            candidates.append(("pedigree", ped, [ped, f"谱系号{ped}", f"谱系号为{ped}"]))

    father = _extract_parent(text, "father") if "father" in dims else None
    mother = _extract_parent(text, "mother") if "mother" in dims else None
    if father or mother:
        parts: List[str] = []
        tokens: List[str] = []
        if father:
            parts.append(f"父亲为{father}")
            tokens.append(father)
        if mother:
            parts.append(f"母亲为{mother}")
            tokens.append(mother)
        candidates.append(("parents", "，".join(parts), tokens))

    if "birth_time" in dims:
        bt = _extract_birth_time(text)
        if bt:
            year = re.search(r"(\d{4})", bt)
            tokens = [bt]
            if year:
                tokens.append(year.group(1))
            candidates.append(("birth_time", bt, tokens))

    if "birth_place" in dims:
        bp = _extract_birth_place(text)
        if bp:
            tokens = [bp]
            for frag in re.findall(r"[\u4e00-\u9fff]{2,6}", bp):
                if frag not in tokens:
                    tokens.append(frag)
            candidates.append(("birth_place", bp, tokens[:6]))

    if "sex" in dims:
        sex = _extract_sex(text)
        if sex:
            candidates.append(("sex", sex, [sex]))

    if "nick" in dims:
        nick = _extract_nick(text)
        if nick:
            candidates.append(("nick", nick, [nick]))

    if not candidates:
        # 至少问一句身份相关，便于发现「整只缺失」
        candidates.append(
            (
                "identity",
                name,
                [name],
            )
        )

    # 硬字段优先
    priority = {
        "pedigree": 0,
        "parents": 1,
        "birth_time": 2,
        "birth_place": 3,
        "sex": 4,
        "nick": 5,
        "identity": 6,
    }
    candidates.sort(key=lambda x: (priority.get(x[0], 99), x[0]))
    if len(candidates) > max_questions:
        head = candidates[: max(1, max_questions - 1)]
        rest = candidates[len(head) :]
        rng.shuffle(rest)
        candidates = head + rest[: max(0, max_questions - len(head))]

    out: List[SpotQuestion] = []
    for dim, gold, tokens in candidates[:max_questions]:
        if dim == "identity":
            q = f"大熊猫{name}的基本档案信息是什么？"
        else:
            tmpl = QUESTION_TEMPLATES.get(dim, "大熊猫{name}的相关信息是什么？")
            q = tmpl.format(name=name)
        out.append(
            SpotQuestion(
                panda=name,
                dim=dim,
                question=q,
                gold_answer=gold,
                gold_tokens=tokens,
                source_file=panda.source_file,
            )
        )
    return out


def _call_neo4j_qa(
    question: str,
    *,
    top_k: int,
    persona: str,
    strict_mode: bool,
) -> Dict[str, Any]:
    from tools.neo4j_qa import neo4j_qa

    payload = {
        "question": question,
        "top_k": top_k,
        "strict_mode": strict_mode,
        "persona": persona,
        "category": CATEGORY,
    }
    fn = getattr(neo4j_qa, "func", None) or getattr(neo4j_qa, "coroutine", None)
    if callable(fn):
        raw = fn(**payload)
    else:
        raw = neo4j_qa.invoke(payload)
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _token_cover(answer: str, tokens: Sequence[str]) -> float:
    if not tokens:
        return 0.0
    compact = re.sub(r"\s+", "", answer or "")
    hits = 0
    for t in tokens:
        t2 = re.sub(r"\s+", "", str(t))
        if t2 and t2 in compact:
            hits += 1
    return hits / len(tokens)


def classify_gap(
    *,
    answer: str,
    rows_count: int,
    gold_tokens: Sequence[str],
    ok: bool,
) -> str:
    """返回 gap 标签：ok / no_hit / partial / error。"""
    if not ok:
        return "error"
    cover = _token_cover(answer, gold_tokens)
    if rows_count <= 0:
        return "no_hit"
    if any(cue in (answer or "") for cue in NO_EVIDENCE_CUES) and cover < 0.5:
        return "no_hit"
    if cover >= 0.5:
        return "ok"
    if cover > 0:
        return "partial"
    return "no_hit"


def run_spotcheck(
    *,
    rounds: int = 10,
    batch_size: int = 5,
    questions_per_panda: int = 2,
    seed: int = 42,
    top_k: int = 20,
    persona: str = "educator",
    strict_mode: bool = True,
    dry_run: bool = False,
    files: Sequence[str] | None = None,
) -> Path:
    pool = load_panda_pool(files)
    if len(pool) < batch_size:
        raise RuntimeError(f"熊猫池不足：{len(pool)} < batch_size={batch_size}")

    rng = random.Random(seed)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"starmap_kb_spotcheck_{ts}.json"
    gaps_path = REPORT_DIR / f"starmap_kb_spotcheck_gaps_{ts}.jsonl"
    focus_path = REPORT_DIR / f"starmap_kb_spotcheck_focus_{ts}.json"

    all_results: List[Dict[str, Any]] = []
    gaps: List[Dict[str, Any]] = []
    used_names: Set[str] = set()

    _safe_print(
        f"熊猫池={len(pool)} 轮次={rounds} 每批={batch_size} "
        f"每只问题≤{questions_per_panda} seed={seed} dry_run={dry_run}"
    )

    for round_idx in range(1, rounds + 1):
        # 尽量不重复名字；池耗尽后允许重复抽样
        available = [p for p in pool if p.name not in used_names]
        if len(available) < batch_size:
            used_names.clear()
            available = list(pool)
        batch = rng.sample(available, batch_size)
        for p in batch:
            used_names.add(p.name)

        _safe_print(
            f"\n=== Round {round_idx}/{rounds} === "
            + ", ".join(p.name for p in batch)
        )

        for panda in batch:
            questions = build_questions_for_panda(
                panda,
                max_questions=questions_per_panda,
                rng=rng,
            )
            for sq in questions:
                row: Dict[str, Any] = {
                    "round": round_idx,
                    "panda": sq.panda,
                    "dim": sq.dim,
                    "question": sq.question,
                    "gold_answer": sq.gold_answer,
                    "gold_tokens": sq.gold_tokens,
                    "source_file": sq.source_file,
                    "dims": sorted(panda.dims),
                }
                if dry_run:
                    row.update(
                        {
                            "ok": True,
                            "answer": "",
                            "rows_count": 0,
                            "gap_label": "dry_run",
                            "token_cover": 0.0,
                        }
                    )
                    all_results.append(row)
                    _safe_print(f"  [dry] {sq.question} | gold={sq.gold_answer}")
                    continue

                try:
                    qa = _call_neo4j_qa(
                        sq.question,
                        top_k=top_k,
                        persona=persona,
                        strict_mode=strict_mode,
                    )
                    answer = str(qa.get("answer") or "")
                    rows_count = int(qa.get("rows_count") or 0)
                    ok = bool(qa.get("ok"))
                    cover = _token_cover(answer, sq.gold_tokens)
                    gap = classify_gap(
                        answer=answer,
                        rows_count=rows_count,
                        gold_tokens=sq.gold_tokens,
                        ok=ok,
                    )
                    row.update(
                        {
                            "ok": ok,
                            "answer": answer,
                            "rows_count": rows_count,
                            "gap_label": gap,
                            "token_cover": round(cover, 4),
                            "online_quality": qa.get("quality") or {},
                            "error": qa.get("error") or "",
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    row.update(
                        {
                            "ok": False,
                            "answer": "",
                            "rows_count": 0,
                            "gap_label": "error",
                            "token_cover": 0.0,
                            "error": str(exc),
                        }
                    )
                    gap = "error"

                all_results.append(row)
                status = row["gap_label"]
                _safe_print(
                    f"  [{status}] {sq.panda}/{sq.dim} "
                    f"cover={row.get('token_cover', 0):.2f} "
                    f"rows={row.get('rows_count', 0)}"
                )
                if status in {"no_hit", "partial", "error"}:
                    gaps.append(row)

    # 汇总
    label_counts: Dict[str, int] = {}
    for r in all_results:
        lab = str(r.get("gap_label") or "")
        label_counts[lab] = label_counts.get(lab, 0) + 1

    gap_pandas = sorted({str(g.get("panda")) for g in gaps if g.get("panda")})
    focus_groups: Dict[str, List[str]] = {}
    for g in gaps:
        doc = str(g.get("source_file") or "")
        q = str(g.get("question") or "")
        if not doc or not q:
            continue
        bucket = focus_groups.setdefault(doc, [])
        if q not in bucket:
            bucket.append(q)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "rounds": rounds,
        "batch_size": batch_size,
        "questions_per_panda": questions_per_panda,
        "seed": seed,
        "dry_run": dry_run,
        "pool_size": len(pool),
        "unique_names": len({p.name for p in pool}),
        "total_questions": len(all_results),
        "label_counts": label_counts,
        "gap_count": len(gaps),
        "gap_pandas": gap_pandas,
        "source_files": list(SOURCE_FILES if files is None else files),
        "report_path": _rel(report_path),
        "gaps_path": _rel(gaps_path),
        "focus_path": _rel(focus_path),
    }

    payload = {"summary": summary, "results": all_results}
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with gaps_path.open("w", encoding="utf-8") as fp:
        for g in gaps:
            fp.write(json.dumps(g, ensure_ascii=False) + "\n")
    focus_path.write_text(
        json.dumps(
            {
                "groups": [
                    {"doc_path": doc, "questions": qs}
                    for doc, qs in sorted(focus_groups.items())
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    _safe_print("\n=== 汇总 ===")
    _safe_print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="星图/金标准档案随机抽检（每批5只×10轮，neo4j_qa 查漏补缺）"
    )
    parser.add_argument("--rounds", type=int, default=10, help="抽检轮次，默认 10")
    parser.add_argument("--batch-size", type=int, default=5, help="每批熊猫数，默认 5")
    parser.add_argument(
        "--questions-per-panda",
        type=int,
        default=2,
        help="每只熊猫最多提问数，默认 2",
    )
    parser.add_argument("--seed", type=int, default=42, help="随机种子")
    parser.add_argument("--top-k", type=int, default=20, help="neo4j_qa top_k")
    parser.add_argument(
        "--persona",
        choices=["educator", "kid"],
        default="educator",
    )
    parser.add_argument("--no-strict", action="store_true", help="关闭 strict_mode")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只抽样出题，不调用知识库",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        run_spotcheck(
            rounds=max(1, int(args.rounds)),
            batch_size=max(1, int(args.batch_size)),
            questions_per_panda=max(1, int(args.questions_per_panda)),
            seed=int(args.seed),
            top_k=max(1, int(args.top_k)),
            persona=str(args.persona),
            strict_mode=not bool(args.no_strict),
            dry_run=bool(args.dry_run),
        )
    except Exception as exc:  # noqa: BLE001
        _safe_print(f"失败: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
