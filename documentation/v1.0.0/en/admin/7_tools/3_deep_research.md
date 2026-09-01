# Deep Research

**Deep Research** creates a cited report from a user question. Administrators choose between Omlorix's custom workflow and a supported provider-native workflow.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the research-specific limits below.

## Configure

Open **Admin Settings > Tools > Deep Research** and choose the available execution mode:

- **Custom** uses an Omlorix model together with configured Web Search providers and, when enabled, Code Execution.
- **Google Native** delegates the research workflow to a supported Google AI Studio model.

For **Custom**, select the research model, **Search provider**, and **Scrape provider**. Configure conservative iteration and output limits first. For native research, select a supported provider and model and verify account entitlement.

Then:

1. Enable **Deep Research** on the user-facing models that may start it.
2. Run a narrow, fact-checkable request with a pilot user.
3. Review citations, report quality, files, activity status, time, provider usage, and failure behavior.

## Operations and governance

Research can make many provider, search, scrape, and code-execution calls. One user request may therefore take several minutes and cost much more than a normal chat. Apply provider budgets, rate limits, timeouts, and monitoring.

Web content is untrusted and can contain falsehoods or prompt injection. A cited result still requires verification. Review the data sent to each model, search, scrape, and code service, including user questions, discovered URLs, page contents, and generated files.

Changes to Deep Research settings affect new runs. Existing reports remain with their chat and files until normal retention or deletion removes them.

If a run stops early, check the selected research model, Web Search assignments, Code Execution availability, group access, provider quotas, and outbound policy. Reduce the request scope before increasing time or iteration limits.
