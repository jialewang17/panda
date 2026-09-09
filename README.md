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

基于问题清单做“基础抽取 + 二次定向抽取”（`--focus-questions-json`）：

| 文件 | 用途 |
|------|------|
| `docs/panda_detailed_questions.json` | 按文档整理的**细节题库**，首次/加细抽取时把文中细点抽成三元组 |
| `docs/panda_gap_focus_questions.json` | 评测挖缺口后沉淀的**补缺题库**，二次定向补抽查漏 |

```bash
# 细节加细抽取
python -m tools.panda_history_extractor --docs-dir "docs/熊猫知识" --category "熊猫知识" --focus-questions-json "docs/panda_detailed_questions.json" --max-concurrency 5

# 缺口定向补抽（更慢，可与 curated seed 互补）
python -m tools.panda_history_extractor --docs-dir "docs/熊猫知识" --category "熊猫知识" --focus-questions-json "docs/panda_gap_focus_questions.json" --max-concurrency 3
```

### 1.1) 熊猫知识文档编号
`docs/熊猫知识/` 已统一为可递增编号，便于后续新增：

- 命名：`熊猫知识_{NNN}_{标题}.md`（当前约 `001`–`044`，下一个可用 **`045`**）
- 说明：`docs/熊猫知识/README_编号说明.md`
- 旧→新映射：`data/curated/panda_knowledge_source_map.json`（改名时同步迁移了 Neo4j 的 `r.source_file` / `n.source_files`，并保留 `source_file_prev`）

新增文档请按下一个编号命名后再抽取入库；若需重跑改名迁移：

```bash
python scripts/renumber_panda_knowledge_docs.py --dry-run
python scripts/renumber_panda_knowledge_docs.py --apply
```

### 2) 抽取单文件
```bash
python -m tools.panda_history_extractor --md-path "docs/熊猫知识/熊猫知识_032_气味标记.md" --category "熊猫知识"
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

本地若仍有新的 `sandbox/gap_fact_upsert*.json`，可重新沉淀：

```bash
python scripts/curate_gap_facts.py
```

### 3.2) 百度百科星图分卷按日入库（熊猫资料）
星图分卷 `百度百科熊猫星图4.md`～`22.md` 按日抽取并**增量**写入 Neo4j，丰富个体档案。计划与进度见：

- `docs/熊猫资料/百度百科星图入库计划.md`
- `data/curated/starmap_ingest_plan.json` / `starmap_ingest_progress.json`

```bash
python scripts/ingest_starmap_daily.py --status
python scripts/ingest_starmap_daily.py
# 预览：python scripts/ingest_starmap_daily.py --dry-run
```

### 3.3) 星图分卷多轮核验与补齐（熊猫资料）
入库完成后，对星图4～22 逐卷对照金标准档案做覆盖核验；未达标则缺口补抽并增量写库。计划见：

- `docs/熊猫资料/百度百科星图核验计划.md`
- `data/curated/starmap_verify_plan.json` / `starmap_verify_progress.json`
- 核验报告：`data/curated/starmap_verify_reports/`

```bash
python scripts/verify_starmap_daily.py --self-check
python scripts/verify_starmap_daily.py --status
python scripts/verify_starmap_daily.py --day 1 --fix
```

### 3.4) 星图/金标准随机抽检（熊猫资料）
从星图4～22 与 `熊猫资料.md` / `2` / `3` 每批随机抽 5 只熊猫，调用 `neo4j_qa` 做查漏补缺（默认 10 轮）：

```bash
python scripts/starmap_kb_spotcheck.py --dry-run
python scripts/starmap_kb_spotcheck.py --rounds 10 --batch-size 5 --seed 42
```

产物在 `reports/starmap_kb_spotcheck_*.json`（含 gaps / focus）；可用 focus 对缺口个体定向补抽后增量写库（参考近期 `sandbox/starmap_spotcheck_fix/` 流程与 `data/curated/starmap_spotcheck_focus_fix.json`）。

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
{"id":"q1","question":"大熊猫幼仔一般在哪里出生？","gold_answer":"一般出生在母兽搭建的产仔巢（树洞或岩洞）中。","gold_sources":["熊猫知识_036_繁殖.md"]}
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
- `docs/熊猫知识/`：知识源文档（栏目：熊猫知识；统一编号 `熊猫知识_NNN_标题.md`）
- `docs/熊猫资料/`：个体档案与百度百科星图分卷（栏目：熊猫资料）
- `docs/熊猫谣言/`：辟谣文档（栏目：熊猫谣言）
- `docs/谣言与辟谣/`：谣言与辟谣原始/衍生文档
- `docs/panda_detailed_questions.json`：熊猫知识细节抽取题库
- `docs/panda_gap_focus_questions.json`：挖缺口二次定向抽取题库
- `cli/`：交互式 CLI 主入口（`python -m cli.main`）
- `tools/`：抽取、入库、问答工具（含 `qa_web` 演示页入口）
- `web/qa_demo/`：问答演示页静态资源
- `scripts/`：辅助脚本（入库/核验/抽检/编号迁移、schema 统计等）
- `data/wiki/`：抽取运行结果目录（按运行批次分组）
- `data/curated/`：可复现产物（缺口 seed、星图进度、知识文档旧→新映射等）
- `reports/`：评测与抽检报告
- `config/model.yaml`：模型配置（问答默认走 `text_generation`）

## 注意事项
- 若 Aura 连接报认证错误，先检查 `NEO4J_URI/USERNAME/PASSWORD/DATABASE`。
- 若要重跑某批次，优先使用 `--resume-run-output-dir` 续跑。
- 生产数据谨慎使用 `neo4j_graph_writer --clear`。
- 只更新某一栏目时，优先使用 `--clear-category <栏目名>`，避免误清其他栏目。
- 重新全量入库时，建议先 `--clear` 再写入，避免旧关系缺少 `category`。
- 熊猫知识文档请按编号规则新增；改名后务必同步 `source_file`（可用 `renumber_panda_knowledge_docs.py`），以免溯源断链。
- `sandbox/` 为本地任务产出目录，默认不入库 git。
