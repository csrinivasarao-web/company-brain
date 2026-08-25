# Customer Escalation Runbook

**Owner:** GTM Customer Success | **Last verified:** 2026-08-16 | **Access tier:** team_restricted_gtm

## Escalation tiers

- **Tier 1 (self-serve delay):** portal timeout, auto-retried by the system. No customer-facing action needed.
- **Tier 2 (payer portal re-auth required):** the payer's portal has logged us out and needs manual re-authentication. Per our customer-facing SLA, these are resolved within **24 hours** of being flagged.
- **Tier 3 (data discrepancy):** QA has flagged a mismatch between payer data and PMS records. Escalate directly to the QA lead; no fixed SLA, depends on payer responsiveness.

## What to tell a customer who asks about delays

For Tier 2 issues specifically, it's safe to tell a customer the 24-hour SLA applies. If a customer reports a Tier 2 delay beyond that window, escalate to Ops immediately rather than re-promising the same timeline.
