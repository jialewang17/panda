# AnyClaw

面向“大熊猫知识”场景的知识图谱工程：从 Markdown 抽取结构化知识，写入 Neo4j，再基于图谱做问答。

## 当前能力
- 文档抽取：`tools/panda_history_extractor.py`
  - 单文件/目录批量抽取
  - 失败重试与断点续跑
  - 运行结果独立目录输出
- 图谱写入：`tools/neo4j_graph_writer.py`
  - 支持 Neo4j Aura
  - 关系类型使用抽取结果中的 `predicate`
  - 节点/关系保存 `evidence` 与 `source_file`
- 图谱问答：`tools/neo4j_qa.py`
  - 两阶段检索（先文档候选，再关系精排）
  - 支持 persona（如 `kid`）
  - 支持来源文档展示与交互模式

## 环境准备
1. Python 3.10+
2. 安装依赖：
   - `pip install -r requirements.txt`
3. 配置 `.env`（示例）：

```env
QWEN_APIKEY=your_qwen_api_key
NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=neo4j
```

## 常用命令

### 1) 抽取目录下全部文档
```bash
python -m tools.panda_history_extractor --docs-dir "docs/熊猫知识" --max-concurrency 5 --retry-failed-times 1
```

### 2) 抽取单文件
```bash
python -m tools.panda_history_extractor --md-path "docs/熊猫知识/【熊猫知识】气味标记 - 成都大熊猫繁育研究基地.md"
```

### 3) 写入 Neo4j
```bash
python -m tools.neo4j_graph_writer --result-json "data/wiki/<run_dir>/<result>.json"
```

### 4) 问答（单问）
```bash
python -m tools.neo4j_qa --question "大熊猫是怎么交流的" --persona kid --show-sources
```

### 5) 问答（交互）
```bash
python -m tools.neo4j_qa --interactive --persona default --show-sources
```

### 6) 导出 Neo4j schema 统计到 JSON
默认输出到 `sandbox/neo4j_schema_<timestamp>.json`：
```bash
python scripts/inspect_neo4j_schema.py --database 941568f4
```

指定输出文件：
```bash
python scripts/inspect_neo4j_schema.py --database 941568f4 --output "sandbox/neo4j_schema_latest.json"
```

## 目录说明
- `docs/熊猫知识/`：知识源文档
- `tools/`：抽取、入库、问答工具
- `scripts/`：辅助脚本（如 Neo4j schema 统计导出）
- `data/wiki/`：抽取运行结果目录（按运行批次分组）
- `config/model.yaml`：模型配置（问答默认走 `text_generation`）

## 注意事项
- 若 Aura 连接报认证错误，先检查 `NEO4J_URI/USERNAME/PASSWORD/DATABASE`。
- 若要重跑某批次，优先使用 `--resume-run-output-dir` 续跑。
- 生产数据谨慎使用 `neo4j_graph_writer --clear`。
