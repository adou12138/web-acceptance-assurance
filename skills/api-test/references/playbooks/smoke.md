# 冒烟批量探活 — 编排卡

> 本卡只做编排：步骤顺序、命令、判定档位、产物落点与出口。判定与清理的完整口径见 `references/smoke_testing.md` 与 `workflow_steps.md`「冒烟模式的完整口径」。
> 判路来源：`SKILL.md` §0 行 4。红线编号（R1–R8）定义见 `SKILL.md` §1。

## 0. 进入条件

用户要「把一批 / 全部接口跑一遍，看哪些通」——广度优先，不套七步深度流程、不做深度断言；冒烟圈出的 FAIL 再转深度定位根因，两者不互相替代。

## 1. 备三份资产

- `--config` 环境配置（地址模板 / 公共参数 / 账号 / QPS 档位 / 判定关键词）。
- `--cases` 案例清单（按**依赖顺序**排列）。
- `--hooks` 业务适配层（可选，只放无法用数据表达的动作：环境准备、资源登记、收尾清理）。

问卷星 48 接口可直接复用 `assets/smoke_config_wjx.json（站点专属配置，未随本包分发；通用配置用 smoke_config.example.json）` · `assets/smoke_cases_wjx.json（站点专属资产，未随本包分发）` · `assets/smoke_hooks_wjx.py（站点专属钩子，未随本包分发）`。

## 2. 执行（命令已经 `scripts/smoke_runner.py` argparse 核对）

1. 先空跑：`python scripts/smoke_runner.py --config <cfg> --cases <cases> --dry-run`（只渲染参数不发调用）。
2. 单接口试连通：加 `--only <action>`。
3. 全量：`python scripts/smoke_runner.py --config <cfg> --cases <cases> [--hooks <hooks>] --out-dir reports`。

开关：`--env <name>` / `--region cn|intl`（先筛 region 再选环境；环境不存在报错 exit 2，R3）· `--timeout <秒>`（覆盖配置超时，只影响等待不改判定）· `--no-redact`（仅内部调试，外发禁止；对应 `WJX_NO_REDACT`）。

## 3. 判定档位（硬口径 + 指针）

判定档位 `PASS / FAIL / N/A（环境限制）/ ERROR（网络异常）`。硬口径：**服务端 5xx 一律判 FAIL**（R5）；HTTP 200 内的服务端内部错误优先于 `na_keywords` 判定。结果去向：FAIL / ERROR 进 16 列台账（R8，统一归因 + 脱敏证据包）；N/A 留痕不计缺陷——**台账行数 ≠ 缺陷数**。逐条判定规则见 `references/smoke_testing.md`。

## 4. 产物落点与反哺

- `--out-dir` 下产出：结果 CSV（请求 + 响应报文）、问题 CSV、report JSON、`smoke_summary_<run_id>.md`。
- 延迟分布只作离群提示：不参与判定、不进台账、不改变 J 维度适用性。
- `--feedback` 回流缺陷模式候选（纪律见 `references/pattern_candidates.md`）。

## 5. 清理与出口

- 收尾清理**默认不删数据**（`assets/smoke_hooks_wjx.py（站点专属钩子，未随本包分发）` 判 `teardown_enabled`）；案例清单里显式的删除类案例仍照常执行，**删除前必须先取得授权**（R2 / `SKILL.md` §2）。清理细则与开启条件见 `workflow_steps.md`「冒烟模式的完整口径」。
- 出口：FAIL 项转 `references/playbooks/seven_step.md` 深挖根因。