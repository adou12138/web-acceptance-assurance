# 知识库驱动业务流程（不接入 CLI）

`business_flow.py` 将知识库中的业务流程定义转换为**只读审阅计划**。当前版本不发送请求、不调用 `wjx-cli`，也不把 CLI 参考流程当成已确认事实。

## 流程知识文件

约定知识库可增加：

```text
<KB_ROOT>/project/business_flows/*.json
```

每个流程文件包含：

- `resource_type`：业务资源类型，例如 `survey`
- `sources`：来源、连续原文证据、置信度
- `steps`：按业务顺序排列的 action
- `requires`：对前置步骤产出的资源引用，如 `${create.survey_id}`
- `produces`：从响应提取的字段，如 `data.survey_id`
- `preconditions`：调用前状态要求
- `verifies`：调用后的回读/状态断言
- `cleanup`：测试资源的清理 action；问卷删除语义必须明确区分“标记删除并进入回收站”和物理删除
- `confirmed_rules`：用户或知识库已确认的业务规则，如创建 action、问卷类型、状态语义、AI 主页边界、模块隔离和共享子账号
- `module_scope`：模块边界；声明流程所属模块、是否允许跨模块操作以及共享账号的边界
- `approval`：正式执行审批信息
- `status`：步骤状态；可标记为 `deferred` 表示暂缓纳入流程
- `optional`：可选步骤；缺少 action 且标记为可选时只进入 `deferred`，不会阻塞其他步骤
- `reason`：步骤暂缓或不可执行的原因

示例：

```powershell
python scripts/business_flow.py `
  --flow assets/business_flow.example.json `
  --environment '{"type":"test","write_enabled":true}'
```

该命令只输出计划。示例文件中的 action 是占位符，不能直接执行。

## 从知识库查流程

```powershell
python scripts/business_flow.py `
  --kb <KB_ROOT> `
  --query survey
```

检索到多个候选时只输出候选清单，不自动选择。检索不到时也不猜测流程。暂时没有 action 的步骤应标记 `status: deferred`、`optional: true`，并说明 `reason`；计划会将其标记为 `deferred`，不会阻塞其他已确认步骤。

流程目录允许同时存放 `manifest.json` 等索引文件；查找时按内容特征识别流程（顶层含 `flow_id` 且 `steps` 为数组），索引文件和解析失败的 JSON 不会被当成流程候选。

## 审批流程

流程事实必须先由人工确认。审批操作会另存新文件，不覆盖来源文件：

```powershell
python scripts/business_flow.py `
  --flow <candidate.json> `
  --approve-by <approver> `
  --approved-flow <new-approved-flow.json> `
  --environment '{"type":"test","write_enabled":true}'
```

审批哈希绑定流程内容。流程被修改后必须重新审批。

## 执行器安全约束

`business_flow_executor.py（站点绑定入口，未随本包分发）` 执行普通问卷最小闭环前，必须同时满足：

```text
提供 --approval 已审批流程文件
approval.approved == true 且 flow_hash 与流程内容一致
审批文件含 execution_template；执行时所有请求参数取自该模板，不在执行器内硬编码
审批文件 target.host / target.environment_type 与实际配置和环境一致
environment.type == "test" 且 environment.write_enabled == true
请求地址只能由配置 base_url 推导（无 --endpoint 覆盖）
```

### 报文本体受审批保护

实际发送的每一步参数来自审批文件中的 `execution_template`。改动模板中的问卷类型、
题目内容、答卷内容等任意字段都会使审批哈希失效，必须重新审批后才能执行。
执行器不提供绕过入口：没有 `execution_template` 的旧审批文件会被直接拒绝。

生成待审批文件：

```powershell
python scripts/business_flow_executor.py（站点绑定入口，未随本包分发） --config <cfg> --env <name> --print-candidate <candidate.json>
python scripts/business_flow.py --flow <candidate.json> --approve-by <approver> --approved-flow <approved.json> --environment '{"type":"test","write_enabled":true}'
```

### 请求链全局不被污染

执行器复用 `api_test_runner` 的请求链，但只在请求期间临时改写其 `BASE_URL` /
`SSL_CTX` / `TIMEOUT`，退出时（含异常路径）恢复原值，避免同进程后续请求发往错误地址。

### 清理可补跑

资源创建成功后立即向持久化清单（默认 `<out>.cleanup.jsonl`）写入 `created` 记录，
清理并回读确认后写入 `cleaned` 记录。进程被杀或清理失败时，用同一份审批文件补跑：

```powershell
python scripts/business_flow_executor.py（站点绑定入口，未随本包分发） --config <cfg> --cleanup-only --approval <approved.json> --manifest <out>.cleanup.jsonl --out <cleanup-result.json>
```

无待清理项时返回 `NOTHING_TO_DO`。

### 其它

TLS 默认开启证书校验；仅显式设置 `WJX_INSECURE_TLS=1` 时才允许对自签/内网测试环境关闭。

清理失败或回读状态不是已删除时，最终状态降级为 `PARTIAL`，不会报告 `PASS`。

真实个人配置应放在 skill 包外（`~/.workbuddy/wjx-api-test/personal_config.json（真实配置不随包分发，用 personal_config.example.json）`），不要放在 skill 的 `assets/` 目录。

## 与 wjx-api-test 的边界

- 知识库：提供契约、流程、状态、前置条件和证据。
- `business_flow.py`：校验流程并生成审阅计划，不发请求。
- `wjx-api-test`：负责普通 action 的请求、断言、覆盖率、台账和运行产物；当前候选流程覆盖问卷、子账号、用户体系和通讯录。
- `wjx-cli`：当前不接入；其流程只可作为候选设计参考。

所有写操作在正式执行前都必须同时满足：

```text
authorization.approved == true
且
environment.type == "test"
且
environment.write_enabled == true
```
