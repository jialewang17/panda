"""逐条测试「熊猫谣言」栏目能否精准辟谣。

从 `docs/熊猫谣言/大熊猫辟谣汇总_仅辟谣.md` 解析全部谣言条目，
将每条谣言转成用户会问的问题，调用 `neo4j_qa(category=熊猫谣言)`，
对照「正确结论」判定是否精准辟谣。

用法：
  python scripts/test_rumor_debunk.py
  python scripts/test_rumor_debunk.py --limit 3
  python scripts/test_rumor_debunk.py --ids 1,21,23
  python scripts/test_rumor_debunk.py --no-llm-judge
  python scripts/test_rumor_debunk.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.env_loader import get_env_config

get_env_config()


def _clear_broken_local_proxy() -> None:
    """规避本机失效代理（如 127.0.0.1:789x）导致 LLM APIConnectionError。"""
    import os

    suspect_ports = ("7890", "7891", "7897", "10809", "1080")
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = str(os.environ.get(key, "") or "")
        if any(port in value for port in suspect_ports) or "127.0.0.1" in value or "localhost" in value:
            os.environ.pop(key, None)
    # 若仍可能走系统代理，显式放行常见模型域名
    os.environ.setdefault(
        "NO_PROXY",
        "localhost,127.0.0.1,dashscope.aliyuncs.com,api.deepseek.com,api.openai.com,api.moonshot.cn",
    )
    os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])


_clear_broken_local_proxy()

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from model.factory import get_text_generation_model  # noqa: E402
from tools.neo4j_qa import neo4j_qa  # noqa: E402

CATEGORY = "熊猫谣言"
DEFAULT_DOC = PROJECT_ROOT / "docs" / "熊猫谣言" / "大熊猫辟谣汇总_仅辟谣.md"
REPORT_DIR = PROJECT_ROOT / "reports"

NO_EVIDENCE_CUES = (
    "知识库暂无足够依据",
    "暂无足够依据",
    "暂无完整依据",
    "知识库中未检索到",
    "检索0条",
    "以下内容来自通用知识，不是当前知识库证据",
)

# 仅当这些拒答句式出现在答案开头时，才判定为未命中知识库
NO_EVIDENCE_PREFIXES = (
    "暂无依据",
    "知识库中未检索",
    "知识库暂无",
    "未收录相关依据",
    "没有找到相关依据",
)

# 标题「谣言：…」→ 更贴近用户提问的测试问句（保证覆盖文档全部 37 条）
QUESTION_OVERRIDES: Dict[int, str] = {
    1: "大熊猫是猫科动物吗？还是浣熊科？",
    2: "大熊猫是活化石，几百万年没进化吗？",
    3: "大熊猫的黑白色是为了伪装，因为没有天敌吗？",
    4: "大熊猫幼崽出生时像老鼠，很容易被母熊猫压死吗？",
    5: "大熊猫只吃竹子，营养单一，容易饿死吗？",
    6: "大熊猫在野外根本不会吃竹子，是人工训练出来的吗？",
    7: "大熊猫吃竹子是因为竹子开花导致食物短缺，人类才被迫圈养的吗？",
    8: "大熊猫繁殖能力极差，人工圈养下无法自然交配吗？",
    9: "大熊猫基地让近亲交配，导致后代畸形吗？",
    10: "大熊猫基地为了赚钱让熊猫频繁生育，导致母熊猫身体崩溃吗？",
    11: "大熊猫基地给熊猫注射激素催情，会导致内分泌紊乱吗？",
    12: "圈养大熊猫被电击取精，属于残忍虐待吗？",
    13: "大熊猫基地强行分离母幼，会导致母熊猫抑郁、幼崽死亡吗？",
    14: "大熊猫基地让幼崽过早断奶，导致营养不良吗？",
    15: "大熊猫基地为了省事长期喂单一品种竹子，导致营养不良吗？",
    16: "大熊猫基地为了节省成本，让熊猫住在狭小肮脏的笼舍里吗？",
    17: "圈养大熊猫被过度抽血、做实验，导致贫血或死亡吗？",
    18: "大熊猫基地强迫熊猫表演、合影，违背天性吗？",
    19: "大熊猫在野外无法生存，放归全部失败了吗？",
    20: "大熊猫在野外会主动攻击人类，是危险动物吗？",
    21: "大熊猫是濒危动物，数量极少，即将灭绝吗？",
    22: "大熊猫是国宝，所以不能租借或送到国外吗？",
    23: "旅美大熊猫丫丫被虐待，饿得皮包骨吗？",
    24: "大熊猫的粪便可以用来造纸吗？",
    25: "大熊猫每天要睡20小时，非常懒吗？",
    26: "大熊猫性格温顺，不会主动攻击人吗？",
    27: "大熊猫没有天敌，所以进化缓慢吗？",
    28: "大熊猫只生活在四川吗？",
    29: "大熊猫基地靠卖熊猫赚钱吗？",
    30: "大熊猫的伪拇指是进化缺陷，导致抓握能力差吗？",
    31: "有人编造大熊猫被虐待的网络谣言博取流量变现，这件事怎么回事？",
    32: "旅美大熊猫宝力在美国遭虐待，身体有洞、频繁抽搐吗？",
    33: "旅美大熊猫美香、添添回国后遭藏匿、虐待吗？",
    34: "大熊猫国际合作是中方把大熊猫送给外国人做黑实验吗？",
    35: "野化放归大熊猫和盛、雪雪死亡，说明野化放归失败了吗？",
    36: "大熊猫福宝在韩国遭虐待，回国后状态差吗？",
    37: "极端动保团伙网暴熊猫专家和饲养员，真实情况是什么？",
}

# 每条必须命中的关键证据词（来自正确结论；命中率过低视为未精准辟谣）
MUST_HIT_TOKENS: Dict[int, List[str]] = {
    1: ["熊科", "Ursidae", "分子生物学", "DNA"],
    2: ["伪拇指", "进化", "活化石", "300万"],
    3: ["双重伪装", "天敌", "豺", "豹", "黄喉貂"],
    4: ["100", "150", "压死", "存活率", "90"],
    5: ["竹子", "肠道菌群", "12", "38", "竹鼠", "窝窝头"],
    6: ["化石", "始熊猫", "本能", "野生"],
    7: ["迁移", "遗传多样性", "科研", "600万"],
    8: ["发情", "1-3", "人工授精", "野生"],
    9: ["谱系", "亲缘系数", "遗传多样性", "近亲"],
    10: ["2-3年", "政府拨款", "科研", "盈利"],
    11: ["激素监测", "精准", "兽医", "成功率"],
    12: ["全身麻醉", "电刺激", "兽医"],
    13: ["双胞胎", "人工辅助育幼", "90"],
    14: ["6-9", "1岁", "断奶", "野生"],
    15: ["多个品种", "箭竹", "辅食", "窝窝头"],
    16: ["室内外", "清理", "标准", "整改"],
    17: ["每年1-2次", "伦理", "采血", "未发生"],
    18: ["严禁", "表演", "2010", "合影"],
    19: ["母兽带崽", "淘淘", "放归", "11"],
    20: ["护崽", "自卫", "安全距离", "主动攻击"],
    21: ["IUCN", "易危", "2016", "1800"],
    22: ["合作研究", "租借", "所有权", "中国"],
    23: ["老年", "慢性肠炎", "孟菲斯", "虐待"],
    24: ["较短", "杂质", "很少", "工业"],
    25: ["10-12", "睡眠", "进食"],
    26: ["熊类", "护崽", "咬合力", "安全距离"],
    27: ["豺", "豹", "黄喉貂", "伪拇指"],
    28: ["陕西", "甘肃", "秦岭", "四川"],
    29: ["政府拨款", "科研", "门票", "保护基金"],
    30: ["腕骨", "抓握竹子", "适应", "缺陷"],
    31: ["都江堰", "编造", "流量", "判刑"],
    32: ["寄生虫", "竹子残渣", "白线虫", "宝力"],
    33: ["隔离检疫", "官方", "辟谣", "美香"],
    34: ["CITES", "所有权", "归还", "科研合作"],
    35: ["败血症", "母兽带崽", "成功率", "和盛"],
    36: ["爱宝乐园", "神树坪", "专业照料", "福宝"],
    37: ["编造", "短视频", "直播", "非法获利"],
}


@dataclass
class RumorCase:
    """单条谣言测试用例。"""

    id: int
    claim: str
    question: str
    gold_conclusion: str
    gold_bullets: List[str] = field(default_factory=list)
    must_hit_tokens: List[str] = field(default_factory=list)
    source_file: str = "大熊猫辟谣汇总_仅辟谣.md"


@dataclass
class TestResult:
    """单条辟谣测试结果。"""

    id: int
    claim: str
    question: str
    passed: bool
    verdict: str
    answer: str
    rows_count: int
    answer_mode: str
    token_hit_rate: float
    tokens_hit: List[str]
    tokens_miss: List[str]
    affirms_rumor: bool
    llm_correctness: Optional[float] = None
    llm_debunk_ok: Optional[bool] = None
    llm_reason: str = ""
    error: str = ""
    elapsed_sec: float = 0.0


def _safe_print(msg: str) -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace"), flush=True)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _claim_from_title(title: str) -> str:
    title = title.strip()
    m = re.match(r"^\d+\.\s*谣言[：:]\s*(.+)$", title)
    if m:
        return m.group(1).strip().strip("「」\"“”")
    m = re.match(r"^谣言[：:]\s*(.+)$", title)
    if m:
        return m.group(1).strip().strip("「」\"“”")
    return title


def _bullets_from_conclusion(conclusion: str) -> List[str]:
    bullets: List[str] = []
    for line in conclusion.splitlines():
        raw = line.strip()
        if not raw:
            continue
        raw = re.sub(r"^[-*•]\s*", "", raw)
        raw = re.sub(r"^\*\*(.+?)\*\*[：:]\s*", r"\1：", raw)
        if len(raw) >= 8:
            bullets.append(raw)
    return bullets


def parse_rumor_doc(path: Path) -> List[RumorCase]:
    """解析辟谣汇总文档为测试用例。"""
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"(?m)^##\s+", text)
    cases: List[RumorCase] = []
    for section in sections[1:]:
        lines = section.strip().splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        id_match = re.match(r"^(\d+)\.", title)
        if not id_match:
            continue
        rumor_id = int(id_match.group(1))
        claim = _claim_from_title(title)
        m = re.search(r"\*\*正确结论[（(]辟谣[)）]?\*\*[：:]?\s*(.*)", body, re.S)
        conclusion = (m.group(1).strip() if m else body).strip()
        conclusion = re.sub(r"(?m)^##+.*", "", conclusion).strip()
        bullets = _bullets_from_conclusion(conclusion)
        question = QUESTION_OVERRIDES.get(rumor_id) or f"{claim}，是真的吗？"
        tokens = list(MUST_HIT_TOKENS.get(rumor_id, []))
        if not tokens:
            # 回退：从结论中抽数字与短中文词
            compact = _normalize(conclusion)
            tokens = re.findall(r"\d{2,4}|[\u4e00-\u9fff]{2,6}", compact)[:6]
        cases.append(
            RumorCase(
                id=rumor_id,
                claim=claim,
                question=question,
                gold_conclusion=conclusion,
                gold_bullets=bullets,
                must_hit_tokens=tokens,
                source_file=path.name,
            )
        )
    cases.sort(key=lambda c: c.id)
    return cases


def _call_neo4j_qa(
    *,
    question: str,
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
        "category": CATEGORY,
    }
    if callable(fn):
        raw = fn(**payload)
    else:
        raw = neo4j_qa.invoke(payload)
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _token_coverage(answer: str, tokens: Sequence[str]) -> Tuple[float, List[str], List[str]]:
    ans = _normalize(answer)
    hit: List[str] = []
    miss: List[str] = []
    for token in tokens:
        variants = {
            _normalize(token),
            _normalize(token.replace("-", "–")),
            _normalize(token.replace("–", "-")),
            _normalize(token.replace("—", "-")),
        }
        if any(v and v in ans for v in variants):
            hit.append(token)
        else:
            miss.append(token)
    rate = (len(hit) / len(tokens)) if tokens else 0.0
    return rate, hit, miss


def _looks_like_no_evidence_answer(answer: str) -> bool:
    """判断回答是否实质拒答/承认无知识库证据（避免误伤正常辟谣文中的「知识库未收录某细节」）。"""
    text = (answer or "").strip()
    if not text:
        return True
    if any(cue in text for cue in NO_EVIDENCE_CUES):
        return True
    head = text[:120]
    return any(prefix in head for prefix in NO_EVIDENCE_PREFIXES)


def _affirm_rumor_heuristic(claim: str, answer: str) -> bool:
    """粗略检测回答是否在认同谣言（而非辟谣）。"""
    ans = answer or ""
    ans_l = ans.lower()
    # 明确辟谣信号
    debunk_cues = (
        "并非",
        "不是",
        "不属实",
        "不实",
        "谣言",
        "误解",
        "实际上",
        "正确的是",
        "其实",
        "属于熊科",
        "已降级",
        "易危",
        "辟谣",
        "并无",
        "并没有",
        "不能简单认为",
        "这种说法不准确",
        "这种说法是错误",
    )
    if any(cue in ans for cue in debunk_cues):
        return False

    # 危险的认同句式（且缺少否定）
    affirm_patterns = [
        r"是的[，,].{0,8}(猫科|浣熊科|活化石|即将灭绝|虐待|黑实验)",
        r"确实是(猫科|浣熊科|活化石)",
        r"大熊猫(就是|属于)(猫科|浣熊科)",
        r"放归全部失败",
        r"故意虐待",
    ]
    for pat in affirm_patterns:
        if re.search(pat, ans):
            return True

    # 若几乎复述谣言标题且缺少否定词
    claim_compact = _normalize(claim)[:24]
    if claim_compact and claim_compact in _normalize(ans):
        neg = ("不", "非", "否", "误", "谣")
        if not any(n in ans for n in neg):
            return True
    _ = ans_l
    return False


def _llm_judge_debunk(
    *,
    claim: str,
    question: str,
    gold_conclusion: str,
    predicted_answer: str,
) -> Dict[str, Any]:
    llm = get_text_generation_model()
    gold_brief = gold_conclusion[:1200]
    prompt = f"""
