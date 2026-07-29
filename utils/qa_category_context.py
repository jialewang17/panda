"""会话问答栏目上下文：供 CLI 选择后，neo4j_qa 等工具自动读取。"""

from __future__ import annotations

import re
import threading
from contextvars import ContextVar
from typing import Optional

CATEGORY_KNOWLEDGE = "熊猫知识"
CATEGORY_RUMOR = "熊猫谣言"
CATEGORY_PROFILE = "熊猫资料"
MODE_AUTO = "auto"

ALLOWED_FIXED_CATEGORIES = (CATEGORY_KNOWLEDGE, CATEGORY_PROFILE, CATEGORY_RUMOR)

PERSONA_GENERAL = "educator"
PERSONA_KID = "kid"
ALLOWED_PERSONAS = (PERSONA_GENERAL, PERSONA_KID)

# 会话栏目模式：auto / 熊猫知识 / 熊猫资料 / 熊猫谣言
QA_CATEGORY_MODE_CTX: ContextVar[Optional[str]] = ContextVar("qa_category_mode", default=None)
_global_qa_mode_store: dict[str, Optional[str]] = {}

# 会话语气：educator（普通科普）/ kid（儿童科普）
QA_PERSONA_CTX: ContextVar[Optional[str]] = ContextVar("qa_persona", default=None)
_global_qa_persona_store: dict[str, Optional[str]] = {}

_PROFILE_NAMES = (
    "和花",
    "和叶",
    "七仔",
    "丫丫",
    "萌兰",
    "家姐",
    "加加",
    "雅一",
    "雅二",
    "妹珠",
    "星一",
    "星二",
    "皓月",
    "正正",
    "美兰",
    "萌萌",
    "盈盈",
    "乐乐",
    "成和花",
    "小优",
    "功仔",
    "美香",
    "福宝",
    "华妮",
    "成成",
    "睿宝",
    "辉宝",
    "新星",
    "玲玲",
    "巴斯",
    "奇福",
    "和盛",
    "七喜",
    "艾玖",
    "伦伦",
    "和雨",
    "香香",
    "成大",
    "淼淼",
    "兴兴",
    "嫣嫣",
    "梅兰",
    "奥莉奥",
    "雅莉",
    "团团",
    "圆圆",
    "圆仔",
    "八喜",
    "祥祥",
    "久久",
    "欢欢",
    "成功",
    "盼盼",
    "高高",
    "莽仔",
    "宝力",
    "福菀",
    "宝新",
    "福茹",
    "园润",
    "心心",
    "科比",
    "润玥",
    "安安",
    "菜花园",
    "玖菜花叶",
    "四最",
    "玖菜姐妹",
    "国宝F4",
    "菜菜",
    "二狗",
)


def get_qa_category_mode() -> Optional[str]:
    """获取当前会话栏目模式。"""
    mode = QA_CATEGORY_MODE_CTX.get()
    if mode:
        return mode
    thread_id = threading.get_ident()
    return _global_qa_mode_store.get(str(thread_id))


def set_qa_category_mode(mode: Optional[str]) -> None:
    """设置当前会话栏目模式。"""
    if mode:
        QA_CATEGORY_MODE_CTX.set(mode)
        thread_id = threading.get_ident()
        _global_qa_mode_store[str(thread_id)] = mode
    else:
        QA_CATEGORY_MODE_CTX.set(None)
        thread_id = threading.get_ident()
        _global_qa_mode_store.pop(str(thread_id), None)


def get_qa_persona() -> Optional[str]:
    """获取当前会话语气。"""
    persona = QA_PERSONA_CTX.get()
    if persona:
        return persona
    thread_id = threading.get_ident()
    return _global_qa_persona_store.get(str(thread_id))


def set_qa_persona(persona: Optional[str]) -> None:
    """设置当前会话语气。"""
    if persona:
        QA_PERSONA_CTX.set(persona)
        thread_id = threading.get_ident()
        _global_qa_persona_store[str(thread_id)] = persona
    else:
        QA_PERSONA_CTX.set(None)
        thread_id = threading.get_ident()
        _global_qa_persona_store.pop(str(thread_id), None)


