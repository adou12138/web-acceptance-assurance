# figma-ui-parity

版本：4.48.0 · 分类：ui-testing · 平台：Windows / macOS / Linux

以 Figma 设计稿为基准，对本地或远程页面执行智能 UI 还原、响应式、交互可用性、可访问性、页面性能、异常数据和视觉回归检查：自动生成元素级场景、双向盘点设计与页面元素、验证按钮及浮层真实行为，统一去重缺陷并输出 HTML、JSON、CSV 报告。

## 核心能力

- **45 个检查维度（7.1–7.45）**：覆盖对象对应、静态样式、交互生效、异常状态、运行期安静、性能达标、可访问性合规、双端对比度、键盘可达性与跨轮稳定性
- **元素级场景自动生成**：双向盘点设计与页面元素，结论六态标注（pass / fail / warn / pending / notRun / dataMissing）
- **运行期观测（7.35–7.38 / 7.45）**：JS 异常、交互期网络失败（含 HTTP 200 + 业务失败 / 错误页伪装）、重复提交、元素遮挡、请求载荷完整性（占位符泄漏 / 同名参数重复 / 写请求零参数）
- **跨页用户旅程走查（7.39）**：BFS 有界点击，判定 404/500、落地空白、返回中断等落地页缺陷
- **性能与 Core Web Vitals（7.40）**：加载前装观察器采集 TTFB / FCP / LCP / CLS / TBT 与资源体积；无网络节流时耗时只记参考，不自动确认缺陷
- **可访问性规则合规（7.41）**：内置 WCAG 2.1 A/AA 子集 15 条规则，首次运行即可判定，不依赖历史基线；可叠加 axe-core
- **设计稿侧对比度（7.42）**：在设计模型上直接算文本对比度，评审阶段就能报出不可读的配色；图片/渐变/半透明底降级待确认，不按白底硬判
- **键盘可达性与焦点顺序（7.43）**：Tab 逐次采样 activeElement，判定不可达、正 tabindex 导致顺序错乱、弹层焦点逃逸；只按 Tab，无副作用
- **交互首响应时延（7.44）**：点击到首个实质变化的时间，>2000ms 才算缺陷；明确不是 INP，不冒充 INP 数值
- **网络条件矩阵（7.33）**：接口 500 / 断网 / 弱网三条件各测一遍——三者走前端三条不同代码路径，只测 500 会整片漏掉 catch 分支与加载反馈
- **跨轮 flaky 量化**：只读历史快照统计维度翻转，把「稳定的真缺陷」与「不稳定的判定」分开；交付 JSON + CSV
- **组合场景**：`--combo-depth`（默认 1，2 档加深异常数据与逐断点展开）
- **执行档位**：`--profile smoke / standard / full`，只改「未执行维度」的记账口径，不抬高覆盖率
- **缺陷模式池**：31 条 DP-* 模式，按历史高频缺陷维度自动补场景
- **四档交互安全策略**：全部操作 / 安全操作 / 写操作拦截 / 仅静态检查，每次测试开始前由用户选择
- **写操作清理台账**：真实写/删/支付动作必须登记回收方式，未清理项自动进报告「待确认」
- **指纹合并去重 + 覆盖率门禁**：同一 fingerprint 只保留一条；覆盖率低于 70% 时报告顶部提示「非全量覆盖」；元素定位按稳定/一般/脆弱分级标注
- **三格式报告**：report.html / report.json / defects.csv 同源一致

## 快速开始

依赖 8 项：Python 3.10+、Node 运行时、playwright-core、有头浏览器、playwright-mcp、figma-source（Figma MCP 或 REST API 接入）、visual-parity（可选，缺失时降级为确定性差异量化）、可写的输出目录。

```bash
# 环境检查（只读，不安装、不登录）
python scripts/check_environment.py

# 初始化工作目录（建立 parity-test/ 与凭据忽略规则）
python scripts/init_workdir.py --root .

# 测试启动门禁（缺安全策略即阻断；all 须确认隔离测试环境）
python scripts/start_test.py \
  --instruction "<用户原始指令>" \
  --figma-url "<Figma URL>" \
  --page-url "<页面 URL>" \
  --safety-policy <all|safe|mockWrites|staticOnly> \
  --user-safety-selection "<用户原始选择>"

# Figma MCP 返回为 YAML-ish 文本时，先转换为设计模型输入
python scripts/figma_mcp_to_json.py \
  --input ./parity-test/figma-mcp.txt \
  --output ./parity-test/design/figma-mcp.json \
  --root-name "主页面"
python scripts/extract_design_fields.py \
  --input ./parity-test/design/figma-mcp.json \
  --output ./parity-test/design/design_model.json

# 或由管线自动转换并接入设计字段、对比度与场景阶段
python scripts/run_parity_pipeline.py \
  --workdir ./parity-test \
  --figma-mcp-raw ./parity-test/figma-mcp.txt \
  --figma-root-name "主页面" \
  --page-url "<页面URL>" \
  --page-json ./parity-test/page.json \
  --report ./parity-test/report.json \
  --outdir ./parity-test/reports

# 无设计稿快速档（只有页面 URL：设计侧阶段自动跳过并留痕，报告只写「页面 UI 走查」）
python scripts/run_parity_pipeline.py \
  --workdir ./parity-test \
  --page-url "<页面URL>" \
  --page-json ./parity-test/page.json \
  --report ./parity-test/report.json \
  --outdir ./parity-test/reports
```

MCP 文本转换额外需要 PyYAML；设计稿拼图需要 Pillow，报告可选使用 Pillow 校验截图格式。未安装 Pillow 时报告仍生成，但截图不内联、不提供未经验证的链接，并明确说明无法验证。转换过程离线执行，不读取 token、不访问页面。多画板需显式指定 `--figma-node-id` 或 `--figma-root-name`，引用缺失或转换失败时停止，不消费旧模型。未知坐标、字距、填充等会贯穿下游标记为不可判定，而非补默认值后确认缺陷。转换范围与限制见 `references/figma-fields.md`。

国内网络（代理与 Playwright 浏览器镜像源）配置见 `references/setup-guide.md`。

## 目录结构

| 条目 | 说明 |
|---|---|
| `assets/` | 缺陷模式池 `defect_patterns.json` 等数据资产 |
| `parity-test/` | 运行工作目录（报告、截图、提取产物、基线、凭据） |
| `references/` | 各检查维度与流程阶段的详细规范文档 |
| `schemas/` | 机器可读契约（维度定义、场景覆盖、缺陷结构） |
| `scripts/` | Python / Node 脚本（环境检查、管线、门禁、报告） |
| `SKILL.md` | AI 指令入口（路由分诊、执行契约、阶段契约卡与命令链） |
| `skill-dependencies.json` | skill 依赖声明（不驱动环境检查） |
| `test-prompts.json` | 测试提示词 |

## 深入阅读

- [SKILL.md](SKILL.md) —— AI 指令入口（路由分诊、执行契约、阶段契约卡与命令链；各阶段完整口径见 references/）
- [references/setup-guide.md](references/setup-guide.md) —— 环境配置与国内镜像指引