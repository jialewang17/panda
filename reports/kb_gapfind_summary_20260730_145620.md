# 知识库挖缺口评估摘要（第四轮 20260730_145620）

## 总览
- 样本数: 42
- OK Rate: 0.571429
- No-Hit Rate: 0.02381
- Char F1: 0.028876
- Hit@5: 0.880952
- gap_label_counts: `{'ok': 24, 'hallucination_risk': 13, 'partial': 4, 'no_hit': 1}`

## 对比前几轮
- OK Rate: 0.444 → 0.524 → **0.571**
- No-Hit Rate: 0.306 → 0.143 → **0.024**
- Hit@5: 0.667 → 0.833 → **0.881**

## 分栏目缺口
- **熊猫资料** (15): ok=10 no_hit=0 hallucination_risk=4 partial=1
- **熊猫谣言** (15): ok=6 no_hit=0 hallucination_risk=7 partial=2
- **熊猫知识** (12): ok=8 no_hit=1 hallucination_risk=2 partial=1

## 缺口事实类型 Top
- 其他: 6
- 父母: 4
- 繁殖: 3
- 保护: 2
- 去世: 1
- 天敌: 1
- 行为: 1

## 优先补缺清单
- [hallucination_risk][熊猫资料][父母] 大熊猫团团的父母分别是谁？
  - gold: 父亲是灵灵，母亲是华美。
  - rows=15 mode=knowledge_base
- [partial][熊猫资料][去世] 大熊猫兴兴（龙龙）的去世原因是什么？
  - gold: 麻醉恢复过程中呕吐物误吸入肺导致窒息死亡
  - rows=15 mode=knowledge_base
- [hallucination_risk][熊猫谣言][其他] 网传大熊猫“宝力”臀部流出的“白线虫”实际是什么？
  - gold: 竹子残渣
  - rows=2 mode=knowledge_base
- [partial][熊猫知识][其他] 【熊猫知识】伴生动物这篇文章的来源网址是什么？
  - gold: https://www.panda.org.cn/cn/education/database/kpzs/2023-07-03/369.html
  - rows=15 mode=knowledge_base
- [hallucination_risk][熊猫谣言][其他] 大熊猫在过去300万年中经历了什么种群变化？
  - gold: 多次种群扩张和收缩
  - rows=2 mode=knowledge_base
- [hallucination_risk][熊猫资料][父母] 大熊猫美兰的父母是谁？
  - gold: 伦伦和洋洋
  - rows=15 mode=knowledge_base
- [partial][熊猫谣言][其他] 旅美大熊猫‘丫丫’体型消瘦的主要原因是什么？
  - gold: 老年疾病，包括慢性肠炎、牙齿磨损导致的进食困难以及季节性脱毛。
  - rows=3 mode=knowledge_base
- [hallucination_risk][熊猫谣言][保护] 中国正规大熊猫保护机构是否允许动物表演？
  - gold: 严禁动物表演。
  - rows=1 mode=knowledge_base
- [hallucination_risk][熊猫谣言][保护] 大熊猫基地的主要收入来源是什么？
  - gold: 政府拨款和科研合作
  - rows=1 mode=knowledge_base
- [partial][熊猫谣言][其他] 大熊猫是否可能主动攻击人类？
  - gold: 是，尤其在护崽、受惊或发情期可能主动攻击人类。
  - rows=1 mode=knowledge_base
- [hallucination_risk][熊猫谣言][天敌] 大熊猫有哪些天敌？
  - gold: 豺、豹、黄喉貂等。
  - rows=3 mode=knowledge_base
- [hallucination_risk][熊猫知识][繁殖] 雌性大熊猫每年发情几次？
  - gold: 每年发情一次
  - rows=7 mode=knowledge_base
- [hallucination_risk][熊猫资料][父母] 大熊猫科比的父母是谁？
  - gold: 苏苏和越越
  - rows=15 mode=knowledge_base
- [no_hit][熊猫知识][其他] 大熊猫的官方中文名称是什么？
  - gold: 大熊猫
  - rows=0 mode=llm_fallback
- [hallucination_risk][熊猫资料][父母] 大熊猫七喜的父母分别是谁？
  - gold: 母亲为奇福，父亲为美兰
  - rows=15 mode=knowledge_base
- [hallucination_risk][熊猫谣言][繁殖] 圈养大熊猫繁殖中使用外源激素的目的是什么？
  - gold: 提高自然交配或人工授精的成功率。
  - rows=2 mode=knowledge_base
- [hallucination_risk][熊猫谣言][繁殖] 大熊猫在圈养条件下通常多久繁殖一次？
  - gold: 每2-3年才繁殖一次
  - rows=1 mode=knowledge_base
- [hallucination_risk][熊猫知识][行为] 大熊猫为什么采用慢吞吞的行走方式？
  - gold: 为了保存能量，以适应低能量的食物。
  - rows=5 mode=knowledge_base
