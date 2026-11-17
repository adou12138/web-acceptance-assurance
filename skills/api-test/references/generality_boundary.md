# 通用性与站点绑定边界

本文件记录 `wjx-api-test` 的**通用范围**与**站点专属范围**，供维护者判断：改一个文件时会不会把通用框架改成站点专用工具。

## 结论速览

```text
通用引擎（契约解析 / 用例生成 / 执行 / 断言 / 覆盖率 / 台账 / 冒烟）  → 通用，无站点绑定
适配器层（rest / graphql / grpc）                                 → 通用
SojumpAdapter                                                    → 站点专属，可选、可插拔
business_flow_executor.py（站点绑定入口，未随本包分发）                                        → 站点专属，独立入口，未被通用引擎引用
业务流程 JSON（assets/ 与知识库 project/business_flows/）           → 站点数据，不是代码
```

**本 skill 本身是通用接口测试框架；站点绑定集中在「业务流程 + 执行器」这一层，且与通用引擎解耦。**

## 一、通用部分（可测任意 HTTP/OpenAPI/REST/RPC 接口）

以下文件不含 action、域名、字段级站点绑定：

| 文件 | 职责 |
|---|---|
| `scripts/flow.py` | 一键流水线（载入契约→判适用性→生成用例→执行→出报告） |
| `scripts/case_runner.py` | 用例执行与断言调度 |
| `scripts/case_generator.py` | 契约驱动用例生成 |
| `scripts/smoke_runner.py` | 批量探活 |
| `scripts/contract_model.py` | 契约模型（ShowDoc / OpenAPI 3.x / Swagger 2.0） |
| `scripts/plan_builder.py` | 计划构建 |
| `adapters/rest.py` | 通用 REST 适配器，地址取自 `env["base_url"]` |
| `adapters/graphql.py` | GraphQL（HTTP 200 + errors 判失败） |
| `adapters/grpc.py` | gRPC（懒加载 grpcio） |

域名、appid、签名、账号等个人参数一律外置到 `personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 或环境变量，不写死在代码里。

公共参数**键名**的数据源是 `assets/exclude_params.default.json`（出厂兜底，随仓库分发；
`DEFAULT_EXCLUDE_PARAM_NAMES` 常量仅作内容一致性对照）。四级回退（替换语义）：
`--exclude-config` 显式 > `exclude_params_<project>.json`（项目级，wjx 与国际版是两个
独立项目、各建一份）> `exclude_params.json`（本地/单项目）> default.json；第一个存在的
文件即基底清单，CLI `--exclude-params` 只追加。有效排除集**全链路联动**：生成侧
（契约副本过滤/base_params 剔除/B-MISS 跳过/check_key 分母）与执行侧（`build_payload`
只注入 `envelope_keys = 排除集 ∩（参数表 ∪ 请求示例）` 内的键）同源——内部接口契约
不声明信封键即自动不注入，零开关按接口区分。动态签名项目经 `--payload-hook` 适配
（`compute_envelope` 按最终业务参数现算信封值）。装载与联动由 `tests/test_exclude_config.py`
/`test_envelope_keys.py`/`test_envelope_injection.py`/`test_payload_hook.py`/`test_auth_gating.py`
锁定；契约示例块解析、`- #`/锚点标题归属、子对象表识别、同名字段合并与基线退化留痕由
`tests/test_realworld_contract.py` 锁定；4xx 报文解析、分页判定、归因落点、前置匹配边界与
`base_url + endpoint` 拼接由 `tests/test_judgement_fixes.py` 锁定。被排除参数不参与任何用例生成、固定值/默认值种进每条请求
（值优先级与报错口径见 `mechanisms.md` §M7）。
`common_params.NAMED_PAYLOAD_KEYS` 是报文拼装具名键的同一来源，smoke_runner 透传分流与
SojumpAdapter 防线都从它派生，漂移由 `tests/test_exclude_params.py` 锁定。

`scripts/api_test_runner.py` 内含 `1000107` / `1000108`，但源码已明确标注为**示例**：

```text
本文件是通用模板：下面所有 Sojump / 1000107 / 1000108 等仅作可运行示例
在 main() 把 EXAMPLE 用例替换为你自己的接口用例
```

换被测系统只需替换三样：契约来源、环境配置、用例中的 action 与参数；框架、维度、断言方法、台账格式完全复用。

