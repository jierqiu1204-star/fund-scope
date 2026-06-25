# Design

## Design Principles
- 数据优先：页面第一屏优先放排名、分数、买入观察、今日买点、持仓状态和数据时间。
- 少装饰：移除米色渐变、大圆角和重阴影，用白色面板、浅灰背景、细边框表达层级。
- 高密度：PC 端用更紧凑的标题、按钮、输入框和卡片；移动端继续使用 tab，但减少长卡片和无效滑动。
- 谨慎表达：金融标签只做研究提示，不用强营销视觉暗示“必买”。

## Tokens
- Page background: `#fafafa`。
- Primary text: `#171717`。
- Secondary text: `#4d4d4d` / alpha text。
- Border: translucent black `#0000001a`。
- Accent: `#006bff` only for focus, links and primary state hints。
- Radius: controls 6px, panels 12px, large surfaces 16px。
- Shadow: only `0 2px 2px rgba(0,0,0,0.04)` for raised panels。
- Font: Geist Sans for UI and Geist Mono for tabular numeric/code-like data。

## Shell
- Top navigation becomes compact and sticky.
- Primary routes highlighted by proximity: 短线研究、资产、交易、估值、高级策略、新闻、设置、任务。
- Remove hero-style narrative and large usage-principle card.

## Shared Components
- `Panel`: white surface, 12px radius, thin border, subtle shadow.
- `StatPill`: compact metric block with neutral surface and readable numeric value.
- `EmptyState`: dashed border surface, clear first action, no gradient.

## Short-term Page
- Preserve current data requests and state management.
- Keep “我的持仓” above the short线研究工作台 on desktop.
- Workbench metrics become compact neutral cells.
- Left ranking column stays sticky and scrollable but uses tighter cards and controls.
- Right detail first screen prioritizes conclusion, buy-point logic, observation/holding summary, and data trust context.
- Charts, AI and explanation stay below the first screen and keep existing behavior.

## Accessibility
- Keep visible focus states for buttons, inputs and selects.
- Avoid color-only state; labels remain textual.
- Avoid horizontal overflow at desktop and mobile breakpoints.
