# 探索观察、下一步候选与真实执行

## 责任分层

专家负责理解规则、实际操作浏览器、选择下一步、复核证据内容及判断业务结果。本包工具提供观察候选、清单验证和派生视图，不自动浏览网站、不授予操作权限、不承诺零漏测。内部JSON是记录/交换格式，面向用户的数据结果使用UTF-8-sig、逗号分隔CSV；不生成TSV。工具层「用哪套 MCP、怎么连、句柄怎么用」统一按 `browser-connection.md`，本文件只讲探索循环与清单契约。

原设计源继续遵循 `case-data-contract.md`，正式执行事实继续使用 `execution-management.md` 的 `execution_ledger.py`，变更回归沿用 `change_impact.py`。探索清单是可修改的观察与计划工作稿，不代替冻结规格、attempt历史或缺陷台账；每次有效发现关联REQ/SCN/PATH/TC/RUN/EXP，缺引用时先补来源而非编造。纯结构检查不证明业务完整。

## 动态探索工作循环

1. 从需求、初次观察和历史风险建立实际可见入口/页面/角色/状态清单。先登记用户目标与判据来源，未定义规则保持待确认。
2. 选择高影响、高未知及有异常线索的下一步；常规安全测试沿已授权范围推进。预算是批次与停止依据，不是充分性证明；必选未测仍影响最终结论。
3. 核对前置、数据、角色、写入与恢复边界后实际执行；每次动作后重新观察，不复用过期元素句柄；截图、网络、业务读回与具体断言关联。
4. 观察到新页面、弹窗、错误态或交互后立即补清单；异常扩查同类字段、共享组件、相邻状态与数据组合。不把盲猜地址或未经授权接口重放当自主性。
5. 已失败项列复现候选；写操作状态未知先核对副作用，不能因候选排序自动重放。中断按原执行台账恢复。
6. 每批核对剩余前沿、重复操作、阻塞与当前版本；需要继续则选下一项，停止则写预算/环境/风险依据及未测项。发现转为可复跑TC，并保留原EXP/RUN证据。

## 探索清单工具

`scripts/exploration_state.py` 标准库实现，测试为 `tests/test_exploration_state.py`。API为`summarize(data)`和`export_csv(data, output)`；CLI：

```bash
python scripts/exploration_state.py <本轮exploration.json> --output <新的检查视图.csv>
```

使用绝对路径或以专家包根为当前目录；输出父目录必须存在，拒绝覆盖。退出码0仅表示校验/导出完成，2表示数据或工具错误，不是网站质量门禁。

摘要另含`gate`执行门禁：`ready_pending`为仍可执行的NOT_RUN项数，`ready_high_risk`其中高风险数，`missing_views`为界面样式、交互易用性、基础无障碍、功能、性能中**没有 PASS/FAIL 实测记录**的视角，`failed_items`为状态为 FAIL 的条目，`verdict_ceiling`为当前允许的最高技术结论。`ready_pending>0`、有视角无实测记录或**覆盖过薄**（同一视角的 `(page_url, state)` 单元少于 2 个）、清单中没有任何已记录控件遍历条目（`traversal_recorded`为假，ID 前缀 `EXP-TRAV-` 且状态非 NOT_RUN）、`failed_items`非空或必选质量属性未闭合时上限为“待核实”，报告不得写“通过”；存在 FAIL 时须按缺陷必选性与闭环状态判为“不通过”或“待核实”。`traversal_pending`单列仍为 NOT_RUN 的遍历条目数。`views`保留全部记录，`executed_views`只含 PASS/FAIL，`skipped_views`只含 BLOCKED/SKIPPED——后两者**不计覆盖**，但须留痕并写明缺口。门禁只约束结论上限，不代替缺陷、断言与证据判定。探索门禁不掌握缺陷闭环信息，对 FAIL 条目只机械压到「待核实」；是否判「不通过」由裁决阶段按缺陷必选性、证据与闭环状态独立判定，不得因结论停在「待核实」就默认未闭环的 FAIL 可以放行。需要分清两层：机械「不通过」来自台账中未闭环的 FAIL 用例；探索清单的 FAIL 属观察候选，须先落成台账里的缺陷/用例条目并经闭环核对，才进入「不通过」判定，探索门禁本身不代判缺陷是否成立。

