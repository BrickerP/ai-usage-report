# Token Skyline 实现验收源

## 一句话问题

线性日柱状图中的纪录日会压扁普通日期；需要重新分配展示空间，但不能改变任何 token、费用或筛选口径。

## Keep

- Ground / Record Horizon / Sky 三层语义化布局。
- 动态识别纪录组，不写死日期或 token 数值。
- 纪录日完整值、日期、tooltip、隐藏精确表格和 aria 描述。
- 工具选择、模型下钻、Focused、`Other` 点击和 spend 线同步。
- 复用现有 ECharts、颜色、格式化函数和响应式容器。

## Non-goals

- 不使用 log、sqrt、直接截断或静默丢弃极值。
- 不增加新统计口径、卡片、路由、依赖或动态服务。
- 不重做 Explore 之外的页面结构。

## 验收标准

- [ ] 纪录日的 Ground 段与 Sky 段之和等于原始日 token 总量。
- [ ] 普通日期仍按线性 Ground 刻度呈现，且不被纪录日压到贴地。
- [ ] Sky 使用线性刻度并明确标注 `RECORD SKY`；Horizon 有文字标识。
- [ ] tooltip 在纪录日显示完整原始值，不显示拆分后的内部段值。
- [ ] 点击工具、模型和 `Other` 的行为与之前一致；Focused 同步作用于两层。
- [ ] 桌面与移动端无横向滚动；`prefers-reduced-motion` 下无动态 beacon。
- [ ] 隐藏表格继续提供所有日期和原始数据，作为无障碍与校验出口。
