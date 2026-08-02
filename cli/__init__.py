"""CLI 模块：提供命令行交互界面。"""

from __future__ import annotations

from typing import Any

__all__ = ["main"]


def __getattr__(name: str) -> Any:
    """延迟导出 main，避免 `python -m cli.main` 触发 RuntimeWarning。"""
    if name == "main":
        from cli.main import main as _main

        return _main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
