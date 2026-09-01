# Statistics

**Admin Settings > Statistics** shows operational data for model generations, tool calls, and realtime sessions.

## Model Generation Statistics

Filter the page by period, granularity, provider, and model. Model-generation views report request volume, success and errors, token types, throughput, estimated cost, distribution, and recent errors.

Estimated cost uses configured pricing and recorded usage. It can differ from provider invoices because of pricing changes, discounts, free tiers, unpriced models, taxes, or incomplete usage reporting. Reconcile it before using the result for financial decisions.

**Export Statistics** exports all stored model-generation statistics. It is not limited to the visible period and does not include tool or realtime statistics. **Delete all statistics** permanently deletes model-generation statistics only.

## Tool Statistics

Tool views show calls, success and error rates, distribution, estimated cost where available, and recent errors. The page has no separate bulk export or bulk-delete control for tool statistics.

Treat tool names, failures, and timing as potentially sensitive operational data. Use audit records for accountability; aggregate tool statistics do not by themselves show the full context of an action.

## Realtime Statistics

Realtime views report sessions, turns, interruptions, model use, timelines, and errors.

- **Export Realtime Stats** exports realtime records for the selected period.
- **Delete Realtime Stats** permanently deletes all stored realtime session and turn statistics, regardless of the selected period.

## Interpretation and Retention

An empty result can mean no eligible activity, a filter mismatch, disabled collection, or a new time window. It is not proof that the feature was unused without supporting evidence.

Statistics deletion does not remove chats, feedback, provider billing, audit records, or previously downloaded exports. Restrict access to exports and error details and define a deletion schedule. Use [User Statistics](21_user_statistics.md) only when there is an approved need to attribute usage to people.
