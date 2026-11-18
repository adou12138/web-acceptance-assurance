# 设计数据通道（Figma）

本文件规定「设计数据从哪来、取到后怎么处理、取不到怎么记账」。取数的工具细节与字段口径由 `skills/figma-ui-parity` 权威维护（`skills/figma-ui-parity/references/tooling.md` 1.1/1.2、`skills/figma-ui-parity/references/figma-fields.md`、`skills/figma-ui-parity/scripts/figma_mcp_to_json.py`），本文件只做前置判定与边界，不复制其脚本参数。

## 一、三种来源与判定顺序

按顺序尝试，命中即停；**判定按能力，不按工具名**（不同 Figma MCP 实现的工具名不同）：

| 顺序 | 来源 | 判定方式 | 产物 |
|---|---|---|---|
| 1 | Figma MCP | `ToolSearch` 搜 `figma`：取节点能力 = `get_figma_data`（figma-developer-mcp）或 `get_file_nodes`/`get_file`（官方远程 MCP）；取图能力 = `download_figma_images` 或 `get_images`。命中任一 → 可用 | 节点数据（YAML-ish 文本或 REST JSON）、切图 |
| 2 | REST + PAT | 从 `~/.workbuddy/mcp.json` 的 `figma.headers.X-Figma-Token` **或** `figma.env.FIGMA_API_KEY` 取 token，`GET /v1/me` 返回 200 | REST JSON、`/v1/images` 的 S3 URL |
| 3 | 用户导出物 | 用户提供 JSON / PNG / 截图包 | 静态设计文档，标注来源与导出时间 |

- token 只读不打印：不写入报告、日志、对话或证据文件；只在进程内以环境变量传给 `curl`。
- 判定「MCP 不可用」的依据只能是**能力缺失**（一个取节点工具都搜不到），不能因为工具名不是 `get_file_nodes` 就判不可用并降级。

## 二、取到之后

1. **文本不是 JSON**：`get_figma_data` 等多返回 YAML-ish 文本，必须先经 `figma_mcp_to_json.py --input <文本> --output <节点JSON> --node-id <Frame-ID>` 转换，再进 `extract_design_fields.py`；直接把文本喂字段提取会失败。
2. **`nodeId` 形态**：URL 的 `node-id` 是 `1234-5678`，取数时用 `1234:5678`；深链实例 ID 是分号串，整体算一个节点，不拆分。
3. **多画板必须显式选**：转换脚本要求 `--node-id` 或唯一 `--root-name`；多画板未指定、模板/全局变量缺失、结构损坏时必须失败并报告，**不得默认取第一个画板**。
4. **页码/组件 ≠ 页面设计稿**：`node-id` 指向 COMPONENT / INSTANCE / 子 FRAME 或尺寸明显小于页面时，先浅层枚举（`depth=1/2`）映射到页面级 FRAME，再进入字段比对。
5. **落盘**：设计侧产物统一放 `<task>/design/`，命名带 `fileKey`、`nodeId` 与取数时间；切图按 `download_figma_images` 的 `localPath` 规则落在工作区内。

## 三、取不到时（不得越界）

三种来源都不可用时：

- 设计侧相关维度记 **`dataMissing`**（缺数据），不要记 `notRun`（未执行）——两者含义不同，混用会让覆盖率统计失真；
- 报告标题与结论**不得**出现「还原度」「像素级一致」；只能写「页面 UI 走查（无设计稿对照）」；
- **禁止**用页面截图倒推设计值、禁止按当前实现编造节点尺寸/色值/字号、禁止把「看起来差不多」写成 `pass`；
- 可以做的是：页面侧五视角走查、响应式/无障碍/性能等不依赖设计稿的检查，并在报告中列出缺数据导致的未覆盖维度。

部分取到（例如只有节点数据、没有切图）时：像素级 diff 记 `dataMissing`，样式级比对照常做，报告写明降级原因。

## 四、与主体五视角、figma 升级的边界

- **无设计稿**：主体五视角照常执行；figma 页面侧链路（R0 的纯页面分支）仍可用于 45 维深查，但**不得产出还原度结论**——这两件事互相独立。
- **有设计稿**：先做对象对应性（7.23）再谈样式；`mismatch` 必须停下来问用户，不得自动映射到"最像的那个元素"后继续比对。
- figma 返回的结论按 `bug-detection-map.md` 的六态→五态映射回写探索清单后才计入技术结论；未回写的 figma 结论只能作附件，且报告须列为外部结论缺口。

## 五、证据与记账

- 取数证据：`design_model.json`、原始节点响应、切图、`/v1/me` 探活状态（只记状态码，不记 token）；
- 取数失败：在探索清单留一条记录，写明尝试过的来源、失败原因（401 / 404 / 工具缺失 / 无导出物）与影响维度；
- 设计数据版本与页面版本必须同时记录（`fileKey` + `nodeId` + 页面 URL + 时间），否则跨轮比对无意义。
