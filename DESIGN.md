# AI Usage: The Endless Run — UI 重构方案

- 状态：**提案；尚未改变运行时代码或线上页面**。
- 日期：2026-10-31；代码基线：`a43211b`。
- 本文件是 UI 方案的唯一入口，替换已经与实现脱节的六阶段 guided-loop 说明。
- 当前仍运行模型 archive world；下文“目标”均待实施，不代表已经发布或验证。

## 目标与范围

保留深蓝底、奶油白数字、工具固定配色、像素边缘和少量路线标记。正文和数值优先可读；token、成本和纪录不代表生产力、贡献、质量或游戏得分。

最终只保留一条路径：

`时间范围 → 工具 → 模型 → 同一套 token / 成本趋势 → 精确记录`

不增加普通/游戏模式，不保留旧跑道备用入口，不新增图表库、游戏引擎、通用 dashboard 配置或状态框架。

## 当前代码地图

实际入口：`src/main.tsx → App → loadUsage() → public/usage.json → range / tool / model → UsageCharts`。这是静态数据前端，没有需要新增的在线后端 API。`docs/` 是 Vite 构建输出，不能放手写方案或当源码修改。

| 文件或机制 | 当前责任与问题 | 最终处理 |
| --- | --- | --- |
| `src/App.tsx`，1208 行 | 加载、URL、日期、工具、模型、焦点和全部 JSX 集中 | 保留持久状态与编排，提取三个有独立责任的 UI 区块 |
| `src/index.css`，2639 行 | 基础样式后叠加 Pixel Night Run，响应式相互覆盖 | 合并为唯一主题与唯一组件声明，不再末尾追加覆盖 |
| `.tool-grid` 手机规则 | 前面单列，后面同权重覆盖成两列 | 390px 使用紧凑单列工具行 |
| `.lifetime-hero` | 手机 `min-height: 520px` | 内容自然高度，以 padding 控制节奏 |
| `UsageCharts.tsx`，859 行 | 峰值分两个 token 坐标区；模型 focus 提前返回跑道 | 连续 token 轴；focus 只改变数据 scope |
| `RunArchiveWorld.tsx` / `lib/runLevel.ts` | runner、camera、signature、landmark 和独立日期操作 | 整条删除；day 检查融入同一分析图 |
| 旧 DESIGN 六阶段旅程 | 后续 6bae0fa 已改成 playable archive，说明未同步 | 删除旧契约，改为本方案的单一浏览链路 |

## 页面顺序与交互

### 1. 顶栏、当前范围总览

顶栏保留身份、更新时间、降级源数量和一个 Report details 入口。

主数字覆盖所有工具，明确标为 `All tools · <range>`：

- 默认无参数仍为 All，展示 lifetime recorded tokens。
- `?range=7/30/90` 展示与 Explore 相同 visible 数据的总量；lifetime 总量和跨度降为旁注。
- `from/to` 展示自定义区间，不能显示成 All。
- 工具/模型的局部总量在图表标题展示，不能悄悄改变页面主数字的工具口径。

这是对旧“hero 永远为 lifetime”呈现规则的有意调整。默认范围、数据计算和 URL 格式保持现有规则。删除独立全历史首屏图；All 时主图本身就是全历史，不再另设第二张 lifetime 图。

### 2. 时间与唯一工具选择器

顺序固定为 `7 days / 30 days / 90 days / All / Custom dates`，随后才是工具卡。日期继续使用已有 Astryx DateRangeInput；Earlier / Later / Reset 留在自定义展开区。

桌面四张紧凑卡，手机四条单列工具行。默认只展示名称、当前范围 token、成本与数据状态。卡片整体作为一个选择动作，保留文本选中态、可见焦点和 `aria-pressed`。All tools 清除工具及模型筛选。

覆盖范围长说明与 token parts 在所选工具详情中展示。删除 skyline 可点击图例和主图内第二套 tool selector，不在四张卡中重复常驻长说明。

### 3. 模型与唯一分析区

