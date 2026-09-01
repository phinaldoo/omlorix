# Subagent Delegation

Subagent Delegation lets a capable model ask another available model or saved Agent to work on a bounded part of a request. It is useful when subtasks can be checked independently.

## Choose allowed targets

Select **Delegation: Automatic** in the message box when it is available. Keep **Any accessible target** or choose up to 20 specific models and Agents that are appropriate for the task and data. The picker uses your current accessible-model catalog and omits image-, video-, audio-, music-, transcription-, and speech-only targets that cannot run a chat task.

Then describe the overall goal, separable subtasks, evidence to use, and how results should be checked and combined. Useful patterns include independent review, one source or option per run, and draft–critique–revision.

The parent model decides whether to delegate and which allowed target to use. Selecting targets does not force a Subagent run; make the split explicit in your request when it matters.

## Review the work

Open a **Subagent** card to see its transcript, status, tool activity, and visible results. Ask the parent to compare evidence, resolve conflicts, and identify unsupported claims before accepting its synthesis.

Subagent targets can have different capabilities, instructions, tools, service terms, and usage. Keep delegated work narrow and select only targets authorized for the information involved. More delegation can increase time and usage without improving the answer.

Subagent activity, completed state, events, failures, results, and artifacts remain embedded with the parent chat. They are not separate chats in your sidebar or a separate Subagent-run workspace. A complete account archive carries this history as part of the chat. See [Understanding AI Models](../5_models/1_understanding_models.md), [Data Control](../6_privacy_data/2_data_controls.md), and [Usage Limits](../3_user_settings/14_usage_limits.md).
