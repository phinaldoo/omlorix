# User Statistics

**Admin Settings > User Statistics** attributes model and tool usage to individual users and groups. This is more privacy-sensitive than aggregate [Statistics](20_statistics.md).

## Before Enabling

Define the purpose, lawful basis or consent where required, access rules, notice, retention, deletion, and who may use the result. The enablement dialog requires an explicit compliance confirmation. That confirmation records an operator decision; it is not legal approval.

## Choose Who Is Tracked

- **Track All Users** attributes new eligible activity for every user.
- When it is off, use **Add User** and **Tracked Users** to maintain a selected audience.

Removing a user from **Tracked Users** or disabling User Statistics stops new attribution. Existing attributed statistics remain until removed by the applicable deletion process. Do not promise erasure based only on disabling collection.

## Review the Results

The overview shows **LLM Requests**, **Tool Calls**, **Tokens**, and **Est. Cost** for the selected period. User and group details can break results down by time, model, provider, category, token type, result, and tool.

Estimated cost has the limitations described under aggregate Statistics. Group results reflect recorded attribution and membership context; changing today's membership does not necessarily rewrite past records.

## Recommended Workflow

1. Begin with selected pilot users rather than **Track All Users**.
2. Verify the notice, approved purpose, access, and expected fields.
3. Compare totals with aggregate Statistics and provider billing.
4. Test removal from tracking and the separate deletion procedure.
5. Set a review date and retention schedule.
6. Expand only when attribution is necessary and reliable for the stated purpose.

Do not export, share, or use attributed data more broadly than the approved purpose. For the interaction with account deletion and backups, see [Post-Deletion Retention](22_6_post_deletion_retention.md).
