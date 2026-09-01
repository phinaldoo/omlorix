# Todos

**Todos** lets a model read and manage the signed-in user's persistent lists and tasks in **Workspace > Todo**.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify every supported item action.

## Enable and test

1. Select **Todos** on a reliable tool-calling model.
2. Enable it only for groups that may let models change their workspace data.
3. Test listing and searching tasks; creating and editing lists and tasks; completing and reopening tasks; marking and unmarking tasks; moving or reordering items; and the supported bulk actions as a normal user.

The tool supports bulk complete, reopen, move, and tag changes. It cannot delete a task or list; users must perform deletions themselves in **Workspace > Todo**. The tool acts only with the current user's access, but model interpretation can still be wrong. Teach users to be explicit about the list and item and to verify every bulk change. Do not rely on a model prompt as the authorization boundary.

Disabling the tool stops model access; it does not delete existing Todos. If changes appear in the wrong place, check the user's active account and request an explicit list name.
