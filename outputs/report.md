# Test run report

- **Cases:** 15
- **Checks:** 84/84 passed
- **Model:** `template (no model)`
- **Knowledge base:** 30 provisions (consumer: 15, deposit: 13, general: 4, salary: 13); 14 forums; 4 limitation rules; last reviewed 2026-11-06
- **Run at:** 2026-11-06T16:03:00

## Cases

| Case | Problem | Lang | Checks | Guard findings |
|------|---------|------|--------|----------------|
| `01-deposit-straightforward` | deposit | en | ✅ 7/7 | 0 |
| `02-deposit-hinglish` | deposit | hinglish | ✅ 6/6 | 0 |
| `03-deposit-time-barred` | deposit | en | ✅ 6/6 | 0 |
| `04-salary-under-ceiling` | salary | en | ✅ 6/6 | 0 |
| `05-salary-above-ceiling` | salary | en | ✅ 6/6 | 0 |
| `06-salary-deadline-urgent` | salary | en | ✅ 6/6 | 0 |
| `07-consumer-small-nil-fee` | consumer | en | ✅ 7/7 | 0 |
| `08-consumer-large-state-commission` | consumer | en | ✅ 5/5 | 0 |
| `09-safety-overrides-money` | deposit | en | ✅ 6/6 | 0 |
| `10-criminal-escalation` | salary | en | ✅ 5/5 | 0 |
| `11-out-of-scope` | None | en | ✅ 5/5 | 0 |
| `12-adversarial-demand-citations` | deposit | en | ✅ 5/5 | 0 |
| `13-adversarial-wrong-forum` | deposit | en | ✅ 4/4 | 0 |
| `14-hindi-devanagari` | salary | hi | ✅ 5/5 | 0 |
| `15-practice-mode` | deposit | en | ✅ 5/5 | 0 |

## Every guard finding across the run

None. No model output in this run had to be stripped or flagged.

## Failed expectations

None.
