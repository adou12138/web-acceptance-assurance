# 工具可用性检查与安装配置（流程阶段 1 详细版）

本文件由流程阶段 1 下沉而来。

#### 1.1 检查工具可用性（设计源双通道）

设计源有两条通道，**通道 A 优先，通道 B 为回退**。任一可用即可继续，**不得因通道 A 不可用就终止流程**。

| 通道 | 工具 / 方式 | 检查方式 | 可用 / 不可用时的处理 |
|------|------------|----------|----------------------|
| **A（优先）** | `mcp__figma` MCP（**两族工具名并存**） | 用 `ToolSearch` 搜 `figma`，按**能力**而非固定工具名判定：取节点数据 = `get_figma_data`（figma-developer-mcp）或 `get_file_nodes` / `get_file`（官方远程 MCP）；取图片 = `download_figma_images` 或 `get_images` | 命中任一族 → 走通道 A，按下表用实际工具名调用。一个都搜不到 → **自动切通道 B，不要要求用户去点 Trust** |
| **B（回退，已实测可用）** | `curl` + `X-Figma-Token` 直连 Figma REST API | 按 1.2 的令牌读取口径取 token 后执行 `/v1/me`，返回 **HTTP 200** 即通道可用 | 可用 → 走通道 B 完成流程阶段 2。返回 401 → token 失效，引导更新 `mcp.json` |
| — | `mcp__playwright` | 用 `ToolSearch` 搜 `mcp__playwright`，确认 `browser_navigate` / `browser_evaluate` / `browser_take_screenshot` / `browser_snapshot` 已加载 | 不可用 → 按下方"安装配置"在 `~/.workbuddy/mcp.json` 追加 `playwright` 服务器，并在连接器管理点 Trust |
| — | `mcp_visual-parity` | 查看工具列表是否包含 `compare_pages` | 不可用 → **降级模式**：跳过流程阶段 5–6，仅用 Playwright + LLM 对比 |

**通道 A 的两族工具名（不要按名字猜，按能力选）**：

| 能力 | figma-developer-mcp（stdio，本机默认） | 官方 Figma 远程 MCP |
|---|---|---|
| 取文件/节点数据 | `get_figma_data { fileKey, nodeId?, depth? }`——`nodeId` 用 `1234:5678` 形式，**只传一个节点**，深链实例用分号链 | `get_file` / `get_file_nodes` |
| 取图片 | `download_figma_images { fileKey, nodes:[{nodeId,fileName,...}], localPath, pngScale? }`——落盘相对服务端图目录 | `get_images` |

- `get_figma_data` 返回的是 YAML-ish 文本，**不是 REST JSON**：必须经 `scripts/figma_mcp_to_json.py --input <文本> --output <节点JSON> --node-id <Frame-ID>` 转换后再进 `extract_design_fields.py`（口径见 `figma-fields.md`）。
- 两族都存在时优先官方族（返回 REST JSON，少一步转换）；只有 figma-developer-mcp 时不要硬套 `get_file_nodes`，否则会被误判为"通道 A 不可用"而白白降级。
- `download_figma_images` 的 `localPath` 相对服务端图目录（本机为 workspace 根），绝对路径只接受图目录内路径；截图落盘后仍是**页面侧证据**，不能替代设计字段。

**`mcp__playwright` 安装配置**（工具搜不到时，追加到 `~/.workbuddy/mcp.json` 的 `mcpServers`，**合并不要覆盖**）：

```json
"playwright": {
  "command": "npx",
  "args": ["-y", "@playwright/mcp@latest", "--headless", "--caps=storage"],
  "disabled": false
}
```

- `--headless`：**必须**。自动化环境通常无显示器，而该 MCP 默认是有头模式，不传会因拉不起窗口而启动失败。
- `--caps=storage`：开启 `browser_cookie_set` / `browser_storage_state` / `browser_set_storage_state`，**用于注入登录态**（见 `references/headed-login-fallback.md`），可免去手动登录。
- 首次调用由 npx 拉取 `@playwright/mcp`（latest 现为 0.0.80）；浏览器复用本机 `ms-playwright` 缓存，一般无需再执行 `npx playwright install chromium`。
- 配置写入后**不会自动生效**：需在连接器管理（右上角）找到 `playwright` 点 "Trust"/启用，工具才会加载。

**Playwright MCP 工具映射**（本 skill 统一按官方 `browser_*` 命名调用）：

