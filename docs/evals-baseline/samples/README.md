# 脱敏样本与基线

## 这里有什么

| 文件 | 说明 |
|---|---|
| `requirement-sample-desensitized.json` | 脱敏需求样本（**合成教学需求**，非真实客户需求） |
| `requirement-sample-desensitized-baseline.json` | 该样本的已发布基线（`case-eval-baseline/2`） |

## 可复跑命令

```bash
# 打分并与已发布基线比对（退化时退出码 1）
python scripts/eval_cases.py \
  --cases docs/evals-baseline/samples/requirement-sample-desensitized.json \
  --baseline docs/evals-baseline/samples/requirement-sample-desensitized-baseline.json \
  --out out/sample-score.csv --out-diff out/sample-diff.csv

# 重新发布基线（仅在确认质量确实提升并评审通过后）
python scripts/eval_cases.py \
  --cases docs/evals-baseline/samples/requirement-sample-desensitized.json \
  --save-baseline docs/evals-baseline/samples/requirement-sample-desensitized-baseline.json
```

基线文件同时固定了**输入指纹**（用例文件 sha256）与**工具版本**：
换一份用例来比时，输出会标 `comparable=false` 并说明「指纹不同（换题了）」——
指标口径可能已变，这时不得把差异当退化判定。旧格式基线（如 `fixture-12.json`）
仍可读，但不含条目级摘要，差异定位不可用。

## 真实需求样本的接入规范

**本目录现有样本是合成教学需求，不是真实客户需求。** 真实需求样本须由项目方提供
**已脱敏且获准使用**的素材后接入，接入方式与本目录现有样本一致：

1. 素材放到本目录，命名 `<需求名>-desensitized.json`；
2. **脱敏要求**：去掉可识别主体（真实名称、账号、手机号、地址、内部系统标识、
   真实域名与路径参数）；业务规则与验收判据保留——脱敏掉判据，样本就没有评测价值；
3. **授权要求**：素材须获准用于评测；若含不可公开内容，另置受限目录并只在受限区运行；
4. 接入后跑上面的命令发布基线，并在本文件的表格里补一行；
5. 与 M9 的检出指标**分账**：结构评分（M10）与实际检出（M9）分别出具，
   两者不可互相替代——结构更完整不等于抓到了更多真缺陷。

## 边界

- 这里全是**机械指标**，只回答「用例结构是否更完整、方法是否更多样、断言是否更密」；
- 不回答「用例是否抓到了真缺陷」——那要靠 M9 的检出评测与真实执行证据；
- 基线相同不代表质量相同；基线持平不等于没问题。
