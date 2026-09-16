# 熊猫谣言逐条辟谣测试报告

- 文档：`docs/熊猫谣言/大熊猫辟谣汇总_仅辟谣.md`
- 时间：2026-09-14 11:03:07
- 结果：**0/8 通过**

## 汇总

| ID | 结果 | 命中率 | rows | verdict | 问题 |
|---:|:---:|---:|---:|:---|:---|
| 12 | FAIL | 0% | 19 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 圈养大熊猫被电击取精，属于残忍虐待吗？ |
| 13 | FAIL | 0% | 5 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 大熊猫基地强行分离母幼，会导致母熊猫抑郁、幼崽死亡吗？ |
| 19 | FAIL | 0% | 5 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 大熊猫在野外无法生存，放归全部失败了吗？ |
| 21 | FAIL | 0% | 3 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 大熊猫是濒危动物，数量极少，即将灭绝吗？ |
| 25 | FAIL | 0% | 4 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 大熊猫每天要睡20小时，非常懒吗？ |
| 28 | FAIL | 0% | 12 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 大熊猫只生活在四川吗？ |
| 32 | FAIL | 0% | 20 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 旅美大熊猫宝力在美国遭虐待，身体有洞、频繁抽搐吗？ |
| 37 | FAIL | 0% | 17 | `fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)` | 极端动保团伙网暴熊猫专家和饲养员，真实情况是什么？ |

## 失败明细

### #12 圈养大熊猫被“电击取精”，属于残忍虐待

- 问题：圈养大熊猫被电击取精，属于残忍虐待吗？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：全身麻醉, 电刺激, 兽医
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>

### #13 大熊猫基地强行分离母幼，导致母熊猫抑郁、幼崽死亡

- 问题：大熊猫基地强行分离母幼，会导致母熊猫抑郁、幼崽死亡吗？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：双胞胎, 人工辅助育幼, 90
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>

### #19 大熊猫在野外无法生存，放归全部失败

- 问题：大熊猫在野外无法生存，放归全部失败了吗？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：母兽带崽, 淘淘, 放归, 11
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>

### #21 大熊猫是濒危动物，数量极少，即将灭绝

- 问题：大熊猫是濒危动物，数量极少，即将灭绝吗？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：IUCN, 易危, 2016, 1800
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>

### #25 大熊猫每天要睡20小时，非常懒

- 问题：大熊猫每天要睡20小时，非常懒吗？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：10-12, 睡眠, 进食
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>

### #28 大熊猫只生活在四川

- 问题：大熊猫只生活在四川吗？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：陕西, 甘肃, 秦岭, 四川
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>

### #32 大熊猫“宝力”在美国遭虐待，身体有洞、频繁抽搐

- 问题：旅美大熊猫宝力在美国遭虐待，身体有洞、频繁抽搐吗？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：寄生虫, 竹子残渣, 白线虫, 宝力
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>

### #37 极端“动保团伙”网暴熊猫专家和饲养员

- 问题：极端动保团伙网暴熊猫专家和饲养员，真实情况是什么？
- verdict：`fail_rule:no_evidence_answer,low_token_hit(0.00<0.34)`
- token 命中：(无)
- token 未命中：编造, 短视频, 直播, 非法获利
- LLM：ok=None, score=None, reason=
- error：(无)

<details><summary>系统回答</summary>

```
(空)
```

</details>


## 用例清单

- **#12** Q: 圈养大熊猫被电击取精，属于残忍虐待吗？
- **#13** Q: 大熊猫基地强行分离母幼，会导致母熊猫抑郁、幼崽死亡吗？
- **#19** Q: 大熊猫在野外无法生存，放归全部失败了吗？
- **#21** Q: 大熊猫是濒危动物，数量极少，即将灭绝吗？
- **#25** Q: 大熊猫每天要睡20小时，非常懒吗？
- **#28** Q: 大熊猫只生活在四川吗？
- **#32** Q: 旅美大熊猫宝力在美国遭虐待，身体有洞、频繁抽搐吗？
- **#37** Q: 极端动保团伙网暴熊猫专家和饲养员，真实情况是什么？