## 二、站点专属部分

### 2.1 SojumpAdapter（可选、可插拔）

`adapters/sojump.py（站点适配器，未随本包分发；自装方式见 adapter_contract.md）` 处理问卷星公共参数（`action` / `encode` / `appid` / `sign`）与写操作门禁。
它不硬编码域名与 action，属于与 rest / graphql / grpc 平级的第 4 个适配器；不使用即不生效。
`flow.py` 默认注入它以便写操作门禁生效，这是一处**有意的保守默认**。

### 2.2 business_flow_executor.py（站点绑定入口，未随本包分发）（站点专属，独立入口）

这是问卷星专用的最小闭环执行器，硬编码了站点语义：

```text
CREATE   = 1000101   问卷创建
SETTINGS = 1000103   问卷设置
PUBLISH  = 1000102   修改问卷状态（state=1 发布 / state=3 标记删除）
SUBMIT   = 1001001   答卷提交
QUERY    = 1001002   答卷查询
REPORT   = 1001101   默认报告
READBACK = 1000001   获取问卷内容
FLOW_ID       = survey_lifecycle_minimal_loop
RESOURCE_TYPE = survey
字段名：vid / jid / atype / status
删除语义：标记删除并进入回收站，非物理删除
```

它**不能用于其他系统**。

关键约束（保证不污染通用框架）：

- 未被通用引擎导入。`flow.py` / `case_runner.py` / `case_generator.py` / `smoke_runner.py` 均不引用它；
- 有独立 CLI 入口，与主链路并行，不参与契约驱动与冒烟路径；
- 可整体移除而不影响通用功能。

执行前的门禁（缺任一项都在发出请求前拒绝）：

- 审批绑定：流程必须已审批且哈希一致，模板动作与目标域名、环境类型必须与运行时一致；
- 动作白名单：步骤动作必须在允许集合内，且**业务参数里的 `action` 不得与被审批的步骤动作不一致**——
  报文里的 action 由信封注入，业务参数后传会覆盖它，不一致即拒绝（否则白名单形同虚设）；
- 凭据：`appid` 必填、`sign` 必填且不得是占位值（缺任一项都会被服务端判失败，报错却指向服务端）；
- 响应形态：闭环要读 `data.vid` / `data.jid` 做引用解析与清理回读，响应必须是 JSON；
- 参数位置：闭环地址固定（`default.aspx`）没有路径占位符，参数声明成 path 位置会被静默丢弃，
  执行前直接拒绝并指到配置上。

### 2.3 业务流程 JSON（数据，非代码）

以下文件描述问卷、AI 主页、多用户、用户体系、通讯录的业务流程，属于**数据资产**：

```text
assets/business_flow.example.json                      通用示例（占位 action）
assets/business_flow.survey_lifecycle.candidate.json（站点业务候选，未随本包分发）   问卷全生命周期与答卷管理
assets/business_flow.ai_homepage.candidate.json（站点业务候选，未随本包分发）        AI 主页创建/更新/暂停/回收站
assets/business_flow.multi_user_management.candidate.json（站点业务候选，未随本包分发）  多用户子账号生命周期
assets/business_flow.user_system.candidate.json（站点业务候选，未随本包分发）        用户体系参与者与问卷绑定
assets/business_flow.contacts.candidate.json（站点业务候选，未随本包分发）           通讯录成员/部门/标签/管理员
```

它们亦可落入外部知识库 `user_knowledge/project/business_flows/*.json` 供检索（剥离适配：本包内不内置知识库，需外部提供 `--kb` 根路径）。
移除它们只会让业务流程相关功能不可用，不影响通用接口测试能力。

## 三、凭据与站点信息卫生

