# Subagent Delegation

Subagent Delegation lets a capable model ask another available model or saved Agent to work on a bounded part of a request. It is useful when subtasks can be checked independently.

## Choose allowed targets

Open **Model Settings** and find **Delegation targets** when the selected model supports Subagents. Keep **Any accessible target** or choose up to 20 specific models and Agents that are appropriate for the task and data. Selections apply immediately. The picker uses your current accessible-model catalog and omits image-, video-, audio-, music-, transcription-, and speech-only targets that cannot run a chat task.

Then describe the overall goal, separable subtasks, evidence to use, and how results should be checked and combined. Useful patterns include independent review, one source or option per run, and draft–critique–revision.

The parent model lists the allowed models and saved Agents, then starts a target using its returned ID. Discovery does not require an exact model-name search. The parent model decides whether to delegate and which allowed target to use. Selecting targets does not force a Subagent run; make the split explicit in your request when it matters.

## Review the work

Subagent cards use the same layout as Canvas file cards, with the model name, current status, and an **Open**/**Hide** button. Select **Open** to see its transcript, status, tool activity, and visible results in the right-hand details panel. The parent conversation stays usable beside it on desktop. The panel reuses the Canvas layout and resizable border. Its tabs switch between Subagent transcripts only; Canvas and other artifacts keep their own toolbars. Use their result cards to switch between artifact previews and Subagent transcripts. Arrow keys, Home, and End navigate the tabs, and the focused resize border supports arrow keys, Home, End, and Enter to restore the default width.

Each Subagent card’s **Open** button opens its tab. **Hide** closes the panel; opening another card switches to that transcript. Closing the panel does not stop delegated work. New agents appear as tabs without taking focus, and completed or failed runs remain available. Each transcript retains its reading position, and background output is shown when you return to its tab. Open generated artifacts explicitly from their cards.

On screens up to 900 pixels wide, the same panel fills the screen. Use **Back to chat** to return to the parent conversation and its draft. Switching tabs does not create a separate chat or a composer for messaging the Subagent directly.

Ask the parent to compare evidence, resolve conflicts, and identify unsupported claims before accepting its synthesis.

Subagent targets can have different capabilities, instructions, tools, service terms, and usage. Keep delegated work narrow and select only targets authorized for the information involved. More delegation can increase time and usage without improving the answer.

Subagent activity, completed state, events, failures, results, and artifacts remain embedded with the parent chat. They are not separate chats in your sidebar or a separate Subagent-run workspace. A complete account archive carries this history as part of the chat. See [Understanding AI Models](../5_models/1_understanding_models.md), [Data Control](../6_privacy_data/2_data_controls.md), and [Usage Limits](../3_user_settings/14_usage_limits.md).