你是大熊猫辟谣评测员。判断系统回答是否对该谣言做了「精准辟谣」。

判定标准（必须同时满足才算通过）：
1. 明确否定或纠正谣言说法，不能认同谣言。
2. 给出的正确结论与金标要点一致（允许措辞不同，关键事实不能错）。
3. 不能把谣言标题当事实复述。
4. 若回答「暂无依据」或答非所问，判定失败。

谣言说法：{claim}
用户问题：{question}
金标正确结论：
{gold_brief}

系统回答：
{predicted_answer}

只输出 JSON：
{{"debunk_ok": true/false, "correctness": 0到5的数字, "reason": "一句话原因"}}
""".strip()
    response = llm.invoke(
        [
            SystemMessage(content="你是严格的辟谣评测器，只输出合法 JSON。"),
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
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"LLM 裁判输出非 JSON: {text[:200]}")
    parsed = json.loads(text[start : end + 1])
    return {
        "debunk_ok": bool(parsed.get("debunk_ok")),
        "correctness": max(0.0, min(5.0, float(parsed.get("correctness", 0)))),
        "reason": str(parsed.get("reason", "")).strip(),
    }


def evaluate_case(
    case: RumorCase,
    *,
    top_k: int,
    database: str,
    persona: str,
    strict_mode: bool,
    use_llm_judge: bool,
    min_token_hit_rate: float,
) -> TestResult:
    started = time.perf_counter()
    try:
        qa = _call_neo4j_qa(
            question=case.question,
            top_k=top_k,
            database=database,
            persona=persona,
            strict_mode=strict_mode,
        )
    except Exception as exc:  # noqa: BLE001
        return TestResult(
            id=case.id,
            claim=case.claim,
            question=case.question,
            passed=False,
            verdict="error",
            answer="",
            rows_count=0,
            answer_mode="",
            token_hit_rate=0.0,
            tokens_hit=[],
            tokens_miss=list(case.must_hit_tokens),
            affirms_rumor=False,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_sec=round(time.perf_counter() - started, 3),
        )

    answer = str(qa.get("answer", "") or "")
    rows_count = int(qa.get("rows_count", 0) or 0)
    answer_mode = str(qa.get("answer_mode", "") or "")
    qa_error = str(qa.get("error", "") or "").strip()
    hit_rate, hit, miss = _token_coverage(answer, case.must_hit_tokens)
    affirms = _affirm_rumor_heuristic(case.claim, answer)
    no_evidence = _looks_like_no_evidence_answer(answer)

    if qa_error and not answer:
        return TestResult(
            id=case.id,
            claim=case.claim,
            question=case.question,
            passed=False,
            verdict="fail_qa_error",
            answer=answer,
            rows_count=rows_count,
            answer_mode=answer_mode,
            token_hit_rate=round(hit_rate, 4),
            tokens_hit=hit,
            tokens_miss=miss,
            affirms_rumor=affirms,
            error=qa_error,
            elapsed_sec=round(time.perf_counter() - started, 3),
        )

    llm_correctness: Optional[float] = None
    llm_debunk_ok: Optional[bool] = None
    llm_reason = ""
    if use_llm_judge and answer:
        try:
            judged = _llm_judge_debunk(
                claim=case.claim,
                question=case.question,
                gold_conclusion=case.gold_conclusion,
                predicted_answer=answer,
            )
            llm_correctness = float(judged["correctness"])
            llm_debunk_ok = bool(judged["debunk_ok"])
            llm_reason = str(judged["reason"])
        except Exception as exc:  # noqa: BLE001
            llm_reason = f"judge_error: {type(exc).__name__}: {exc}"

    # 规则层判定
    rule_fail_reasons: List[str] = []
    if rows_count <= 0:
        rule_fail_reasons.append("no_hit")
    if no_evidence:
        rule_fail_reasons.append("no_evidence_answer")
    if answer_mode == "llm_fallback":
        rule_fail_reasons.append("llm_fallback")
    if affirms:
        rule_fail_reasons.append("affirms_rumor")
    if hit_rate < min_token_hit_rate:
        rule_fail_reasons.append(f"low_token_hit({hit_rate:.2f}<{min_token_hit_rate:.2f})")

    # 最终判定：启用 LLM 时以 LLM 为主，规则作为硬否决
    hard_block = bool({"no_hit", "no_evidence_answer", "affirms_rumor"} & set(rule_fail_reasons))
    if use_llm_judge and llm_debunk_ok is not None:
        passed = (not hard_block) and llm_debunk_ok and (llm_correctness or 0) >= 3.5
        if hard_block:
            verdict = "fail_hard"
        elif passed:
            verdict = "pass"
        else:
            verdict = "fail_llm"
    else:
        passed = not rule_fail_reasons
        verdict = "pass" if passed else "fail_rule:" + ",".join(rule_fail_reasons)

    return TestResult(
        id=case.id,
        claim=case.claim,
        question=case.question,
        passed=passed,
        verdict=verdict,
        answer=answer,
        rows_count=rows_count,
        answer_mode=answer_mode,
        token_hit_rate=round(hit_rate, 4),
        tokens_hit=hit,
        tokens_miss=miss,
        affirms_rumor=affirms,
        llm_correctness=llm_correctness,
        llm_debunk_ok=llm_debunk_ok,
        llm_reason=llm_reason,
        error="",
        elapsed_sec=round(time.perf_counter() - started, 3),
    )


def _write_markdown_report(
    path: Path,
    *,
    cases: Sequence[RumorCase],
    results: Sequence[TestResult],
    doc_path: str,
) -> None:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines = [
        "# 熊猫谣言逐条辟谣测试报告",
        "",
        f"- 文档：`{doc_path}`",
        f"- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 结果：**{passed}/{total} 通过**",
        "",
        "## 汇总",
        "",
        "| ID | 结果 | 命中率 | rows | verdict | 问题 |",
        "|---:|:---:|---:|---:|:---|:---|",
    ]
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        q = r.question.replace("|", "\\|")
        lines.append(
            f"| {r.id} | {mark} | {r.token_hit_rate:.0%} | {r.rows_count} | `{r.verdict}` | {q} |"
        )

    fails = [r for r in results if not r.passed]
    if fails:
        lines.extend(["", "## 失败明细", ""])
        for r in fails:
            lines.extend(
                [
                    f"### #{r.id} {r.claim}",
                    "",
                    f"- 问题：{r.question}",
                    f"- verdict：`{r.verdict}`",
                    f"- token 命中：{', '.join(r.tokens_hit) or '(无)'}",
                    f"- token 未命中：{', '.join(r.tokens_miss) or '(无)'}",
                    f"- LLM：ok={r.llm_debunk_ok}, score={r.llm_correctness}, reason={r.llm_reason}",
                    f"- error：{r.error or '(无)'}",
                    "",
                    "<details><summary>系统回答</summary>",
                    "",
                    "```",
                    r.answer or "(空)",
                    "```",
                    "",
                    "</details>",
                    "",
                ]
            )
    else:
        lines.extend(["", "## 结论", "", "全部谣言条目均通过精准辟谣判定。", ""])

    # 附用例清单
    lines.extend(["", "## 用例清单", ""])
    for case in cases:
        lines.append(f"- **#{case.id}** Q: {case.question}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="逐条测试熊猫谣言栏目能否精准辟谣")
    parser.add_argument(
        "--doc",
        default=str(DEFAULT_DOC),
        help="辟谣汇总 markdown 路径",
    )
    parser.add_argument("--limit", type=int, default=0, help="只测前 N 条（0=全部）")
    parser.add_argument("--ids", default="", help="只测指定 ID，逗号分隔，如 1,21,23")
    parser.add_argument("--top-k", type=int, default=20, help="neo4j_qa top_k")
    parser.add_argument("--database", default="", help="Neo4j database 名")
    parser.add_argument("--persona", default="educator", help="回答人设")
    parser.add_argument("--no-strict", action="store_true", help="关闭 strict_mode")
    parser.add_argument(
        "--no-llm-judge",
        action="store_true",
        help="不用 LLM 裁判（仅规则：检索/关键词/认同检测）",
    )
    parser.add_argument(
        "--min-token-hit-rate",
        type=float,
        default=0.34,
        help="规则层关键证据词最低命中率",
    )
    parser.add_argument("--dry-run", action="store_true", help="只解析用例，不调用 QA")
    parser.add_argument(
        "--output-dir",
        default=str(REPORT_DIR),
        help="报告输出目录",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    doc_path = Path(args.doc)
    if not doc_path.is_file():
        _safe_print(f"[error] 文档不存在: {doc_path}")
        return 2

    cases = parse_rumor_doc(doc_path)
    if not cases:
        _safe_print("[error] 未解析到任何谣言条目")
        return 2

    if args.ids.strip():
        wanted = {int(x) for x in re.split(r"[,，\s]+", args.ids) if x.strip()}
        cases = [c for c in cases if c.id in wanted]
    if args.limit and args.limit > 0:
        cases = cases[: args.limit]

    _safe_print(f"[info] 文档={doc_path} 用例数={len(cases)}")
    if args.dry_run:
        for case in cases:
            _safe_print(f"  #{case.id} Q={case.question}")
            _safe_print(f"       tokens={case.must_hit_tokens}")
        return 0

    results: List[TestResult] = []
    for idx, case in enumerate(cases, start=1):
        _safe_print(f"[{idx}/{len(cases)}] #{case.id} {case.question}")
        result = evaluate_case(
            case,
            top_k=max(1, args.top_k),
            database=args.database,
            persona=args.persona,
            strict_mode=not args.no_strict,
            use_llm_judge=not args.no_llm_judge,
            min_token_hit_rate=max(0.0, min(1.0, args.min_token_hit_rate)),
        )
        mark = "PASS" if result.passed else "FAIL"
        _safe_print(
            f"  -> {mark} verdict={result.verdict} "
            f"rows={result.rows_count} hit={result.token_hit_rate:.0%} "
            f"llm={result.llm_debunk_ok}/{result.llm_correctness} "
            f"({result.elapsed_sec}s)"
        )
        if not result.passed and result.llm_reason:
            _safe_print(f"     reason: {result.llm_reason}")
        results.append(result)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"rumor_debunk_eval_{stamp}.json"
    md_path = out_dir / f"rumor_debunk_eval_{stamp}.md"
    latest_json = out_dir / "rumor_debunk_eval_latest.json"
    latest_md = out_dir / "rumor_debunk_eval_latest.md"

    try:
        rel_doc = str(doc_path.resolve().relative_to(PROJECT_ROOT)).replace("\\", "/")
    except ValueError:
        rel_doc = str(doc_path)

    payload = {
        "doc": rel_doc,
        "category": CATEGORY,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "total": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "use_llm_judge": not args.no_llm_judge,
        "min_token_hit_rate": args.min_token_hit_rate,
        "cases": [asdict(c) for c in cases],
        "results": [asdict(r) for r in results],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown_report(md_path, cases=cases, results=results, doc_path=rel_doc)
    _write_markdown_report(latest_md, cases=cases, results=results, doc_path=rel_doc)

    passed = payload["passed"]
    total = payload["total"]
    _safe_print("")
    _safe_print(f"[done] {passed}/{total} 通过")
    _safe_print(f"[report] {json_path}")
    _safe_print(f"[report] {md_path}")

    return 0 if passed == total and total > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
