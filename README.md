# panda

面向“大熊猫知识”场景的知识图谱工程：从 Markdown 抽取结构化知识，写入 Neo4j，再基于图谱做问答。

## 当前能力
- 文档抽取：`tools/panda_history_extractor.py`
  - 单文件/目录批量抽取
  - 失败重试与断点续跑
  - 支持“问题清单驱动”的二次定向抽取（补强细节事实）
  - 运行结果独立目录输出
  - 支持语料栏目：`熊猫知识` / `熊猫谣言` / `熊猫资料`（入库时写入 `r.category`）
- 图谱写入：`tools/neo4j_graph_writer.py`
  - 支持 Neo4j Aura
  - 关系类型使用抽取结果中的 `predicate`
  - 节点/关系保存 `evidence`、`source_file`、`topic`、`category`
- 图谱问答：`tools/neo4j_qa.py`
  - 两阶段检索（先文档候选，再关系精排）
  - 可先选栏目再提问，按 `r.category` 缩小检索范围
  - 展示流程模块化：运行信息 -> Cypher 解析 -> 命中节点关系 -> 回答 -> 来源
  - 支持 persona（如 `kid`）
  - 支持来源文档展示、交互模式与问答 JSON 自动落盘
  - 当知识库无证据时自动触发 LLM 通用知识兜底回答（会标注为非知识库证据）
  - 回答质量评分支持规则护栏（拒答/无依据不再出现高分）

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

### 0) 交互式 CLI 主入口
```bash
python -m cli.main
```

进入后可用 `/new` 开新会话、`/memory` 选历史会话、`/tools` / `/models` / `/skills` 查看状态，`/exit` 退出。

### 1) 按栏目抽取文档
熊猫知识：
```bash
python -m tools.panda_history_extractor --docs-dir "docs/熊猫知识" --category "熊猫知识" --max-concurrency 5 --retry-failed-times 1
```

熊猫谣言（建议用辟谣汇总，避免把谣言标题当事实）：
```bash
python -m tools.panda_history_extractor --docs-dir "docs/熊猫谣言" --category "熊猫谣言" --max-concurrency 5
```

熊猫资料（个体档案等）：
```bash
python -m tools.panda_history_extractor --docs-dir "docs/熊猫资料" --category "熊猫资料" --max-concurrency 3
```

`--category` 可省略：会按路径自动推断（`docs/熊猫谣言`、`docs/谣言与辟谣` → 熊猫谣言；`docs/熊猫资料` → 熊猫资料；其余默认熊猫知识）。

基于细节问题清单做“基础抽取 + 二次定向抽取”：
```bash
python -m tools.panda_history_extractor --docs-dir "docs/熊猫知识" --category "熊猫知识" --focus-questions-json "docs/panda_detailed_questions.json" --max-concurrency 5
```

### 2) 抽取单文件
```bash
python -m tools.panda_history_extractor --md-path "docs/熊猫知识/【熊猫知识】气味标记 - 成都大熊猫繁育研究基地.md" --category "熊猫知识"
```

### 3) 写入 Neo4j
建议全量重载时先清空，再分别写入各栏目结果；只更新某一栏目时用 `--clear-category`：
```bash
python -m tools.neo4j_graph_writer --result-json "data/wiki/<knowledge_run>/<result>.json" --clear
python -m tools.neo4j_graph_writer --result-json "data/wiki/<rumor_run>/<result>.json"
python -m tools.neo4j_graph_writer --result-json "data/wiki/<profile_run>/<result>.json"
# 仅替换资料栏目：
# python -m tools.neo4j_graph_writer --result-json "..." --clear-category "熊猫资料"
```

### 3.1) 挖缺口定向补录（可复现写入）
多轮评测补录已沉淀为正式产物（不依赖 `sandbox/` 手工 MERGE）：

- `data/curated/kb_gap_facts_v1.json`：writer 兼容的抽取结果 JSON（当前约 179 条）
- `docs/panda_gap_focus_questions.json`：二次定向抽取问题清单
- `scripts/curate_gap_facts.py`：从本地 `sandbox/gap_fact_upsert*.json` 重新生成上述产物

清库 / 换库后，用 writer **增量写入**即可恢复补录事实（不要加 `--clear`）：

```bash
python -m tools.neo4j_graph_writer --result-json "data/curated/kb_gap_facts_v1.json"
```

若希望从原文重新 LLM 抽取这些缺口事实（更慢，可与 seed 互补）：

```bash
python -m tools.panda_history_extractor --docs-dir "docs/熊猫知识" --category "熊猫知识" --focus-questions-json "docs/panda_gap_focus_questions.json" --max-concurrency 3
# 再把新 result_file_path 交给 neo4j_graph_writer（不加 --clear）
```

本地若仍有新的 `sandbox/gap_fact_upsert*.json`，可重新沉淀：

```bash
python scripts/curate_gap_facts.py
```

