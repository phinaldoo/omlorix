# Privacy Policy and Terms of Service

Use the legal-page shortcuts under **Admin Settings > Security** to publish the documents shown to users. Omlorix supplies the editing and enforcement controls; your organization remains responsible for accurate content and legal review.

## Privacy Policy

1. Select **Edit Privacy Policy**.
2. Update the **Markdown Editor**.
3. Under **Policy Change Notice**, choose **No notice** or **Dismissible modal**.
4. Add a short **Notice message (optional HTML)** only when needed.
5. Save and verify the rendered page and notice.

Saving publishes a new Privacy Policy revision. A dismissible modal informs users about that revision but does not record acceptance. Keep optional HTML minimal and trusted, and test links, headings, keyboard use, and mobile layout.

**Show privacy notice link** under **Admin Settings > Login** controls the login-page link.

## Terms of Service

Select **Edit Terms of Service**, update the **Markdown Editor**, and save. Saving publishes a new Terms revision.

Enforcement is configured under **Admin Settings > Login**:

- **Show terms of service link** controls the login-page link.
- **Require terms acceptance during signup** applies to new signups.
- **Block app access until terms are accepted** requires signed-in users to accept the current revision.

A newly saved revision can therefore require users to accept again when blocking is enabled. Test signed-out, signup, and existing-user behavior after every Terms change.

Administrators can inspect the user's recorded legal state under [Users](4_1_users.md). Do not mark acceptance on a user's behalf; the user must complete the applicable acceptance flow.

## Publishing Checklist

- identify the operator or controller and a contact route
- describe data categories, purposes, recipients, transfers, retention, deletion, security logging, and user rights accurately
- reconcile the text with IP Analytics, model providers, tools, file storage, backups, authentication, email, and telemetry
- preserve the approved revision and effective date outside Omlorix
- announce material changes through an appropriate channel

Do not place credentials, private contract material, or internal incident details on a public legal page.
