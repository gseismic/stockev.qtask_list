# 设计 Tokens

设计基准：8pt 网格；字阶 1.25 比例；60-30-10 配色分布。**默认白天（浅色）模式**，可切换黑夜（深色）模式（记忆在 localStorage，正式实现建议同时跟随 `prefers-color-scheme`）。两套主题共用同一组语义 token 名，切换只换 token 值；语义色在各自底色上保证正文对比度 ≥ 4.5:1。

## 色彩

### 基础（60-30-10 中的 60/30）

| Token | 白天（默认） | 黑夜 | 用途 |
|---|---|---|---|
| `--bg-base` | `#f6f8fa` | `#0d1117` | 页面底色 |
| `--bg-surface` | `#ffffff` | `#161b22` | 卡片/面板 |
| `--bg-surface-2` | `#eef1f4` | `#1c2129` | 次级面板/表头 |
| `--border-default` | `#d0d7de` | `#30363d` | 描边 |
| `--text-primary` | `#1f2328` | `#e6edf3` | 正文（对比 ≥ 13:1） |
| `--text-secondary` | `#57606a` | `#9198a1` | 次要文字（对比 ≥ 4.5:1） |
| `--text-faint` | `#6e7781` | `#6e7681` | 弱提示/时间戳（对比 ≥ 4.5:1） |

### 语义色（10 + 状态）

| Token | 白天（默认） | 黑夜 | 语义 |
|---|---|---|---|
| `--c-primary` | `#0969da` | `#2f81f7` | 主操作、链接、选中态 |
| `--c-success` | `#1a7f37` | `#3fb950` | completed、healthy |
| `--c-warning` | `#9a6700` | `#d29922` | retry_wait/delay、警告告警 |
| `--c-danger` | `#cf222e` | `#f85149` | dlq/failed、严重告警、危险操作 |
| `--c-muted` | `#6e7781` | `#8b949e` | skipped/cancelled、禁用 |

各语义色配派生 token（随主题切换）：

| 派生 token | 用途 |
|---|---|
| `--*-dim` | 徽章/告警底色（语义色 12% 透明度） |
| `--danger-dim` / `--danger-border` | 危险横幅、危险按钮 |
| `--primary-dim` | 选中导航底色 |
| `--hover` | 行/菜单 hover |
| `--overlay` | 抽屉/弹窗遮罩 |
| `--sk-a` / `--sk-b` | 骨架屏渐变 |

## 状态色映射（任务/队列状态 → 颜色）

两套主题通用，颜色取当前主题的语义 token：

| 状态 | 颜色 |
|---|---|
| ready | primary |
| processing | primary（呼吸动画） |
| retry / retry_wait / delay | warning |
| completed | success |
| failed / dlq / deadline_missed / expired | danger |
| skipped / cancelled / history | muted |

## 字阶（1.25 比例，全站 5 档）

| Token | 值 | 用途 |
|---|---|---|
| `--fs-xs` | 12px | 表格次要列、时间戳、徽章 |
| `--fs-sm` | 13px | 正文（表格主体、表单） |
| `--fs-md` | 14px | 导航、按钮 |
| `--fs-lg` | 18px | 卡片标题、页面节标题 |
| `--fs-xl` | 24px | 页面标题、KPI 大数字 |

数字（计数、时间）统一 `font-variant-numeric: tabular-nums`。

## 间距 / 尺寸（8pt 网格）

| Token | 值 | 用途 |
|---|---|---|
| `--sp-1` | 4px | 图标与文字间隙 |
| `--sp-2` | 8px | 徽章内边距、紧凑行距 |
| `--sp-3` | 12px | 表格单元格 padding、控件内边距 |
| `--sp-4` | 16px | 卡片内边距、区块间距 |
| `--sp-5` | 24px | 页面区块间距 |
| `--sp-6` | 32px | 页面级留白 |

## 圆角 / 描边 / 阴影

| Token | 值 |
|---|---|
| `--r-sm` | 4px（徽章、输入框） |
| `--r-md` | 6px（按钮、卡片） |
| `--r-lg` | 8px（抽屉、弹窗） |
| `--shadow-1` | `0 1px 3px rgba(0,0,0,.4)` |
| `--shadow-2` | `0 8px 24px rgba(0,0,0,.5)`（抽屉/弹窗） |

## 可交互控件尺寸

- 按钮高度：28px（sm，表格行内）/ 32px（md，默认）
- 表格行高：40px（紧凑模式 32px）
- 可点击区域最小 32×32px（桌面端；触屏场景按 44×44 放大）
