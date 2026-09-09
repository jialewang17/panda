# 熊猫知识文档编号说明

- 命名规则：`熊猫知识_{NNN}_{标题}.md`
- 当前下一个可用编号：**045**
- 旧→新映射：`data/curated/panda_knowledge_source_map.json`
- 图谱关系字段 `r.source_file` / 节点 `n.source_files` 已迁移到新路径；映射表保留旧名便于历史溯源。

## 新增文件

1. 使用下一个编号，例如 `熊猫知识_045_你的主题.md`
2. 抽取入库时 `--category 熊猫知识`
3. 入库后把 `next_id` 更新进映射 JSON（或重跑编号脚本的维护逻辑）
