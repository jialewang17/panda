"""百度百科熊猫星图分卷多轮核验与缺口补抽（栏目：熊猫资料）。

对照 profile 抽取规范与金标准档案密度，对星图4–22 逐卷审计；
未达标时仅对缺口个体（或整卷）补抽并增量写入 Neo4j。

用法：
    python scripts/verify_starmap_daily.py --status
    python scripts/verify_starmap_daily.py --day 1
    python scripts/verify_starmap_daily.py --day 1 --fix
    python scripts/verify_starmap_daily.py --day 1 --fix --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
VERIFY_PLAN_PATH = ROOT / "data" / "curated" / "starmap_verify_plan.json"
VERIFY_PROGRESS_PATH = ROOT / "data" / "curated" / "starmap_verify_progress.json"
INGEST_PROGRESS_PATH = ROOT / "data" / "curated" / "starmap_ingest_progress.json"
REPORT_DIR = ROOT / "data" / "curated" / "starmap_verify_reports"
CHUNK_DIR = ROOT / "sandbox" / "starmap_verify_chunks"
CATEGORY = "熊猫资料"

COVERAGE_THRESHOLD = 0.90
FULL_REEXTRACT_GAP_RATIO = 0.40
HARD_DIMS = frozenset({"pedigree", "father", "mother", "birth_time"})

# 维度 -> 已抽谓词匹配
PRED_PATTERNS: Dict[str, re.Pattern[str]] = {
    "pedigree": re.compile(r"谱系号"),
    "sex": re.compile(r"性别"),
    "birth_place": re.compile(r"出生于|出生地"),
    "birth_time": re.compile(r"出生时间|出生日期|出生于"),
    "father": re.compile(r"父亲"),
    "mother": re.compile(r"母亲"),
    "nick": re.compile(r"昵称|又名|乳名|外号|认养名|曾用名|别名"),
    "child": re.compile(r"育有|诞下|产下|子女|后代"),
    "move": re.compile(r"迁至|赴|返回|旅居|入驻|放归|转移|康复于|生活于|现居"),
    "death": re.compile(r"逝世|去世|死亡|离世"),
}

# profile 优先谓词（Round3 归一扫描用）
CANONICAL_PREDICATES = frozenset(
    {
        "谱系号",
        "性别为",
        "出生于",
        "出生时间",
        "出生体重",
        "现居地",
        "昵称为",
        "又名",
        "乳名为",
        "外号为",
        "认养名为",
        "曾用名",
        "父亲为",
        "母亲为",
        "同胞为",
        "育有",
        "被认养于",
        "终生认养于",
        "属于组合",
        "成员为",
        "迁至",
        "赴",
        "返回",
        "旅居于",
        "放归于",
        "入驻",
        "逝世于",
        "去世年份",
        "死亡时间",
        "公开亮相于",
        "举办生日会于",
        "参与活动",
    }
)


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


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _load_plan() -> Dict[str, Any]:
    if not VERIFY_PLAN_PATH.exists():
        raise FileNotFoundError(f"缺少核验计划: {VERIFY_PLAN_PATH}")
    return _load_json(VERIFY_PLAN_PATH)


def _load_progress() -> Dict[str, Any]:
    data = _load_json(VERIFY_PROGRESS_PATH)
    if not data:
        data = {"updated_at": "", "runs": [], "file_status": {}}
    data.setdefault("runs", [])
    data.setdefault("file_status", {})
    return data


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
            item["report"] = st.get("report", "")
            item["error"] = st.get("error", "")
            item["coverage"] = st.get("coverage")
        else:
            item.setdefault("status", "pending")
        items.append(item)
    return items


def _print_status(items: List[Dict[str, Any]]) -> None:
    pending = [x for x in items if x.get("status") == "pending"]
    verified = [x for x in items if x.get("status") == "verified"]
    needs_fix = [x for x in items if x.get("status") == "needs_fix"]
    failed = [x for x in items if x.get("status") == "failed"]
    _safe_print(
        f"核验进度：verified={len(verified)} needs_fix={len(needs_fix)} "
        f"pending={len(pending)} failed={len(failed)} total={len(items)}"
    )
    for item in items:
        mark = {
            "verified": "[x]",
            "needs_fix": "[~]",
            "failed": "[!]",
            "pending": "[ ]",
        }.get(str(item.get("status")), "[?]")
        line = f"  {mark} Day{item.get('day')} {item.get('date')}  {item.get('file')}"
        if item.get("coverage") is not None:
            line += f"  cov={item.get('coverage')}"
        if item.get("report"):
            line += f"  -> {item.get('report')}"
        if item.get("error"):
            line += f"  ERR={str(item.get('error')).replace(chr(10), ' ')[:140]}"
        _safe_print(line)


def _select_items(
    items: List[Dict[str, Any]],
    *,
    batch_size: int,
    day: int = 0,
    file_path: str = "",
    retry: bool = False,
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
        if status == "pending" or (
            retry and status in {"needs_fix", "failed"}
        ):
            selected.append(item)
        if len(selected) >= max(1, batch_size):
            break
    return selected


# ---------------------------------------------------------------------------
# Heuristics: parse MD / extract JSON
# ---------------------------------------------------------------------------


def split_panda_entries(md_text: str) -> List[Tuple[str, str]]:
    """返回 [(短名, 条目全文), ...]。"""
    text = (md_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    parts = re.split(r"(?m)(?=^\d+\.\s*大熊猫)", text)
    entries: List[Tuple[str, str]] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^\d+\.\s*大熊猫\s*([^\s，,（(]+)", part)
        name = (m.group(1) if m else "").strip()
        entries.append((name, part))
    return entries


def expected_dims_from_text(text: str, name: str = "") -> Set[str]:
    """从原文启发式推断应有字段维度。"""
    exp: Set[str] = set()
    # 去掉「他者名（谱系号xxx）」以免误判主体谱系号
    pedigree_scan = re.sub(
        r"[\u4e00-\u9fffA-Za-z]{1,12}\s*[（(]谱系号\s*\d+[)）]",
        "",
        text,
    )
    pedigree_scan = re.sub(
        r"[「\"“][^」\"”]{1,20}[」\"”]\s*[（(]谱系号\s*\d+[)）]",
        "",
        pedigree_scan,
    )
    if re.search(r"谱系号\s*[：:]?\s*\d+|谱系号\d+", pedigree_scan):
        exp.add("pedigree")
    if re.search(r"雌性大熊猫|雄性大熊猫|，(?:雌性|雄性)[，,]|（(?:雌性|雄性)）", text) or re.search(
        r"^\d+\.\s*大熊猫[^\n]{0,100}(?:雌性|雄性)", text, flags=re.M
    ):
        exp.add("sex")
    if re.search(r"出生于(?!\s*\d{4}\s*年)|出生在|生于(?!\s*\d{4})", text):
        exp.add("birth_place")
    # 成对条目（亲亲爱爱）若无明确个体出生句，不强制 birth_time
    pair_entry = bool(re.search(r"一对双胞胎|双胞胎儿子|双胞胎女儿", text))
    if (
        re.search(r"出生|生于", text)
        and re.search(r"\d{4}\s*年(?:\d{1,2}\s*月)?|\d{4}-\d{1,2}", text)
        and not (pair_entry and not re.search(r"出生于|生于\d{4}年\d", text))
    ):
        exp.add("birth_time")
    # 父亲/母亲：亲属介绍；排除「取自父亲」「英雄母亲」等
    if re.search(
        r"父亲[为是]|其父[为是]|爸爸[为是]|父母[为是]",
        text,
    ):
        exp.add("father")
    elif re.search(r"父亲|其父|爸爸", text) and not re.search(
        r"当上父亲|成为父亲|熊猫爸爸|的父亲|是[^。]{0,12}父亲|"
        r"取自父亲|源自父亲|名称中.{0,6}父亲|父亲熊猫",
        text,
    ):
        exp.add("father")
    if re.search(
        r"母亲[为是]|其母[为是]|妈妈[为是]|父母[为是]",
        text,
    ):
        exp.add("mother")
    elif re.search(r"母亲|其母|妈妈", text) and not re.search(
        r"当上母亲|成为母亲|传奇母亲|英雄母亲|熊猫妈妈|母性|当妈|"
        r"成长为[^。]{0,6}母亲|为[「\"]母亲[」\"]|"
        r"与母亲走散|和母亲走散|离开母亲|失去母亲|母亲走失",
        text,
    ):
        exp.add("mother")
    if re.search(r"昵称|又名|乳名|外号|别名", text):
        exp.add("nick")
    elif re.search(r"得名", text):
        # 「谐音得名“奇珍”」等同官名时是得名由来，不算昵称
        nick_m = re.search(r'得名[「『“"]([^」』”"]+)[」』”"]', text)
        if not nick_m or not name or nick_m.group(1).strip() != name.strip():
            exp.add("nick")
    elif re.search(r"认养.*命名", text) and not re.search(
        r"(?:幼崽|幼仔|儿子|女儿|长子|宝宝|双胞胎|[一-龥]{1,6}).{0,48}"
        r"(?:获[^。]{0,24})?(?:终身)?认养并?命名|"
        r"获[^。]{0,30}(?:终身)?认养并命名",
        text,
    ):
        exp.add("nick")
    elif re.search(r"命名为|并命名", text) and not re.search(
        r"(?:幼崽|幼仔|儿子|女儿|长子|宝宝|双胞胎).{0,16}(?:命名为|并命名)|"
        r"后被命名为|正式命名|获[^。]{0,20}命名",
        text,
    ):
        exp.add("nick")
    # 主体生育/育有；排除「其中{name}」作为幼仔被介绍
    has_own_offspring = bool(
        re.search(r"育有|共生养|后代包括|一生共生育|共生育|为其首次产仔", text)
        or re.search(
            r"(?:再?产下|诞下|生育了?)\s*(?:雄性|雌性|双胞胎|单胎|幼崽|儿子|女儿|[一-九\d]+只)",
            text,
        )
    )
    only_was_born = bool(re.search(r"是[^。；;]{0,24}(?:产下|诞下)的", text))
    if name and re.search(
        rf"(?:诞下|产下)[^。]{{0,48}}其中[^。]{{0,20}}{re.escape(name)}", text
    ):
        only_was_born = True
    if has_own_offspring and not only_was_born:
        exp.add("child")
    # 迁居：不用「转移至」（常写幼崽转移）
    if re.search(
        r"迁至|旅居于|入驻|返回|飞抵|启程|现居|生活于|抵达",
        text,
    ):
        exp.add("move")
    # 死亡：主体生卒年；抢救无效若紧跟大崽/幼崽则排除
    has_lifespan = bool(
        re.search(r"（[^）]*\d{4}\s*年[^）]*[－\-—至~～]\s*\d{4}", text)
    )
    has_death_phrase = bool(
        re.search(r"以\d+岁[^。]{0,12}离世|因[^。]{0,16}在[^。]{0,16}死亡", text)
    )
    rescue_death = bool(
        re.search(r"(?:抢救|救治)无效(?:死亡|去世)", text)
    ) and not bool(
        re.search(
            r"(?:大崽|二崽|幼崽|幼仔|儿子|女儿|长子)[^。]{0,40}(?:抢救|救治)无效",
            text,
        )
    )
    if has_lifespan or has_death_phrase or rescue_death:
        exp.add("death")
    return exp


def dims_from_predicates(predicates: Iterable[str]) -> Set[str]:
    got: Set[str] = set()
    for pred in predicates:
        for dim, pat in PRED_PATTERNS.items():
            if pat.search(pred or ""):
                got.add(dim)
    return got


def walk_triples(obj: Any) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if not isinstance(node, dict):
            return
        if "predicate" in node and "subject" in node:
            found.append(
                {
                    "subject": str(node.get("subject") or ""),
                    "predicate": str(node.get("predicate") or ""),
                    "object": str(node.get("object") or ""),
                    "evidence": str(node.get("evidence") or ""),
                    "object_type": str(node.get("object_type") or ""),
                }
            )
        for val in node.values():
            _walk(val)

    _walk(obj)
    return found


# 模型偶发把 SPO 写成「主体-谓词-客体」字符串塞进实体桶；补抽核验时需回收
_HYPHEN_PREDICATES: Tuple[str, ...] = tuple(
    sorted(
        {
            "谱系号",
            "性别为",
            "出生于",
            "出生时间",
            "出生日期",
            "出生体重",
            "出生体重范围",
            "死亡时间",
            "逝世于",
            "去世年份",
            "昵称为",
            "又名",
            "乳名为",
            "曾用名",
            "父亲为",
            "母亲为",
            "同胞为",
            "育有",
            "被认养于",
            "迁至",
            "赴",
            "返回",
            "旅居于",
            "放归于",
            "入驻",
            "现居地",
            "属于",
            "属于组合",
            "公开亮相于",
            "参与活动",
        },
        key=len,
        reverse=True,
    )
)
_SKIP_HYPHEN_OBJECTS = frozenset({"无记录", "未知", "不详", "暂无", "无"})
_ENTITY_BUCKETS = (
    "person_entities",
    "place_entities",
    "panda_aliases",
    "biology_terms",
    "behavior_terms",
    "habitats",
    "communication_terms",
    "taxonomy_terms",
)


def parse_hyphen_spo(
    raw: str, *, default_subject: str = ""
) -> Optional[Dict[str, str]]:
    """解析「主体-谓词-客体」或「谓词-客体」字符串。"""
    text = str(raw or "").strip()
    if not text or "-" not in text:
        return None
    for pred in _HYPHEN_PREDICATES:
        token = f"-{pred}-"
        if token in text:
            left, right = text.split(token, 1)
            subj = (left or default_subject).strip()
            obj = right.strip()
            if subj and obj and obj not in _SKIP_HYPHEN_OBJECTS:
                return {
                    "subject": subj,
                    "predicate": pred,
                    "object": obj,
                    "evidence": "",
                    "object_type": "",
                }
        prefix = f"{pred}-"
        if text.startswith(prefix) and default_subject:
            obj = text[len(prefix) :].strip()
            if obj and obj not in _SKIP_HYPHEN_OBJECTS:
                return {
                    "subject": default_subject,
                    "predicate": pred,
                    "object": obj,
                    "evidence": "",
                    "object_type": "",
                }
    return None


def _infer_default_subject(file_item: Dict[str, Any], strings: Sequence[str]) -> str:
    source = str(file_item.get("source_file") or "")
    stem = Path(source).stem if source else ""
    m = re.search(r"大熊猫\s*([^\s_./\\]{1,12})", stem)
    if m:
        return m.group(1).strip()
    # 从已有「主体-谓词-」串投票
    votes: Counter[str] = Counter()
    for s in strings:
        for pred in _HYPHEN_PREDICATES:
            token = f"-{pred}-"
            if token in s:
                left = s.split(token, 1)[0].strip()
                if left and left not in {"大熊猫", "现居地"}:
                    votes[left] += 1
                break
    if votes:
        return votes.most_common(1)[0][0]
    return ""


def recover_hyphen_triples(data: Dict[str, Any]) -> List[Dict[str, str]]:
    """从实体桶回收连字符 SPO，供审计与写库。"""
    recovered: List[Dict[str, str]] = []
    for file_item in data.get("files") or []:
        if not isinstance(file_item, dict):
            continue
        extracted = file_item.get("extracted") or {}
        if not isinstance(extracted, dict):
            continue
        strings: List[str] = []
        for key in _ENTITY_BUCKETS:
            for item in extracted.get(key) or []:
                if isinstance(item, str) and item.strip():
                    strings.append(item.strip())
        default_subj = _infer_default_subject(file_item, strings)
        for raw in strings:
            triple = parse_hyphen_spo(raw, default_subject=default_subj)
            if triple:
                recovered.append(triple)
    return recovered


def inject_recovered_relation_triples(result_json: Path) -> int:
    """把回收的 SPO 写回 relation_triples，便于 neo4j_graph_writer 增量入库。"""
    path = result_json if result_json.is_absolute() else ROOT / result_json
    data = json.loads(path.read_text(encoding="utf-8"))
    added = 0
    for file_item in data.get("files") or []:
        if not isinstance(file_item, dict):
            continue
        extracted = file_item.setdefault("extracted", {})
        if not isinstance(extracted, dict):
            continue
        existing = extracted.get("relation_triples") or []
        if not isinstance(existing, list):
            existing = []
        strings: List[str] = []
        for key in _ENTITY_BUCKETS:
            for item in extracted.get(key) or []:
                if isinstance(item, str) and item.strip():
                    strings.append(item.strip())
        default_subj = _infer_default_subject(file_item, strings)
        seen = {
            (
                str(t.get("subject") or "").strip(),
                str(t.get("predicate") or "").strip(),
                str(t.get("object") or "").strip(),
            )
            for t in existing
            if isinstance(t, dict)
        }
        for raw in strings:
            triple = parse_hyphen_spo(raw, default_subject=default_subj)
            if not triple:
                continue
            key = (triple["subject"], triple["predicate"], triple["object"])
            if key in seen:
                continue
            existing.append(
                {
                    "subject": triple["subject"],
                    "predicate": triple["predicate"],
                    "object": triple["object"],
                    "object_type": "Other",
                    "evidence": triple.get("evidence") or "",
                }
            )
            seen.add(key)
            added += 1
        extracted["relation_triples"] = existing
    if added:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return added


def load_triples(result_json: Path) -> List[Dict[str, str]]:
    path = result_json if result_json.is_absolute() else ROOT / result_json
    data = json.loads(path.read_text(encoding="utf-8"))
    return merge_triples(walk_triples(data), recover_hyphen_triples(data))


def index_predicates_by_subject(
    triples: Sequence[Dict[str, str]],
) -> Dict[str, Set[str]]:
    by_subj: Dict[str, Set[str]] = defaultdict(set)
    for t in triples:
        subj = (t.get("subject") or "").strip()
        pred = (t.get("predicate") or "").strip()
        if subj and pred:
            by_subj[subj].add(pred)
    return by_subj


def match_subject_predicates(
    name: str,
    by_subj: Dict[str, Set[str]],
    entry_text: str = "",
) -> Tuple[str, Set[str]]:
    """将档案短名对齐到抽取 subject（支持正文别名，如标题玲玲/正文陵陵）。"""
    candidates: List[str] = []
    if name:
        candidates.append(name)
    if entry_text:
        m = re.search(r"大熊猫\s*([^\s，,（(「\"“]{1,12})", entry_text)
        if m:
            alt = m.group(1).strip()
            if alt and alt not in candidates:
                candidates.append(alt)
        m2 = re.search(
            r"(?:^|\n)\d+\.\s*大熊猫[^\n]*\n+([^\s，,（(]{1,12})\s*[（(]",
            entry_text,
        )
        if m2:
            alt2 = m2.group(1).strip()
            if alt2 and alt2 not in candidates:
                candidates.append(alt2)

    resolved: List[Tuple[str, Set[str]]] = []
    for cand in candidates:
        if not cand:
            continue
        if cand in by_subj:
            resolved.append((cand, set(by_subj[cand])))
            continue
        for prefix in (f"大熊猫{cand}", f"大熊猫 {cand}"):
            if prefix in by_subj:
                resolved.append((prefix, set(by_subj[prefix])))
                break
        else:
            hits = [s for s in by_subj if cand in s or s in cand]
            if hits:
                exact = [s for s in hits if s == cand or s.endswith(cand)]
                pool = exact or hits
                best = sorted(pool, key=lambda s: (len(s), s))[0]
                resolved.append((best, set(by_subj[best])))
    if not resolved:
        return "", set()
    # 多别名时取谓词最多的对齐结果（避免标题错名抢走正文主体）
    resolved.sort(key=lambda x: (-len(x[1]), len(x[0])))
    return resolved[0]


def merge_triples(
    base: Sequence[Dict[str, str]], extra: Sequence[Dict[str, str]]
) -> List[Dict[str, str]]:
    """按 subject+predicate+object 去重合并。"""
    seen: Set[Tuple[str, str, str]] = set()
    out: List[Dict[str, str]] = []
    for t in list(base) + list(extra):
        key = (
            (t.get("subject") or "").strip(),
            (t.get("predicate") or "").strip(),
            (t.get("object") or "").strip(),
        )
        if key in seen or not key[0]:
            continue
        seen.add(key)
        out.append(dict(t))
    return out


def resolve_result_json(file_key: str) -> Optional[Path]:
    """从入库进度或 wiki 目录解析最新抽取 JSON。"""
    ingest = _load_json(INGEST_PROGRESS_PATH)
    st = (ingest.get("file_status") or {}).get(file_key) or {}
    rel = str(st.get("result_json") or "").strip()
    if rel:
        path = ROOT / rel
        if path.is_file():
            return path

    stem = Path(file_key).stem  # 百度百科熊猫星图N
    candidates = sorted(
        (ROOT / "data" / "wiki").glob(f"**/*{stem}*/panda_extract_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for cand in candidates:
        name = cand.name
        if "checkpoint" in name or name.endswith(("_nodes.json", "_rels.json")):
            continue
        return cand
    return None


def load_merged_triples(file_key: str) -> Tuple[List[Dict[str, str]], str]:
    """合并入库抽取与既往补抽结果。"""
    base_path = resolve_result_json(file_key)
    triples: List[Dict[str, str]] = []
    labels: List[str] = []
    if base_path and base_path.is_file():
        triples = load_triples(base_path)
        labels.append(_rel(base_path))

    fix_rels: List[str] = []
    verify = _load_progress()
    st = (verify.get("file_status") or {}).get(file_key) or {}
    one = str(st.get("fix_result_json") or "").strip()
    if one:
        fix_rels.append(one)
    for rel in st.get("fix_result_jsons") or []:
        rel_s = str(rel).strip()
        if rel_s and rel_s not in fix_rels:
            fix_rels.append(rel_s)

    report_path = REPORT_DIR / f"{Path(file_key).stem}.json"
    if report_path.is_file():
        rep = _load_json(report_path)
        for rel in [rep.get("fix_result_json"), *(rep.get("fix_result_jsons") or [])]:
            rel_s = str(rel or "").strip()
            if rel_s and rel_s not in fix_rels:
                fix_rels.append(rel_s)

    for rel in fix_rels:
        path = ROOT / rel
        if path.is_file():
            triples = merge_triples(triples, load_triples(path))
            labels.append(_rel(path))

    if not triples:
        raise FileNotFoundError(f"找不到可合并的抽取 JSON: {file_key}")
    return triples, "+".join(labels)

# ---------------------------------------------------------------------------
# Audit rounds
# ---------------------------------------------------------------------------


def round1_structure(
    entries: Sequence[Tuple[str, str]], triples: Sequence[Dict[str, str]]
) -> Dict[str, Any]:
    by_subj = index_predicates_by_subject(triples)
    matched = 0
    unmatched: List[str] = []
    for name, text in entries:
        subj, preds = match_subject_predicates(name, by_subj, entry_text=text)
        if subj and preds:
            matched += 1
        else:
            unmatched.append(name or "(无名)")

    issues: List[str] = []
    if not triples:
        issues.append("抽取结果无三元组")
    generic = sum(1 for t in triples if t.get("subject") in {"大熊猫", "熊猫"})
    if generic:
        issues.append(f"subject 为统称「大熊猫」的三元组 {generic} 条")
    no_evidence = sum(1 for t in triples if not (t.get("evidence") or "").strip())
    if no_evidence:
        issues.append(f"缺少 evidence 的三元组 {no_evidence} 条")
    garbled = sum(
        1
        for t in triples
        if "\ufffd" in (t.get("predicate") or "") or "\ufffd" in (t.get("subject") or "")
    )
    if garbled:
        issues.append(f"含替换字符的三元组 {garbled} 条")

    return {
        "entry_count": len(entries),
        "triple_count": len(triples),
        "unique_subjects": len(by_subj),
        "matched_subjects": matched,
        "unmatched_names": unmatched,
        "issues": issues,
        "ok": matched >= max(1, int(len(entries) * 0.8)) and not (
            set(issues) & {"抽取结果无三元组"}
        ),
    }


def round2_coverage(
    entries: Sequence[Tuple[str, str]], triples: Sequence[Dict[str, str]]
) -> Dict[str, Any]:
    by_subj = index_predicates_by_subject(triples)
    per_panda: List[Dict[str, Any]] = []
    field_miss = Counter()
    hard_miss_count = 0
    expected_total = 0
    got_total = 0

    for name, text in entries:
        expected = expected_dims_from_text(text, name=name)
        subj, preds = match_subject_predicates(name, by_subj, entry_text=text)
        got = dims_from_predicates(preds)
        missing = sorted(expected - got)
        hard_missing = sorted(set(missing) & HARD_DIMS)
        expected_total += len(expected)
        got_total += len(expected & got)
        for dim in missing:
            field_miss[dim] += 1
        if hard_missing:
            hard_miss_count += 1
        per_panda.append(
            {
                "name": name,
                "matched_subject": subj,
                "triple_preds": len(preds),
                "expected": sorted(expected),
                "got": sorted(got & expected),
                "missing": missing,
                "hard_missing": hard_missing,
                "ok": not missing,
            }
        )

    gap_pandas = [p for p in per_panda if p["missing"]]
    hard_gap_pandas = [p for p in per_panda if p["hard_missing"]]
    n = max(1, len(entries))
    coverage = (n - len(gap_pandas)) / n
    field_recall = (got_total / expected_total) if expected_total else 1.0
    passed = coverage >= COVERAGE_THRESHOLD and hard_miss_count == 0

    return {
        "coverage": round(coverage, 4),
        "field_recall": round(field_recall, 4),
        "gap_count": len(gap_pandas),
        "hard_gap_count": hard_miss_count,
        "field_miss_counts": dict(field_miss),
        "gap_pandas": gap_pandas,
        "hard_gap_names": [p["name"] for p in hard_gap_pandas],
        "soft_gap_names": [
            p["name"] for p in gap_pandas if not p["hard_missing"]
        ],
        "passed": passed,
        "per_panda": per_panda,
    }


def round3_spotcheck(
    entries: Sequence[Tuple[str, str]],
    triples: Sequence[Dict[str, str]],
    round2: Dict[str, Any],
    *,
    skip_qa: bool = False,
) -> Dict[str, Any]:
    by_subj = index_predicates_by_subject(triples)
    pred_counter = Counter(t.get("predicate") or "" for t in triples)
    non_canonical = [
        {"predicate": p, "count": c}
        for p, c in pred_counter.most_common()
        if p and p not in CANONICAL_PREDICATES
    ][:30]

    # 抽样：1 只缺口边缘 + 2 只完整
    gap_names = [p["name"] for p in round2.get("gap_pandas") or [] if p.get("name")]
    ok_names = [
        p["name"]
        for p in (round2.get("per_panda") or [])
        if p.get("ok") and p.get("name")
    ]
    samples: List[str] = []
    if gap_names:
        samples.append(gap_names[0])
    for name in ok_names:
        if name not in samples:
            samples.append(name)
        if len(samples) >= 3:
            break
    while len(samples) < min(3, len(entries)):
        for name, _ in entries:
            if name and name not in samples:
                samples.append(name)
            if len(samples) >= 3:
                break
        break

    qa_checks: List[Dict[str, Any]] = []
    for name in samples:
        text = next((t for n, t in entries if n == name), "")
        subj, preds = match_subject_predicates(name, by_subj, entry_text=text)
        question = f"{name}的父母是谁" if name else "这只大熊猫的父母是谁"
        has_parent = any(PRED_PATTERNS["father"].search(p) for p in preds) or any(
            PRED_PATTERNS["mother"].search(p) for p in preds
        )
        expect_parent = "father" in expected_dims_from_text(
            text, name=name
        ) or "mother" in expected_dims_from_text(text, name=name)
        ok = (not expect_parent) or has_parent
        check: Dict[str, Any] = {
            "name": name,
            "matched_subject": subj,
            "question": question,
            "expect_parent": expect_parent,
            "has_parent_triple": has_parent,
            "ok": ok,
            "mode": "local_graph_spotcheck",
        }
        if not skip_qa:
            try:
                proc = _run_cmd(
                    [
                        sys.executable,
                        "-m",
                        "tools.neo4j_qa",
                        "--category",
                        CATEGORY,
                        "--question",
                        question,
                        "--plain",
                        "--no-save-json",
                        "--strict",
                    ],
                    dry_run=False,
                    timeout=120,
                )
                out = (proc.stdout or "")[-800:]
                check["qa_exit"] = proc.returncode
                check["qa_snippet"] = out.replace("\n", " ")[:240]
            except Exception as exc:  # noqa: BLE001
                check["qa_error"] = f"{type(exc).__name__}: {exc}"
        qa_checks.append(check)

    passed = all(c.get("ok") for c in qa_checks) if qa_checks else True
    return {
        "samples": samples,
        "non_canonical_predicates": non_canonical,
        "qa_checks": qa_checks,
        "passed": passed,
        "skip_qa": skip_qa,
    }


def evaluate_pass(r1: Dict[str, Any], r2: Dict[str, Any], r3: Dict[str, Any]) -> bool:
    return bool(r1.get("ok")) and bool(r2.get("passed")) and bool(r3.get("passed"))


def build_report(
    *,
    file_key: str,
    day: int,
    result_json: str,
    entries: Sequence[Tuple[str, str]],
    triples: Sequence[Dict[str, str]],
    r1: Dict[str, Any],
    r2: Dict[str, Any],
    r3: Dict[str, Any],
) -> Dict[str, Any]:
    passed = evaluate_pass(r1, r2, r3)
    gap_ratio = (r2.get("gap_count") or 0) / max(1, len(entries))
    return {
        "file": file_key,
        "day": day,
        "result_json": result_json,
        "audited_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entry_count": len(entries),
        "triple_count": len(triples),
        "round1": r1,
        "round2": {
            k: v
            for k, v in r2.items()
            if k != "per_panda"  # 明细另存精简版
        },
        "round2_per_panda": r2.get("per_panda") or [],
        "round3": r3,
        "gap_ratio": round(gap_ratio, 4),
        "suggest_full_reextract": gap_ratio >= FULL_REEXTRACT_GAP_RATIO,
        "passed": passed,
        "thresholds": {
            "coverage": COVERAGE_THRESHOLD,
            "hard_dims": sorted(HARD_DIMS),
            "full_reextract_gap_ratio": FULL_REEXTRACT_GAP_RATIO,
        },
    }


# ---------------------------------------------------------------------------
# Fix / extract
# ---------------------------------------------------------------------------


def _run_cmd(
    cmd: List[str],
    *,
    dry_run: bool,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    _safe_print(">", " ".join(cmd))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
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
        timeout=timeout,
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
            f"补抽失败 code={proc.returncode}; "
            f"stdout={proc.stdout[-1200:]}; stderr={proc.stderr[-800:]}"
        )
    result_path = ""
    for line in (proc.stdout or "").splitlines():
        if line.strip().startswith("result_file_path:"):
            candidate = line.split(":", 1)[1].strip()
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
            if "checkpoint" in name or name.endswith(("_nodes.json", "_rels.json")):
                continue
            result_path = str(cand.resolve())
            break
    if not result_path:
        raise RuntimeError("补抽完成但未解析到 result_file_path")
    try:
        return _rel(Path(result_path))
    except Exception:
        return result_path


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
        _safe_print(proc.stdout[-800:])


def prepare_gap_chunk_dir(
    md_path: Path,
    entries: Sequence[Tuple[str, str]],
    gap_names: Sequence[str],
    *,
    full_reextract: bool,
    chunk_size: int = 2,
) -> Tuple[Path, int]:
    out_dir = CHUNK_DIR / md_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.md"):
        old.unlink()

    if full_reextract:
        selected = [text for _, text in entries]
    else:
        name_set = set(gap_names)
        selected = [text for name, text in entries if name in name_set]
    if not selected:
        raise RuntimeError("没有可补抽的条目")

    size = max(1, int(chunk_size))
    chunks = [selected[i : i + size] for i in range(0, len(selected), size)]
    hint = (
        "【补抽要求】请把每只大熊猫主体写成 relation_triples"
        "（subject/predicate/object/evidence）；"
        "文中若出现谱系号、性别、出生时间、出生地、父母、昵称/乳名、迁居、死亡时间，"
        "必须抽为独立三元组，勿只写入 biology_terms 等列表。\n\n"
    )
    for i, chunk in enumerate(chunks, start=1):
        (out_dir / f"gap_{i:02d}.md").write_text(
            hint + "\n\n".join(chunk).rstrip() + "\n",
            encoding="utf-8",
        )
    _safe_print(
        f"补抽分片 {_rel(md_path)} -> {len(selected)} 只 / {len(chunks)} 片"
        f"（full={full_reextract}, size<={size}）"
    )
    return out_dir, len(chunks)


def audit_volume(
    file_key: str,
    *,
    day: int,
    skip_qa: bool,
    triples_override: Optional[List[Dict[str, str]]] = None,
    result_json_override: str = "",
) -> Dict[str, Any]:
    md_path = ROOT / file_key
    if not md_path.exists():
        raise FileNotFoundError(f"源文件不存在: {file_key}")

    if triples_override is not None:
        triples = triples_override
        result_label = result_json_override or "(override)"
    else:
        triples, result_label = load_merged_triples(file_key)

    entries = split_panda_entries(md_path.read_text(encoding="utf-8"))
    r1 = round1_structure(entries, triples)
    r2 = round2_coverage(entries, triples)
    r3 = round3_spotcheck(entries, triples, r2, skip_qa=skip_qa)
    report = build_report(
        file_key=file_key,
        day=day,
        result_json=result_label,
        entries=entries,
        triples=triples,
        r1=r1,
        r2=r2,
        r3=r3,
    )
    return report


def _mark_progress(
    progress: Dict[str, Any],
    *,
    file_key: str,
    status: str,
    report_path: str = "",
    coverage: Optional[float] = None,
    error: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    progress["updated_at"] = now
    row: Dict[str, Any] = {
        "status": status,
        "done_at": now if status == "verified" else "",
        "report": report_path,
        "coverage": coverage,
        "error": error[:500],
    }
    if extra:
        row.update(extra)
    progress["file_status"][file_key] = row
    progress["runs"].append(
        {
            "at": now,
            "file": file_key,
            "status": status,
            "report": report_path,
            "coverage": coverage,
            "error": error[:300],
        }
    )


def run_one(
    item: Dict[str, Any],
    *,
    fix: bool,
    dry_run: bool,
    concurrency: int,
    skip_qa: bool,
    progress: Dict[str, Any],
) -> int:
    file_key = str(item.get("file", "")).replace("\\", "/")
    day = int(item.get("day") or 0)
    md_path = ROOT / file_key
    stem = md_path.stem

    _safe_print(f"=== 核验 Day{day} {file_key} ===")
    try:
        report = audit_volume(file_key, day=day, skip_qa=skip_qa)
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        _safe_print(f"审计失败: {err}")
        if not dry_run:
            _mark_progress(progress, file_key=file_key, status="failed", error=err)
            _save_json(VERIFY_PROGRESS_PATH, progress)
        return 1

    report_path = REPORT_DIR / f"{stem}.json"
    if not dry_run:
        _save_json(report_path, report)

    _safe_print(
        f"Round1 matched={report['round1']['matched_subjects']}/"
        f"{report['round1']['entry_count']} ok={report['round1']['ok']}"
    )
    _safe_print(
        f"Round2 coverage={report['round2']['coverage']} "
        f"hard_gaps={report['round2']['hard_gap_count']} "
        f"gaps={report['round2']['gap_count']} passed={report['round2']['passed']}"
    )
    _safe_print(f"Round3 passed={report['round3']['passed']}")

    if report["passed"]:
        _safe_print(f"达标: {file_key}")
        if not dry_run:
            _mark_progress(
                progress,
                file_key=file_key,
                status="verified",
                report_path=_rel(report_path),
                coverage=report["round2"]["coverage"],
            )
            _save_json(VERIFY_PROGRESS_PATH, progress)
        return 0

    if not fix:
        _safe_print(f"未达标（未启用 --fix）: {file_key}")
        if not dry_run:
            _mark_progress(
                progress,
                file_key=file_key,
                status="needs_fix",
                report_path=_rel(report_path),
                coverage=report["round2"]["coverage"],
            )
            _save_json(VERIFY_PROGRESS_PATH, progress)
        return 0

    # 缺口补抽
    entries = split_panda_entries(md_path.read_text(encoding="utf-8"))
    gap_names = [
        p["name"]
        for p in (report.get("round2_per_panda") or [])
        if p.get("missing") and p.get("name")
    ]
    full = bool(report.get("suggest_full_reextract"))
    try:
        chunk_dir, _ = prepare_gap_chunk_dir(
            md_path,
            entries,
            gap_names,
            full_reextract=full,
            chunk_size=2,
        )
        if dry_run:
            _safe_print(f"(dry-run) 将补抽 {_rel(chunk_dir)} 并增量写库")
            return 0
        fix_json = _extract_docs_dir(
            _rel(chunk_dir), concurrency=concurrency, dry_run=False
        )
        added = inject_recovered_relation_triples(ROOT / fix_json)
        if added:
            _safe_print(f"从实体桶回收 relation_triples +{added}")
        _write_neo4j(fix_json, dry_run=False)

        # 合并既往补抽 + 本次补抽后复审
        prev_triples, prev_label = load_merged_triples(file_key)
        fix_triples = load_triples(ROOT / fix_json)
        merged = merge_triples(prev_triples, fix_triples)
        prev_fixes: List[str] = []
        st_prev = (progress.get("file_status") or {}).get(file_key) or {}
        if st_prev.get("fix_result_json"):
            prev_fixes.append(str(st_prev["fix_result_json"]))
        for rel in st_prev.get("fix_result_jsons") or []:
            if str(rel) not in prev_fixes:
                prev_fixes.append(str(rel))
        if fix_json not in prev_fixes:
            prev_fixes.append(fix_json)

        report2 = audit_volume(
            file_key,
            day=day,
            skip_qa=skip_qa,
            triples_override=merged,
            result_json_override=f"{prev_label}+{fix_json}",
        )
        report2["fix_result_json"] = fix_json
        report2["fix_result_jsons"] = prev_fixes
        report2["fixed_gap_names"] = gap_names
        report2["full_reextract"] = full
        _save_json(report_path, report2)

        status = "verified" if report2["passed"] else "needs_fix"
        _safe_print(
            f"补抽后 coverage={report2['round2']['coverage']} "
            f"passed={report2['passed']} -> {status}"
        )
        _mark_progress(
            progress,
            file_key=file_key,
            status=status,
            report_path=_rel(report_path),
            coverage=report2["round2"]["coverage"],
            extra={
                "fix_result_json": fix_json,
                "fix_result_jsons": prev_fixes,
            },
        )
        _save_json(VERIFY_PROGRESS_PATH, progress)
        return 0 if report2["passed"] else 1
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        _safe_print(f"补抽失败: {err}")
        _mark_progress(
            progress,
            file_key=file_key,
            status="failed",
            report_path=_rel(report_path) if report_path.exists() else "",
            coverage=report["round2"]["coverage"],
            error=err,
        )
        _save_json(VERIFY_PROGRESS_PATH, progress)
        return 1


def run_batch(
    *,
    batch_size: int = 1,
    day: int = 0,
    file_path: str = "",
    fix: bool = False,
    dry_run: bool = False,
    concurrency: int = 2,
    skip_qa: bool = False,
    retry: bool = False,
) -> int:
    plan = _load_plan()
    progress = _load_progress()
    items = _merge_item_status(plan, progress)
    selected = _select_items(
        items,
        batch_size=batch_size,
        day=day,
        file_path=file_path,
        retry=retry,
    )
    if not selected:
        _safe_print("没有待核验分卷。")
        _print_status(items)
        return 0

    _safe_print(f"本次核验 {len(selected)} 卷（fix={fix}, dry_run={dry_run}）：")
    for item in selected:
        _safe_print(f"  - Day{item.get('day')} {item.get('file')}")

    failures = 0
    for item in selected:
        code = run_one(
            item,
            fix=fix,
            dry_run=dry_run,
            concurrency=concurrency,
            skip_qa=skip_qa,
            progress=progress,
        )
        if code:
            failures += 1

    items = _merge_item_status(plan, _load_progress() if not dry_run else progress)
    _print_status(items)
    return 1 if failures else 0


def self_check_heuristics() -> int:
    """用星图4 + 金标准 MD 做启发式冒烟。"""
    samples = [
        ROOT / "docs/熊猫资料/百度百科熊猫星图4.md",
        ROOT / "docs/熊猫资料/熊猫资料.md",
        ROOT / "docs/熊猫资料/熊猫资料2.md",
        ROOT / "docs/熊猫资料/熊猫资料3.md",
    ]
    ok = True
    for path in samples:
        entries = split_panda_entries(path.read_text(encoding="utf-8"))
        if not entries:
            _safe_print(f"[FAIL] 无法解析条目: {_rel(path)}")
            ok = False
            continue
        dims = Counter()
        for _, text in entries:
            for d in expected_dims_from_text(text, name=name):
                dims[d] += 1
        _safe_print(f"[OK] {_rel(path)} n={len(entries)} dims={dict(dims)}")

    # 星图4 对抽取覆盖
    file_key = "docs/熊猫资料/百度百科熊猫星图4.md"
    result = resolve_result_json(file_key)
    if result and result.is_file():
        report = audit_volume(file_key, day=1, skip_qa=True)
        _safe_print(
            f"[OK] 星图4 audit coverage={report['round2']['coverage']} "
            f"gaps={report['round2']['gap_count']} "
            f"hard={report['round2']['hard_gap_count']} passed={report['passed']}"
        )
    else:
        _safe_print("[WARN] 未找到星图4 抽取 JSON，跳过覆盖审计冒烟")
    return 0 if ok else 1


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="百度百科熊猫星图多轮核验与补齐")
    parser.add_argument("--status", action="store_true", help="仅查看进度")
    parser.add_argument("--self-check", action="store_true", help="启发式冒烟检查")
    parser.add_argument("--dry-run", action="store_true", help="不写进度/不补抽写库")
    parser.add_argument("--fix", action="store_true", help="未达标时缺口补抽并写库")
    parser.add_argument("--batch-size", type=int, default=1, help="一次处理几个，默认 1")
    parser.add_argument("--day", type=int, default=0, help="指定 Day 编号")
    parser.add_argument("--file", default="", help="指定 md 相对路径")
    parser.add_argument("--concurrency", type=int, default=2, help="补抽并发，默认 2")
    parser.add_argument("--skip-qa", action="store_true", help="Round3 跳过 neo4j_qa 调用")
    parser.add_argument(
        "--retry",
        action="store_true",
        help="重试 needs_fix/failed",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if args.self_check:
        return self_check_heuristics()
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
        fix=bool(args.fix),
        dry_run=bool(args.dry_run),
        concurrency=max(1, int(args.concurrency)),
        skip_qa=bool(args.skip_qa),
        retry=bool(args.retry),
    )


if __name__ == "__main__":
    raise SystemExit(main())