**结论：`assets/personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 无需处理。** 经 GitHub 分享时不会被带出：

| 检查 | 结果 |
|---|---|
| 是否被 git 跟踪 | 否 |
| `.gitignore` 是否覆盖 | 是（`personal_config.json（真实配置不随包分发，用 personal_config.example.json）` / `personal_config_*.json`） |
| `git log --all` 是否有历史残留 | 无 |

真正需要守住的是**会被 git 跟踪、随仓库分享的 Python 源码**——它不靠 `.gitignore` 保护。
`tests/test_asset_integrity.py` 已加两条护栏（判据本身不携带明文，避免"护栏文件把要拦的东西写进仓库"）：

- `test_python_sources_have_no_real_environment_hosts` — `scripts/` 与 `tests/` 不得出现已知真实内部主机；denylist 只存主机名的 **SHA256 前 12 位指纹**（`_REAL_HOST_FINGERPRINTS`），扫描时对源码里提取到的 host 逐个求哈希比对
- `test_python_sources_have_no_real_credentials` — 不得把 3~8 位纯数字直接赋给 `appid`，不得内联 `WJX_API_KEY` 赋值。**语义判据，不需要知道真实值**，新增环境也不会因忘补清单而失效

**新增真实环境时**，先算 `_fingerprint(host)` 再把指纹补进 `_REAL_HOST_FINGERPRINTS`——
**不要把主机名明文写回该文件**，它自己也会入库。源码里一律用保留域名
（`example.test` / `x.test`）与占位符（`<APPID>` / `<SIGN>`）承载。

已清理的历史欠账：

| 位置 | 处理 |
|---|---|
| `tests/test_business_flow_executor.py（站点绑定入口，未随本包分发）` | 改用 `example.test` 与 `<APPID>`/`<SIGN>` |
| `tests/test_trace_acceptance.py` · `tests/test_trace_p0_fixes.py` | 内联的真实 appid 改为 `<APPID>` |
| `scripts/business_flow_executor.py（站点绑定入口，未随本包分发）` docstring | 注释中的真实主机名改为通用示例 |
| `scripts/flow.py` · `scripts/case_generator.py` docstring | 示例命令含 `--action 1000107`，仅帮助文本，保留 |

| 位置 | 情况 | 影响 |
|---|---|---|
| `scripts/flow.py` 文档串 | 示例命令含 `--action 1000107` | 仅帮助文本，不影响逻辑 |
| `scripts/case_generator.py` 文档串 | 示例命令含 `--action 1000107` | 仅帮助文本，不影响逻辑 |
| `assets/personal_config.json（真实配置不随包分发，用 personal_config.example.json）` | 仍存有真实域名与 appid | **无需处理**：经 GitHub 分享时不会被带出——`.gitignore` 覆盖（`personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 与 `personal_config_*.json`）、未被 git 跟踪、`git log --all` 无历史残留。运行时不读取（主路径读 `~/.workbuddy/wjx-api-test/`） |
| `tests/test_business_flow_executor.py（站点绑定入口，未随本包分发）` | ~~硬编码真实测试域名与 appid~~ | **已修复**：改用保留域名 `example.test` 与占位符 `<APPID>`/`<SIGN>` |
| `tests/test_trace_acceptance.py` · `tests/test_trace_p0_fixes.py` | ~~内联真实 appid 3585~~ | **已修复**：改用 `<APPID>` 占位符（这两处是护栏测试跑全量后暴露的既有欠账） |
| `scripts/business_flow_executor.py（站点绑定入口，未随本包分发）` docstring | ~~注释含真实主机名~~ | **已修复**：改为通用示例主机名 |

## 四、如需恢复为纯通用 skill

按顺序执行：

1. 删除 `scripts/business_flow.py`、`scripts/business_flow_executor.py（站点绑定入口，未随本包分发）`；
2. 删除 `tests/test_business_flow.py`、`tests/test_business_flow_executor.py（站点绑定入口，未随本包分发）`；
3. 删除 `assets/business_flow*.json`、`schemas/business_flow.schema.json`、`references/business_flow.md`（含本文件的相关引用）；
4. 从 `SKILL.md` / `README.md` 移除业务流程与执行器条目。

`assets/personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 不需要删除（第 2.3 节与第三节已说明其不随仓库分享）；
`assets/personal_config.example.json` 作为占位模板应保留。

`adapters/sojump.py（站点适配器，未随本包分发；自装方式见 adapter_contract.md）` 可保留：它通用性足够（不绑域名与 action），且写操作门禁对任何 Sojump 接口都适用。

## 五、维护纪律

- 新增通用能力 → 只放 `scripts/` 主打脚本与 `adapters/`，**不得**引入站点字段名或 action 常量；
- 新增站点能力 → 独立成 `*_executor.py` 之类脚本或纯数据文件，**不得**被通用引擎反向依赖；
- 判断一条改动是否越界：**删掉该文件后，契约驱动 / 深度流程 / 冒烟三条通用路径是否仍可用**。仍可用即未越界。