def normalize_qa_persona(raw: str) -> str:
    """规范化语气；空字符串表示未设置。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    aliases = {
        "1": PERSONA_GENERAL,
        "普通": PERSONA_GENERAL,
        "普通科普": PERSONA_GENERAL,
        "科普": PERSONA_GENERAL,
        "educator": PERSONA_GENERAL,
        "default": PERSONA_GENERAL,
        PERSONA_GENERAL: PERSONA_GENERAL,
        "2": PERSONA_KID,
        "儿童": PERSONA_KID,
        "儿童科普": PERSONA_KID,
        "小孩": PERSONA_KID,
        "kid": PERSONA_KID,
        PERSONA_KID: PERSONA_KID,
    }
    key = text.lower() if text.isascii() else text
    if key in aliases:
        return aliases[key]
    if text in ALLOWED_PERSONAS:
        return text
    raise ValueError("不支持的语气，可选：1 普通科普 / 2 儿童科普")


def format_persona_label(persona: str) -> str:
    """语气展示名。"""
    if persona == PERSONA_KID:
        return "儿童科普"
    if persona == PERSONA_GENERAL:
        return "普通科普"
    return persona or "未指定"


def resolve_effective_persona(explicit_persona: str = "") -> str:
    """
    解析最终回答语气。

    优先使用会话语气；若无会话配置，再使用显式 persona；默认普通科普。
    """
    session_persona = get_qa_persona()
    if session_persona:
        return session_persona
    explicit = str(explicit_persona or "").strip().lower()
    if not explicit or explicit == "default":
        return PERSONA_GENERAL
    if explicit == "kid":
        return PERSONA_KID
    if explicit == "educator":
        return PERSONA_GENERAL
    return explicit


def normalize_qa_mode(raw: str) -> str:
    """规范化栏目模式；空字符串表示未设置。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    aliases = {
        "auto": MODE_AUTO,
        "随便问问": MODE_AUTO,
        "全部": MODE_AUTO,
        "1": MODE_AUTO,
        "knowledge": CATEGORY_KNOWLEDGE,
        "知识": CATEGORY_KNOWLEDGE,
        CATEGORY_KNOWLEDGE: CATEGORY_KNOWLEDGE,
        "2": CATEGORY_KNOWLEDGE,
        "profile": CATEGORY_PROFILE,
        "资料": CATEGORY_PROFILE,
        CATEGORY_PROFILE: CATEGORY_PROFILE,
        "3": CATEGORY_PROFILE,
        "rumor": CATEGORY_RUMOR,
        "谣言": CATEGORY_RUMOR,
        CATEGORY_RUMOR: CATEGORY_RUMOR,
        "4": CATEGORY_RUMOR,
    }
    key = text.lower() if text.isascii() else text
    if key in aliases:
        return aliases[key]
    if text in {MODE_AUTO, *ALLOWED_FIXED_CATEGORIES}:
        return text
    raise ValueError(
        f"不支持的栏目模式 mode={text!r}，可选：随便问问(auto) / "
        + " / ".join(ALLOWED_FIXED_CATEGORIES)
    )


def auto_detect_category(question: str) -> str:
    """
    根据问题自动判断栏目。

    规则优先：谣言线索 > 个体资料线索 > 默认熊猫知识。
    """
    q = str(question or "").strip()
    if not q:
        return CATEGORY_KNOWLEDGE

    rumor_cues = (
        "谣言",
        "辟谣",
        "是不是真的",
        "真的假的",
        "猫科",
        "浣熊科",
        "活化石",
        "虐待",
        "电击",
        "近亲交配",
        "假的吗",
        "有没有天敌",
        "实验",
        "做实验",
        "抽血",
        "采血",
    )
    if any(cue in q for cue in rumor_cues):
        return CATEGORY_RUMOR

    if any(name in q for name in _PROFILE_NAMES):
        return CATEGORY_PROFILE

    profile_cues = (
        "谱系号",
        "几岁",
        "哪天出生",
        "什么时候出生",
        "出生于",
        "父亲是谁",
        "母亲是谁",
        "爸爸是谁",
        "妈妈是谁",
        "旅美",
        "回国",
        "认养",
        "昵称",
        "外号",
        "乳名",
        "孩子",
        "子女",
        "育有",
        "后代",
        "现居",
        "迁至",
        "旅居",
        "组合",
        "成员",
        "菜花园",
        "国宝F4",
    )
    if any(cue in q for cue in profile_cues):
        return CATEGORY_PROFILE

    # 含“某某熊猫叫什么/哪只”等个体问法
    if re.search(r"(哪只|哪头).{0,6}熊猫|(熊猫).{0,6}(叫什么|是谁)", q):
        return CATEGORY_PROFILE

    return CATEGORY_KNOWLEDGE


def resolve_effective_category(question: str, explicit_category: str = "") -> tuple[str, str]:
    """
    解析最终检索栏目。

    Returns:
        (effective_category, mode_label)
        effective_category 为空表示不限制栏目。
        mode_label 用于展示（如 随便问问→熊猫资料）。
    """
    explicit = str(explicit_category or "").strip()
    if explicit:
        # 显式传入时：允许全部（空）或固定栏目，由调用方自行 normalize
        return explicit, explicit or "全部"

    mode = get_qa_category_mode() or ""
    if not mode:
        return "", "未指定"
    if mode == MODE_AUTO:
        detected = auto_detect_category(question)
        return detected, f"随便问问→{detected}"
    if mode in ALLOWED_FIXED_CATEGORIES:
        return mode, mode
    return "", str(mode)


def format_mode_label(mode: str) -> str:
    """栏目模式展示名。"""
    if mode == MODE_AUTO:
        return "随便问问（自动判断栏目）"
    if mode in ALLOWED_FIXED_CATEGORIES:
        return mode
    return mode or "未指定"
