# SYNC-NOTES — 抽取副本人工对照清单

> 只服务开发期对照，**运行时完全不读**（集成方案 v2.4 §9）。
> 冻结源：qa-req2testcase-generator **4.17.0**（以 SKILL.md frontmatter 为准），frozen_at **2026-11-16T18:12:05+08:00**（policy.json meta）。
> 已知风险并接受：源 skill 升级后副本可能漂移；漂移只影响判据新旧，不影响本包运行（运行时不读取 qa 的任何路径、版本或配置）。

## 对照清单

| # | 对照对象（qa 侧） | 副本（本包） | 比什么 | 状态 |
|---|---|---|---|---|
| 1 | `tools/export_excel.py` `COLUMNS` | `skills/case-generator/references/design-spec.md` §1 与 `config/policy.json` views.columns | 19 列名与顺序逐字 | 已对照（4.17.0；「需求」列取 project 值的口径一并保留） |
| 2 | `config/test_design_capabilities.json` | `config/policy.json` structure/authority | 枚举、budget、forbidden_patterns、coverage_layers | 已对照（阶段 0 §7A 对账：priority P0-P2、attributes 校验器七类、layers 五类、词表以 case_data.py 为权威） |
| 3 | `SKILL.md` 元规则 9/10/12 + orchestrator 阈值段 | `config/policy.json` thresholds | 冒烟三层/P0/去重/小样本/分批阈值 | 已对照（定稿值见 policy.json，各带 role） |
| 4 | `tools/gate_checker.py` G1~G7、`tools/p5_validate.py` V05~V13 | `skills/case-generator/scripts/gate_checks.py` | 判据与白名单 | 已对照（用例化改写：G1 禁止模式+确认拦截、G2 九种模糊表述+具体性信号、G3 A≥3 步/B 不标冒烟、G4 冒烟来源、G5 Jaccard+占位并集、G6 合并模式+豁免、G7 追溯；V01-V04/V10 明确不搬，见 policy.gates.not_migrated。**G1 规则集已抽象为 `policy.gate_rules.g1` 可配置**（方案 §12，2026-11-17 落地：脚本内置默认与 policy 段一致，policy 段存在时以 policy 为准；确认分级豁免简化为纯「确认」步拦截，恢复口径见 policy.gate_rules.g1.confirm.note） |
| 5 | `prompts/P0.5_prd_quality_review.md` 八维度 | `skills/requirement-review/references/prd-dimensions.md` | 维度清单与 blocking 判定 | 已对照（只取维度与判定，不取分数/权重/等级） |
| 6 | `prompts/schemas/*.json`（16）、`knowledge/**`（4） | `skills/case-generator/references/schemas/`、`references/methodology/` | 复制件完整性 | 已对照（一次性复制，参考用） |

## 人工对照方法

1. 读取 qa 侧 `SKILL.md` frontmatter 确认当前版本，逐段读取上表左列文件。
2. 19 列：diff `export_excel.py:COLUMNS` 的列名与顺序 ↔ `policy.json` views.columns 的 header。
3. 阈值：diff gate_checker/orchestrator 数值 ↔ `policy.json` thresholds（含 role 标注；冲突时回源裁定后更新 policy 并记录）。
4. 词表：diff `case_data.py` 常量（NONCOMMITTAL/PLACEHOLDERS/METHOD_CLAIMS）↔ `policy.json` authority.mirrored_constants（校验器为权威，policy 只存副本）。
5. 对照后更新 `policy.json` 的 `meta.source_version` / `meta.frozen_at` 与本清单状态列。

## 本次抽取未搬运清单（有意不搬，防误判遗漏）

orchestrator 状态机、6 次「继续」确认交互、gate/HMAC 五元哈希绑定、主会话逐批 P6 调度、图片抽取/OCR/视觉理解、L5 知识库（459M）、`p6_templates.py`、`p6_guide.py`、评分等级与分数放行。理由见集成方案 v2.4 §2「不搬清单」与 §12（`render_xlsx` 不得整段复制 `export_excel.py`：HMAC 硬校验与 `p6_output` 结构假设，质检一律留给 gate_checks.py）。
## 审核修复记录（2026-11-17，v1.10.1）

