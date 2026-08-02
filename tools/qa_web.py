"""大熊猫知识库问答演示页（FastAPI），复用 neo4j_qa 核心逻辑。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from tools.neo4j_qa import neo4j_qa
from utils.path import get_project_root

WEB_DIR = get_project_root() / "web" / "qa_demo"

app = FastAPI(title="Panda QA Demo", version="1.0.0")


class AskRequest(BaseModel):
    """演示页提问请求。"""

    question: str = Field(..., min_length=1, description="用户问题")
    category: str = Field(default="", description="栏目：空/全部/熊猫知识/熊猫谣言/熊猫资料")
    persona: str = Field(default="educator", description="educator 或 kid")
    top_k: int = Field(default=20, ge=1, le=50)
    strict_mode: bool = Field(default=True)


def _call_neo4j_qa(payload: Dict[str, Any]) -> Dict[str, Any]:
    """与评测脚本一致：优先调用 langchain tool 的底层函数。"""
    fn = getattr(neo4j_qa, "func", None)
    raw = fn(**payload) if callable(fn) else neo4j_qa.invoke(payload)
    if isinstance(raw, dict):
        return raw
    return json.loads(raw)


def _normalize_category(category: str) -> str:
    text = (category or "").strip()
    if text in {"", "全部", "all", "ALL"}:
        return ""
    return text


@app.get("/")
def index() -> FileResponse:
    index_path = WEB_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail=f"演示页不存在: {index_path}")
    return FileResponse(index_path)


@app.post("/api/ask")
def ask(req: AskRequest) -> Dict[str, Any]:
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    persona = (req.persona or "educator").strip().lower()
    if persona not in {"educator", "kid"}:
        raise HTTPException(status_code=400, detail="persona 仅支持 educator / kid")

    try:
        result = _call_neo4j_qa(
            {
                "question": question,
                "category": _normalize_category(req.category),
                "persona": persona,
                "top_k": int(req.top_k),
                "strict_mode": bool(req.strict_mode),
                "database": "",
            }
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"问答失败: {type(exc).__name__}: {exc}",
        ) from exc

    # 前端只需摘要字段；完整 evidences 仍返回便于调试
    return {
        "ok": bool(result.get("ok")),
        "question": result.get("question", question),
        "answer": result.get("answer", ""),
        "answer_mode": result.get("answer_mode", ""),
        "category": result.get("category", ""),
        "persona": result.get("persona", persona),
        "rows_count": result.get("rows_count", 0),
        "quality": result.get("quality", {}),
        "evidences": result.get("evidences", []),
        "hit_graph": result.get("hit_graph", {}),
        "error": result.get("error", ""),
    }


def create_app() -> FastAPI:
    """供 uvicorn 加载的工厂（挂载静态资源）。"""
    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")
    return app


# 模块导入时挂载静态目录，便于 `uvicorn tools.qa_web:app`
create_app()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="启动大熊猫知识库问答演示页。")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="端口，默认 8000")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="开发模式自动重载（可选）。",
    )
    args = parser.parse_args(argv)

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "缺少依赖：请先执行 pip install fastapi uvicorn"
        ) from exc

    if not WEB_DIR.exists():
        raise SystemExit(f"演示静态目录不存在: {WEB_DIR}")

    print(f"Panda QA Demo: http://{args.host}:{args.port}/")
    print("终端问答仍可用: python -m tools.neo4j_qa --interactive")
    uvicorn.run(
        "tools.qa_web:app",
        host=args.host,
        port=int(args.port),
        reload=bool(args.reload),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
