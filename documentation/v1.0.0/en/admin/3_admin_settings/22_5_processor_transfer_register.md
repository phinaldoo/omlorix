# Processor & Transfer Register

Maintain an operator-owned register for every third party that receives, hosts, or can remotely access Omlorix data. This is an operational template, not legal advice. The responsible privacy or legal owner must classify each relationship.

Create separate records when the same vendor serves different purposes, products, accounts, or regions.

| Record | What to capture |
| --- | --- |
| Service and owner | Omlorix feature, product, contracting entity, tenant or account, internal owner, and status |
| Purpose and people | Specific purpose, necessity, and affected users or visitors |
| Data flow | Data categories, source, destination, frequency, returned data, and onward recipients |
| Location and role | Processing, storage, support, and remote-access countries; processor or controller classification |
| Contract and transfer | Terms, processing agreement, subprocessors, transfer mechanism, assessment, and supplementary controls |
| Security and retention | Protection, access control, provider retention, deletion or return procedure, and incident contact |
| Evidence and review | Approvers, document versions, test evidence, last review, next review, and removal evidence |

## Build the Register

1. Inventory enabled LLM providers, provider groups, speech and realtime services, search, tools, MCP servers, Service Connections, OAuth, SSO, LDAP, email, IP location, backups, file storage, webhooks, and update checks.
2. Add hosting, database, cache, proxy or CDN, DNS, monitoring, support, and administrator access.
3. Trace real flows for prompts, chats, files, audio, images, identifiers, IP addresses, usage data, logs, and credentials.
4. Identify onward routing. Gateways, custom tools, Agents, and user-managed connections can reach additional services.
5. Verify current regions, contracts, subprocessor lists, provider retention, training or data-use choices, deletion paths, and security controls.
6. Obtain the required technical, security, privacy, and business approval before production use.
7. Reconcile the register with legal pages, architecture records, incident handling, account deletion, backups, and restore.

Review a record before enabling a service and whenever its endpoint, region, routing, data scope, contract, subprocessors, or retention changes. Review again after incidents and on the organization's governance schedule.

When removing a service, record the date, disable the integration, revoke credentials, request provider-side deletion where required, and retain evidence. Keep secrets and private contracts in a restricted evidence store, not in this register.
