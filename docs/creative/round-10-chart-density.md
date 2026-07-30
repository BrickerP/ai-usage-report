# Round 10 — Tooltip 只解释眼前的图

## 假设

Tooltip 枚举全部原始模型和每个工具的 token 分解，会在 One API 等场景中变成一张遮住图表的长清单。置信度：95%。

## 自问

Tooltip 应展示全部底层数据、只展示当前 focused 值，还是展示图中真实绘制的层级？

## 自答与选择

- 全部底层数据：完整但与 `All models` 明细重复，也无法快速对应柱段。
- 只展示 focused 值：聚焦时清楚，未聚焦时缺乏比较上下文。
- 展示图中绘制的层级：所有工具视图一行一个工具；模型视图一行一个 plotted series；Focused 时只显示该模型与工具总量。

选择第三种。Tooltip 与图形一一对应，完整明细仍在模型详情和隐藏精确表中。

## 落地

- 模型 tooltip 从 `modelSelection.series` 取值，不再遍历当天全部 raw models。
- Focused tooltip 只显示 focused 模型和独立的工具总量，零用量仍明确显示 0。
- 所有工具 tooltip 删除重复的 input/cache/output 子行。
- 轴标题缩短为 `Tokens / day` 与 `Spend / day`；日期标签缩短为月日并启用 overlap 抑制。
- Tooltip padding、圆角、阴影和 chart 高度轻量收敛；数据数组、series id、费用线和 ARIA/精确表未变。

## 反思

Tooltip 的职责是解释指针下的图形，不是替代完整报表。把“所有明细”留在可主动打开的位置，可以同时提高扫读速度和精确数据可达性。
