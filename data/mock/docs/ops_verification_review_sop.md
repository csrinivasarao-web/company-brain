# Verification Review SOP — Escalation vs Auto-Approve

**Owner:** Ops Lead | **Last verified:** 2026-08-09 | **Access tier:** team_restricted_ops

## Auto-approve criteria (no human review)

- Portal confidence score 95%+ AND plan not updated in last 30 days AND account not flagged "high-touch."

## Escalate to a human QA specialist

- Anything not meeting the auto-approve criteria above (see QA Training Manual for the full routing logic).

## Escalate to the QA lead directly (skip standard queue)

- Any case where the payer's stated coverage contradicts the practice's PMS record for the same patient (a data discrepancy, Tier 3 in the escalation runbook).
- Any case involving a payer we've marked "unstable integration" in the payer-coverage matrix (see structured data).

## Documentation requirement

Every QA-corrected verification must include a one-line reason code (e.g., "stale cache," "portal misread plan tier," "payer data lag") — this is what feeds the error-pattern log and, downstream, gap detection for the knowledge base.
