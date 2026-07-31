# 知识库挖缺口评估摘要（第六轮 20260731_150022）

## 采样策略
- `--skip-tested`：跳过历史 `kb_auto*.jsonl` 已测 chunk（97 → 剩 130）
- `--seed 20260731`
- 数据集：`data/eval/kb_auto_round6.jsonl`（资料21 / 谣言4 / 知识17）

## 总览
- 样本数: 42
- OK Rate: 0.642857
- No-Hit Rate: 0.214286
- Hit@5: 0.714286
- Char F1: 0.029088
- gap_label_counts: `{'ok': 27, 'no_hit': 9, 'hallucination_risk': 5, 'partial': 1}`

## 对比前几轮
- OK: 0.44 → 0.52 → 0.57 → 0.40 → **0.64**（本轮）
- No-Hit: 0.31 → 0.14 → 0.02 → 0.33 → **0.21**（本轮）
- Hit@5: … → 0.67 → **0.71**（本轮）

## 分栏目
- **熊猫知识** (17): ok=5 no_hit=9 hallucination_risk=2 partial=1
- **熊猫谣言** (4): ok=3 no_hit=0 hallucination_risk=1 partial=0
- **熊猫资料** (21): ok=19 no_hit=0 hallucination_risk=2 partial=0

## 缺口事实类型 Top
- 分布: 5
- 其他: 3
- 父母: 2
- 研究: 1
- 繁殖: 1
- 保护: 1
- 行为: 1
- 外形: 1

## no_hit 优先清单
- [熊猫知识] 大熊猫粪便中能提取出什么信息用于分析种群特征？
  - gold: DNA信息，可分析性别、年龄、亲缘关系和种群数量。
  - doc: docs/熊猫知识/[202108101754]大熊猫奇妙课堂大熊猫的粪便青团.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 大熊猫的婚姻制度是什么？
  - gold: 多雄多雌制
  - doc: docs/熊猫知识/【熊猫知识】繁殖 - 成都大熊猫繁育研究基地.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 大熊猫在野外偶然与其他动物或人相遇时通常会采取什么行为？
  - gold: 采用回避的方式
  - doc: docs/熊猫知识/【熊猫知识】行为特点 - 成都大熊猫繁育研究基地.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 截至2020年，全世界圈养大熊猫总数是多少？
  - gold: 633只
  - doc: docs/熊猫知识/【熊猫知识】大熊猫分布和现状 - 成都大熊猫繁育研究基地.md
  - rows=2 mode=knowledge_base
- [熊猫知识] 截至2007年底，全国圈养大熊猫数量是多少只？
  - gold: 239只
  - doc: docs/熊猫知识/[202109111000]大熊猫奇妙课堂大熊猫的保护.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 为什么竹子开花目前会威胁到大熊猫的生存？
  - gold: 因为大熊猫栖息地极小且相互隔离，导致其无法迁徙寻找其他竹子。
  - doc: docs/熊猫知识/【熊猫知识】致危因素 - 成都大熊猫繁育研究基地.md
  - rows=5 mode=knowledge_base
- [熊猫知识] 大熊猫的尾长范围是多少？
  - gold: 100～120mm
  - doc: docs/熊猫知识/【熊猫知识】外形特征 - 成都大熊猫繁育研究基地.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 大熊猫皮肤最厚处可达多少毫米？
  - gold: 10毫米
  - doc: docs/熊猫知识/【熊猫知识】外形特征 - 成都大熊猫繁育研究基地.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 野生大熊猫蛔虫感染率是多少？
  - gold: 100%
  - doc: docs/熊猫知识/[202109021950]大熊猫奇妙课堂大熊猫的病历本.md
  - rows=0 mode=llm_fallback

## hallucination_risk / partial
- [hallucination_risk][熊猫资料] 大熊猫淼淼的父母分别是谁？
  - gold: 母亲是娇子，父亲是勇勇。
  - rows=20 mode=knowledge_base
- [hallucination_risk][熊猫谣言] 旅美大熊猫“美香”一家回国后是否遭到藏匿或虐待？
  - gold: 否，官方已辟谣并说明其接受规范隔离检疫和专业照料。
  - rows=10 mode=knowledge_base
- [hallucination_risk][熊猫知识] 为什么大熊猫缺乏可见的信号交流方式？
  - gold: 因为它们常年生活在高山上茂密的薄雾弥漫的竹林里，看不见彼此。
  - rows=15 mode=knowledge_base
- [hallucination_risk][熊猫知识] 大熊猫目前在中国哪些山系有分布？
  - gold: 秦岭山系、岷山山系、邛崃山系、凉山和大、小相岭山系
  - rows=20 mode=knowledge_base
- [hallucination_risk][熊猫资料] 大熊猫萌萌的父母分别是谁？
  - gold: 母亲为英英，父亲为灵灵
  - rows=20 mode=knowledge_base
- [partial][熊猫知识] 大熊猫最早被科学发现的时间和发现者是谁？
  - gold: 1869年3月，法国博物学家阿尔芒·戴维神父。
  - rows=20 mode=knowledge_base
