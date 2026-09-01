# Skills

**Admin Settings > Skills** manages reusable instructions and supporting files. Skills can be available for user selection or enforced by a model.

## Create a Skill

1. Select **New Skill** to open **Create Skill**.
2. Enter **Name** using lowercase letters, numbers, and hyphens.
3. Add a clear **Description** and the full **Instructions**.
4. Choose an **Icon** and save.
5. Reopen the saved skill to add supporting files and test it.

The description should explain when the skill belongs in a conversation. In **Edit Skill**, supporting files are organized as **Scripts**, **References**, and **Assets**. Keep them narrowly relevant; their contents can be used by the skill, and scripts are executable material that must come only from a trusted source.

## Import and Export

**Import managed skills** accepts pasted Skill Markdown, multiple Markdown files, or an Agent Skills ZIP. A ZIP must place each `SKILL.md` inside its own matching skill folder. Selected packages are imported independently, so one invalid or conflicting package can fail while valid siblings remain created.

**Export managed skills** downloads valid managed-skill packages and bundled files as an Agent Skills ZIP; it can contain confidential instructions, scripts, or reference material. Group assignments, model fixed-skill references, Agents, and Automations are not part of that ZIP and imported skills receive new local IDs. Reassign those dependencies on the destination and verify that the ZIP contains every expected skill folder before relying on it as a migration copy.

Do not import an untrusted skill without reading it. Instructions can influence model behavior and may encourage tool calls or disclosure even though they do not grant permissions by themselves.

## Group Assignment and Fixed Skills

Assign managed skills in the **Skills** section of [Groups](5_groups.md). A user can select an assigned skill only while the applicable group access remains available.

A model's **Fixed skill** is applied to every generation with that model, and users cannot remove or override it. Use fixed skills only for requirements that truly belong to every request through that model. Test ordinary prompts, tool use, files, and refusal behavior before broad rollout.

Tool, model, provider, and connected-service permissions remain separate security boundaries. A skill cannot grant access that the user lacks.

## Change Control

When editing a shared skill:

1. export or copy the current approved version
2. review changes to **Title**, **Description**, **Instructions**, **Icon**, and supporting files
3. test with a restricted audience
4. identify groups that assign it and models that use it as a fixed skill
5. announce behavior changes before wider use

Deleting a skill permanently removes its supporting files and automatically removes the skill from group assignments, model fixed-skill settings, user Agents, and Automations. Review and replace those dependencies first; otherwise affected models run without the fixed skill and saved workflows lose that skill context.
