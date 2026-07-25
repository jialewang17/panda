# TOOLS.md - 工具策略

## 工具使用原则
- 优先复用已有工具，不重复造轮子。
- 每个工具都应同时支持：`@tool` 调用 + CLI 调用。
- 输出默认简洁，必要时提供 `--verbose` JSON。

## 项目关键工具
- `panda_history_extractor`
  - 用途：抽取 Markdown 为结构化知识与三元组。
  - 特性：并发、失败重试、断点续跑、Neo4j 导出文件。
- `neo4j_graph_writer`
  - 用途：将抽取结果写入 Neo4j/Aura。
  - 特性：支持 dry-run、可选清库、关系类型映射自 `predicate`。
- `neo4j_qa`
  - 用途：图谱检索 + 大模型生成带依据回答。
  - 特性：两阶段检索、意图增强、persona、交互模式。
  - 栏目：`category` 可为 `熊猫知识` / `熊猫资料` / `熊猫谣言`；空值时读取会话栏目模式。
  - 会话模式：`/new` 后可选「随便问问（自动判断）」或固定栏目。

## 工具注册占位
{{tool_registry}}
