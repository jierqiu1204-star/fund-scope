## 1. Filter State And Matching Logic

- [x] 1.1 Define label filter state for buy-observation labels, entry timing labels, and tracked-position labels in `/short-term`.
- [x] 1.2 Add helper functions to derive an asset's active observation label, entry timing label, and tracked-position state from existing response fields.
- [x] 1.3 Apply label filters before client-side display while preserving the selected sort mode and existing mode/direction/search/range filters.
- [x] 1.4 Reset pagination to page 1 when label filters, mode, direction, search keyword, ETF range, or sort mode changes.
- [x] 1.5 Add empty-state handling for “no assets match selected labels”.

## 2. Geist-Style Filter UI

- [x] 2.1 Add a compact `标签筛选` control with selected-count text.
- [x] 2.2 Render grouped checkbox options for 买入观察, 今日/盘中买点, and 持仓状态.
- [x] 2.3 Render selected filter chips with single-chip remove behavior.
- [x] 2.4 Add quick filters for `稳妥观察`, `高位谨慎`, `只看持仓`, and `清空`.
- [x] 2.5 Keep “实时综合排序” as a sort option but visually separate sorting from label filtering.

## 3. Mobile And Accessibility

- [x] 3.1 Ensure mobile layout uses a compact full-width filter panel or equivalent non-overflow layout.
- [x] 3.2 Ensure filter controls are keyboard accessible and have visible focus states.
- [x] 3.3 Ensure active filters are readable without relying on color alone.
- [x] 3.4 Verify labels do not use direct buy wording such as `可以买` or `推荐买入`.

## 4. Verification

- [x] 4.1 Run `corepack pnpm exec tsc --noEmit`.
- [x] 4.2 Run `corepack pnpm build:static`.
- [x] 4.3 Manually verify `/short-term` desktop: selecting `短线观察 + 健康回踩` filters the list and preserves sorting.
- [x] 4.4 Manually verify `/short-term` mobile: filter panel does not cause horizontal overflow and can be cleared.
- [x] 4.5 Verify current ranking, detail, tracking, and email alert behavior are unchanged.
