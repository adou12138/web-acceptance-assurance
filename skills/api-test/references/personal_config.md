# 个人参数配置与多环境

本 skill **不写死任何账号 / 子账号 / appid / 域名 / corpid**。这些"个人参数"全部外置，
团队分享 skill 文件夹时不会泄露你的环境信息。包内只保留 `*.example.json` 占位模板。

## 1. 两种配置方式（运行时合并，环境变量优先于配置文件）

1. **配置文件（推荐，便于团队分发）**
   - 复制 `assets/personal_config.example.json` 为 `personal_config.json（真实配置不随包分发，用 personal_config.example.json）`，填入你的真实值；
   - 放到 **skill 包外** 的 `~/.workbuddy/wjx-api-test/personal_config.json（真实配置不随包分发，用 personal_config.example.json）`，
     或用环境变量 `WJX_CONFIG=/path/to/your/personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 指定路径；
   - 该真实文件**不要入库 / 不要随 skill 分享**。
2. **环境变量（轻量，适合 CI / 临时切换）**

   | 变量 | 含义 |
   |---|---|
   | `WJX_BASE_URL` | 测试地址（域名+路径），取自接口文档『接口地址』 |
   | `WJX_APPID` | 当前环境统一 appid |
   | `WJX_MAIN` / `WJX_SUB` | 主账号 / 子账号 |
   | `WJX_CORPID` | 企业 corpid |
   | `WJX_CROSS_APPID` | 跨企业异常场景用 appid（仅在显式开启跨环境时使用） |
   | `WJX_SIGN` | 签名值（约定不校验时仍建议配置，便于落台账） |
   | `WJX_INSECURE_TLS` | 设为 `1` 才关闭 TLS 证书校验（**默认开启校验**）；仅内网 / 自签证书调试用，启动即打印醒目警告。深度、冒烟、业务流三处共用 |
   | `WJX_SYSID` / `WJX_ENV` / `WJX_REGION` / `WJX_CONFIG` / `WJX_CROSS_ENV` / `WJX_CROSS_CONFIG` / `WJX_CROSS_REGION` | 见下方各节 |

**加载规则**（集中在 `scripts/personal_config.py` 的 `load_personal_config()`，深度测试与冒烟测试
共用同一份逻辑，保证「个人参数单一来源」）：环境变量 > `personal_config.json（真实配置不随包分发，用 personal_config.example.json）` > 报错。
任何必需参数（地址 / appid / 主账号）缺失时，运行器在启动即明确报错并给出配置指引，
**不会带任何默认个人值跑测试**。

### TLS 校验：安全默认值优先于配置

`WJX_INSECURE_TLS=1` 是**唯一**能关闭证书校验的入口。配置里的 `tls_verify` 字段
**不作为生效项**（保留仅为兼容旧文件）：写成 `false` 不会关闭校验，写成 `true` 也不会额外开启。
这是刻意的——配置文件里一个 `false` 就能悄悄形成不安全请求，代价（中间人可读全部报文与凭据）
远大于便利，所以把开关收在环境变量上，让「关闭校验」成为一次显式动作并在启动时出声。
三处入口（`api_test_runner` / `smoke_runner` / `business_flow_executor`）行为一致。

### `base_url` 只写域名/根，不写接口路径

写成 `https://env.example.com`，**不要**写 `https://env.example.com/openapi/default.aspx`。
接口路径由契约的「接口地址」提供（`endpoint`），运行时拼成 `base_url + endpoint` 并打印实际地址。

把路径写进 `base_url` 的后果是**一个环境只能测一个接口**——同一环境的不同 action 走不同路径，
换接口就得改配置，而改了配置又影响别的接口。契约给的是生产完整地址时，**只取路径、host 用配置的**。

旧写法（`base_url` 自带路径）仍可用：此时 `base_url` 按原样作为请求地址，**契约的接口地址被忽略**，
启动时打印「配置已含路径 X，忽略契约接口地址 Y——若两者不同，请把 base_url 改成纯域名」。
看到这句话就去改配置，否则你以为在测接口 B，实际打的是接口 A。

## 2. 多套环境（region 分组：国内版 / 国际版，多轮隔离测试）

`personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 支持「多套环境结构」——顶层放：

- `envs` 数组：每组一个隔离环境，可带 `region` 字段（`cn`=国内版 / `intl`=国际版）；
- `default_env`（可选）：默认选中的环境名；
- 顶层（`envs` 之外）字段：视为所有环境共享的公共字段，仅在某个环境块未定义时回退。

envs 块数量不限（如国内版 2 套 + 国际版 2 套），模板不必一次写全、按需增删。
模板 `assets/personal_config.example.json` 已内置 cn 2 套 + intl 1 套示例占位
（`round1_local` / `round2_devtest` / `intl_1`）。

**运行时选择分两步**：

1. **先按 region 过滤**——命令行 `--region` 或 `WJX_REGION`（如 `cn` / `intl`）；
   跨 region 的参数不会进入本次运行；
2. **再按以下优先级选定环境**：命令行 `--env <环境name>`（冒烟）> 环境变量 `WJX_ENV` >
   顶层 `default_env` > 候选中第一个。

**兼容性**：旧的单环境扁平结构（无 `envs` 键）仍完全兼容。
真实凭据一律放 skill 包外，包内不得存在任何含真实值的配置文件。
字段级含义见 `assets/配置参数说明.md` 第五、六节。

## 3. 跨环境纪律（防参数串环境）

- 正常用例**只使用所选环境的参数**，未选中的环境参数完全不进入运行时；
- 仅**异常场景测试**（跨企业 / 跨租户 / 越权隔离验证等）允许显式跨环境：
  - 深度测试：设 `WJX_CROSS_ENV=<环境name>`（加载到 `CROSS_CFG`），可另配
    `WJX_CROSS_CONFIG`（指定其他配置文件）与 `WJX_CROSS_REGION`（region 过滤）；
  - 冒烟：用 `--cross-env <环境name>`（挂到 `ctx.cross_env`），也可设 `WJX_CROSS_ENV`；
- 显式加载的跨环境参数带 `_cross_env` 标记，便于在产物中审计；
- **未显式开启时，跨环境参数一律不可用。**

## 4. 冒烟配置同样支持多套环境（region 分组）

`scripts/smoke_runner.py` 加载 `smoke_config_*.json` 后：

1. 先自动把占位符替换为 `personal_config.json（真实配置不随包分发，用 personal_config.example.json）` / `WJX_*` 的真实值（`<域名>` 由 `base_url` 解析）；
2. 再按 **`--region` / `WJX_REGION` 过滤 + `--env` / `WJX_ENV` / 顶层 `default_env`
   选定环境并扁平化（`select_smoke_env()`）**。

冒烟配置与个人配置一样支持两种结构：

- **多套环境结构**：顶层 `envs` 数组，每组含各自的 `name` / `region` / `base_url` /
  `common_params` / `account` / `overrides`；顶层放 `method` / `timeout` / `qps_tiers` /
  判定关键词等共享字段。`smoke_config.example.json` 已内置 cn 2 套 + intl 1 套示例占位
  （真实环境值由包外 `personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 注入）。
- **单环境扁平结构**（无 `envs` 键，旧配置完全兼容）。

**占位符两种写法**（都解析为同一字段，旧配置不失效）：

1. 简短形式：`<APPID>` / `<主账号>` / `<子账号>` / `<CORPID>` / `<SYSID>` / `<域名>`；
2. 自带中文接口参数含义的长串形式，如
   `<appid 应用ID：所有请求的公共参数 appid，唯一标识你的企业/应用>`（见 `smoke_config.example.json`）。

也支持 `--personal /path/to/personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 显式指定。
冒烟配置里**不要写死**个人数据，只放占位符。

> **实例 ID 按环境独立**：各环境 overrides 里的 vid 类业务实例 ID 不通用，某环境生成的实例
> 在其他环境不存在，须按各环境分别重新生成（未生成前相关用例会被 na_keywords 判为 N/A）。

## 5. 自定义请求头（两级来源）

需要自带认证头 / 业务头时，两个来源，**环境块优先**：

1. **独立文件 `assets/request_headers.json`**（兜底）：以 `assets/request_headers.example.json`
   为模板复制；已 gitignore，**不入库、不随 skill 分发**。
   路径按 **skill 根**解析而不是当前工作目录——skill 总是在别人的项目目录下被调用，
   用 cwd 相对路径必然找不到，而「找不到」与「没配」在行为上都等于「不发这个头」，属静默失败。
2. **`personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 的 `headers` 对象**（优先）：跟着环境配置走，切换环境即自动切换头。

文件里支持 `by_env` 按**当前选中环境名**覆盖（与 `envs` / `--env` 选中的一致）：

```json
{
  "_comment": "下划线开头的键是注释，不会作为请求头发出去",
  "headers": {"X-Api-Version": "2"},
  "by_env": {"round1_local": {"X-Tenant": "local-tenant"}}
}
```

**三条必须知道的规则**：

- **下划线开头的键是注释**（`_comment` / `_note` 等）：`_comment` 本身是合法 HTTP 头名，
  不做这个约定它会被真的发出去。
- **头名原样发送，头值禁含 CR/LF**：装载期即报错，不静默吞——头值里混进换行等于让人能
  在报文里插新头，是请求走私的入口。
- **用例里禁止写真实头值**：批准门禁的凭据扫描会拒绝。需要鉴权时把值放配置，用例只留占位符。

**优先级（低 → 高）**：引擎默认 `Content-Type` < 独立文件 < 环境块 < 用例 header 位置参数 < UA 变体。
用例里把某个头写成**空值或 null = 这条用例不带这个头**（用于验证「缺这个头必须被拒」）。

**四个协议头只由引擎决定**：`Content-Type` / `Content-Length` / `Host` / `Transfer-Encoding`。
配置里写了会被忽略并出声（同类只提示一次）——报文按 `body_kind` 编码而头被改成另一个值时，服务端按头解析失败，
报告上长得像「接口不认这个参数」，是最难查的一类假缺陷。

**产物只记头名与来源，不记头值**：头里有凭据，落盘就是自造泄露面。

## 6. 分享给团队

分发前必须先确认 **`assets/personal_config.json（真实配置不随包分发，用 personal_config.example.json）` 不在分发物里**。它已被 `.gitignore` 排除、
**不进 git**，但「不发进 git」不等于「不在文件夹里」——直接把整个 skill 目录打包或复制给别人时，
本地那份含真实值的文件会一起被带走。分发前做一次确认：

```bash
ls assets/personal_config.json（真实配置不随包分发，用 personal_config.example.json） assets/request_headers.json 2>/dev/null   # 有输出就先移走
```

包内正常只应有占位模板（`personal_config.example.json` + `smoke_config.example.json` +
`request_headers.example.json`）。同事复制模板填自己的值，或用环境变量，即可使用。

> 每个配置项对应哪个接口参数、中文含义、取值示例：见 `assets/配置参数说明.md`
> （配置项 ↔ Sojump 接口参数字段 ↔ 中文名 一一对照）。
