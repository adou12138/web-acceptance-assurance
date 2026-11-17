# 浏览器连接与工具选择

本文件规定「用什么连、怎么连、句柄怎么用」。它是执行期工具层的唯一入口；具体业务断言、证据归因仍按 `methodology.md`、`execution-evidence.md`、`exploration-runtime.md` 执行。工具能力只提供观察与操作通道，不构成业务通过证据。

## 一、先判定会话里有哪套 MCP

判定只看会话中实际出现的工具名，不靠猜：

| 工具名特征 | 结论 |
|---|---|
| `take_snapshot` / `list_pages` / `evaluate_script` / `wait_for` / `click`（无 `browser_` 前缀） | Chrome DevTools MCP |
| `browser_snapshot` / `browser_evaluate` / `browser_wait_for` / `browser_click` | Playwright MCP |
| 两套都没有 | 先按第四节处置，不得改用 HTTP 探测替代页面验证 |

**默认 Chrome DevTools MCP**；用户明确要求，或既有多轮资产已按 Playwright 记录 `ref` 时才切 Playwright。切换必须在报告中留痕，并重取该通道快照——两套的 `uid`/`ref` 互不相通。

## 二、连接与登录态

| 连法 | 配置要点 | 适用 |
|---|---|---|
| 自起 + 固定 profile（主推） | `--userDataDir <固定目录>` | 常规测试，登录态跨会话保留 |
| `--autoConnect` | Chrome ≥144 且已开远程调试 | 复用用户正在用的已登录 Chrome |
| `--browser-url` | 先手动带 `--remote-debugging-port` 起浏览器 | 沙箱起不了浏览器或端口方案 |

- **不要用 `--isolated`**：临时目录，关闭即清空，登录态不保留，测完无法复跑。
- **登录由用户在有头窗口手动完成**：不自动填账号密码、不读浏览器 Cookie（读凭据会被安全策略拦）。
- 登录成功用多信号判定：URL 离开登录页 **且** 出现真实业务内容（账号名、列表首项），不看「还有没有密码框」。
- 掉登录就让用户重登一次，比导出 Cookie 快。

## 三、机器人检测（只在真被拦时处理）

表现：页面早跳验证码、直接回「访问异常」、登录反复失败而手动浏览器正常。先切连接模式（`--autoConnect` / `--browser-url`，不注入自动化开关）；必须自起时**两个参数一起加**：

| 层 | 消除方式 |
|---|---|
| 浏览器 UI 层（顶部「正受到自动测试软件控制」信息栏） | `--ignoreDefaultChromeArg=--enable-automation` |
| JS 特征层（`navigator.webdriver === true`） | `--chromeArg=--disable-blink-features=AutomationControlled` |

只加一个会漏：删开关 ≠ 关特性。命中检测时把手段记入问题单；无证据不假设被封。

## 四、连不上或工具缺失时

1. 确认 `~/.workbuddy/mcp.json`（不是 `.mcp.json`）已配置对应 server，args 与 schema 一致；
2. 改配置不热加载：重启宿主 + 在连接器管理里手动「连接 / 信任」；
3. 仍不可用：报告工具阻塞，受影响用例记 `BLOCKED` 并写明缺口，**不得**以 `curl`/HTTP 状态码冒充页面渲染与交互验证——HTTP 探测只能作为可达性旁证，需与页面证据分开标注。

## 五、句柄与调用纪律

- **Chrome DevTools**：页面级工具（`take_snapshot`/`evaluate_script`/`wait_for`/`click`/`fill`）必填 `pageId`，取自 `list_pages` 行首编号；刷新或新开标签后重取。`fill` 参数名是 `value`（不是 `text`）；`wait_for` 的 `text` 是数组；**没有「等消失」语义**，判断结束要「先等出现 → 轮询快照直到不在」。
- **Playwright**：`ref` 来自最近一次快照；需要坐标判重叠时启动加 `--snapshot-boxes`。
- **`uid`/`ref` 只在当次快照有效**：页面异步刷新、路由跳转、弹层开合后必须重读快照再操作，禁止跨轮复用。
- 快照过大时工具会落盘为文件，**必须读落盘全文**再断言，预览截断处往往正是要判的内容。
- 截图落盘：Chrome DevTools 默认只允许系统临时目录，需 `--filesystemRoot` 才能写入 `<task>/evidence/`；写入失败按工具限制记录，不要改写到无关目录。
- 等待用状态信号（文本出现/消失、列表项、按钮恢复、URL 变化），不用固定 `sleep`；超时不是失败，重等一次并记录。

## 六、双通道协同（驱动归 Playwright，观察归 DevTools）

仅当两套附着同一浏览器实例时可用：先比对 Playwright 标签列表与 DevTools `list_pages` 的 URL/标题，一致才算同实例；不一致**退化为单通道**，固定一套完成驱动与观察。

1. **单通道写操作**：任一时刻只有一个通道点击/输入/导航，切换前对齐页面并记一行 `[切换] 驱动 → 诊断，原因：…`；
2. **标识不互通**：`ref` 只给 Playwright、`uid` 只给 DevTools，换通道必须重取快照；
3. **驱动成功但状态没变 → 换诊断通道**，顺序固定：控制台 → 网络 → 运行时求值；不要同通道重复点击制造「工具返回成功」的噪音；
4. 弹窗只由驱动通道处理；测试期间不调用关闭浏览器的动作。

未对齐时看到的差异一律不采信；通道越权、标识混用、未对齐导致的异常记为**流程错误**，不计入应用缺陷。页面状态确实没变且控制台/网络/运行时均正常，才升级为前端逻辑缺陷候选。

## 七、证据边界

- 快照、截图、控制台日志、网络记录按 `execution-evidence.md` 归档到当轮 `evidence/`，命名带时间前缀；
- 采集工具返回空 issues、进程成功退出、候选排名靠前，**都不构成**业务通过证据；
- 只用观察通道看到的现象记「待复核候选」，附复现动作后才转缺陷；
- 因工具限制未做成的检查（跨域 iframe 内脚本、无坐标快照、文件写入受限）如实记为证据缺口，不写「已检查」。

## 八、深层排障入口

跨域 iframe 读取、审批卡循环、流式输出结束判定、标签接管失败等细节，若本机装有 `web-agent-stream-capture` 技能，按其 references 目录下的 MCP 适配层与排障文档处理；没有该技能时按本文件与当次工具 schema 自行收敛，并把新踩的坑补进当轮工作记录，不写回站点专属选择器。