### 必选质量属性逐类裁定（`attribute_rulings`）

安全、兼容、文案、**界面样式、交互易用性、基础无障碍**六类**不能靠"本次没提"默认不需要**。清单根对象用可选 `attribute_rulings` 逐类给出裁定，否则 `gate.mandatory_attributes` 记为 `undeclared`、上限压到"待核实"：

```json
"attribute_rulings": {
  "security": {"state": "not_applicable", "reason": "本次仅改列表文案，无权限/会话变更"},
  "compatibility": {"state": "applicable", "reason": "需求承诺 Chrome/Firefox 双内核"},
  "content": {"state": "applicable", "reason": "新增错误提示与按钮文案"},
  "visual": {"state": "applicable", "reason": "新增弹层与表格列"},
  "usability": {"state": "applicable", "reason": "新增批量操作入口"},
  "accessibility": {"state": "applicable", "reason": "新增自定义下拉与快捷键"}
}
```

界面/易用/无障碍纳入必选的理由：这三类最容易被一张首页截图"顺带通过"，而用户实际投诉最多的正是文案错误、布局遮挡、按钮太小、提示误导。判 `applicable` 就要有同属性 PASS 实测记录，判 `not_applicable` 要写清为什么（例如"本批次为纯接口验证，无页面渲染"）。

- `state`：`applicable`（必须有同属性 PASS 实测记录才算闭合）/ `not_applicable`（必须写理由）/ `blocked`（写明缺什么环境或角色）。
- 判为 `applicable` 但只有 NOT_RUN/BLOCKED，或根本没写裁定，都进入"必选质量属性未闭合"，不得由功能视角的一条 PASS 抵消。
- 文案类检查面（术语、单位、未渲染变量、错误定位与恢复指引）见 `references/visual-usability.md`；兼容须记录真实内核/设备，视口模拟不得冒充。

**视角覆盖按页面×状态单元计**（`executed_cells`）：同一页面同一状态下的一条 PASS 不能清空整个视角——曾出现 7 条同页同状态的记录各带一个属性，就把界面样式/易用性/无障碍全部点亮，等于"截一张首页图 = UI 已测"。每个必查视角至少 2 个不同 `(page_url, state)` 单元；确属单页单状态的小任务，用根对象可选 `view_scope` 显式缩小下限并写理由：

```json
"view_scope": {"界面样式": {"cells": 1, "reason": "本需求仅一个弹窗，无其他页面"}}
```

`cells` 取 1~2 的整数；不写则默认 2。

根对象严格含`schema_version: "1.0"`、`context`及`items`。context严格含非空`target_url/application_version/environment`，无其他字段。每份清单严格同源：`page_url` 必须与 `target_url` 同协议、主机和有效端口。被测对象分布在不同域时**不得合成单份跨域清单**——视角覆盖按 (page_url, state) 单元统计，跨域同池会让一域的记录补齐另一域的缺口（"每域各自达标"变"跨域合计达标"），构成放水旁路（v1.47.2 实测证实后于 v1.47.3 回退该设计）。正确做法：每域各出一份同源清单，用 `phase_gate.py` 的 `--exploration`（可重复）一次登记多份 gate，合并取最严 ceiling、缺口合并、任一份缺控件遍历即整体缺。每个item必填：

