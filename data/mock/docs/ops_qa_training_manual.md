# QA Specialist Training Manual — Eligibility Verification

**Owner:** Ops Lead | **Last verified:** 2026-08-13 | **Access tier:** team_restricted_ops

## What gets routed to a human QA specialist

Not every verification needs manual review. A transaction is routed to a QA specialist when any of the following occur:

- The portal-automation confidence score for the coverage result is below 90%.
- The payer portal returned conflicting information across two lookups in the same session.
- The patient's plan was updated within the last 30 days (higher chance of stale cached data).
- The account is flagged "high-touch" per the customer's onboarding notes.

## Review process

1. Pull up the automated result alongside the raw portal/voice transcript.
2. Confirm coverage percentage, plan status, and any exclusions against the payer's own language, not just the parsed summary.
3. If confirmed correct, mark "QA approved" and release to the customer.
4. If incorrect or ambiguous, mark "QA corrected" and log the discrepancy in the error-pattern log so Product can see the trend.

## Common new-hire mistakes

Trusting the automated confidence score over the raw transcript when the two disagree — always read the actual transcript for anything routed to you; the fact that it's in your queue at all already means the system wasn't fully confident.
