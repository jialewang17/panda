"""工具模块：提供 Agent 可用的工具函数。"""

__all__ = [
    "panda_history_extractor",
    "neo4j_graph_writer",
    "neo4j_qa",
    "qa_metrics",
    "qa_evaluator",
]


def __getattr__(name: str):
    """按需导出工具，避免模块级副作用。"""
    if name == "panda_history_extractor":
        from tools.panda_history_extractor import panda_history_extractor

        return panda_history_extractor
    if name == "neo4j_graph_writer":
        from tools.neo4j_graph_writer import neo4j_graph_writer

        return neo4j_graph_writer
    if name == "neo4j_qa":
        from tools.neo4j_qa import neo4j_qa

        return neo4j_qa
    if name == "qa_evaluator":
        from tools.qa_evaluator import main as qa_evaluator

        return qa_evaluator
    if name == "qa_metrics":
        from tools import qa_metrics

        return qa_metrics
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