| 字段 | 约束 |
|---|---|
| id | 清单内唯一稳定ID，字符集为字母数字及`_.:-`，首字符字母数字 |
| requirement_ids | 非空且无重复ID数组；只校验格式，不加载外部REQ目录验证存在性 |
| page_url | 无凭据的绝对http/https，与target_url同协议、主机和有效端口（严格同源；跨域见上文多份清单路径） |
| role / viewport / state / action | 非空字符串，写真实角色、视口、状态及拟做/已做动作 |
| risk | high / medium / low；具体风险依据记录在场景/manifest |
| status | NOT_RUN / PASS / FAIL / BLOCKED / SKIPPED |
| observation | 字符串，PASS/FAIL必须非空，写实际观察不写臆测 |
| evidence_paths | 无重复绝对本地证据文件数组；PASS/FAIL至少一项，重新读文件计算当前SHA-256 |
| blocked_reason | 字符串；BLOCKED/SKIPPED必须非空，NOT_RUN有阻塞原因也不进入ready |
| side_effect_pending | 严格布尔，待核对副作用时为true，不推荐直接执行 |
| attributes | 非空无重复属性：functional/visual/usability/accessibility/performance/security/reliability/compatibility/content（data/recovery 归并 reliability；usability 与 content 为探索域特有） |
| case_id | 可选稳定TC引用；外部存在性由专家按设计源核对 |

文件权限和路径受平台规则约束；不能用链接、空文件名或随便一个有哈希的文件代替内容证据。API输出原始清单记录和证据当前指纹；没有冻结旧摘要就不能宣称检测历史篡改。

## 控件遍历条目（EXP-TRAV-）

控件遍历是对本次需求触达的每个页面/弹窗做一次系统性控件清点（输入项、按钮、开关、链接、下拉），逐个记录可交互性与当前态，ID 使用 `EXP-TRAV-` 前缀以便门禁识别。属性用 `functional+visual+usability` 组合（遍历同时服务功能可用性、界面样式与交互易用性三个视角）。每个产出技术结论的批次，清单中必须至少有一条已记录（非 NOT_RUN）的遍历条目：没有时 `gate.traversal_recorded` 为假，`verdict_ceiling` 上限为待核实；仍为 NOT_RUN 的遍历条目数单列在 `gate.traversal_pending`。确无控件可遍历（如纯接口批次）时写一条 SKIPPED 遍历条目并给出原因，不静默缺席。遍历条目是观察记录，不替代逐控件断言用例，也不把“清点过”当作“测过”。

摘要含`planned/counts/ready/reproduction_candidates/needs_reconcile/views/executed_views/skipped_views/items/scope/gate`。ready仅为NOT_RUN、无阻塞且无待核对副作用的项，按风险再ID排序。FAIL列复现候选；所有有副作用待核对项单独列出。排序不基于完整站点知识，不是自动规划引擎，新增观察及风险仍由专家更新。

`gate.next_actions` 是把上述排序落成可执行的换路视图：`ordered`（可执行项，按风险→异常跟进→ID 排序——与 FAIL 条目同 case_id 的待执行项标记 `linked_fail` 并排前，把"发现异常后补测能区分解释的取值"提到普通待执行之前）、`recheck`（BLOCKED 条目及其复查条件）、`next_focus`（清单首项，执行遇阻时的显式切换目标）、`fail_candidates`。某条链路受阻（等待审核、环境不可达）时按 `next_focus` 切换，而不是空等或就地收尾；清单只呈现排序与依据，选择仍由专家负责并在台账留痕。

CSV派生“界面样式”“交互易用性”“基础无障碍”“功能”“性能”视角，一条记录可属于多个视角，planned与总量按唯一id去重。CSV采用标准引号转义，疑似公式单元格加前导单引号避免表格应用执行，原始JSON不改。此视图不自动生成BUG严重度、UX评分或AC满足率；业务判断仍用实际断言与证据。正式执行结果不要直接从可编辑清单反向覆盖events.jsonl。

## 观察结果桥接

`scripts/observation_bridge.py` 把观察器JSON中的候选映射为可校验的待复核条目，测试为 `tests/test_observation_bridge.py`。CLI：

```bash
python scripts/observation_bridge.py <观察.json> --target-url <同源URL> --application-version <版本> \
  --environment <环境> --requirement <REQ> --output <新的清单草稿.json>
```

映射按finding code固定属性与风险：`unnamed-control`为无障碍+易用性高风险；`horizontal-overflow`、`missing-body`、`blank-content`、`occluded-control`为高风险；`text-clipping`、`broken-image`、`image-aspect-distortion`、`placeholder-link`为中风险。所有条目状态为NOT_RUN、观察为空、证据指向观察文件绝对路径，必须由专家复核后回填。

