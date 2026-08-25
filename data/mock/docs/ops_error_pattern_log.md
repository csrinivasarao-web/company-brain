# Error Pattern Log — Recurring QA Corrections (Last Quarter)

**Owner:** Ops Lead | **Last verified:** 2026-07-08 | **Access tier:** team_restricted_ops

## Top recurring patterns

1. **Payer portal re-authentication backlog.** When a payer portal logs us out and needs manual re-authentication (Tier 2 in the escalation runbook), actual resolution time has been running **up to 48 hours**, not 24, during the last quarter — the manual review queue for re-auth cases has been backed up due to headcount, not a system limitation. This has not yet been reflected back to GTM's customer-facing SLA language.
2. **Denticon payer-mapping mismatches at onboarding.** Root cause of most week-one "not covered" false negatives — matches what CS has separately flagged as a churn driver.
3. **Stale cached plan data.** Occurs when a patient's plan changed within 30 days of verification; QA training manual's 30-day flag catches most but not all of these.

## Action items open with Product

- Re-auth backlog needs either more QA headcount or a faster manual re-auth flow — flagged to Product, not yet resourced.
- Consider surfacing "plan updated in last 30 days" as a customer-facing flag rather than only an internal QA trigger.