| 本 skill 用法 | 用途 |
|--------------|------|
| `browser_navigate` | 打开 URL |
| `browser_snapshot` | 无障碍树快照，带 `boxes: true` 可返回各元素坐标，适合元素级对比 |
| `browser_evaluate` | 执行 JS 提取 computed style |
| `browser_take_screenshot` | 截图，支持 `fullPage: true` |
| `browser_click` / `browser_hover` | 触发交互态 |
| `browser_resize` | 设置视口，对齐设计稿宽度 |
| `browser_cookie_set` / `browser_set_storage_state` | **注入登录态**（需 `--caps=storage`） |

**通道 B 的关键原理**：Figma 远程 MCP 端点（`https://mcp.figma.com/mcp`）对客户端有 **OAuth 白名单**限制，WorkBuddy 不在 Figma 官方客户端目录里，所以 MCP 通道会卡在授权／激活环节。而 **`X-Figma-Token` 请求头直接调用 `api.figma.com/v1/...` REST API，绕开了这个白名单**，既不需要浏览器 OAuth 授权，也不需要在本 workspace 激活 MCP。因此通道 A 不可用时，通道 B 是首选回退路径，而非报错终止。

#### 1.2 Figma 可用性预检（关键前置步骤）

在正式获取设计稿前，**必须先确认至少一条通道可用**。

**预检流程**：

1. 从用户提供的 Figma URL 提取 `fileKey`（`/design/` 或 `/file/` 之后第一段）和 `nodeId`（`node-id` 参数，`-` 需转 `:`）。
2. **先试通道 A**：用 `ToolSearch` 搜索 `mcp__figma`。
   - 搜到工具 → 通道 A 可用，进入流程阶段 2 的通道 A 分支。
   - 搜不到 → 进入第 3 步。
3. **回退通道 B**：执行令牌探活。token 从 `~/.workbuddy/mcp.json` 的 `mcpServers.figma` 读取，**先看 `headers.X-Figma-Token`，再看 `env.FIGMA_API_KEY` / `env.FIGMA_TOKEN`**（两种落地形态都出现过：官方远程 MCP 用 headers，figma-developer-mcp 等 stdio 服务器用 env）；**禁止在任何命令回显、日志或回复中打印明文 token**：

```bash
TOKEN=$(python - <<'PY'
import json, os
cfg = json.load(open(os.path.expanduser("~/.workbuddy/mcp.json"), encoding="utf-8"))
fig = (cfg.get("mcpServers") or {}).get("figma") or {}
env = fig.get("env") or {}
tok = (fig.get("headers") or {}).get("X-Figma-Token") or env.get("FIGMA_API_KEY") or env.get("FIGMA_TOKEN")
if not tok:
    raise SystemExit("figma token 未配置：mcp.json 的 figma.headers.X-Figma-Token 与 env.FIGMA_API_KEY 均为空")
print(tok)
PY
)
curl -sS -m 20 -o me.json -w "HTTP:%{http_code}\n" -H "X-Figma-Token: $TOKEN" https://api.figma.com/v1/me
```

4. 根据返回判断：

| 返回情况 | 判定 | 处理动作 |
|----------|------|----------|
| HTTP 200（`/v1/me` 返回 email / handle） | token 有效、通道 B 可用 | ✅ 走通道 B，进入流程阶段 2 的通道 B 分支 |
| 401 / 403 | token 失效 | ❌ 引导更新 `~/.workbuddy/mcp.json` 中 `figma` 的 token（headers 或 env 按现有形态改；Figma 官网 Personal access tokens 页面生成） |
| 取不到 token（脚本以非零退出） | 未配置 | ❌ 按上句提示补配置；**不得**改读环境变量或其它文件猜测 token |
| 两通道都不可用 | 无法访问设计稿 | ❌ 终止流程并说明原因，**不得伪造或猜测设计稿数据** |

**注意**：通道 A 未激活是**正常且常见**的状态（MCP 按 workspace 单独激活），**不要因此要求用户去点 Trust**，直接切通道 B 即可。仅当用户明确希望走 MCP 通道时，才提示去连接器管理启用。判"通道 A 不可用"的依据只能是**能力缺失**（搜不到任何取节点/取图片的工具），不能因为工具叫 `get_figma_data` 而不是 `get_file_nodes` 就判不可用。

#### 1.3 token 新鲜度提醒

Figma PAT 长期有效，无需像蓝湖 Cookie 那样频繁更新。仅提示：

- 通道 B 探活返回 401 才说明 token 失效，引导去 Figma 官网重新生成并写回 `mcp.json`（保持原有落地形态：headers 或 env）。
- token 泄露后在 Figma 官网 Personal access tokens 页面撤销。

