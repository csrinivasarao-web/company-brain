# Payer-Specific Handling Notes

**Owner:** Ops Lead | **Last verified:** 2026-08-17 | **Access tier:** team_restricted_ops

## Delta Dental

Portal times out after 8 minutes of inactivity — voice fallback should trigger automatically, but confirm this is actually enabled per-account, since a few older accounts were onboarded before voice fallback was default-on for this payer.

## Cigna Dental

Requires manual re-auth roughly every 30 days regardless of activity — this is a portal-side session policy, not something on our end. Don't treat this as an error pattern for Cigna specifically; it's expected.

## MetLife

Coverage responses sometimes omit the plan tier field entirely for out-of-network claims — QA should cross-check plan tier against the PMS record rather than treating a missing field as "no tier restriction."

## Marked "unstable integration" (see verification review SOP)

Guardian Dental — portal structure has changed twice in the last six months without notice; treat any Guardian result as requiring QA review regardless of confidence score until this stabilizes.