选中工具后展示模型选择；复用现有 top-four、Other 与完整列表的守恒逻辑。完整列表按需原位展开，手机不要求横向拖动。All models / Clear 清除模型筛选。

同一分析区始终包含：

1. 当前范围、工具、模型与局部总量。
2. 零基线、连续线性坐标的 token 趋势图。
3. 共用日期、独立 USD 轴的成本趋势图。
4. 可通过鼠标、键盘、触摸操作的精确日期检查。

All tools 按工具堆叠；选中工具按已有模型分组堆叠；focus 精确模型后仅展示该模型，token 与 cost 同时过滤。成本继续独立子图，避免双轴误读。

纪录只在主图标出真实日期和数值。移除峰值上下分区、顶部浅色全边框和 run signature。长范围保留全部日级数据，抽样日期刻度，通过同一日期检查区查询精确值；需要缩小范围时使用既有日期控件。本次不新增独立缩放状态、断轴、对数或备用模式。

`day` 深链保留，含义是图表中已提交的选中日期，不再依赖关卡。连续日期模型把未发布日期表示为 null，区分“无记录”与真实零值。模型在当前范围无记录时保留明确空态。

仅当 tool 与 model 有效，且 day 是当前 visible range 日历跨度内的真实日期时保留它。范围内缺失发布行仍保留 day，检查区显示 unavailable/null。malformed、范围外、缺少有效 tool/model，或切换范围后被排除的 day 从 canonical URL 清除；不能自动提交另一个默认日期。悬停预览与已提交选中日期保持区分。

精确记录使用可见的原生 disclosure，打开语义化 table；不再只藏在无障碍文本中。保持各 scope 的精确 token、成本与可用分类数据。

### 4. 唯一说明区域

Report details 只有一个实例。顶栏按钮展开该区域，包含 source freshness、工具覆盖、token/cost methodology、machines 与 timezone。工具行只露状态，完整原因在这里说明。

保留 loading、error retry、empty、stale、reduced-motion、forced-colors、Escape 层级与焦点恢复。用户触发选择才滚动/转移焦点；直接打开范围或模型链接时不强制跳动页面。

## 布局草图

```text
AI USAGE / LIVE ARCHIVE                 Updated…  3 delayed  Report details

16.24B RECORDED TOKENS                  Lifetime 340B · 272 recorded days
All tools · Last 7 days · 2026-10-25 — 2026-10-31
[7 days] [30 days] [90 days] [All] [Custom dates]

[Codex 3.24B] [Claude Code 8.60B] [Cursor 790.88M] [One API 3.60B]
Models: [All models] [top models…] [All models and details]

Codex / gpt-6.1-sol · 当前范围与局部总量
Token trend       连续坐标、真实纪录标记
Cost trend        同日期，USD 独立坐标
Selected date     精确 token / cost / parts

Exact records ▸
Report details ▸
```

上述快照数字仅说明布局，不进入生产 JSX/CSS。

| 布局 | 桌面 | 390px 手机 |
| --- | --- | --- |
| Hero | 内容自然高度，lifetime 弱化 | 移除固定 min-height |
| 工具 | 四张紧凑卡 | 四条单列行，约 56–72px |
| 模型 | 可换行，完整列表原位展开 | 同一组件纵向展开，无横向拖动 |
| 图表 | token/cost 日期对齐 | token 约 260px；减少刻度，不缩小正文 |
| 操作 | 键盘、Escape、一次状态播报 | 触摸目标至少 44px，无 hover 专属数据 |

主要布局断点统一为 500px 与 900px。主题 tokens、组件默认样式、对应 responsive 规则各有唯一归属，不靠 !important 或第三层 override 修复。

## 最小代码重构图

只拆真实责任，不拆 ToolCard/ModelRow 等单层包装，不增加通用 section、全局 store 或配置注册表：