未映射的code直接报错而不是丢弃；观察状态为blocked或page.url与target-url不同源时拒绝生成；生成后先调用`exploration_state.summarize`自检字段、同源与门禁，通过后才独占新建文件。桥接只减少手工转写，不代表候选已证实，也不自动执行或重放。

页面声明为受控夹具（`page.fixture.declared`为真）时，所有条目（含`OBS-performance`）的`action`前置统一标记，提示其中的异常可能是预埋项、不得直接作为产品缺陷上报或计入缺陷率；标记不改变状态与风险等级，也不把条目从清单里剔除，避免用一句声明替代实际判断。`page.fixture`结构非法时报错退出，不猜默认值。

观察结果带已实测性能数据时，另生成一条`performance`属性的汇总复核条目`OBS-performance`：动作里写明已采族与仍未覆盖的族，仍有未采族时风险为中、全部覆盖时为低。性能状态为未请求或不可用时不生成该条目，宁可让门禁继续报性能视角缺失，也不伪造覆盖。宿主追踪或审计产物用`--performance-evidence`多次追加，必须是绝对存在的本地文件，登记为该条目证据链。

## 只读浏览器观察器

`scripts/browser_observer.js` 为返回API的IIFE；Node中可`require`后调用纯逻辑`evaluateFacts(facts, options)`；真实浏览器内调用`collect(options)`。测试见 `tests/test_browser_observer.js`。平台支持文件脚本时读取该文件为表达式，再对返回API调用collect；具体传参以当次浏览器工具schema为准。不支持直接把API函数对象作为JSON结果，必须调用collect后返回可序列化结果。平台仅运行脚本文本而不调用返回API时，结果不是一次采集。

options可含`expectedOrigin`（精确http/https origin，不能含路径、查询或凭据）、`collectPerformance`（严格布尔）、`maxElements`（1..20000，默认5000）。同源不保证同页面，必须由专家继续核对实际目标路径、状态和角色；URL输出移除查询/片段及凭据，不能据净化路径判断业务实体一致。平台拒绝读取路径时停止该通道，说明阻塞，不换路径或内联复制绕过拒绝。

输出`status`为`observed/incomplete/blocked`，不是PASS。扫描截断为incomplete，错origin为blocked；`findings`均为candidate且needsReview，`confirmedBugs`为空。有限规则涉及横溢出、文本裁切（含祖先容器裁切、后代持文本的常见结构）、图片已完成但零自然宽、交互控件缺可辨名称、控件中心点被其他元素接收、object-fit为fill且比例明显失真的图片、占位链接，以及文档未声明`width=device-width`的viewport元信息。裁切在多层嵌套时只报最内层，`viaDescendant`标明由后代文本触发；`ellipsis`、遮挡与viewport类候选附`needsReviewReason`提示可能合法或误报；viewport检查只按文档声明判断，不代表真机缩放后的实际观感。合法省略、滚动容器、透明浮层和故意隐藏仍可能产生误报。索引仅对应本次事实数组次序，非稳定选择器；回到实时快照或实际页面定位。

观察器不读取表单value，不输出正文、控件名称和href原文；为了布尔判断会在页面内读文本及相关属性。标题与净化后的URL路径仍可能含敏感信息，导出前人工核对脱敏。不会导航、点击、修改DOM或存储、发送网络请求，不代表动态安全证明。遮挡只抽样控件中心点，iframe、Shadow DOM、完整辅助树与布局位移不在有限采集范围。

`page.fixture`用于区分受控夹具与真实待测站：`declared`为真表示页面自身声明为非生产样例、演练站或夹具，`declaredBy`给出命中位置（`attribute`、`meta`、`title`、`body`），`keywords`列出命中的自述词。强信号包括“受控”“夹具”“预埋”“非生产”，以及显式的`data-acceptance-fixture="true"`或`meta[name=acceptance-fixture][content=true]`；只命中“样例、示例、演示、测试站”这类弱词时`declared`仍为假，仅记录关键词交人工判断，避免把正常页面的文案当成声明。声明为真时`limitations`追加一条：页面内异常可能是预埋项，不得直接作为产品缺陷上报或计入缺陷率。判定依据是页面自述而非内容核实，生产站误写这类自述同样会命中，没写自述的夹具也不会被识别，两种情况都需人工确认用途。受控夹具用于校验采集链路时，结论只能写成“按要求检出预埋项”，不能写成“发现产品缺陷”。

