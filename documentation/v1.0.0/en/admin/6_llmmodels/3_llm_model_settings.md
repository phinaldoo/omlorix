# Model Settings

The model editor shows settings relevant to the selected provider and model. Availability in the form is not a guarantee that the upstream service supports the option.

## Identity and access

- **Name**, **Description**, and **Icon** are user-facing. Make the intended purpose and limitations obvious.
- **Active** controls whether the model can be selected.
- Group access determines who can see and use it.
- **Default model** is configured at instance level; keep it broadly available and reliable.

## Context and output

- **Input token limit** caps how much conversation and attached content Omlorix can send for this model. It must not exceed the provider model's real context capacity.
- **Output token limit** caps generated response tokens, not the full request. The upstream service can impose a lower limit.
- Attachment count and size settings are additional limits; storage, group, proxy, and provider limits can be lower.
- Generation settings such as temperature, top-p, penalties, seed, verbosity, and stop sequences should stay at provider defaults unless there is a tested need. Some combinations are invalid for reasoning models.

## Instructions and titles

Use the model's chat instructions for stable, model-specific behavior. Project, Agent, skill, user, and conversation context can add or override instructions, so do not treat this field as a security boundary.

Configure automatic chat titles with a small, reliable model when possible. A title-generation model must remain accessible and funded even if the chat uses another model.

## Capabilities

Enable only capabilities supported and tested on the exact model:

- input and output types;
- files, images, audio, video, PDFs, or documents;
- reasoning or thinking controls;
- function tools and MCP;
- provider-native search or other native tools;
- prompt caching, storage, service tier, or provider routing.

For Omlorix Web Search, turn on **Web Search**, select compatible **Search provider** and **Scrape provider** assignments, and test both a search query and a direct public page. **Native web search** is separate and follows the model provider's own controls.

## Tools, skills, and MCP

Selecting a tool makes it available to the model; it does not bypass group policy, user permissions, connection access, or tool-specific configuration. Keep the set small so tool selection remains reliable and the model receives only the necessary tool descriptions.

Skills and MCP servers can add instructions, data, and external actions. Review their trust, access, costs, and data destinations before assigning them to a model. Use the [Tool Rollout Checklist](../7_tools/0_tool_rollout.md) for every newly assigned tool.

## Provider-specific settings

Provider sections can include reasoning detail, safety controls, routing, local-inference settings, cache behavior, storage, and media support. Follow the provider guide and upstream documentation. If a request starts failing after a setting change, disable the newest optional controls and retest with text only.

## Verification checklist

Test as an intended user with:

1. a short and a long text conversation;
2. every enabled input type;
3. reasoning controls, if shown to users;
4. each tool and connection;
5. title generation and regeneration;
6. quota, timeout, and provider-error behavior;
7. statistics and provider billing.

Recheck models after provider upgrades, model alias changes, or imports. Model exports carry settings and references but do not remap destination IDs; follow the complete [model import procedure](2_manage_llmmodels.md#import-and-export).
