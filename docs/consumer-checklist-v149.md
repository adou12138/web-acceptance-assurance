# v1.49.0 新输出 → 真实消费者核对表（C-2 / C-4）

> 来源：v1.49.0 方案 §9。纪律：每一格落脚本前先 `grep` 确认消费者真实存在，不得凭类比写"按 X 机制处理"。
> 本表为初版（G0）；G1 集成时逐行复核并回填 grep 证据，产物契约行并入 `references/output-templates.md` §0.4。

| 新输出 | 真实消费者（可 grep） | 旧产物缺该键时显示 | G1 复核结果 |
|---|---|---|---|
| `blocking_reasons[]` / `limitations[]` / `policy_version`（phase-verdict-check/2） | `check-verdict` 调用方、`_pending_verdict_inputs`、报告「限档原因」 | 回退 /1 口径消费 `reasons` 全集，标注「策略版本缺失，按旧口径呈现」；无渲染器回退（X-05：全仓仅 phase_gate.py:1354 一处生产者、零消费者） | 待复核 |
| `section=surface_path` CSV 行（attack_coverage） | `output-templates.md` §5.8 第 9 行（披露表） | 「不适用（旧攻击覆盖结论无此字段）」，不写 0 | 待复核 |
| README 有效性状态页四栏 | 使用者 / 评审者 / 后续改造方案作者 | 未跑项写「已就绪待执行」，不得留空 | 已落（README A3） |
| `runtime-capabilities.json` | `exploration_runner.py` 队列过滤；探索清单 `blocked_reason` 依据；报告「能力边界」段；交接包 `runtime_diagnostics` | 「未探测（旧运行）」，不写「已具备」也不写 0 | 待复核 |
| `runtime-manifest.json` | `runtime_probe.py`、`exploration_runner.py`（缺省时只读环境变量与已知路径） | 缺省可用，各通道按 `unprobed` 处理 | 待复核 |
| `batch-plan.json` / `budget-consumption.json` | Agent 批次队列、`runtime_diagnostics`、`autonomy-result.md` | 「未运行（旧批次）」；不因缺字段压档 | 待复核 |
| `handoff.runtime_diagnostics` | `_render_handoff_md` 新增只读段、报告能力边界 | 旧交接包无该字段时该段不渲染，其余字段原样 | 待复核 |

## 附：登记缺口

- **A2 真实逃逸样本**：`docs/evals-baseline/samples/` 现有样本为合成教学需求；真实需求样本待项目方提供已脱敏且获准使用的素材后接入（规范见 samples/README.md），本轮留缺口，不编造样本。