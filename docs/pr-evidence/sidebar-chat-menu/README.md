# Sidebar chat menu spacing proof

Idle desktop chat titles previously kept 40px of end padding for an invisible
menu button. The fix reduces that to the normal 8px when the row is idle and
read, recovering 32px for the title. Hover, keyboard focus, open menus, unread
markers, and non-hover/coarse-pointer layouts retain their action space.

The screenshots show the [isolated fixture](preview.html) running production
`createChatRow`, title and unread helpers, icons, fonts, and styles. Sample chats
are local; backend requests are deliberately left pending. This is not an
authenticated end-to-end chat session.

- [Before](before.png): sidebar CSS from `f7f967e59`, with the unnecessary gap.
- [After](after.png): idle titles use the available row width.
- [Hover](hover.png): only the hovered row reveals its right-aligned menu.

Captured with the T3 collaborative Chromium preview at 620 × 465 CSS pixels
(2× display scale). All screenshots were visually inspected.

## Reproduce

From the repository root, run `python3 -m http.server 18769 --bind 127.0.0.1`
and open <http://127.0.0.1:18769/docs/pr-evidence/sidebar-chat-menu/preview.html>.
Move the pointer outside the sidebar for the idle state, then hover a long
title. Tab from a chat link to its menu button and press Enter to open it.
For the baseline, replace the sidebar stylesheet with the version from
`git show f7f967e59:frontend/css/chat/sidebar.css`.

## Validation

- `npm run test:frontend`: 1,325 passed.
- `npm run lint:js`: passed.
- Browser measurements: idle title ends 8px from the row edge; hover restores
  40px padding and an opaque, clickable trigger with no title overlap.
- Pinned rows and project sidebar styling recover the same space; RTL uses
  the correct logical end edge. Unread rows retain their 40px action space.
- Tab reaches the native menu button and Enter opens its dropdown. The open
  dropdown keeps the trigger visible and 40px padding after blur/pointer exit.
- Focus styling was checked by temporarily substituting a matching attribute
  for `:focus-within` in the browser CSSOM: this background preview did not
  have OS focus. Non-hover styles were checked by enabling the existing
  non-hover media rules and disabling fine-pointer rules in CSSOM. Both keep
  40px padding and visible menu controls; touch hardware was not tested.

No application strings or persistent data changed.