| 文件 | 最终职责 |
| --- | --- |
| `App.tsx` | 加载/重试、URL hydration/sync、range/tool/model/day、页面编排 |
| 新 `components/ReportOverview.tsx` | 当前范围 hero、次级 lifetime、更新时间与唯一说明区域 |
| 新 `components/ExploreControls.tsx` | 范围预设/自定义日期、唯一工具选择器与工具说明 |
| 新 `components/ModelExplorer.tsx` | 模型选择/详情、focus/clear、键盘焦点、组合 UsageCharts |
| `components/UsageCharts.tsx` | 一份 ECharts 生命周期、token/cost、选中日期与可见精确 table |
| `lib/usage.ts` / `interaction.ts` | 保留数据选择、模型守恒、日期、URL 与键盘纯函数 |
| `lib/chart.ts` | 颜色、record 与小型数据建模函数；ECharts option 留在组件 |
| `lib/chronicle.ts` | 保留 summarizeLifetime；删除独立 skyline 消失后无人调用的 period/几何辅助逻辑 |
| `index.css` | 在原文件收敛 reset、唯一主题和上述区域样式 |

图表接口收敛为 `daily / selectedTool / modelSelection / focusedModel / selectedDay / onSelectedDayChange`。删除 `runLevel / onSelectTool / onOpenModelList`；图表不承担工具和模型导航。

删除清单：`UsageSkyline.tsx`、`RunArchiveWorld.tsx`、`lib/runLevel.ts`、`tests/run_world.test.mjs`；它们的 imports、状态、CSS、camera/runner/signature 及旧旅程术语同步移除。删除 chart.ts 的 PeakGroup/findPeakGroup/stackSegment 双区域辅助函数，保留仍用到的 record/color 函数。

已按真实引用确认：`scripts/generate_readme_cards.mjs` 独立实现卡片汇总，不依赖 chart.ts、chronicle.ts 或上述三个页面组件。chronicle.ts 的 summarizeLifetime 仍服务页面总览，其余只服务 skyline 的辅助函数与测试随孤立入口一起删除。删除旧机制不能连带影响确定性 SVG 卡片。

## 数据和发布边界

- 仍从 `public/usage.json` 读取。采集、合并、冻结历史、定价、发布和 source freshness 口径不因 UI 改动而变化。
- 保留四工具互斥总量、模型守恒、缓存子集、成本精度及估算披露；不把 `$0` 当作免费，也不把缺失记录填成真实零消耗。
- 保留 `tool/model/range/from/to/day` 与无参数 All 默认；删除旧视觉机制不增加兼容页、重定向或数据迁移。
- README light/dark SVG 继续固定 560×160，由现有脚本生成；UI 不改变卡片入口或发布目标。
- 数字、日期、成本、纪录、geometry 均从同一发布数据派生，禁止硬编码当前快照。

## 实施顺序与验收

以一个完整 UI CR 交付最终单一路径，中间提交按责任划分，不在主分支保留双实现：

1. 提取三个 UI 区块，保留数据与 URL 状态；局部 disclosure/refs 留在组件。
2. 新顺序与唯一 CSS 一起落地，移除独立 skyline 和重复 selector。
3. 统一 token/cost 图与 day 检查，删除 archive world 和双区域逻辑。
4. 删除旧契约测试，补行为验收并更新实现状态。

保留有价值的纯函数测试：日期边界、模型/账本守恒、token/cache/cost、URL round-trip、Escape。删掉仅匹配外立面、CSS 字符串、禁止 axis 文案或强制模型替换图表的实现镜像断言。

新增必要测试验证 focus 后 token/cost 来自同一模型，以及 day 对正常日、零值日、缺失日的 round-trip、无 tool/model 时清除、非法日期与范围切换失效规则。

代码落地后直接推远端开 CR，**不在本地编译**。远端 CI 执行现有 collector tests、frontend tests、production build。UI CR 再加入 390px/1280px 浏览器验收，覆盖范围切换、工具→模型→清除、day 深链、键盘、降级状态和无横向溢出；验证计算后的布局，而非 CSS 字符串。

对比发布账本与各范围总量、模型合计、成本和日期，核对生成 README SVG 与 Pages 产物。远端检查及浏览器验收完成前，不将状态改为已实施/已验证。

本次只提交设计整理，未运行本地构建、测试或 lint。可点击布局草图是方案演示，不是重构完成证据。
