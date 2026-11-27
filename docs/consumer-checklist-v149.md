# v1.49.0 新输出 → 真实消费者核对表（C-2 / C-4）

> 来源：v1.49.0 方案 §9。纪律：每一格落脚本前先 `grep` 确认消费者真实存在，不得凭类比写"按 X 机制处理"。
> 本表为初版（G0）；G1 集成时逐行复核并回填 grep 证据（2026-11-25 已回填），产物契约行已并入 `references/output-templates.md` §0.4。

| 新输出 | 真实消费者（可 grep） | 旧产物缺该键时显示 | G1 复核结果 |
|---|---|---|---|
| `blocking_reasons[]` / `limitations[]` / `policy_version`（phase-verdict-check/2） | `check-verdict` 调用方（phase_gate.py:1904-1911）、`_pending_verdict_inputs`（B2/B4 消费 `remedy`）、报告「限档原因」（output-templates §0.3） | 回退 /1 口径消费 `reasons` 全集，标注「策略版本缺失，按旧口径呈现」；无渲染器回退（X-05：全仓仅 phase_gate.py:1354 一处生产者、零消费者） | **已复核**：3 真实旧状态副本 `check_verdict` 均输出 /2 且不崩；`render_report_html`/`finding_summary` 对三新字段零消费（rg 实证）；`test_verdict_policy` 8 例锁定 |
| `section=surface_path` CSV 行（attack_coverage） | `output-templates.md` §5.8 第 9 行（披露表，已加九项） | 「不适用（旧攻击覆盖结论无此字段）」，不写 0 | **已复核**：`_emit_csv` 已追加段且 `result.get` 兜底；`test_csv_exports_combo_and_surface_path_sections` 锁定（撤段变红） |
| README 有效性状态页四栏 | 使用者 / 评审者 / 后续改造方案作者 | 未跑项写「已就绪待执行」，不得留空 | **已落**（README A3，E1/E2 如实「已就绪待执行」） |
| `runtime-capabilities.json` | `exploration_runner.py` 队列过滤（`--capabilities` required）；探索清单 `blocked_reason` 依据；报告「能力边界」段；交接包 `runtime_diagnostics`（`_runtime_diagnostics` 派生 `blocked_by_capability`） | 「未探测（旧运行）」，不写「已具备」也不写 0 | **已复核**：D2 CLI `--capabilities` required 实证；D3 派生含 `blocked_by_capability`（实测正确）；`test_exploration_runner`/`test_phase_gate` 双端锁定 |
| `runtime-manifest.json` | `runtime_probe.py`（`--runtime-manifest`）、`exploration_runner.py`（`--runtime-manifest`→预算） | 缺省可用，各通道按 `unprobed` 处理 | **已复核**：两脚本 CLI 参数实证；`test_runtime_probe` 缺省 unprobed 分支锁定 |
| `batch-plan.json` / `budget-consumption.json` | Agent 批次队列、`runtime_diagnostics`（读 `batch_count`/`probes_consumed`/`budget_remaining`）、`autonomy-result.md` 指标源 | 「未运行（旧批次）」；不因缺字段压档 | **已复核**：D2 产出 `batch-plan/1`+`budget-consumption/1`（CLI 全流程测试）；D3 消费 budget 派生（实测 diag 正确）；E2 协议五指标源已对齐 |
| `handoff.runtime_diagnostics` | `_render_handoff_md` 新增只读段（"运行时诊断"）、报告能力边界 | 旧交接包无该字段时该段不渲染，其余字段原样 | **已复核**：D3 两例锁定（派生段渲染 + 缺省 None 不渲染）；`phase-handoff/1` 未升版 |

## 附：登记缺口

- **A2 真实逃逸样本**：`docs/evals-baseline/samples/` 现有样本为合成教学需求；真实需求样本待项目方提供已脱敏且获准使用的素材后接入（规范见 samples/README.md），本轮留缺口，不编造样本。
## v1.55.0 新产物消费核对（G0 初版）

| 新产物/字段 | 生产者 | 消费者 | grep 证据 |
|---|---|---|---|
| `coverage_matrix.dimension_gaps` | coverage_matrix.py | main() 退出码；self-check 开工问 1 | scripts/coverage_matrix.py:build_matrix |
| `oracle_scope`（台账字段） | execution_ledger._attack_fields | attack_coverage._oracle_scope_problem（same_source_unverified） | scripts/attack_coverage.py:_oracle_scope_problem |
| `direction`（SURF 可选字段） | adversarial_data.validate | attack_coverage（direction_single_held 义务） | scripts/attack_coverage.py R3 块 |
| P25 模板 | probe-playbook.md | adversarial_data.PLAYBOOK_TEMPLATES | scripts/adversarial_data.py:46 |
| `--ui-handoff` / entry.ui_handoff | phase_gate complete/revise | REASON_POLICY.ui_handoff_missing；check-verdict limitations；_pending_verdict_inputs | scripts/phase_gate.py |
| `lead_key_unknown` notice | scan_collection_records | complete() 收集进 entry.collection.notices（不阻断） | scripts/phase_gate.py |
| `headline.blocked/uncovered`（/2） | execution_ledger._build_headline | render_report_html.py（分栏渲染；/1 回退标注） | scripts/render_report_html.py:load_headline |
| `baseline.blocked_faces` | execution_ledger init --blocked-faces | _build_headline → headline.blocked | scripts/execution_ledger.py |
| `gate_absent_check.py` | scripts/gate_absent_check.py | self-check.md 交付前问 1 | references/self-check.md |
| `self-check.md` | — | agents/web-acceptance-assurance.md 读取契约 | agents/web-acceptance-assurance.md:90 |

## v1.56.0 新产物消费核对（S 批次）

| 新产物/字段 | 生产者 | 消费者 | grep 证据 |
|---|---|---|---|
| `direction_reason`（SURF） | adversarial_data.validate | —（落账即拒 n/a 无理由） | scripts/adversarial_data.py |
| `persistence`（SURF）→ `consistency_path_absent` | adversarial_data / attack_coverage | analyse gaps | scripts/attack_coverage.py S2 块 |
| `resource_axes`/`axis` → `axis_uncovered` | adversarial_data / execution_ledger / attack_coverage | analyse gaps | scripts/attack_coverage.py S3 块 |
| `probe_inputs`（ADV）+ `adv_id`/`actual_inputs` → `probe_input_mismatch` | adversarial_data / execution_ledger / attack_coverage | analyse gaps | scripts/attack_coverage.py S4 块 |
| `driver`/`driver_justified` → 落账拒 + notice | execution_ledger._attack_fields | attack_coverage `driver_forbidden_observed` | scripts/execution_ledger.py ATTACK_DRIVERS |
| `groups`（--cross-view）→ 组内候选 + `cross_view_unaligned` | copy_consistency | analyse_cross_view | scripts/copy_consistency.py |
| ui_handoff 交叉核验 + `criterion_source` | phase_gate.complete/_ui_handoff_check_text | REASON_POLICY（沿用 ui_handoff_missing） | scripts/phase_gate.py S7/S8 |
| `data_shapes`（baseline）→ headline.uncovered | execution_ledger init --data-shapes | _build_headline | scripts/execution_ledger.py |
| `delivery_ledger_binding.py` | scripts/delivery_ledger_binding.py | self-check 交付前问 13 | references/self-check.md |
| 反馈三态对账节 | output-templates.md | execution-management 对账入口 | references/output-templates.md |
