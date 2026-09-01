# Bulk Import Users

Use **Admin Settings → Users → Bulk import users** to create local accounts from **Excel (.xlsx)** or **CSV (.csv)**. This is account onboarding, not the **Import/Export Users** archive workflow.

## Prepare the file

1. Choose a format and select **Download Template**.
2. Keep the **email**, **first name**, and **last name** template columns and header row unchanged.
3. Add one user per row and remove examples.
4. Verify emails and save as `.xlsx` or `.csv`.

The first non-empty row is the header. Header matching is case-insensitive and spaces are accepted in place of underscores. Unsupported or duplicate columns cause rejection. CSV delimiter and common text encodings are detected automatically.

Files are limited to 5 MB and 10,000 user rows. Test a small representative file before a large production import.

## Import

1. Upload or drop the completed file and select **Import Users**.
2. Under **Generate import passwords**, enter a strong base password.
3. Keep **Force password change** enabled unless an approved onboarding process requires otherwise.
4. Confirm and wait for the result.
5. Copy every generated temporary password immediately; it is shown only once.
6. Correct failed rows and import only those rows again.
7. Verify a representative account before distributing credentials.

Each created user receives a different generated password and joins the configured **Default user group** as an active User. The file cannot assign a different group or role per row; change those afterward when necessary. Accounts are committed row by row, so cancellation or a late failure does not roll back users already reported as created.

Rows are processed independently. Some accounts can be created even when other rows fail, so do not upload the full file again without checking the results and Users list. There is no dry-run mode.

If generated passwords are lost, reset the affected accounts individually. Store and deliver them only through an approved credential channel.

## Common failures

- Missing or unsupported columns, wrong file type, file/row limit exceeded.
- Missing name or email, invalid email, or an email that already exists.
- **Default user group** is not configured under **Admin Settings → Groups → Registration defaults**.
- The base password does not satisfy **Admin Settings → Login** requirements.

Keep the results page until the reported created and failed counts match your reconciliation. The generated-password list is credential material, not an onboarding report.