性能摘要只读取当前Performance API已有的缓冲，按族给出`coverage`：`navigation`（导航时序）、`resources`（资源数量与体积）、`loading`（FCP）、`contentful`（LCP）可能为available/partial/missing，`interaction`（INP）与`layout`（CLS）固定为not-collected，`notCollected`列出cls/inp/longtask。导航与资源齐全时`status`仍为`partial`，不得读成性能已测或达标。资源耗时合计不是页面总耗时或平均请求时延，零传输量可能是缓存或跨源限制，不直接判快。LCP/FCP为采集时缓冲快照，后续内容变化可能改变结论。完整视觉与体验检查按 `visual-usability.md` 实际执行。

只读观察拿不到交互、布局与长任务，须用当次会话真实提供的性能追踪与审计能力补齐全族，按以下顺序执行：

1. 加载型：开启追踪并刷新页面（冷缓存时注明忽略缓存），读取LCP及其分解（TTFB、渲染延迟）、FCP、CLS，并保留原始追踪文件。
2. 交互型：不刷新、直接开始录制，用真实指针或键盘操作目标控件，停止录制后读取INP；脚本合成的点击不产生可用交互样本，不能替代真实操作。
3. 审计型：用会话提供的审计能力获取无障碍、最佳实践等结论，与只读观察是两套独立证据，同一元素被两边同时命中时可互相印证。
4. 归档：原始追踪与审计报告必须落盘并登记为条目证据；把已采族与未采族写回观察结果或作为桥接的外部证据，避免“采到了却没有进台账”。

采集结果只表达“哪个族有实测数据、取到什么值”，是否达标由阈值与业务判据决定。真实用户分位数需要现场数据或压测样本，单次实验不得外推。缺少追踪能力时明确记录未测族与影响，不用0或“达标”代替未知。

### 条件劣化矩阵

单一条件的一次采集容易被读成“页面性能达标”。按条件格组织，才能看出哪一维在恶化、恶化多少：

- 视口用设备模拟设置，不用宿主窗口尺寸代替；目标移动端须设到真实宽度（如375）并注明设备像素比与触摸模拟。宿主窗口最小宽度不足以达到目标宽度时，改用设备模拟而不是改写目标值。
- 网络与CPU各取两档（如Slow 4G与Fast 3G、1倍与4倍降速），与视口组合成矩阵；每组条件只采集一次，避免同条件多样本混算。冷缓存加载标cold，交互型标warm。
- 加载型与交互型分开读数：加载型读LCP及其分解、FCP、CLS；交互型读INP，且必须先不刷新开录、真实操作、再停录。
- 每格必须保留原始追踪文件与SHA-256。阈值必须写明来源（如Core Web Vitals良好线），没有出处的阈值不得用于判定。
- 用 `scripts/perf_matrix.py` 汇总：`python perf_matrix.py <矩阵数据.json> --output <新矩阵.csv>`。工具校验指标值只能是非负数字或null、同条件不重复、每格证据哈希可核验，导出UTF-8-sig CSV。指标判定（`overall`）与结论（`conclusion`）分开：证据未通过哈希校验时结论一律“待核实”，指标超标也不得作为已证实结论。
- 移动端还需检查文档是否声明 `width=device-width`；缺少时移动浏览器会按默认布局视口渲染再整体缩放，矩阵里的移动格数据必须同时注明这一点。

矩阵结论只覆盖所列条件组合。同一页面在8格加载中全部达标、却在交互格超出INP阈值是常见结果，说明只测加载会漏掉交互问题；报告须按条件分别陈述，不合并成一句“性能通过”。

## 样式与易用性证据串链

