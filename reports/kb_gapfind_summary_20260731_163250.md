# 知识库挖缺口评估摘要（第八轮 20260731_163250）

## 采样策略
- `--skip-tested`：剩余未测 chunk 46/227，出题成功 19（部分块 LLM 未出题）
- `--seed 2026073118`
- 数据集：`data/eval/kb_auto_round8.jsonl`（全熊猫知识）
- 前置：第七轮行为类 no_hit 已补录（15+ 条）

## 总览
- 样本数: 19
- OK Rate: 0.526316
- No-Hit Rate: 0.263158
- Hit@5: 0.210526
- Char F1: 0.040982
- gap_label_counts: `{'ok': 10, 'partial': 1, 'hallucination_risk': 3, 'no_hit': 5}`

## 对比
- OK: 0.64(r6) → 0.52(r7) → **0.53**(r8)
- No-Hit: 0.21(r6) → 0.33(r7) → **0.26**(r8)

## 分栏目
- **熊猫知识** (19): ok=10 no_hit=5 hallucination_risk=3 partial=1

## 缺口事实类型 Top
- 其他: 4
- 行为: 3
- 分布: 1
- 食性: 1

## no_hit 优先清单
- [熊猫知识] 与大熊猫共存于成都大熊猫繁育研究基地的鸟类是什么？
  - gold: 红腹锦鸡
  - doc: docs/熊猫知识/熊猫知识_031_大熊猫的朋友.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 成都大熊猫繁育研究基地的官方网址是什么？
  - gold: https://www.panda.org.cn/cn/education/database/kpzs/2023-07-03/361.html
  - doc: docs/熊猫知识/熊猫知识_036_繁殖.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 五一棚为什么得名？
  - gold: 因为观察站的帐篷距离水源地有51步台阶。
  - doc: docs/熊猫知识/熊猫知识_014_大熊猫奇妙课堂五一棚.md
  - rows=1 mode=knowledge_base
- [熊猫知识] 大熊猫“福龙”是在哪里出生的？
  - gold: 奥地利美泉宫动物园
  - doc: docs/熊猫知识/熊猫知识_017_大熊猫奇妙课堂海归大熊猫.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 大熊猫在不同季节采食竹子部位的顺序是什么？
  - gold: 竹笋、嫩竹、竹竿
  - doc: docs/熊猫知识/熊猫知识_042_进食.md
  - rows=19 mode=knowledge_base

## hallucination_risk / partial
- [partial][熊猫知识] 成都大熊猫繁育研究基地的熊猫知识栏目中，育幼生长相关内容的来源网址是什么？
  - gold: https://www.panda.org.cn/cn/education/database/kpzs/2023-07-03/360.html
  - rows=20 mode=knowledge_base
- [hallucination_risk][熊猫知识] 大熊猫通过什么方式进行气味标记？
  - gold: 通过肛周腺分泌物涂抹在树干、竹子等物体上。
  - rows=20 mode=knowledge_base
- [hallucination_risk][熊猫知识] 1972年，中国政府赠送给美国人民的大熊猫名字是什么？
  - gold: 玲玲和兴兴
  - rows=6 mode=knowledge_base
- [hallucination_risk][熊猫知识] 大熊猫的野外天敌主要袭击哪些个体？
  - gold: 幼仔和病弱年老者
  - rows=20 mode=knowledge_base
