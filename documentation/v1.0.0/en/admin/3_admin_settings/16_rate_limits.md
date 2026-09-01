# Rate Limits

**Admin Settings > Rate Limits** controls per-user usage over a calendar period.

## Rule Targets

A rule can limit one of these target types:

- **Models:** requests and, where supported, tokens
- **Tools:** invocations
- **Dictation:** transcription minutes
- **Realtime calls:** minutes

When several models or tools are selected in one rule, they share that rule's counter for each affected user. Create separate rules when each target needs an independent allowance.

Token limits are appropriate only where Omlorix receives reliable token accounting. Use request limits as the dependable fallback for providers or workflows without complete token usage.

## Who a Rule Affects

Select at least one user or group. Group assignment is evaluated from the user's current membership, so moving a user can change which rules apply. The same user can match more than one rule.

An active direct overlap for the same selected model, tool, or feature and the same directly selected user or group blocks saving. Modify the assignments or targets, combine the intended allowance into one rule, or deactivate the superseded rule before saving. Review groups as well as users when resolving an overlap.

## Period and Reset

Choose **Per Day**, **Per Week**, or **Per Month** and the rule's **Time Zone**. Resets follow the calendar boundaries in that time zone, not a rolling interval. Communicate the time zone to users who work elsewhere.

Inactive rules remain stored but are not enforced. Deactivation does not erase past usage; reactivate only after verifying the current period and allowance.

## Safe Workflow

1. Define the resource, metric, audience, allowance, period, and timezone.
2. Check existing rules for direct and group-based overlap.
3. Save the rule inactive when preparing a future policy.
4. Activate it and test with an ordinary affected user.
5. Monitor usage and denial notifications before broad rollout.
6. Edit or deactivate the rule when capacity changes; delete only when its history and configuration are no longer needed.

Rate limits are guardrails, not billing controls. Provider-side quotas, service failures, and access settings can still deny a request before an Omlorix allowance is exhausted.
