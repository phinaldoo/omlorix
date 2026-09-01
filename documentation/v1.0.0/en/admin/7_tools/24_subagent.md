# Subagent

**Subagent** lets one model delegate a bounded task to another accessible chat-capable model or saved Agent. Activity and the completed transcript are embedded in the parent chat rather than stored as a separate sidebar conversation or independent Subagent-run workspace.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the delegation-specific checks below.

## Configure and pilot

1. Select **Subagent** on the parent models that may delegate.
2. Limit which models or Agents can be selected where the form offers an allowlist. The user picker allows up to 20 targets and omits media-, transcription-, and speech-only models.
3. Confirm that intended users can access both the parent and delegated model or Agent.
4. Apply concurrency, usage, and provider budgets.
5. Test a small delegation, a denied target, a timeout, and a provider failure.

Delegation is an additional generation with its own provider, cost, context, instructions, tools, and data destinations. The delegated target must not gain access merely because the parent can use it. Review both targets' group access and tools.

At most six Subagent calls can run concurrently for one parent generation. A direct-model prompt or saved-Agent task is limited to 50,000 characters, as is the optional extra context. The full parent chat up to the call point is already supplied, so do not duplicate it in the context field.

The delegated model uses its own saved tools, not the parent's runtime tool overrides. Its **Subagent** tool is always removed, which prevents recursive delegation. A saved Agent also receives its own instructions, Skills, and accessible reference assets. If no targets appear, check target access, active status, the 20-target user selection, allowlists, and the user's effective groups.

User and administrator account archives carry completed Subagent history inside the containing chat. Review chat portability and retention together; there is no separate Subagent-run export lifecycle.
