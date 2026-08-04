# Token Skyline：外部参照与差异化方向

## 对照物

| 参照物 | 解决什么 | 可借鉴 | 我们不做的 |
| --- | --- | --- | --- |
| [GitHub Skyline](https://github.blog/developer-skills/application-development/how-we-built-the-github-skyline-cli-extension-using-github/) | 把个人贡献历史变成可生成、可收藏、甚至可打印的 3D 城市 | “个人历史 = 一件有形的城市纪念物”；数据不只是报表，而是身份物件 | 不把 AI token 变成 3D 打印项目；不引入新的城市导航或重型 3D 场景 |
| [GitHub Readme Stats](https://github.com/anuraghazra/github-readme-stats) | 用 SVG 卡片快速展示个人统计、主题和 README 嵌入 | 可分享的单一数字、清晰的主题角色、点击进入详情 | 不做通用 stats card、主题选择器、缓存端点或多卡片拼贴 |
| [WakaTime Embeddable Charts](https://wakatime.com/developers) | 将个人时间统计安全地嵌入公开页面 | “公开展示也要保留数据边界”；图表可作为个人身份的一部分 | 不复制 WakaTime 的仪表盘语言，不把页面变成编辑器时间报表 |
| [The Pudding visual prototypes](https://the-pudding.github.io/observable-starter/) | 用视觉叙事让数据按章节讲故事 | 先定一个隐喻，再让交互服务故事，而不是让控件堆叠 | 不加入滚动长叙事、旁白卡片或与 Explore 无关的章节 |
| 线下博物馆展柜 / 纪念碑 | 让一个纪录成为可被凝视、被记住的对象 | 展签、展台、唯一高光、留白和“这件东西属于谁”的感觉 | 不做拟物化金属边框、奖牌图标、廉价勋章 UI |

## 一句话定位

> **把 AI 使用历史做成一座可阅读的个人 token 天际线：普通日子构成街廓，最高纪录成为穿出画面的纪念碑。**

## 具体构图草案

```text
Explore / TOKEN SKYLINE                                  [All time]
┌──────────────────────────────────────────────────────────────┐
│  RECORD HORIZON ────────────────╱───────────────  peak layer  │
│                         ╭──────────────╮                     │
│                   20.6B│  RECORD TOWER│  07–20             │
│                        ╰──────┬───────╯                     │
│                               │ beacon                      │
│  ─────────────────────────────┼────────────────────────────  │
│  ▂ ▃ ▅ ▂ ▄ ▆ ▃ ▂ ▄ ▃ ▅ ▂ ▃ ▂ ▄ ▅ ▂ ▃ ▂ ▄ ▃ ▂ ▅ ▂ ▃        │
│  01  03  05  07  09  11  13  15  17  19  21  23  25  27     │
│  ground / daily streetline                                  │
└──────────────────────────────────────────────────────────────┘
```

### 固定空间

- **顶部 28%：Sky / Record layer**。只展示动态 peak group 的完整上段，最多 1–3 根纪录塔；峰值数字常驻。
- **中间 8%：Record Horizon**。金色细线 + 单个折角符号；旁边只写一次 `same tokens / split linear view`，不放长解释。
- **底部 64%：Ground / Daily streetline**。所有日期保留，普通柱体采用现有堆叠色和轻微材质高光。
- **右上角**：`206亿` 级别的短展签，不新增 hero KPI；它只属于纪录塔。

### 数值语法

- Ground y 轴标签：动态显示 `0 / 1B / 2B ...` 的线性刻度，范围由非纪录层的最大值和固定 headroom 计算。
- Sky y 轴标签：动态显示 `record floor / peak / +headroom`，仍然是线性刻度。
- 断轴处必须有清晰的折角和文字，不使用模糊的 `…`。
- 任何超过 Ground 范围的日期都进入 Sky；tooltip 仍显示原始完整 tokens 和各堆叠分项。

### 交互语法

1. 默认静止：纪录塔只做一次很短的 beacon 呼吸，不自动循环发光。
2. Hover / keyboard focus 某天：该日塔基与上段同时升起细金线；其它日期只降低对比度，不改变高度。
3. Focused 模型：只改变颜色饱和度和灯光，不改变两个 band 的坐标；模型详情仍在原有 panel 中出现。
4. 工具下钻：标题从 `TOKEN SKYLINE` 变为现有工具 / 模型语义，纪录塔自动重算，不保留旧峰值。
5. Reduced motion：取消 beacon 和升起动画，只保留静态天际线、展签和 aria 摘要。

### 移动端

- 不做横向滚动，不把塔压成缩略图。
- 顺序变成：纪录塔（约 34%）→ Horizon（约 8%）→ 日常街廓（约 58%）。
- 日期只保留每 3–5 个一个 tick，其余日期通过 tooltip / focus 读取。
- 纪录数字移到塔顶左侧，避免被窄屏裁切；Ground / Sky 的线性单位仍各自可见。

## 借鉴结论

1. 从 GitHub Skyline 借“历史是可收藏的个人物件”，把 token 使用从报表提升为身份表达。[GitHub Skyline](https://github.blog/developer-skills/application-development/how-we-built-the-github-skyline-cli-extension-using-github/)
2. 从 README Stats 借“一个明确的展示入口”，但拒绝多卡片、主题参数和缓存服务的复杂度。[GitHub Readme Stats](https://github.com/anuraghazra/github-readme-stats)
3. 从 WakaTime 借“公开数据要有清晰边界”，不在客户端暴露采集凭据；本项目继续使用现有静态 usage 数据口径。[WakaTime API Docs](https://wakatime.com/developers)
4. 从 The Pudding 借“先定叙事，再安排交互”，因此本设计只保留一条视觉主线：从街廓看到纪录塔。[The Pudding](https://the-pudding.github.io/observable-starter/)
