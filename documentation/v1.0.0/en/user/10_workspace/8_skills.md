# Skills

Skills are reusable instructions with optional bundled resources that help a model follow a specialized workflow. They guide the model but do not add permissions or guarantee a correct result.

## Create, import, and use

Select **Create Skill** and provide **Skill name**, **Short description**, **Skill instructions**, and any optional **Compatibility**, **License**, or **Metadata** shown. After creation, **Edit** can add files under **Scripts**, **References**, and **Assets**.

You can also select **Import Skill** to **Upload Files** or **Paste Markdown**. The file workflow accepts one or more `.md` files, up to 1 MB each, with valid Skill frontmatter and instructions. It imports each Markdown definition only; it cannot infer or bundle accompanying Scripts, References, or Assets from a local folder. Review the validity badge, preview, instructions, metadata, and any failure for each item before importing. If an external link opens **Unverified skill import**, review its source and **Skill Content Preview** and cancel unless you trust both. Imported or shared Skills can contain unsafe instructions or scripts.

Open a Skill to inspect its Instructions, Details, and Bundled resources. A **Managed Skill** is provided by your organization and can be used but not edited by you.

In chat, add a Skill through `@`, or attach it to an Agent or Automation. Review the final request and the Skill's bundled resources before using it with sensitive data or powerful tools.

## Share

Select **Share**, then create a **Link** or **Invite Users** in one of these modes:

- **Clone:** independent copy.
- **Live:** synchronized, read-only Skill.
- **Collaborate:** synchronized Skill that recipients can edit.

Review **Active Shares** and their subscriber counts, and stop obsolete links. Only the owner can delete the shared source. Removing a shared Skill from your Workspace ends your subscription; it does not delete the owner's source. A Live or Collaborate Skill can change later, while a Clone does not.

See [Skills in Chats](../4_chat_conversations/7_skills.md) and [Skill Draft Widget](../4_chat_conversations/9_skill_draft_widget.md).

For a complete Omlorix-to-Omlorix move, **Download Everything** includes owned Skill records and their bundled Scripts, References, and Assets. Account import recreates those owned Skills and attempts accepted shared-Skill subscriptions when the source still resolves. A supported source share identifier can remain on an imported Skill; a conflicting identifier can make the Skill section fail. Inspect **Active Shares** immediately. For a cross-instance move, revoke any carried link and create a new destination-specific link. Then verify imported bundles and every Agent or Automation dependency before use.
