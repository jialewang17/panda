"""
Extract only "辟谣" sections from the rumor-vs-debunk markdown document.

Output: a new markdown file with each rumor section's debunk bullets only,
so downstream entity/relation extraction focuses on correct conclusions.
"""

from __future__ import annotations

import re
from pathlib import Path


def extract_rebuttals(
    src_path: Path,
    out_path: Path,
) -> int:
    text = src_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # e.g. "### 22. 谣言：大熊猫是国宝，所以不能租借或送到国外"
    rumor_header_re = re.compile(r"^###\s+(\d+)\.\s+谣言：(.+)$")
    # e.g. "- **辟谣**："
    rebuttal_start_re = re.compile(r"^-\s*\*\*辟谣\*\*[:：]\s*")
    # e.g. "- **信息来源**："
    source_start_re = re.compile(r"^-\s*\*\*信息来源\*\*[:：]\s*")

    sections: list[tuple[str, str, list[str]]] = []
    cur_num: str | None = None
    cur_title: str | None = None
    capturing = False
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if cur_num is not None and cur_title is not None and buf:
            sections.append((cur_num, cur_title, buf))
        buf = []

    for line in lines:
        m = rumor_header_re.match(line)
        if m:
            if capturing:
                capturing = False
                flush()
            cur_num, cur_title = m.group(1), m.group(2).strip()
            continue

        if rebuttal_start_re.match(line):
            capturing = True
            buf = []
            continue

        if capturing and source_start_re.match(line):
            capturing = False
            flush()
            continue

        if capturing:
            buf.append(line.rstrip())

    if capturing:
        flush()

    out_lines = [
        "# 大熊猫辟谣汇总（仅辟谣）",
        "",
        f"来源文档：{src_path.as_posix()}",
        "",
        "> 说明：各节标题中的「谣言：…」是被驳斥的错误说法，不是事实；",
        "> 标题下方的条目才是正确结论。抽取知识时请只采信正文要点，勿将谣言标题当作实体关系。",
        "",
    ]

    for num, title, rebut_lines in sections:
        # 保留「谣言：」前缀，避免标题被误抽成正确事实
        out_lines.append(f"## {num}. 谣言：{title}")
        out_lines.append("")
        out_lines.append("**正确结论（辟谣）：**")
        out_lines.append("")
        out_lines.extend(rebut_lines)
        out_lines.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")

    return len(sections)


def main() -> None:
    project_root = Path(".").resolve()
    src = project_root / "docs" / "谣言与辟谣" / "大熊猫谣言与辟谣：全面知识库.md"
    out = project_root / "docs" / "谣言与辟谣" / "大熊猫辟谣汇总_仅辟谣.md"

    if not src.exists():
        raise FileNotFoundError(f"源文档不存在：{src}")

    count = extract_rebuttals(src, out)
    print("OK")
    print("sections:", count)
    print("output:", out.as_posix())


if __name__ == "__main__":
    main()

