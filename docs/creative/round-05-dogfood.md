# Round 5 — 真实任务 Dogfood

验证环境：本地 production build 预览，数据为当前发布的 `usage.json`。

## 任务 1：选择工具 → 聚焦模型 → 图表同步

1. 进入 One API 模型视图。
2. 聚焦 `deepseek-v4-flash`。
3. 检查可见标题、状态播报、序列按钮、图形描述和隐藏精确表。

结果：

- URL 为 `?tool=oneapi&model=deepseek-v4-flash`。
- 模型按钮为 pressed，并显示 Focused。
- 其他模型仍保留但弱化；费用列标题变为 `deepseek-v4-flash spend`。
- ECharts 图形描述明确说明聚焦模型、其他模型弱化及费用跟随。

发现并修复：ECharts 的 ARIA 组件会改写 chart host 的无障碍名称。将完整 Focused 语义写入 `aria.description` 后复验通过。

## 任务 2：键盘连续探索与退出

1. 在 focused 模型按钮上按 `ArrowRight`。
2. 确认焦点移动到 `grok-4.5`，但 `deepseek-v4-flash` 仍保持选择。
3. 按第一次 `Escape`。
4. 确认模型聚焦清除、One API 工具视图保留，URL 收敛为 `?tool=oneapi`。
5. 再按一次 `Escape`。
6. 确认回到所有工具，URL 恢复为根路径，焦点落在 `Usage chart exploration` 区域。

结果：通过。焦点移动与选择改变彼此独立，Esc 按预定层级回退。

## 任务 3：刷新恢复视图

1. 直接打开 `?tool=oneapi&model=deepseek-v4-flash&range=7`。
2. 检查 7 days、One API、Focused 模型和图形描述。
3. 刷新页面后再次检查。

结果：刷新前后 URL 和四项状态均保持一致；日期显示为当前数据末尾的 7 天。

## 结论

核心旅程已经闭环：

> 选择工具 → 查看模型 → 聚焦模型 → 图表与精确数据同步 → 键盘分层退出或刷新恢复

## 架构审查后的负向补测

审查发现“全量范围有模型、最近 7 天无该模型”的边界。当前发布数据中至少可由 Codex `gpt-5`、Claude Code `claude-fable-5-high`、One API `Auto` 触发。

修复后只读校验确认：

- 三个模型在最近 7 天都继续生成明确的 Focused series；
- tokens 与 spend 均为 0，不会退回工具总费用；
- 工具 totals 与其余模型汇总仍守恒；
- 清除 Focus 后，焦点回到命名的图表区域，不落到页面 body。
