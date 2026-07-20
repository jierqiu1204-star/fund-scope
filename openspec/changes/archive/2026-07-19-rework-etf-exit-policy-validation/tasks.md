## 1. Evidence Contract And Mapping

- [x] 1.1 Add a small ETF exit policy classification helper that maps signal types to policy classes.
- [x] 1.2 Add policy-specific KPI names and recommended usage labels for insurance stop, profit protection, trend guard, portfolio exit, and research-only states.
- [x] 1.3 Add tests for policy class mapping and no strong conclusion when evidence is old or insufficient.

## 2. Backend Evidence Output

- [x] 2.1 Extend ETF exit credibility or calibration output with policy class, evidence status, recommended usage, and KPI summary fields using existing JSON payloads.
- [x] 2.2 Ensure validation and calibration runs remain read-only and do not create real alerts, notifications, or tracked-position mutations.
- [x] 2.3 Mark unapproved optimized candidates as research-only and keep live threshold lookup unchanged.

## 3. Frontend Evidence Presentation

- [x] 3.1 Update the ETF strategy evidence page to group exit evidence by insurance stop, profit protection, trend guard, and research-only candidates.
- [x] 3.2 Replace generic “预测成功率” wording with policy-specific explanations.
- [x] 3.3 Show old-contract, insufficient-sample, and candidate-only evidence as not live.

## 4. Validation

- [x] 4.1 Run backend tests covering exit evidence, tracked positions, and domain boundaries.
- [x] 4.2 Run frontend type checking.
- [ ] 4.3 Push the completed change to GitHub and Gitee.
