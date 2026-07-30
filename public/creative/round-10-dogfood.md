# Round 10 — 视觉减法 Dogfood

验证环境：本地 production build preview，使用当前发布的 `usage.json`。

## 任务 1：默认进入页面，快速找到图表

在 771 × 863 视口中对比改动前后默认页面。

| 指标 | 改动前 | 改动后 | 变化 |
| --- | ---: | ---: | ---: |
| 页面高度 | 2584 px | 1777 px | -31% |
| 图表起点 | 1892 px | 1126 px | 提前 766 px |
| 工具卡区域 | 1081 px | 622 px | -42% |
| 工具卡模型行 | 24 | 0 | 全部移除 |
| 默认可见按钮 | 18 | 14 | -4 |

结果：通过。首屏仍按“页头 → 总览 → 四张工具卡 → 时间 → 图表”的原结构排列，但图表显著提前。

## 任务 2：展开低频信息，再走核心交互

1. 展开 `Report details`，确认完整生成时间、时区、机器、来源状态、token 与成本口径存在，`aria-expanded=true`。
2. 展开 `Custom dates`，确认日期、Earlier、Later、Reset 存在，`aria-expanded=true`。
3. 重新加载后选择 One API。
4. 聚焦 `deepseek-v4-flash`。
5. 检查 URL、pressed series、图形描述与隐藏精确表。

结果：

- URL 为 `?tool=oneapi&model=deepseek-v4-flash`。
- pressed series 的 accessible label 明确包含模型、tokens、Focused 与再次激活会清除。
- 图形描述仍说明其他模型变暗、费用跟随 focused 模型。
- 精确表费用列为 `deepseek-v4-flash spend`。
- 卡片模型行仍为 0；完整模型列表通过 `All 14 models` 可达。

## 任务 3：移动端阅读

在 390 × 844 视口检查默认状态：

- 横向溢出为 0。
- 四张工具卡完整存在。
- Report details 与 Custom dates 默认收起。
- 图表工具按钮为两列，每个宽 155 px。
- 浏览器 console error 为 0。

结果：通过。移动端仍需要纵向浏览四张卡，但没有横向溢出、截断式高级控件或永久模型库存。

## 验证限制

浏览器截图接口在改动前连续超时，因此本轮视觉比较使用完整 DOM snapshot、受限布局测量与真实交互状态作为证据；没有把截图失败当成通过依据。

## 下一刀

先停止新增交互。后续若继续优化，应基于真实使用观察决定是否压缩移动端 token breakdown，而不是在本轮继续扩大范围。