| 审核编号 | 动作 | 落点 |
|---|---|---|
| D4 | review-summary 恢复方案 §5 固定六节（删除第七节「已确认规则备查」，confirmed_rules 数据仍在 JSON 事实源中） | `skills/requirement-review/scripts/render_review.py` |
| D3 | G1 判据抽象为 `policy.gate_rules.g1` 可配置规则集，脚本内置默认与 policy 段一致；确认分级豁免简化口径写入 policy note | `config/policy.json` + `scripts/gate_checks.py` |
| D1（机制部分） | 门禁耗时 `elapsed_ms` 由 gate_checks 报告自动采集并汇入 aggregate-report.json；`calibration.status` 改 `mechanism_ready`（token 由会话侧登记，数值待真实样本回填） | `scripts/gate_checks.py` + `config/policy.json` |
| D5 | batches.json 的超集字段（unit_id/feature_ids/task_id 等）经审核确认保留，写入记录 | 本表（无代码改动） |
| R4 | requirement-review SKILL.md 内联 ID 规则最小集，消除对 case-generator 文档级强引用 | `skills/requirement-review/SKILL.md` |
| D7（方案侧） | 集成方案 v2.4 §1 表格「40」笔误按 §8/§13C 定稿修正为 30 | `outputs/砺衡_能力集成方案_包内Skills_v2.4.md` |
| D2（结构层） | 回退验证：临时删除 plugin.json skills 数组后 `validate_expert.py` 通过（专家原能力结构完好），恢复后再次通过 | `.codebuddy-plugin/plugin.json` |
| 验收补充 | 19 列 required 14 列非空率 100% 显式断言；跨视图 case_id 一致正式测试；门禁报告 elapsed_ms 断言；摘要六节结构断言 | `tests/test_render_xlsx.py`、`tests/test_gate_checks.py`、`tests/test_render_review.py` |

D1 数值回填（单批 token 与耗时统计）、五场景路由的剩余 3 条实操、G5-W 告警线校准：留待首次真实需求跑批时执行，不属本次修复范围。
## api-test 包内 skill 对照表（v1.12.0，2026-11-17）

- **剥离源**：wjx-api-test **5.0.2**（2026-11-13 冻结）。源 skill **零改动**——对照验证以源目录前后快照证明零实质写入（对照用独立 workdir，产物不落源目录）。
- **运行时独立**：包内无外部路径依赖；KB 契约由会话从外部知识库取回后经 `--contract/--kb` 喂入，本包不内置知识库路径。**复审补全（v1.12.1）**：KB 回退链指向外部兄弟 skill 的引用共 13 处（references 6 + 脚本 docstring/标签 7）已全部补"剥离适配"标注（行为不改：回退候选不存在时报错属预期）；`contract.md` 命令表补 M13 双通道指针行（判路 7 导航断链修复，源 skill 既有问题）。
- **不搬清单**：`adapters/sojump.py`（站点适配）、`scripts/business_flow_executor.py`（站点绑定）、`smoke_*_wjx`/`business_flow.*.candidate`（站点资产）、`personal_config.json`（真实配置）、`tests/`（20531 行，自建 14 例替代）、`docs/reviews`、`backup/`、运行产物。
- **行为改造（3 处，剥离适配）**：`flow.py` 默认 adapter `sojump`→`rest`；sojump 分支 ImportError 友好提示（自装可用）；写操作门禁通用化（适配器声明 `is_write_allowed` 即生效，随包适配器均未实现，写保护由 R2 授权与环境配置承担）。
- **已知局限**：信封键仍为问卷星口径（`common_params.py` 硬编码，其它项目接入需按 `envelope_keys` 扩展）；`WJX_*` 命名空间保留兼容。
- **剥离依据**：import 闭包（AST 遍历 4 入口）41 脚本 + 判路/文档配套 4 脚本；引用面改写 59 处（references 中被排除资产的裸引用清零）。
- **双维护**：源 wjx 升级时按本表人工对照；冻结版本 5.0.2。- **离线示例与链路补全（v1.12.2）**：新增 `assets/smoke_cases.example.json`（通用 2 例，含提取/依赖链 skip_if）——此前 `smoke_cases_wjx` 被排除后判路 4 的 dry-run 无示例可走；`contract.md` 的 M13 行补真实用法（`dependency_scan --contract/--out`、`precondition_scan --prep/--confirm`，均已实跑验证）；SKILL.md 判路 3 注明“未配置 KB 时 `--action` 以校验错误 exit 2 属预期”。自建测试增至 17 例（新增冒烟 dry-run / 依赖扫描 / 回读错误路径三条链路）。