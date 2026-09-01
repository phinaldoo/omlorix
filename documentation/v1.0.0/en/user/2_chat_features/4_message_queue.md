# Message Queue

The Message Queue stores later requests while the current response is running, so you can plan the next turn without interrupting the current one.

Write the next request and select the queue action. The item keeps its text, attachments, selected `@` context, model settings, delegation targets, and intended chat or Split Screen destination.

Open **Queued Messages** to:

- select an item to return it to the message box for editing;
- move it up or down;
- remove it;
- **Pause queue** or **Resume queue**;
- **Send next now**; or
- **Clear queue**.

The next item sends automatically after a successful response. Stopping a response, an error, or moving to a different chat pauses the queue so that messages are not sent to the wrong place. A failed item can be reviewed and retried instead of silently skipped.

Queued requests can become outdated after you read the earlier answer. Review, reorder, edit, or remove them before resuming. In Split Screen, confirm whether each item targets **Left**, **Right**, or **Both**.
