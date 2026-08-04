# 图表下钻交互 Landscape（方案 A）

## 调研问题

在保留当前工具级时间序列概览的前提下，怎样让用户进入某个工具后看见模型种类与对应数据，同时避免 One API 的 14 个模型长期占据图例和画布？

本项目当前模型规模：

- Codex：6 个模型
- Claude Code：7 个模型
- Cursor：5 个模型
- One API：14 个模型，其中若干模型在当前范围内只有约 2K tokens

因此，“完整数据”与“全部 series 常驻”不是一回事。前者必须满足，后者会重新制造图例分页和视觉噪音。

## 对照物

| 名称 | 解决什么 | 痛点 / 限制 | 可借鉴 | 我们不做的 |
|---|---|---|---|---|
| [OpenAI API Usage Dashboard](https://help.openai.com/en/articles/10478918-api-usage-dashboard) | 在相同 Usage 入口中切换项目、时间区间和聚合方式；Usage 详情支持分钟级粒度。官方的详细导出还允许按 model、project、user、API key、batch、service tier 分组，并导出 CSV。[来源](https://help.openai.com/en/articles/20001072) | 更偏组织级运营与账单核对；模型维度主要通过筛选、Group by 和导出进入，不是“点工具后原位拆成模型”的消费级交互。界面也需要处理权限、项目和 UTC 等组织语义。 | 把 model 当作同一份时间数据的“聚合维度”，而不是新增永久面板；概览保持聚合，精确数据可按需展开或导出。 | 不引入项目、API key、service tier 等组织筛选器；不把页面改成报表生成器。 |
| [Anthropic Console Usage / Cost](https://support.anthropic.com/en/articles/9534590-cost-and-usage-reporting-in-console) | 同一 Usage 图按 workspace、model、month、API key 过滤；点击柱子可以进入小时、分钟粒度；图中保持 input/output token 语义。官方 Usage API 同样允许按 model 分组，并在 `1d / 1h / 1m` 时间桶间切换。[来源](https://platform.claude.com/docs/en/manage-claude/usage-cost-api) | 模型是顶部选择器，而不是从“Claude Code”这一级自然进入；选择器适合单一供应商，但放到当前四工具页面会增加常驻控件。 | 点击已有图形后，在原位置改变粒度，同时保留时间范围；进入模型态后要有明确的当前工具名和返回路径。 | 不增加一排全局模型筛选器；不复制 Usage 与 Cost 两套近似页面。 |
| [Langfuse Custom Dashboards](https://langfuse.com/docs/metrics/features/custom-dashboards) | 同一 widget 可选择 metric、dimension、filter、chart type；dimension 支持 model 与 time，官方预置 Cost / Usage dashboard 也可复制后修改。其 metrics 文档明确支持按 model 分解 cost、latency 和 token volume。[来源](https://langfuse.com/docs/metrics/overview) | 自助 BI 能力强，但 widget 编辑、布局、过滤器与多层聚合会把轻量个人报告变成分析工作台；高维度直接分组也会产生过多 series。 | 数据层应支持“同一图、换一个分组维度”；模型下钻状态是工具态图表的重聚合，而不是第二套数据口径。 | 不做 widget builder、拖拽布局、保存视图、任意维度组合。 |
| [Grafana Metrics Drilldown](https://grafana.com/docs/grafana/latest/visualizations/simplified-exploration/metrics/drill-down-metrics/) | 从宽泛指标进入一个选中指标，再用 Breakdown 按 label-value 拆成时间序列；筛选、选中指标和时间范围可以保留。Grafana 的 data link 也能把当前 series 与时间范围带到详情视图。[来源](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-data-links/) | 为高基数可观测性设计，完整的 sidebar、tabs、filters 和 related views 对本项目过重；官方也提示高基数环境会遇到索引上限和性能取舍。 | 采用“先总览、再选中、后 Breakdown”的信息节奏；下钻必须继承当前日期范围，返回后恢复原工具概览。 | 不做查询语言、属性侧栏、相关指标、书签、跨页面导航。 |
| CSV + 透视表（常见替代方案，非产品来源事实） | 导出逐日模型数据后，以日期为行、模型为列建立透视表；可以完整核对每个模型，也能临时排序和筛选。 | 离开当前页面、缺少连续探索；列数随模型增长，14 个模型已经需要横向滚动；很难快速从异常日期回到模型来源。 | “完整精确表”应是按需能力：用户确实需要核对时，所有模型和值都能看到。 | 不把透视表永久嵌进首页，不让表格成为默认阅读路径。 |

## 近期趋势信号

以下是从当前官方文档得到的产品方向，不代表这些产品都实现了完全相同的交互：

1. **聚合维度正在成为一等能力。** OpenAI 的详细 Usage 导出允许按 model 等维度分组；Anthropic 的 Console 与 Usage API 都把 model、时间桶作为可切换维度；Langfuse widget 也把 model 与 time 作为 dimension。共同信号是：模型视图应从同一份数据重聚合，而不是新造统计口径。
2. **“概览 → 选中 → 分解”比全量常驻更常见。** Grafana v12 已将 Drilldown 作为正式能力，Metrics Drilldown 的核心就是选中一个指标后再进入 Breakdown，而不是在首页同时画出所有 label value。[来源](https://grafana.com/docs/grafana/latest/whatsnew/whats-new-in-v12-0/)
3. **时间上下文需要跨层级保留。** Anthropic 点击柱形后进入更细时间粒度；Grafana data links 可携带当前 series 与 time range。对本项目而言，点击工具后不应重置 7d / 30d / All 范围。
4. **完整性越来越多通过按需明细保障。** OpenAI 提供按维度导出的精确数据，Grafana 允许继续筛选具体 label value；这支持“默认画面做摘要、需要时看全量”，而不是把每个低占比项永久画成一条 series。

## 可直接借鉴的交互原则

### 1. 下钻不换页面

- 默认仍是四工具时间序列。
- 点击工具的柱段或图例，原图进入该工具的模型态。
- 日期范围、hover 日期和费用辅轨保持不变。
- 图表标题区只出现一个轻量 breadcrumb：`全部工具 / One API`；点击“全部工具”返回。

### 2. 摘要可见与精确查看分开

模型态不应永久绘制 14 条独立 series。建议设置 **最多 6 条可见 series**：

1. 当前日期范围内 tokens 最高的 4 个已识别模型；
2. `Legacy unknown` 若非零，固定作为中性 series 保留；
3. 其余模型合并为 `其他 N 个模型`。

若 Top 4 之外只剩 1 个已识别模型，直接显示它的真实名称；不制造
`其他 1 个模型` 这种会掩盖身份的伪分组。

这只是视觉摘要，不截断数据：

- 标题区显示 `14 个模型`，让模型总数始终可见；
- 点击 `14 个模型` 或 `其他 N 个模型`，按需展开完整模型清单；
- 清单列出每个模型在当前日期范围的 tokens、占比和费用，默认按 tokens 降序；
- hover 某一天时，tooltip 列出当天全部非零模型的精确值，不只列 Top 4；
- 用户从完整清单选择一个小模型后，可临时将它提升为独立 series；未选择时不占据图例。

这样同时满足：

- **扫一眼**：画布上最多 6 条 series，不再分页；
- **知道种类**：模型总数与完整名称清单可见；
- **精确核对**：每个模型、每天的值均可按需查看；
- **不丢数据**：`其他` 只是画法，不改变 totals，也不删除低用量模型。

### 3. 视觉层级稳定

- 工具态沿用当前四工具颜色。
- 模型态使用所选工具的主色作为家族色，靠明度 / 纹理 / 直接标签区分模型；`Legacy unknown` 始终使用中性色。
- `其他 N 个模型` 使用低强调度聚合色，并明确带数量，避免被误解成真实模型名。
- 不为每个模型创造跨工具的永久全局颜色，否则 32 个模型会形成无法记忆的色表。

## 必须避开的坑

1. **把 Top N 当成数据过滤。** 图上可以汇总，但完整清单、tooltip 和总计必须包含所有模型。
2. **点击后丢失日期上下文。** 用户通常是先看到某天异常，再追问模型来源；重置时间范围会中断这条路径。
3. **用 14 个 legend item 证明“展示完整”。** 这只会把当前 9-series 的分页问题放大。
4. **再加一套模型 dashboard。** 会“抛弃当前设计”，也违背模型是工具下一层的既定层级。
5. **让 `Legacy unknown` 混入普通“其他”。** 它表达的是数据识别边界，应保持独立可见。

## 一句话定位

**它不是一个可配置的 LLM BI 面板，而是一张能从“哪种工具”原位深入到“哪些模型”的个人用量时间图：默认克制，按需完整。**

## 对方向 A 的校准结论

方向 A 值得继续，但应从“下钻后把全部模型都变成常驻 series”修正为：

> 点击工具后，同一张图原位进入模型态；画布显示 Top 4 + `Legacy unknown` + `其他 N 个模型` 的摘要，所有模型名称与精确数据通过按需清单和逐日 tooltip 完整可见。

这个方案最接近 Anthropic 的原图粒度变化、Grafana 的 context-preserving Breakdown，以及 OpenAI / Langfuse 的同数据换维度，同时保留本项目当前页面的轻量感。
