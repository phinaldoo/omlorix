# Skills

**Skills** lets a model read the signed-in user's own skills and prepare a new skill draft for review. It does not silently install drafts or administer all instance skills.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify the draft-review boundary below.

## Enable and test

1. Enable Skills for the intended groups and select **Skills** on a reliable model.
2. Test listing and reading a user-owned skill.
3. Ask the model to draft a harmless skill and confirm that the user must review it before installation.

Skills can add durable instructions and resources to later chats. They may include sensitive context or instructions that influence tool use. Require users to review source, permissions, referenced files, external destinations, and prompt-injection risk before installing or sharing a draft.

Disabling the tool stops model-assisted access and drafting; it does not delete installed or saved skills. Administrator-managed skill policy remains separate.
