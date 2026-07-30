# Round 9 — 图表状态只说一次

## 假设

工具、模型与 Focused 状态被 breadcrumb、标题、副标题、badge、series 数值、Focused 标签、说明条、Reset 与 Esc 提示重复表达。置信度：95%。

## 自问

为了“状态明确”，是否应该把所有反馈都保留？

## 自答与选择

- 全保留：每个提示都正确，但用户要从多处重复文本中寻找真正可操作的状态。
- 只保留颜色：最安静，却会损害色觉障碍用户与首次使用者的理解。
- 保留一个可见状态源，加上完整的无障碍语义：breadcrumb 表示工具层级，标题旁的 `Focused · model · Clear` 表示模型状态，pressed series 表示选择。

选择第三种。

## 落地

- 标题缩短为 `Daily usage` 或 `{tool} models`。
- Focused 状态与 Clear 合并到标题旁的一行；无数据状态也在这里表达。
- 移除 identified / unattributed badges、重复 Reset、可见 Esc 提示、series 数值和第二条 Focused 解释。
- `All N models` 保留为唯一模型详情入口；`All tools` breadcrumb 保留为唯一返回入口。
- series token 数量没有消失，而是进入每个按钮的 accessible label。
- `aria-pressed`、live region、图表 ARIA 描述、方向键与 Escape 行为保持不变。

## 反思

状态明确不等于状态重复。一个可见事实配一个直接操作，比五处一致但分散的提示更容易扫读；完整键盘说明继续通过隐藏帮助和 `aria-keyshortcuts` 提供。