### 4) 问答（单问，指定栏目）
```bash
python -m tools.neo4j_qa --category "熊猫知识" --question "大熊猫是怎么交流的" --persona kid --show-sources
python -m tools.neo4j_qa --category "熊猫谣言" --question "大熊猫是猫科动物吗" --show-sources
python -m tools.neo4j_qa --category "熊猫资料" --question "和花是什么时候出生的" --show-sources
```
说明：
- `neo4j_qa` 每次结果会附带 `quality` 字段（0-100 分与等级）。
- 若知识库未命中证据，会自动走 `llm_fallback` 给出通用知识回答，并在答案中提示该回答非知识库证据。

### 5) 问答（交互）
```bash
python -m tools.neo4j_qa --interactive --persona educator --show-sources
```
进入后会先选择栏目；也可随时用 `:category 熊猫知识|熊猫谣言|熊猫资料|全部` 切换。

### 5.1) 问答演示页（Web，保留终端）
本地演示用轻量页面，后端复用同一套 `neo4j_qa`，**不替代**终端 CLI：

```bash
pip install fastapi uvicorn
python -m tools.qa_web
```

浏览器打开 `http://127.0.0.1:8000/`。支持栏目/语气选择、示例问题、回答、来源与命中关系摘要。

可选参数：
```bash
python -m tools.qa_web --host 127.0.0.1 --port 8000
```

交互问答默认自动保存每轮 JSON 到 `sandbox/qa_sessions/`（可指定会话名）：
```bash
python -m tools.neo4j_qa --interactive --show-sources --session-name panda_demo
```

如需关闭自动保存：
```bash
python -m tools.neo4j_qa --interactive --no-save-json
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

### 7) 评估知识库问答效果（准确率/命中率）
先准备 JSONL 评测集（每行一个问题）：
```json
{"id":"q1","question":"大熊猫幼仔一般在哪里出生？","gold_answer":"一般出生在母兽搭建的产仔巢（树洞或岩洞）中。","gold_sources":["【熊猫知识】繁殖 - 成都大熊猫繁育研究基地.md"]}
```

运行评测：
```bash
python -m tools.qa_evaluator --dataset "data/eval/qa_eval.jsonl" --qa-top-k 20 --hit-at-k 5 --output "reports/qa_eval_latest.json"
```

可选：启用 LLM 语义裁判（速度慢且有调用成本）：
```bash
python -m tools.qa_evaluator --dataset "data/eval/qa_eval.jsonl" --use-llm-judge
```

说明：`qa_evaluator` 会复用 `neo4j_qa` 的在线质量字段（如 `online_quality`），便于离线评测与在线表现对齐。

### 7.1) 文档驱动自动出题 + 查漏补缺
从 `docs/熊猫知识|熊猫谣言|熊猫资料` 自动出题，调用当前 `neo4j_qa` 作答，输出质量报告与补缺清单：

```bash
# 只出题
python scripts/kb_doc_eval.py generate --categories 熊猫资料 --max-questions 40

# 只评测已有集
python scripts/kb_doc_eval.py run --dataset data/eval/kb_auto.jsonl

# 出题+评测一条龙
python scripts/kb_doc_eval.py all --categories 熊猫资料,熊猫谣言 --max-questions 30
```

跳过已测切块、换随机种子（便于挖新缺口）：
```bash
python scripts/kb_doc_eval.py all --categories 熊猫资料,熊猫谣言,熊猫知识 --max-questions 42 --per-chunk 1 --skip-tested --seed 20260730 --output data/eval/kb_auto_round5.jsonl
```

产物默认在 `reports/`：
- `kb_eval_*.json`：汇总与逐题明细
- `kb_gaps_*.jsonl`：缺口题与补缺建议
- `kb_focus_questions_*.json`：可直接喂给 `panda_history_extractor --focus-questions-json`

## 目录说明
- `docs/熊猫知识/`：知识源文档（栏目：熊猫知识）
- `docs/熊猫谣言/`：辟谣文档（栏目：熊猫谣言）
- `docs/熊猫资料/`：个体档案等资料（栏目：熊猫资料）
- `docs/谣言与辟谣/`：谣言与辟谣原始/衍生文档
- `cli/`：交互式 CLI 主入口（`python -m cli.main`）
- `tools/`：抽取、入库、问答工具（含 `qa_web` 演示页入口）
- `web/qa_demo/`：问答演示页静态资源
- `scripts/`：辅助脚本（如 Neo4j schema 统计导出）
- `data/wiki/`：抽取运行结果目录（按运行批次分组）
- `data/curated/`：可复现的定向补录种子（如 `kb_gap_facts_v1.json`）
- `config/model.yaml`：模型配置（问答默认走 `text_generation`）

## 注意事项
- 若 Aura 连接报认证错误，先检查 `NEO4J_URI/USERNAME/PASSWORD/DATABASE`。
- 若要重跑某批次，优先使用 `--resume-run-output-dir` 续跑。
- 生产数据谨慎使用 `neo4j_graph_writer --clear`。
- 只更新某一栏目时，优先使用 `--clear-category <栏目名>`，避免误清其他栏目。
- 重新全量入库时，建议先 `--clear` 再写入，避免旧关系缺少 `category`。
