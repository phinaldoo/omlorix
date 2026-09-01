# File Storage

**Admin Settings > File Storage** reports user-owned workspace storage. Storage-provider selection and object migration are managed separately; see [User File Storage](../2_setup/6_1_user_file_storage.md).

## Read the Page

The overview shows **Total storage**, **Total files**, and **Users with files**. Search and page through the user table to review **User**, **Storage used**, **Stored files**, **Limits**, and **Latest upload**.

Shared files count against the original uploader rather than every recipient. **Uploads disabled** means the user's current group does not allow file uploads. A displayed quota comes from that group, so a membership change can change the limit without moving or deleting existing files.

## Investigate Capacity

1. Find the user and compare usage with the displayed limit.
2. Review **Allow file uploads** and **Per-user storage (GB)** in the user's active group.
3. Confirm that the selected storage service and its underlying capacity are healthy.
4. Include generated artifacts as well as ordinary uploads in the investigation.
5. Ask the owner to delete unneeded files, or change the approved group quota.

A quota controls new uploads; it does not automatically delete existing files when usage is already above the limit.

The database view cannot prove that every external object is readable. Monitor and back up the actual storage destination separately, and test representative downloads after storage maintenance or restore.
