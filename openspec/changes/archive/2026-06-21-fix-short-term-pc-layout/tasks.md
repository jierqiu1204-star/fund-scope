## 1. Frontend Layout

- [x] 1.1 Remove detail-height synchronization state, observer, and CSS variable.
- [x] 1.2 Make the PC ranking panel sticky with viewport-bounded internal scrolling.
- [x] 1.3 Keep the desktop detail column in normal document flow.

## 2. Detail Navigation

- [x] 2.1 Add refs for summary, charts, advisor, explanation, and tracking sections.
- [x] 2.2 Add a compact desktop detail navigation bar.
- [x] 2.3 Scroll PC selection to the summary section after the selected detail renders.
- [x] 2.4 Preserve mobile behavior: selecting an asset opens the detail tab.

## 3. Ranking Card Density

- [x] 3.1 Reduce desktop ranking cards to a compact decision summary.
- [x] 3.2 Avoid six-column text wrapping in the narrow left panel.
- [x] 3.3 Keep detailed explanations in the right panel.

## 4. Verification

- [ ] 4.1 Run `corepack pnpm exec tsc --noEmit`.
- [ ] 4.2 Run `corepack pnpm build:static`.
- [x] 4.3 Screenshot `/short-term` on desktop and mobile.
- [x] 4.4 Verify no horizontal overflow.
