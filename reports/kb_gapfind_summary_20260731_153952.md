# 知识库挖缺口评估摘要（第七轮 20260731_153952）

## 采样策略
- `--skip-tested`：跳过含 round6 在内的历史 `kb_auto*.jsonl`
- `--seed 202607311`
- 数据集：`data/eval/kb_auto_round7.jsonl`
- 前置：第六轮 no_hit 已定向补录（约 19+ 条），并修复 topic 硬过滤漏召回

## 总览
- 样本数: 42
- OK Rate: 0.52381
- No-Hit Rate: 0.333333
- Hit@5: 0.452381
- Char F1: 0.052331
- gap_label_counts: `{'ok': 22, 'hallucination_risk': 6, 'no_hit': 14}`

## 对比
- OK: … → 0.40(r5) → 0.64(r6) → **0.52**(r7 新块)
- No-Hit: … → 0.33(r5) → 0.21(r6) → **0.33**(r7 新块)

## 分栏目
- **熊猫知识** (42): ok=22 no_hit=14 hallucination_risk=6 partial=0

## 缺口事实类型 Top
- 行为: 9
- 其他: 6
- 繁殖: 2
- 疾病: 1
- 食性: 1
- 保护: 1

## no_hit 优先清单
- [熊猫知识] 成都大熊猫繁育研究基地的官方网址是什么？
  - gold: https://www.panda.org.cn/cn/education/database/kpzs/2023-07-03/356.html
  - doc: docs/熊猫知识/熊猫知识_034_生存环境.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 《你好，大熊猫》短视频系列是由哪些机构联合推出的？
  - gold: 中国野生动物保护协会、央视《动物世界》、央视频
  - doc: docs/熊猫知识/[202109241712]30集大熊猫保护科普知识短视频⑫为什么大熊猫是旗舰物种.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 大熊猫是否具有冬眠习性？
  - gold: 不具有冬眠习性。
  - doc: docs/熊猫知识/熊猫知识_005_大熊猫奇妙课堂大熊猫的行为特征.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 圈养大熊猫一胎通常产几只幼仔？
  - gold: 1-2只
  - doc: docs/熊猫知识/熊猫知识_009_大熊猫奇妙课堂攻克三难.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 在野外，大熊猫妈妈面对双胞胎幼仔时通常会如何选择？
  - gold: 忽视或拒绝较弱的一个。
  - doc: docs/熊猫知识/熊猫知识_037_育幼生长.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 成都大熊猫繁育研究基地的官方英文名称是什么？
  - gold: Chengdu Research Base of Giant Panda Breeding
  - doc: docs/熊猫知识/熊猫知识_029_大熊猫历史.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 金丝猴的科学名是根据什么命名的？
  - gold: 根据一个著名的俄国美女。
  - doc: docs/熊猫知识/熊猫知识_031_大熊猫的朋友.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 成都大熊猫繁育研究基地关于熊猫的【熊猫知识】栏目主要介绍什么主题？
  - gold: 疾病
  - doc: docs/熊猫知识/熊猫知识_035_疾病.md
  - rows=7 mode=knowledge_base
- [熊猫知识] 大熊猫的邻居中，哪些动物被明确列为我国一级保护野生动物？
  - gold: 羚牛、豺
  - doc: docs/熊猫知识/熊猫知识_012_大熊猫奇妙课堂大熊猫的邻居们.md
  - rows=0 mode=llm_fallback
- [熊猫知识] 大熊猫爬树行为的主要原因有哪些？
  - gold: 临近求婚期、逃避危险、弱者回避强者。
  - doc: docs/熊猫知识/熊猫知识_040_行为特点.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 大熊猫的朋友这一知识栏目由哪家机构发布？
  - gold: 成都大熊猫繁育研究基地
  - doc: docs/熊猫知识/熊猫知识_031_大熊猫的朋友.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 熊猫的致危因素相关信息来源于哪个机构？
  - gold: 成都大熊猫繁育研究基地
  - doc: docs/熊猫知识/熊猫知识_038_致危因素.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 成都大熊猫繁育研究基地开展的行为富化活动主要目的是什么？
  - gold: 改善圈养大熊猫的行为福利。
  - doc: docs/熊猫知识/熊猫知识_039_行为富化.md
  - rows=20 mode=knowledge_base
- [熊猫知识] 大熊猫在野外发生冲突的主要原因是什么？
  - gold: 发情季节雄性争夺雌性。
  - doc: docs/熊猫知识/熊猫知识_040_行为特点.md
  - rows=0 mode=llm_fallback

## hallucination_risk / partial
- [hallucination_risk][熊猫知识] 大熊猫在1~3岁期间的行为特点是什么？
  - gold: 最为活跃，每天大部分时间都在玩耍、探索和爬树。
  - rows=20 mode=knowledge_base
- [hallucination_risk][熊猫知识] 什么是丰容？
  - gold: 通过构建和改变圈养动物的生活环境，改进喂食策略，使动物表现出正常的行为。
  - rows=4 mode=knowledge_base
- [hallucination_risk][熊猫知识] 大熊猫在野外两次进食之间通常睡多久？
  - gold: 2~4个小时
  - rows=1 mode=knowledge_base
- [hallucination_risk][熊猫知识] 大熊猫为什么一般是隔年产子？
  - gold: 因为大熊猫妈妈需要一年半到两年时间照顾幼仔，育幼期间不再繁殖。
  - rows=20 mode=knowledge_base
- [hallucination_risk][熊猫知识] 扭角羚在冬季会吃哪些熊猫不吃的植物？
  - gold: 已枯萎的竹叶。
  - rows=16 mode=knowledge_base
- [hallucination_risk][熊猫知识] 大熊猫在什么情况下不会发出声音？
  - gold: 当它们在玩或表示友好、没有交配或好斗想法时。
  - rows=20 mode=knowledge_base