真实页面观察 → 核对当前URL/角色/视口/状态 → 实际截图与任务/键盘动作 → 人工复核候选及业务断言 → 关联TC/RUN/EXP → 写正式执行台账与缺陷 → 探索清单更新 → CSV派生视图 → 需要可读交付时渲染HTML报告。每一箭头都保留真实产物和字段对应，不能用手工编造下一阶段输入冒充采集器已串通。候选可用 `observation_bridge.py` 转成待复核条目，再用 `exploration_state.py` 校验与派生视图，两者都不替代人工复核。

## 报告呈现

`scripts/render_report_html.py` 把五视角CSV、门禁JSON、观察/性能/审计JSON渲染成单文件HTML，测试为 `tests/test_render_report_html.py`：

```bash
python scripts/render_report_html.py --output <新的报告.html> \
  --dataset "桌面::<台账.csv>::<门禁.json>" --dataset "窄屏::<台账.csv>::<门禁.json>" \
  --screenshot "<图片>::<标题>::<说明>" --performance <性能摘要.json> \
  --lighthouse <审计报告.json> --evidence-dir <证据目录> --note "<本次未测说明>"
```

数字全部来自台账与采集结果，不做手工改写；截图以base64内联，保证单文件搬移到别处仍能看图；输出拒绝覆盖，输入缺失即报错退出2，不产半成品。多数据集时同一记录按ID合并视角，缺失视角取各数据集的交集，单个数据集的缺口仍在门禁表逐行可见。

HTML是阅读视图，不是数据源：台账CSV与采集JSON仍是唯一事实，报告不替代它们，也不因渲染成功而构成验收通过。面向用户的数据结果仍交付CSV。

截图证据先落盘归档再引用。宿主常限制可写目录：只允许写入受控目录（例如系统临时目录）而拒绝工作区路径时，先写入被允许的目录，再复制到证据目录并核对文件大小与SHA-256；也可直接写入证据目录。复制后的文件才是归档证据，临时目录里的原文件不算交付物。

两条路都不可用（平台拒绝任何写盘）时才降级：内联截图由人工复核并写下判断的具体区域与结论；DOM观察JSON作为可读证据文件登记；观察中明确标注“截图未归档”、复核方式与时间。不得把内联截图描述成已归档附件，也不为落盘绕过平台拒绝或改用未授权路径。降级只降低证据强度，不改变结论措辞。

若路径、截图、浏览器、账号或性能通道受阻，仍完成可做部分，但受影响模块保持BLOCKED/NOT_RUN或候选待核实；单元测试、VM入口、结构注册成功均不能替代真实浏览器采集或专家独立盲测。工具退化时如实交付范围，不自动安装依赖或改配置。


## 诚实记录的出口：`acknowledged_unexplored` 与 `falsify_action`

两个可选字段，解决的是"如实登记反而被罚"这个反向激励：

| 字段 | 用法 | 效果 |
|---|---|---|
| `acknowledged_unexplored` | 挂在 `status=NOT_RUN` 的条目上，`{next_action, owner}` 两项都非空 | 该条**不计入** `ready_pending`、不压结论上限；缺任一项仍按待执行计（不是免罚牌） |
| `falsify_action` | 任意条目，写清"这条观察构造了什么让它坏的条件" | 视角覆盖从"凑够 2 个页面×状态"改为"≥2 种不同构造动作"，二者满足其一即可 |

跨批次纪律：承认项必须在后续批次出现进展；长期 open 且 `next_action` 无变化时升级为风险条目并重新压上限——**罚的是停滞，不是诚实**。

现场怀疑但不是判定时，用结构化出口而不是塞判定字段：

```json
{"id": "EXP-012", "collection_state": "collected",
 "lead": {"suspicion": "审批卡片按钮顺序可疑", "basis": "与同页其他弹窗顺序相反", "where": "审批卡片"},
 "evidence_paths": ["/abs/card.png"]}
```

`lead` 只接受 `suspicion / basis / where` 三个键，夹带 `verdict`/`status` 会被 `scan_collection_records` 判违规。
