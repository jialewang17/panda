"""清洗微信公众号导出的 Markdown，供大熊猫知识抽取使用。

默认：docs/熊猫公众号 → data/panda_wechat_clean
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from utils.path import get_project_root  # noqa: E402

IMAGE_LINE_RE = re.compile(r"^\s*!\[.*?\]\([^)]+\)\s*$")
FILENAME_TS_RE = re.compile(r"^\[(\d{12})\]")
DATETIME_INLINE_RE = re.compile(
    r"_?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s+\d{1,2}\s*:\s*\d{2})\s*_?"
)
FILENAME_DT_FMT = "%Y%m%d%H%M"

DROP_LINE_EXACT = {
    "在小说阅读器读本章",
    "去阅读",
    "预览时标签不可点",
    "知道了",
    "取消",
    "允许",
    "****",
    "**",
    "__",
    "____",
}

DROP_LINE_CONTAINS = (
    "在小说阅读器读本章",
    "预览时标签不可点",
)

FOOTER_ANCHORS = (
    "预览时标签不可点",
    "知道了",
)

AUTHOR_CANDIDATES = (
    "中国大熊猫保护研究中心",
    "成都大熊猫繁育研究基地",
)


@dataclass
class FileCleanStats:
    source_file: str
    output_file: str
    input_chars: int = 0
    output_chars: int = 0
    input_lines: int = 0
    output_lines: int = 0
    dropped_image_lines: int = 0
    dropped_ui_lines: int = 0
    truncated_footer: bool = False
    author: str = ""
    published_at: str = ""


@dataclass
class CleanReport:
    input_dir: str
    output_dir: str
    files_count: int = 0
    files: List[FileCleanStats] = field(default_factory=list)
    timestamp: str = ""


def _clean_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def _normalize_title(line: str) -> str:
    """规范化 Markdown 标题行。"""
    m = re.match(r"^(#{1,6})\s*(.+?)\s*$", line)
    if not m:
        return line.rstrip()
    level, title = m.group(1), m.group(2)
    title = _clean_spaces(title)
    title = re.sub(r"\s*\|\s*", " | ", title)
    return f"{level} {title}"


def _parse_filename_datetime(name: str) -> str:
    m = FILENAME_TS_RE.match(name)
    if not m:
        return ""
    raw = m.group(1)
    try:
        dt = datetime.strptime(raw, FILENAME_DT_FMT)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


def _extract_inline_datetime(text: str) -> str:
    m = DATETIME_INLINE_RE.search(text)
    if not m:
        return ""
    raw = m.group(1)
    raw = re.sub(r"\s+", "", raw)
    raw = raw.replace("年", "-").replace("月", "-").replace("日", " ")
    # e.g. 2021-09-25 14:47
    try:
        parts = raw.split()
        date_part = parts[0]
        time_part = parts[1] if len(parts) > 1 else "00:00"
        y, mo, d = date_part.split("-")
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d} {time_part}"
    except Exception:
        return _clean_spaces(m.group(1))


def _extract_author(lines: Iterable[str]) -> str:
    for line in lines:
        stripped = _clean_spaces(line)
        if not stripped or stripped.startswith("#") or stripped.startswith("!"):
            continue
        for candidate in AUTHOR_CANDIDATES:
            if candidate in stripped:
                return candidate
        # 首个非噪声短文本行也可能是公众号名
        if (
            2 <= len(stripped) <= 40
            and not stripped.startswith(">")
            and "年" not in stripped
            and stripped not in DROP_LINE_EXACT
        ):
            # 仅当看起来像机构名
            if any(k in stripped for k in ("中心", "基地", "协会", "公园", "研究院")):
                return stripped
    return ""


def _is_drop_line(line: str) -> Tuple[bool, str]:
    stripped = _clean_spaces(line)
    if not stripped:
        return False, ""
    if IMAGE_LINE_RE.match(line):
        return True, "image"
    if stripped in DROP_LINE_EXACT:
        return True, "ui"
    # 仅含 markdown 强调符号/下划线
    if re.fullmatch(r"[*_`\s]+", stripped):
        return True, "ui"
    # 日期斜体行整行丢弃（元数据会单独写入）
    if DATETIME_INLINE_RE.search(stripped) and len(stripped) < 80:
        # 可能混有地区名「四川」
        if "年" in stripped and ("月" in stripped or ":" in stripped):
            return True, "ui"
    for needle in DROP_LINE_CONTAINS:
        if needle in stripped:
            return True, "ui"
    if stripped in {"取消 允许", "取消", "允许"}:
        return True, "ui"
    # 「取消  允许」类
    if re.fullmatch(r"(取消|允许)(\s+(取消|允许))*", stripped):
        return True, "ui"
    return False, ""


def _find_footer_cut(lines: List[str]) -> Optional[int]:
    for idx, line in enumerate(lines):
        stripped = _clean_spaces(line)
        for anchor in FOOTER_ANCHORS:
            if anchor in stripped:
                return idx
    return None


def _normalize_bold_heading(line: str) -> str:
    """规范化 ** 标题 ** / 断开的标点加粗。"""
    stripped = line.strip()
    # **title** **？** → **title？**
    m2 = re.fullmatch(r"\*\*([^*]+)\*\*\s*\*\*([？?！!。．])\*\*", stripped)
    if m2:
        return f"**{m2.group(1).strip()}{m2.group(2)}**"
    # **  title  ** → **title**
    m = re.fullmatch(r"\*\*\s*([^*]+?)\s*\*\*", stripped)
    if m:
        return f"**{m.group(1).strip()}**"
    return line.rstrip()


def _collapse_blank_lines(lines: List[str]) -> List[str]:
    out: List[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                out.append("")
            blank = True
            continue
        blank = False
        out.append(line.rstrip())
    # trim leading/trailing blanks
    while out and not out[0].strip():
        out.pop(0)
    while out and not out[-1].strip():
        out.pop()
    return out


def clean_markdown(text: str, *, filename: str = "") -> Tuple[str, FileCleanStats]:
    """清洗单篇微信 Markdown，返回清洗后文本与统计。"""
    stats = FileCleanStats(source_file=filename, output_file="")
    stats.input_chars = len(text)
    raw_lines = text.splitlines()
    stats.input_lines = len(raw_lines)

    author = _extract_author(raw_lines)
    published = _extract_inline_datetime(text) or _parse_filename_datetime(Path(filename).name)
    stats.author = author
    stats.published_at = published

    cut = _find_footer_cut(raw_lines)
    if cut is not None:
        raw_lines = raw_lines[:cut]
        stats.truncated_footer = True

    kept: List[str] = []
    title_seen = False
    author_consumed = False

    for line in raw_lines:
        drop, reason = _is_drop_line(line)
        if drop:
            if reason == "image":
                stats.dropped_image_lines += 1
            else:
                stats.dropped_ui_lines += 1
            continue

        stripped = _clean_spaces(line)

        # 跳过已写入元数据的作者行
        if author and not author_consumed and stripped == author:
            author_consumed = True
            stats.dropped_ui_lines += 1
            continue

        if stripped.startswith("#"):
            if title_seen and stripped.startswith("# "):
                # 保留二级及以下；一级标题只保留第一个
                if re.match(r"^#\s+", stripped) and not re.match(r"^##+", stripped):
                    stats.dropped_ui_lines += 1
                    continue
            normalized = _normalize_title(line)
            if re.match(r"^#\s+", normalized) and not re.match(r"^##+", normalized):
                title_seen = True
            kept.append(normalized)
            continue

        # 规范化加粗小标题
        if stripped.startswith("**") and stripped.endswith("**"):
            kept.append(_normalize_bold_heading(line))
            continue

        kept.append(line.rstrip())

    kept = _collapse_blank_lines(kept)

    meta_lines: List[str] = []
    source = author or "微信公众号"
    meta_lines.append(f"> 来源: {source}")
    if published:
        meta_lines.append(f"> 发布时间: {published}")
    meta_lines.append("")

    body = "\n".join(kept).strip()
    cleaned = "\n".join(meta_lines + ([body] if body else [])).strip() + "\n"
    stats.output_chars = len(cleaned)
    stats.output_lines = cleaned.count("\n") + (0 if cleaned.endswith("\n") else 1)
    return cleaned, stats


def clean_directory(
    input_dir: Path,
    output_dir: Path,
) -> CleanReport:
    """批量清洗目录下全部 .md。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(input_dir.glob("*.md"))
    report = CleanReport(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        files_count=len(files),
        timestamp=datetime.now().isoformat(),
    )

    for path in files:
        raw = path.read_text(encoding="utf-8")
        cleaned, stats = clean_markdown(raw, filename=path.name)
        out_path = output_dir / path.name
        out_path.write_text(cleaned, encoding="utf-8")
        stats.source_file = str(path)
        stats.output_file = str(out_path)
        report.files.append(stats)

    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="清洗微信公众号 Markdown，输出到干净目录。")
    parser.add_argument(
        "--input-dir",
        default="docs/熊猫公众号",
        help="输入目录，默认 docs/熊猫公众号",
    )
    parser.add_argument(
        "--output-dir",
        default="data/panda_wechat_clean",
        help="输出目录，默认 data/panda_wechat_clean",
    )
    parser.add_argument(
        "--report",
        default="sandbox/panda_wechat_clean_report.json",
        help="清洗报告 JSON 路径",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    root = get_project_root()
    input_dir = (root / args.input_dir).resolve()
    output_dir = (root / args.output_dir).resolve()
    report_path = (root / args.report).resolve()

    if not input_dir.exists():
        print(f"ERROR: 输入目录不存在: {input_dir}")
        return 1

    report = clean_directory(input_dir, output_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("=== clean_wechat_md ===")
    print(f"input_dir: {input_dir}")
    print(f"output_dir: {output_dir}")
    print(f"files: {report.files_count}")
    total_img = sum(f.dropped_image_lines for f in report.files)
    total_ui = sum(f.dropped_ui_lines for f in report.files)
    print(f"dropped_image_lines: {total_img}")
    print(f"dropped_ui_lines: {total_ui}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
