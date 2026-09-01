# Automation Webhooks

An Automation webhook lets an external service start one Omlorix Automation. It is an inbound trigger, not a destination for Omlorix notifications. Use it only with a trusted service that can protect credentials.

## Create a webhook

Create or edit an automation, choose **Webhook**, then select a **Payload mode**:

- **Append payload to prompt** adds the request body after the saved prompt.
- **Use template variables** places incoming values where your prompt requests them.
- **Ignore payload** runs the saved prompt without the request body.

Optionally include selected request headers in the context. Include only headers the model genuinely needs; never include authorization, cookie, or other credential headers.

Select **Generate webhook credentials**, then copy the **Webhook URL** and **Secret** immediately. The Secret is shown only when created or rotated. Store both in the external service's protected credential settings and use the example request shown in Omlorix as a guide. Do not test with real sensitive data first.

Keep both **Automation active** and the webhook **Enabled**. A received delivery can still fail while the model runs, so check **Recent deliveries**, the generated chat, and [Workspace Notifications](../10_workspace/3_notifications.md).

## Manage access

Use **Rotate secret** if the Secret may have been exposed; update the external service because the old Secret stops working immediately. Use **Disable webhook** to stop external deliveries without deleting the Automation.

Changing the trigger to **Schedule** or deleting the automation stops webhook delivery. Neither action deletes earlier chats nor reverses external actions from earlier runs.

Send only the data the Automation needs. A successful delivery only means the trigger was accepted; inspect **Recent deliveries** and the resulting chat for model or tool failures. Never place the Secret in the prompt, request body, a screenshot, or a shared document.
