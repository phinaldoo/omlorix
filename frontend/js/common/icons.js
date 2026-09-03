// Provider entries use project-owned Omlorix artwork or a neutral service icon.
// No third-party provider logo artwork is distributed by this registry.
const OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON = "<svg width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"none\" xmlns=\"http://www.w3.org/2000/svg\" class=\"icon\" aria-hidden=\"true\" focusable=\"false\"><path d=\"m6.3 9.1 7.4-3.7m-7.4 5.5 7.4 3.7\" stroke=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/><circle cx=\"4.5\" cy=\"10\" r=\"2\" stroke=\"currentColor\" stroke-width=\"1.33\"/><circle cx=\"15.5\" cy=\"4.5\" r=\"2\" stroke=\"currentColor\" stroke-width=\"1.33\"/><circle cx=\"15.5\" cy=\"15.5\" r=\"2\" stroke=\"currentColor\" stroke-width=\"1.33\"/></svg>";
const OMLORIX_PROVIDER_SERVICE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 512 512" class="icon omlorix-provider-icon" aria-hidden="true" focusable="false"><path d="M104.45 343.5A175 175 0 0 1 301.29 86.96m123.75 123.75A175 175 0 0 1 168.5 407.55" fill="none" stroke="currentColor" stroke-width="32" stroke-linecap="round"/><circle cx="379.74" cy="132.26" r="50" fill="currentColor"/></svg>';
let Icons = {
    omlorix: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512" aria-label="Brand icon"><path d="M104.45 343.5A175 175 0 0 1 301.29 86.96m123.75 123.75A175 175 0 0 1 168.5 407.55" fill="none" stroke="#000" stroke-width="32" stroke-linecap="round"/><circle cx="379.74" cy="132.26" r="50"/></svg>',
    user: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20"><path fill="currentColor" d="M10 1.66A4.17 4.17 0 1 1 10 10a4.17 4.17 0 1 1 0-8.34M10 3a2.83 2.83 0 1 0 0 5.66A2.83 2.83 0 1 0 10 3M2.33 17.5V16A4.67 4.67 0 0 1 7 11.33h6A4.67 4.67 0 0 1 17.67 16v1.5a.67.67 0 0 1-1.34 0V16A3.33 3.33 0 0 0 13 12.67H7A3.33 3.33 0 0 0 3.67 16v1.5a.67.67 0 0 1-1.34 0"/></svg>',
    assistant: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20"><path fill="currentColor" d="M6 8h3.33V6.38a2 2 0 1 1 1.34 0V8H14a3 3 0 0 1 3 3v1a1 1 0 0 1 0 2v1a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-1a1 1 0 0 1 0-2v-1a3 3 0 0 1 3-3m0 1.34A1.66 1.66 0 0 0 4.34 11v4A1.66 1.66 0 0 0 6 16.66h8A1.66 1.66 0 0 0 15.66 15v-4A1.66 1.66 0 0 0 14 9.34zm4-5.5a.66.66 0 1 0 0 1.32.66.66 0 1 0 0-1.32M6.83 12a.67.67 0 0 1 1.34 0v1.5a.67.67 0 0 1-1.34 0zm5 0a.67.67 0 0 1 1.34 0v1.5a.67.67 0 0 1-1.34 0z"/></svg>',
    globe: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20"><path fill="currentColor" d="M10 2.67a7.33 7.33 0 0 1 0 14.66 7.33 7.33 0 0 1 0-14.66m0 1.34a5.99 5.99 0 0 0 0 11.98 5.99 5.99 0 0 0 0-11.98m0-1.34a3.33 7.33 0 0 1 0 14.66 3.33 7.33 0 0 1 0-14.66m0 1.34a1.99 5.99 0 0 0 0 11.98 1.99 5.99 0 0 0 0-11.98M4.5 5.66h11V7h-11Zm-1 3.67h13v1.34h-13Zm1 3.67h11v1.34h-11Z"/></svg>',
    close: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20"><path fill="currentColor" d="M3.03 3.97a.67.67 0 0 1 .94-.94l13 13a.67.67 0 0 1-.94.94Zm0 12.06 13-13a.67.67 0 0 1 .94.94l-13 13a.67.67 0 0 1-.94-.94"/></svg>',
    edit: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M14.312 2.08a2.904 2.904 0 0 1 3.608 3.608L6.876 16.732 2.08 17.92l1.188-4.796zm-1.716 1.716 3.608 3.608" stroke-width="1.17"/></svg>',
    trash: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20"><path fill="currentColor" d="M3 4.83h14a.67.67 0 0 1 0 1.34H3a.67.67 0 0 1 0-1.34m4.33.67V4c0-1.2.97-2.17 2.17-2.17h1c1.2 0 2.17.97 2.17 2.17v1.5a.67.67 0 0 1-1.34 0V4c0-.46-.37-.83-.83-.83h-1c-.46 0-.83.37-.83.83v1.5a.67.67 0 0 1-1.34 0m-3.5 0a.67.67 0 0 1 1.34 0v10c0 .73.6 1.33 1.33 1.33h7c.73 0 1.33-.6 1.33-1.33v-10a.67.67 0 0 1 1.34 0v10c0 1.47-1.2 2.67-2.67 2.67h-7c-1.47 0-2.67-1.2-2.67-2.67ZM7.33 9a.67.67 0 0 1 1.34 0v5a.67.67 0 0 1-1.34 0Zm4 0a.67.67 0 0 1 1.34 0v5a.67.67 0 0 1-1.34 0Z"/></svg>',
    ellipsis: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M2.5 10a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0m6 0a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0m6 0a1.5 1.5 0 1 1 3 0 1.5 1.5 0 0 1-3 0"/></svg>',    
    ellipsisVertical: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3m0 6a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3m0 6a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3"/></svg>',
    filter: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.67 4.17h14.66l-5.5 6.16v4.5l-3.66 1.84v-6.34z"/></svg>',
    copy: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M13.33 13.33h1.34a2.66 2.66 0 0 0 2.66-2.66V5.33a2.66 2.66 0 0 0-2.66-2.66H9.33a2.66 2.66 0 0 0-2.66 2.66v1.34"/><rect x="2.67" y="6.67" width="10.66" height="10.66" rx="2.33"/></svg>',
    check: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m4.75 10.25 3.5 3.5 7-7"/></svg>',
    thumbUp: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M6.33 8.67v8m0 0H4a1.33 1.33 0 0 1-1.33-1.33V10A1.33 1.33 0 0 1 4 8.67h2.33l2.84-5.34a1.55 1.55 0 0 1 2.8 1.28L11.12 8h4.55a1.67 1.67 0 0 1 1.63 2l-1.07 5.33a2 2 0 0 1-1.96 1.34z"/></svg>',
    thumbDown: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M13.67 11.33v-8m0 0H16a1.33 1.33 0 0 1 1.33 1.33V10A1.33 1.33 0 0 1 16 11.33h-2.33l-2.84 5.34a1.55 1.55 0 0 1-2.8-1.28L8.88 12H4.33a1.67 1.67 0 0 1-1.63-2l1.07-5.33a2 2 0 0 1 1.96-1.34z"/></svg>',
    speaker: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path fill-rule="evenodd" clip-rule="evenodd" d="M11.151 8.349a2.335 2.335 0 0 1 0 3.302.665.665 0 0 0 .94.94 3.665 3.665 0 0 0 0-5.182.665.665 0 0 0-.94.94m2.121-2.121a5.335 5.335 0 0 1 0 7.544.665.665 0 0 0 .941.941 6.665 6.665 0 0 0 0-9.426.665.665 0 0 0-.941.941M4.5 7.335h1.725L9.03 4.53a.665.665 0 0 1 1.135.47v10a.665.665 0 0 1-1.135.47l-2.805-2.805H4.5A.665.665 0 0 1 3.835 12V8a.665.665 0 0 1 .665-.665m.665 1.33v2.67H6.5a.67.67 0 0 1 .47.195l1.865 1.865v-6.79L6.97 8.47a.67.67 0 0 1-.47.195Z"/></svg>',
    connections: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="m6.3 9.1 7.4-3.7m-7.4 5.5 7.4 3.7" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"/><circle cx="4.5" cy="10" r="2" stroke="currentColor" stroke-width="1.33"/><circle cx="15.5" cy="4.5" r="2" stroke="currentColor" stroke-width="1.33"/><circle cx="15.5" cy="15.5" r="2" stroke="currentColor" stroke-width="1.33"/></svg>',
    share: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M4.667 11a.667.667 0 0 0-1.334 0v3A2.667 2.667 0 0 0 6 16.667h8A2.667 2.667 0 0 0 16.667 14v-3a.667.667 0 0 0-1.334 0v3A1.333 1.333 0 0 1 14 15.333H6A1.333 1.333 0 0 1 4.667 14zm4.668 1.5V4.939L7.137 7.137a.664.664 0 1 1-.94-.94L9.53 2.863l.101-.083a.66.66 0 0 1 .839.083l3.334 3.334a.666.666 0 0 1-.941.94L10.665 4.94v7.56a.665.665 0 0 1-1.33 0"/></svg>',
    download: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M4.667 11a.667.667 0 0 0-1.334 0v3A2.667 2.667 0 0 0 6 16.667h8A2.667 2.667 0 0 0 16.667 14v-3a.667.667 0 0 0-1.334 0v3A1.333 1.333 0 0 1 14 15.333H6A1.333 1.333 0 0 1 4.667 14zm4.668-7.667v7.561L7.137 8.696a.664.664 0 1 0-.94.94L9.53 12.97l.101.083a.66.66 0 0 0 .839-.083l3.334-3.334a.666.666 0 0 0-.941-.94l-2.198 2.197v-7.56a.665.665 0 0 0-1.33 0"/></svg>',
    undo: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M4.95 15.05a7.15 7.15 0 1 0 0-10.1m0-2.3v2.3h2.3"/></svg>',
    redo: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M15.05 15.05a7.15 7.15 0 1 1 0-10.1m0-2.3v2.3h-2.3"/></svg>',
    redo_circle: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M2.85 10a7.15 7.15 0 0 1 12.2-5.05m0-2.3v2.3h-2.3m4.4 5.05a7.15 7.15 0 0 1-12.2 5.05m0 2.3v-2.3h2.3"/></svg>',
    chevron: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m4 7 6 6 6-6"/></svg>',
    chevronRight: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m7 4 6 6-6 6"/></svg>',
    chatFilesChevron: '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="M6 3L11 8L6 13"/></svg>',
    chevronTop: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m16 13-6-6-6 6"/></svg>',
    chevronLeft: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m13 4-6 6 6 6"/></svg>',
    branch: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" class="icon" aria-hidden="true"><path d="M4 10h5c2 0 3-1 4-2l3-3m-4 0h4v4m-7 1c2 0 3 1 4 2l3 3m-4 0h4v-4" stroke="currentColor" stroke-width="1.333" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    stop: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M6 4h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2"/></svg>',
    plus: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><path fill="currentColor" d="M9.335 3.332a.665.665 0 0 1 1.33 0v6.003h6.003a.665.665 0 0 1 0 1.33h-6.003v6.003a.665.665 0 0 1-1.33 0v-6.003H3.332a.665.665 0 0 1 0-1.33h6.003z"/></svg>',
    exclamation: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><path fill="currentColor" d="M8.58 3.16a.917.917 0 0 1 .92-1.03h1c.56 0 .99.48.92 1.03l-1.02 8.6c-.03.27-.22.47-.4.47s-.37-.2-.4-.47zM10 17.17a1.55 1.55 0 1 0 0-3.1 1.55 1.55 0 0 0 0 3.1"/></svg>',
    question: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M6.9 6.85a3.1 3.1 0 1 1 5.45 2.05c-.95.88-1.9 1.45-1.9 3.05M10 15.5h.01"/></svg>',
    pin: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="currentColor"><path fill-rule="evenodd" clip-rule="evenodd" transform="rotate(45 10 10)" d="M7.5 1.5h5a1.5 1.5 0 0 1 0 3h-.5v4.5c0 1.5 2.5 2 2.5 3a.5.5 0 0 1-.5.5h-3.325l-.325 7.86a.5.5 0 0 1-.7 0l-.325-7.86H6a.5.5 0 0 1-.5-.5c0-1 2.5-1.5 2.5-3v-4.5H7.5a1.5 1.5 0 0 1 0-3z M7.5 2.85h5a.15.15 0 0 1 0 .3h-1.85v6.1c0 1 1.2 1.5 2 1.9h-5.3c.8-.4 2-.9 2-1.9v-6.1H7.5a.15.15 0 0 1 0-.3z" /></svg>',
    unpin: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="currentColor"><path transform="rotate(45 10 10)" d="M7.5 1.5h5a1.5 1.5 0 0 1 0 3h-.5v4.5c0 1.5 2.5 2 2.5 3a.5.5 0 0 1-.5.5h-3.325l-.325 7.86a.5.5 0 0 1-.7 0l-.325-7.86H6a.5.5 0 0 1-.5-.5c0-1 2.5-1.5 2.5-3v-4.5H7.5a1.5 1.5 0 0 1 0-3z" /></svg>',
    archive: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" clip-rule="evenodd" d="M5 2.667h10A2.333 2.333 0 0 1 17.333 5v1.167c0 .727-.333 1.376-.855 1.804V15a2.333 2.333 0 0 1-2.333 2.333h-8.29A2.333 2.333 0 0 1 3.522 15V7.971a2.33 2.33 0 0 1-.855-1.804V5A2.333 2.333 0 0 1 5 2.667M4.167 5c0-.46.373-.833.833-.833h10c.46 0 .833.373.833.833v1.167c0 .46-.373.833-.833.833H5a.833.833 0 0 1-.833-.833zm.855 3.333V15c0 .46.373.833.833.833h8.29c.46 0 .833-.373.833-.833V8.333zM10 9.167c.368 0 .667.298.667.666v2.057l.552-.552a.667.667 0 1 1 .943.943l-1.69 1.69a.667.667 0 0 1-.944 0l-1.69-1.69a.667.667 0 0 1 .943-.943l.552.552V9.833c0-.368.299-.666.667-.666"/></svg>',
    info: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" clip-rule="evenodd" d="M10 2.667a7.333 7.333 0 1 0 0 14.666 7.333 7.333 0 0 0 0-14.666M1.333 10a8.667 8.667 0 1 1 17.334 0 8.667 8.667 0 0 1-17.334 0m8-3.5a.667.667 0 1 1 1.334 0 .667.667 0 0 1-1.334 0m1.334 2.667a.667.667 0 0 0-1.334 0V13.5a.667.667 0 0 0 1.334 0z"/></svg>',
    upload: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13.33V3.75"/><path d="m6.25 7.5 3.75-3.75 3.75 3.75"/><path d="M3.33 13.75v1.08c0 1.01.82 1.84 1.84 1.84h9.66c1.02 0 1.84-.83 1.84-1.84v-1.08"/></svg>',
    markdownAlertTip: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6.37 11.63a5 5 0 1 1 7.26 0c-.72.68-1.13 1.35-1.3 2.04H7.67c-.17-.69-.58-1.36-1.3-2.04Z"/><path d="M7.83 16h4.34M9 18h2"/></svg>',
    markdownAlertImportant: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.67 3h10.66A2.67 2.67 0 0 1 18 5.67v6.66A2.67 2.67 0 0 1 15.33 15H9l-3.67 2.67V15h-.66A2.67 2.67 0 0 1 2 12.33V5.67A2.67 2.67 0 0 1 4.67 3Z"/><path d="M10 6.33V10m0 2.33h.01"/></svg>',
    tool: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m14.974 2.896-2.632 2.632 2.13 2.13 2.632-2.632A3.613 3.613 0 0 1 12.46 9.67l-6.603 6.602a1.506 1.506 0 0 1-2.129-2.129L10.33 7.54a3.613 3.613 0 0 1 4.644-4.644"/></svg>',
    model_tool_subagent: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M9.333 2.667a.667.667 0 0 1 1.334 0v1.366a4.67 4.67 0 0 1 4 4.617v.683h.666A1.667 1.667 0 0 1 17 11v2.667a1.667 1.667 0 0 1-1.667 1.666h-.87A3.326 3.326 0 0 1 11.333 17h-2.666a3.326 3.326 0 0 1-3.13-1.667h-.87A1.667 1.667 0 0 1 3 13.667V11a1.667 1.667 0 0 1 1.667-1.667h.666V8.65a4.67 4.67 0 0 1 4-4.617zm0 2.716A3.334 3.334 0 0 0 6.667 8.65v5.017c0 1.104.895 2 2 2h2.666c1.105 0 2-.896 2-2V8.65a3.334 3.334 0 0 0-2.666-3.267v.284a.667.667 0 0 1-1.334 0zM8 9.333a.833.833 0 1 0 0 1.667.833.833 0 0 0 0-1.667m4 0A.833.833 0 1 0 12 11a.833.833 0 0 0 0-1.667M8.333 13a.667.667 0 0 0 0 1.333h3.334a.667.667 0 0 0 0-1.333z"/></svg>',
    model_tool_weather: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><defs><mask id="a"><path fill="#fff" d="M0 0h20v20H0z"/><path d="M6.5 16a3 3 0 1 1 .13-6 4 4 0 0 1 7.74 0 3 3 0 1 1 .13 6Z"/></mask></defs><g fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><g mask="url(#a)"><circle cx="6.5" cy="7.5" r="2.25"/><path d="M6.5 4.25V3m2.3 2.2.9-.9m-5.5.9-.9-.9m-.05 3.2H2m2.2 2.3-.9.9"/></g><path d="M6.5 16a3 3 0 1 1 .13-6 4 4 0 0 1 7.74 0 3 3 0 1 1 .13 6Z"/></g></svg>',
    model_tool_flashcards: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M5.333 2.667h10A2.667 2.667 0 0 1 18 5.333v6a2.667 2.667 0 0 1-2.667 2.667h-10a2.667 2.667 0 0 1-2.666-2.667v-6a2.667 2.667 0 0 1 2.666-2.666m0 1.333A1.333 1.333 0 0 0 4 5.333v6c0 .737.597 1.334 1.333 1.334h10c.737 0 1.334-.597 1.334-1.334v-6C16.667 4.597 16.07 4 15.333 4zm1.334 2h7.666a.667.667 0 1 1 0 1.333H6.667a.667.667 0 1 1 0-1.333m0 3h4.666a.667.667 0 1 1 0 1.333H6.667a.667.667 0 1 1 0-1.333m-2 6.667h10.666a.667.667 0 1 1 0 1.333H4.667a.667.667 0 1 1 0-1.333"/></svg>',
    model_tool_quiz: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2.5H5.5A2.5 2.5 0 0 0 3 5v10a2.5 2.5 0 0 0 2.5 2.5h9A2.5 2.5 0 0 0 17 15V6.5z"/><path d="M13 2.5v3a1 1 0 0 0 1 1h3m-8.5 0a1.5 1.5 0 1 1 3 0c0 1.25-1.5 1.75-1.5 3.25m0 1.75v.01M5 14.5 6.25 16 8 13.5m4.25 0 2.5 2.5m0-2.5-2.5 2.5"/></g></svg>',
    model_tool_notes: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M5 2.5h7.5L17 7v8.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-11a2 2 0 0 1 2-2"/><path d="M12.5 2.5v3A1.5 1.5 0 0 0 14 7h3M7 10.5h6M7 14h4"/></g></svg>',
    model_tool_memories: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20"><g fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3a3.5 3.5 0 0 0-5 2.5A3.5 3.5 0 0 0 3 11a3.5 3.5 0 0 0 3 5.5 3 3 0 0 0 4 0 3 3 0 0 0 4 0 3.5 3.5 0 0 0 3-5.5 3.5 3.5 0 0 0-2-5.5A3.5 3.5 0 0 0 10 3"/><path d="M5 5.5c1.5 0 3-1 3.5-2m6.5 2c-1.5 0-3-1-3.5-2M3 11c2 0 3.5-1.5 4-3m10 3c-2 0-3.5-1.5-4-3m-7 8.5c1-2 2-3 3.5-3m4.5 3c-1-2-2-3-3.5-3m-.5-8V7m0 3v1.5m0 3v2"/></g></svg>',
    warning: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9.24 3.36a.88.88 0 0 1 1.52 0l6.68 11.44a1.6 1.6 0 0 1-1.38 2.4H3.94a1.6 1.6 0 0 1-1.38-2.4zM10 7.5v3.6m0 2.9h.01"/></svg>',    
    markdownAlertCaution: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 2.67-3.33 3.33v8L6 17.33h8L17.33 14V6L14 2.67Z"/><path d="M10 6.33V11m0 2.67h.01"/></svg>',
    error: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="7.33"/><path d="m12.5 7.5-5 5m0-5 5 5"/></svg>',
    clock: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="7.33"/><path d="M10 6.67v3.5l2.67 1.5"/></svg>',
    refresh: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.5 10A7.5 7.5 0 1 1 10 2.5c2.1 0 4.1.83 5.61 2.28l1.89 1.89"/><path d="M17.5 2.5v4.17h-4.17"/></svg>',
    refreshSpinning: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" class="spinning" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.5 10A7.5 7.5 0 1 1 10 2.5c2.1 0 4.1.83 5.61 2.28l1.89 1.89"/><path d="M17.5 2.5v4.17h-4.17"/></svg>',
    create: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M9.17 3.33H4.5A1.67 1.67 0 0 0 2.83 5v10.5a1.67 1.67 0 0 0 1.67 1.67H15a1.67 1.67 0 0 0 1.67-1.67v-4.67"/><path d="M14.75 2.83a1.77 1.77 0 0 1 2.5 2.5l-7.92 7.92-3.33.83.83-3.33 7.92-7.92z"/><path d="m13.5 4.08 2.42 2.42"/></svg>',
    security: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M10 17.25c-.18-.08-5.83-2.95-5.83-7.25V4.9L10 2.75l5.83 2.15V10c0 4.3-5.65 7.17-5.83 7.25"/></svg>',
    settings: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M8.604 3.597c.355-1.463 2.437-1.463 2.792 0a1.437 1.437 0 0 0 2.144.888c1.286-.783 2.758.688 1.975 1.975a1.437 1.437 0 0 0 .888 2.143c1.463.355 1.463 2.437 0 2.792a1.437 1.437 0 0 0-.888 2.144c.783 1.286-.688 2.758-1.975 1.975a1.437 1.437 0 0 0-2.143.888c-.355 1.463-2.437 1.463-2.792 0a1.437 1.437 0 0 0-2.144-.888c-1.286.783-2.758-.688-1.975-1.975a1.437 1.437 0 0 0-.888-2.143c-1.463-.355-1.463-2.437 0-2.792a1.437 1.437 0 0 0 .888-2.144c-.783-1.286.688-2.758 1.975-1.975.83.507 1.913.058 2.143-.888"/><circle cx="10" cy="10" r="2.5"/></svg>',
    lightning: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M10.73 2.67 3.4 11.47H10l-.73 5.86 7.33-8.8H10l.73-5.86z"/></svg>',
    code: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m13 5 4.5 5-4.5 5M7 5l-4.5 5L7 15"/></svg>',
    grid: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><rect x="2.5" y="2.5" width="15" height="15" rx="1.5"/><path d="M2.5 7.5h15m-10 10v-10"/></svg>',
    layers: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><path d="M10 3 2.5 6.5 10 10l7.5-3.5zm-7.5 7 7.5 3.5 7.5-3.5m-15 3.5L10 17l7.5-3.5"/></svg>',
    vision: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M2.67 10a8 8 0 0 1 14.66 0 8 8 0 0 1-14.66 0M10 6.67a3.33 3.33 0 1 1 0 6.66 3.33 3.33 0 1 1 0-6.66"/></svg>',
    thinking: '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg"><path d="M10 16.5A4 4 0 0 1 3.5 12a3.5 3.5 0 0 1 1-5.5 3.5 3.5 0 0 1 5.5-3 3.5 3.5 0 0 1 5.5 3 3.5 3.5 0 0 1 1 5.5 4 4 0 0 1-6.5 4.5m0-13v13"/><path d="M6.5 9.5c0-1.5 1.5-2 3.5-2m3.5 2c0-1.5-1.5-2-3.5-2m-3 6C7 12 8.5 12 10 12m3 1.5c0-1.5-1.5-1.5-3-1.5"/></svg>',
    alpha: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M7 3h6M8 3v5l-4 7a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l-4-7V3m-6 9h8"/></svg>',
    experimental: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M7 3h6M8 3v5l-4 7a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2l-4-7V3m-6 9h8"/></svg>',
    magnifyingGlass: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="9" r="6"/><path d="M13.25 13.25 17 17"/></svg>',
    zoomIn: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="9" r="6"/><path d="M13.25 13.25 17 17M6 9h6M9 6v6"/></svg>',
    zoomOut: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="9" r="6"/><path d="M13.25 13.25 17 17M6 9h6"/></svg>',
    microphone: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" class="icon" aria-hidden="true"><path d="M15.7806 10.1963C16.1326 10.3011 16.3336 10.6714 16.2288 11.0234L16.1487 11.2725C15.3429 13.6262 13.2236 15.3697 10.6644 15.6299L10.6653 16.835H12.0833L12.2171 16.8486C12.5202 16.9106 12.7484 17.1786 12.7484 17.5C12.7484 17.8214 12.5202 18.0894 12.2171 18.1514L12.0833 18.165H7.91632C7.5492 18.1649 7.25128 17.8672 7.25128 17.5C7.25128 17.1328 7.5492 16.8351 7.91632 16.835H9.33527L9.33429 15.6299C6.775 15.3697 4.6558 13.6262 3.84992 11.2725L3.76984 11.0234L3.74445 10.8906C3.71751 10.5825 3.91011 10.2879 4.21808 10.1963C4.52615 10.1047 4.84769 10.2466 4.99347 10.5195L5.04523 10.6436L5.10871 10.8418C5.8047 12.8745 7.73211 14.335 9.99933 14.335C12.3396 14.3349 14.3179 12.7789 14.9534 10.6436L15.0052 10.5195C15.151 10.2466 15.4725 10.1046 15.7806 10.1963ZM12.2513 5.41699C12.2513 4.17354 11.2437 3.16521 10.0003 3.16504C8.75675 3.16504 7.74835 4.17343 7.74835 5.41699V9.16699C7.74853 10.4104 8.75685 11.418 10.0003 11.418C11.2436 11.4178 12.2511 10.4103 12.2513 9.16699V5.41699ZM13.5814 9.16699C13.5812 11.1448 11.9781 12.7479 10.0003 12.748C8.02232 12.748 6.41845 11.1449 6.41828 9.16699V5.41699C6.41828 3.43889 8.02221 1.83496 10.0003 1.83496C11.9783 1.83514 13.5814 3.439 13.5814 5.41699V9.16699Z"/></svg>',
    microphoneMute: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" class="icon" aria-hidden="true"><defs><mask id="a"><path fill="#fff" d="M0 0h20v20H0z"/><path d="m3 3 14 14" stroke="#000" stroke-width="2.5" stroke-linecap="round"/></mask></defs><path mask="url(#a)" d="M15.78 10.196a.665.665 0 0 1 .449.827l-.08.25a6.5 6.5 0 0 1-5.485 4.357l.001 1.205h1.418l.134.014a.665.665 0 0 1 0 1.302l-.134.014H7.916a.665.665 0 0 1 0-1.33h1.42l-.002-1.205a6.5 6.5 0 0 1-5.484-4.357l-.08-.25-.026-.132a.665.665 0 0 1 1.25-.371l.051.124.064.198a5.17 5.17 0 0 0 9.844-.198l.052-.124a.664.664 0 0 1 .776-.324m-3.529-4.779a2.252 2.252 0 1 0-4.503 0v3.75a2.252 2.252 0 0 0 4.503 0zm1.33 3.75a3.581 3.581 0 0 1-7.163 0v-3.75a3.582 3.582 0 0 1 7.163 0z"/><path d="m3 3 14 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" fill="none"/></svg>',
    removeUser: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13.33 17.5v-1.67a3.33 3.33 0 0 0-3.33-3.33H5a3.33 3.33 0 0 0-3.33 3.33v1.67"/><circle cx="7.5" cy="5.83" r="3.33"/><path d="M14.17 6.67l4.16 4.16M18.33 6.67l-4.16 4.16"/></svg>',
    loading_circle: '<svg xmlns="http://www.w3.org/2000/svg" class="files-preview-spinner" width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="8.33" stroke="currentColor" stroke-width="1.33" stroke-opacity=".2"/><circle cx="10" cy="10" r="8.33" stroke="currentColor" stroke-width="1.33" stroke-dasharray="37.5 12.5" stroke-linecap="round"><animateTransform attributeName="transform" type="rotate" from="0 10 10" to="360 10 10" dur="0.8s" repeatCount="indefinite"/></circle></svg>',
    queue: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M3.25 3.5a.75.75 0 0 0 0 1.5h13.5a.75.75 0 0 0 0-1.5zm0 4.5a.75.75 0 0 0 0 1.5h13.5a.75.75 0 0 0 0-1.5zm0 4.5a.75.75 0 0 0 0 1.5h13.5a.75.75 0 0 0 0-1.5z"/></svg>',
    addToQueue: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M3.25 3.5a.75.75 0 0 0 0 1.5h8a.75.75 0 0 0 0-1.5zm0 4.5a.75.75 0 0 0 0 1.5h8a.75.75 0 0 0 0-1.5zm0 4.5a.75.75 0 0 0 0 1.5h5a.75.75 0 0 0 0-1.5zm8.5 0h1.75v-1.75a.75.75 0 0 1 1.5 0v1.75h1.75a.75.75 0 0 1 0 1.5H15v1.75a.75.75 0 0 1-1.5 0V14h-1.75a.75.75 0 0 1 0-1.5"/></svg>',
    textLines: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 10h10M5 6h10M5 14h6"/></svg>',
    captions: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="4" width="15" height="12" rx="3"/><path d="M8.25 8.25a2.25 2.25 0 1 0 0 3.5M13.5 8.25a2.25 2.25 0 1 0 0 3.5"/></svg>',
    reference: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M7.525 7.525 3.99 11.061a3.5 3.5 0 0 0 4.95 4.95l1.414-1.415a.75.75 0 0 0-1.061-1.06L7.879 14.95a2 2 0 0 1-2.829-2.83l3.536-3.535a.75.75 0 0 0-1.06-1.06m4.95 4.95 3.535-3.536a3.5 3.5 0 0 0-4.95-4.95L9.647 5.405a.75.75 0 0 0 1.061 1.06l1.414-1.414A2 2 0 0 1 14.95 7.88l-3.536 3.535a.75.75 0 0 0 1.06 1.06"/></svg>',
    logout: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M9 4.667a.667.667 0 0 0 0-1.334H6A2.667 2.667 0 0 0 3.333 6v8A2.667 2.667 0 0 0 6 16.667h3a.667.667 0 0 0 0-1.334H6A1.333 1.333 0 0 1 4.667 14V6A1.333 1.333 0 0 1 6 4.667zM7.5 9.335h7.561l-2.198-2.198a.665.665 0 1 1 .94-.94l3.334 3.333.083.101a.66.66 0 0 1-.083.839l-3.334 3.334a.666.666 0 0 1-.94-.941l2.197-2.198H7.5a.665.665 0 0 1 0-1.33"/></svg>',
    signin: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg" class="icon"><path d="M11 4.667a.667.667 0 0 1 0-1.334h3A2.667 2.667 0 0 1 16.667 6v8A2.667 2.667 0 0 1 14 16.667h-3a.667.667 0 0 1 0-1.334h3A1.333 1.333 0 0 0 15.333 14V6A1.333 1.333 0 0 0 14 4.667zM2.5 9.335h7.561L7.863 7.137a.665.665 0 1 1 .94-.94l3.334 3.333.083.101a.66.66 0 0 1-.083.839l-3.334 3.334a.666.666 0 0 1-.94-.941l2.197-2.198H2.5a.665.665 0 0 1 0-1.33"/></svg>',
    urlLink: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8.33 10.83a4.17 4.17 0 0 0 6.28.45l2.5-2.5a4.17 4.17 0 0 0-5.89-5.89L9.79 4.32"/><path d="M11.67 9.17a4.17 4.17 0 0 0-6.28-.45l-2.5 2.5a4.17 4.17 0 0 0 5.89 5.89l1.43-1.43"/></svg>',
    send: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m18.33 1.67-9.16 9.16m9.16-9.16L12.5 18.33l-3.33-7.5-7.5-3.33z"/></svg>',
    music: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="m7.19 4.345 9.34-2a.67.67 0 0 1 .28 1.31l-9.34 2a.67.67 0 0 1-.28-1.31M6.66 5H8v10H6.66ZM16 3h1.34v10H16ZM5.33 12.33a2.67 2.67 0 0 1 0 5.34 2.67 2.67 0 0 1 0-5.34m0 1.34a1.33 1.33 0 0 0 0 2.66 1.33 1.33 0 0 0 0-2.66m9.34-3.34a2.67 2.67 0 0 1 0 5.34 2.67 2.67 0 0 1 0-5.34m0 1.34a1.33 1.33 0 0 0 0 2.66 1.33 1.33 0 0 0 0-2.66"/></svg>',  
    cloud_sync: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M5.42 14.17a3.75 3.75 0 0 1 0-7.5 5.42 5.42 0 0 1 9.16 0 3.75 3.75 0 0 1 0 7.5"/><path d="M9 15.66a2.92 2.92 0 0 1-1.92-2.74A2.92 2.92 0 0 1 9 10.18"/><path d="M7 10.18h2v2m2-2a2.92 2.92 0 0 1 1.92 2.74A2.92 2.92 0 0 1 11 15.66"/><path d="M13 15.66h-2v-2"/></svg>',
    cloud_download: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M5.42 14.17a3.75 3.75 0 0 1 0-7.5 5.42 5.42 0 0 1 9.16 0 3.75 3.75 0 0 1 0 7.5M10 8.5v8"/><path d="m7 13.5 3 3 3-3"/></svg>', 
    cloud_export: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M5.42 14.17a3.75 3.75 0 0 1 0-7.5 5.42 5.42 0 0 1 9.16 0 3.75 3.75 0 0 1 0 7.5M10 16.5v-8"/><path d="m7 11.5 3-3 3 3"/></svg>',
    stats_arrow: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.33 13.33 8 8.67l2.67 2.66 5.5-5.5"/><path d="M12.83 5.83h3.34v3.34"/></svg>',
    text_placeholder: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="2.5" width="15" height="15" rx="3.33"/><path d="M5.83 7.5h8.34"/><path d="M5.83 10.83h5"/></svg>',
    splitPanelsBoth: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" class="icon" aria-hidden="true"><rect x="2.67" y="3" width="6.33" height="14" rx="2"/><rect x="11" y="3" width="6.33" height="14" rx="2"/></svg>',
    splitPanelsLeft: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" class="icon" aria-hidden="true"><rect x="2.67" y="3" width="6.33" height="14" rx="2" fill="currentColor"/><rect x="11" y="3" width="6.33" height="14" rx="2"/></svg>',
    splitPanelsRight: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" class="icon" aria-hidden="true"><rect x="2.67" y="3" width="6.33" height="14" rx="2"/><rect x="11" y="3" width="6.33" height="14" rx="2" fill="currentColor"/></svg>',
    speech: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" class="icon" aria-hidden="true"><path d="M4 8.5v3m4-7v11m4-9v7m4-5v3" stroke="currentColor" stroke-width="1.33" stroke-linecap="round"/></svg>',
    expand: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3h5v5m-9 9H3v-5m14-9-6 6m-8 8 6-6"/></svg>',
    contract: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v5H3m9 9v-5h5M3 8l5-5m9 9-5 5"/></svg>',
    chatFiles: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M14 6v8a3 3 0 0 1-6 0V5a2 2 0 0 1 4 0v9a1 1 0 0 1-2 0V6"/></svg>',
    chatFilesAddMeeting: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" class="icon" aria-hidden="true"><rect x="2.5" y="4" width="15" height="13" rx="2"/><path d="M6 2.83v2.09M14 2.83v2.09M2.5 8h15"/><path d="M10 10.33v3.34M8.33 12h3.34"/></svg>',
    chatFilesScreenCapture: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" class="icon" aria-hidden="true"><rect x="5.75" y="6.85" width="8.5" height="6.3" rx="1.35"/><path d="M5.4 5.4H3.2v2.3m11.4-2.3h2.2v2.3M5.4 14.6H3.2v-2.3m11.4 2.3h2.2v-2.3"/></svg>',
    chatFilesChooseChats: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" class="icon" aria-hidden="true"><path d="M4 3.75h12c1.1 0 2 .9 2 2v7.5c0 1.1-.9 2-2 2H9L5.5 17.5v-2.25H4c-1.1 0-2-.9-2-2v-7.5c0-1.1.9-2 2-2m2.25 4.5h7.5m-7.5 3h4.5"/></svg>',
    chatFilesChooseUploaded: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" class="icon" aria-hidden="true"><path d="M2.5 6.75C2.5 5.78 3.28 5 4.25 5H8L9.5 6.5H15.75C16.72 6.5 17.5 7.28 17.5 8.25V14.25C17.5 15.22 16.72 16 15.75 16H4.25C3.28 16 2.5 15.22 2.5 14.25V6.75Z"/><path d="M10 9.5V13.5M8 11.5H12"/></svg>',
    bookmark: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m17 18-7-4.5L3 18V4a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>',
    bookmarkFilled: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="m16 17-6-4-6 4V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2z"/></svg>',
    citations: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M4 15a2 2 0 0 1 2-2h11"/><path d="M6 3h11v14H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2m3 4h4m-4 3h4"/></svg>',
    todo: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7.5 4.17H5.83A1.67 1.67 0 0 0 4.17 5.83v10a1.67 1.67 0 0 0 1.66 1.67h8.34a1.67 1.67 0 0 0 1.66-1.67v-10a1.67 1.67 0 0 0-1.66-1.66H12.5"/><path d="M7.5 4.17A1.67 1.67 0 0 0 9.17 5.83h1.66A1.67 1.67 0 0 0 12.5 4.17"/><path d="M7.5 4.17A1.67 1.67 0 0 1 9.17 2.5h1.66a1.67 1.67 0 0 1 1.67 1.67"/><path d="m7.5 11.67 1.67 1.66 3.33-3.33"/></svg>',
    workspace: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg"><rect x="2.667" y="3.333" width="14.666" height="11.667" rx="1.667" stroke="currentColor" stroke-width="1.333" stroke-linecap="round" stroke-linejoin="round"/><path d="M6.667 16.667h6.666" stroke="currentColor" stroke-width="1.333" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    // toolCategoryIcons
    todo_management: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 9.5l2.25 2.25 5.75-6"/><path d="M15.75 10.5v4A1.75 1.75 0 0 1 14 16.25H5.25A1.75 1.75 0 0 1 3.5 14.5V5.75A1.75 1.75 0 0 1 5.25 4h7"/></svg>`,
    notes_management: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11.8 3.2H5.4A1.7 1.7 0 0 0 3.7 4.9v10.2a1.7 1.7 0 0 0 1.7 1.7h9.2a1.7 1.7 0 0 0 1.7-1.7V7.7L11.8 3.2z"/><path d="M11.7 3.2v4.6h4.6"/><path d="M7 10.2h6"/><path d="M7 13.1h6"/><path d="M7 7.4h2"/></svg>`,
    automations_management: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4.2" width="14" height="12.3" rx="1.8"/><path d="M6.7 3v3"/><path d="M13.3 3v3"/><path d="M3 8h14"/></svg>`,
    skills_management: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m10 2.8 2.2 4.45 4.9.72-3.55 3.45.84 4.88L10 14l-4.39 2.3.84-4.88L2.9 7.97l4.9-.72z"/></svg>`,
    
    memory_management: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h16v16H4z"/><path d="M9 9h6"/><path d="M9 13h6"/><path d="M9 17h4"/></svg>`,
    media_generation: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2" width="20" height="20" rx="5"/><circle cx="12" cy="12" r="4"/><path d="M12 2v4"/><path d="M12 18v4"/><path d="M2 12h4"/><path d="M18 12h4"/></svg>`,

    education: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1.67 2.5h5A3.33 3.33 0 0 1 10 5.83V17.5A2.5 2.5 0 0 0 7.5 15H1.67zm16.66 0h-5A3.33 3.33 0 0 0 10 5.83V17.5a2.5 2.5 0 0 1 2.5-2.5h5.83z"/></svg>`,
    presentations: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3.5" width="14" height="10.5" rx="1.8"/><path d="M10 14v3"/><path d="M7 17h6"/></svg>`,
    partial: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.17 10h11.66"/></svg>`,


    text: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><defs><linearGradient id="a" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#eef2ff"/><stop offset="100%" stop-color="#c7d2fe"/></linearGradient></defs><rect x="2.71" y="3.75" width="14.58" height="12.5" rx="2.67" fill="url(#a)" stroke="#6366f1" stroke-opacity=".45" stroke-width=".58"/><path d="M5.63 7.71h8.75M5.63 10.42h8.75m-8.75 2.71h5.42" stroke="#3730a3" stroke-width="1.29" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    audio: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" role="img" aria-hidden="true"><defs><linearGradient id="lb-cap-audio-bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#e0f2fe"/><stop offset="100%" stop-color="#7dd3fc"/></linearGradient></defs><path d="M3.75 8.13a.83.83 0 0 1 .83-.83h2.25l2.96-2.29a.5.5 0 0 1 .81.39v9.22a.5.5 0 0 1-.81.39l-2.96-2.29H4.58a.83.83 0 0 1-.83-.83z" fill="url(#lb-cap-audio-bg)" stroke="#0c4a6e" stroke-opacity="0.55" stroke-width="0.58" stroke-linejoin="round"/><path d="M12.67 7.83a3.08 3.08 0 0 1 0 4.34" stroke="#0c4a6e" stroke-width="1.29" stroke-linecap="round" stroke-linejoin="round" fill="none"/><path d="M14.67 6.17a5.33 5.33 0 0 1 0 7.66" stroke="#0c4a6e" stroke-width="1.29" stroke-linecap="round" stroke-linejoin="round" fill="none" opacity="0.65"/></svg>`,
    image: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" role="img" aria-hidden="true"><defs><linearGradient id="lb-cap-image-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#ecfdf5"/><stop offset="100%" stop-color="#86efac"/></linearGradient><clipPath id="lb-cap-image-clip"><rect x="2.71" y="3.75" width="14.58" height="12.5" rx="2.67"/></clipPath></defs><rect x="2.71" y="3.75" width="14.58" height="12.5" rx="2.67" fill="url(#lb-cap-image-sky)" stroke="#047857" stroke-opacity="0.5" stroke-width="0.58"/><g clip-path="url(#lb-cap-image-clip)"><circle cx="7" cy="7.5" r="1.38" fill="#fbbf24" stroke="#b45309" stroke-opacity="0.4" stroke-width="0.33"/><path d="M2.5 14.58l3.83-3.83a.83.83 0 0 1 1.17 0l2 2 2.67-3a.83.83 0 0 1 1.25 0l4.08 4.58v3.17h-15z" fill="#047857"/><path d="M2.5 15.83l3.33-2.17 2.5 1.33 3.33-2 2.92 1.5 2.92-.83v3.84h-15z" fill="#065f46" opacity="0.7"/></g></svg>`,
    text_document: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" role="img" aria-hidden="true"><defs><linearGradient id="lb-cap-doc-bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#f9fafb"/><stop offset="100%" stop-color="#d1d5db"/></linearGradient></defs><path d="M5.42 2.71h5.83L15 6.46v9.37a1.46 1.46 0 0 1-1.46 1.46H5.42a1.46 1.46 0 0 1-1.46-1.46V4.17a1.46 1.46 0 0 1 1.46-1.46z" fill="url(#lb-cap-doc-bg)" stroke="#4b5563" stroke-width="0.58" stroke-linejoin="round"/><path d="M11.25 2.71v3.96H15" fill="none" stroke="#4b5563" stroke-width="0.58" stroke-linejoin="round"/><path d="M6.25 9.58h6.67M6.25 12.08h6.67M6.25 14.58h4.17" stroke="#374151" stroke-width="1.17" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    attachment_file: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><defs><linearGradient id="a" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#fffbeb"/><stop offset="100%" stop-color="#fde68a"/></linearGradient></defs><path d="M5.42 2.71h5.83L15 6.46v9.37a1.46 1.46 0 0 1-1.46 1.46H5.42a1.46 1.46 0 0 1-1.46-1.46V4.17a1.46 1.46 0 0 1 1.46-1.46z" fill="url(#a)" stroke="#b45309" stroke-width=".58" stroke-linejoin="round"/><path d="M11.25 2.71v3.96H15" fill="none" stroke="#b45309" stroke-width=".67" stroke-linejoin="round"/><path d="M10.75 13.9v-5a1.5 1.5 0 0 0-3 0v5a.75.75 0 0 0 1.5 0v-4" fill="none" stroke="#b45309" stroke-width="1.13" stroke-linecap="round" stroke-linejoin="round"/></svg>`,
    pdf: `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><defs><linearGradient id="a" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#fef2f2"/><stop offset="100%" stop-color="#fecaca"/></linearGradient><linearGradient id="b" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#ef4444"/><stop offset="100%" stop-color="#b91c1c"/></linearGradient></defs><path d="M5.42 2.71h5.83L15 6.46v9.37a1.46 1.46 0 0 1-1.46 1.46H5.42a1.46 1.46 0 0 1-1.46-1.46V4.17a1.46 1.46 0 0 1 1.46-1.46z" fill="url(#a)" stroke="#b91c1c" stroke-width=".58" stroke-linejoin="round"/><path d="M11.25 2.71v3.96H15" fill="none" stroke="#b91c1c" stroke-width=".67" stroke-linejoin="round"/><rect x="4.48" y="10.42" width="10" height="5" rx="1.17" fill="url(#b)"/><text x="9.48" y="14.13" text-anchor="middle" fill="#fff" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="3.25" font-weight="800" letter-spacing=".15">PDF</text></svg>`,


    // Admin sidebar
    admin_sidebar_general: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="2.5"/><path d="M16.17 12.5a1.38 1.38 0 0 0 0.28 1.52l0.05 0.05a1.67 1.67 0 0 1 0 2.36 1.67 1.67 0 0 1-2.36 0l-0.05-0.05a1.38 1.38 0 0 0-1.52-0.28 1.38 1.38 0 0 0-0.83 1.26v0.14a1.67 1.67 0 0 1-1.67 1.67 1.67 1.67 0 0 1-1.67-1.67v-0.07A1.38 1.38 0 0 0 7.5 16.17a1.38 1.38 0 0 0-1.52 0.28l-0.05 0.05a1.67 1.67 0 0 1-2.36 0 1.67 1.67 0 0 1 0-2.36l0.05-0.05a1.38 1.38 0 0 0 0.28-1.52 1.38 1.38 0 0 0-1.26-0.83H2.5a1.67 1.67 0 0 1-1.67-1.67A1.67 1.67 0 0 1 2.5 8.4h0.07A1.38 1.38 0 0 0 3.83 7.5a1.38 1.38 0 0 0-0.28-1.52l-0.05-0.05a1.67 1.67 0 0 1 0-2.36 1.67 1.67 0 0 1 2.36 0l0.05 0.05a1.38 1.38 0 0 0 1.52 0.28h0.07a1.38 1.38 0 0 0 0.83-1.26V2.5A1.67 1.67 0 0 1 10 0.83a1.67 1.67 0 0 1 1.67 1.67v0.07a1.38 1.38 0 0 0 0.83 1.26 1.38 1.38 0 0 0 1.52-0.28l0.05-0.05a1.67 1.67 0 0 1 2.36 0 1.67 1.67 0 0 1 0 2.36l-0.05 0.05a1.38 1.38 0 0 0-0.28 1.52v0.07a1.38 1.38 0 0 0 1.26 0.83h0.14A1.67 1.67 0 0 1 19.17 10a1.67 1.67 0 0 1-1.67 1.67h-0.07a1.38 1.38 0 0 0-1.26 0.83z"/></svg>',
    admin_sidebar_skills: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12.08 5.42a1.77 1.77 0 1 1 2.5 2.5L5.83 16.67l-3.33 0.83 0.83-3.33Z"/><path d="m8.33 9.17 2.5 2.5"/></svg>',
    admin_sidebar_automations: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7.5 9.17l2.5 2.5 8.33-8.34"/><path d="M17.5 10v5.83a1.67 1.67 0 0 1-1.67 1.67H4.17a1.67 1.67 0 0 1-1.67-1.67V4.17A1.67 1.67 0 0 1 4.17 2.5h9.16"/></svg>',
    admin_sidebar_todo: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7.5 5h9.17"/><path d="M7.5 10h9.17"/><path d="M7.5 15h9.17"/><path d="m4.17 5 0.83 0.83 1.67-1.67"/><path d="m4.17 10 0.83 0.83 1.67-1.67"/><path d="m4.17 15 0.83 0.83 1.67-1.67"/></svg>',
    admin_sidebar_notes: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 16.67h7.5"/><path d="M13.75 2.92a1.77 1.77 0 1 1 2.5 2.5L5.83 15.83l-3.33 0.83 0.83-3.33Z"/></svg>',
    admin_sidebar_memories: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 17.5s-5.6-3.62-7.5-7.08C0.61 7 2.8 3.33 6.25 3.33c1.7 0 2.98 0.89 3.75 2.08 0.78-1.18 2.05-2.08 3.75-2.08 3.45 0 5.64 3.67 3.75 7.09C15.6 13.88 10 17.5 10 17.5Z"/></svg>',
    admin_sidebar_prompts: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 1.67v5"/><path d="M10 15v3.33"/><path d="m4.11 4.11 3.53 3.53"/><path d="m12.36 12.36 3.53 3.53"/><path d="M1.67 10h5"/><path d="M13.33 10h5"/><path d="m4.11 15.89 3.53-3.53"/><path d="m12.36 7.64 3.53-3.53"/></svg>',
    admin_sidebar_bookmarks: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15.83 17.5 10 13.33 4.17 17.5V4.17A1.67 1.67 0 0 1 5.84 2.5h8.33a1.67 1.67 0 0 1 1.67 1.67z"/></svg>',
    admin_sidebar_agents: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 2.5v3.33"/><path d="M6.67 17.5h6.66"/><path d="M8.33 5.83h3.34"/><rect x="5" y="9.17" width="10" height="6.67" rx="1.67"/><path d="M7.5 12.5h0.01"/><path d="M12.5 12.5h0.01"/></svg>',
    admin_sidebar_byok: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m17.5 1.67-1.67 1.66"/><path d="m6.25 6.25 1.67-1.67a2.36 2.36 0 1 1 3.33 3.34L9.58 9.58"/><path d="m9.17 9.17 1.66 1.66"/><path d="m2.5 17.5 2.92-0.83 9.16-9.17-2.08-2.08-9.17 9.16Z"/></svg>',
    admin_sidebar_temporary_access: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="6.67" r="3.33"/><path d="M5 16.67a5 5 0 0 1 10 0"/></svg>',
    admin_sidebar_sharing: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="15" cy="4.17" r="2.5"/><circle cx="5" cy="10" r="2.5"/><circle cx="15" cy="15.83" r="2.5"/><path d="m7.16 11.26 5.69 3.32"/><path d="m12.84 5.42-5.68 3.32"/></svg>',
    admin_sidebar_chat: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.5 12.5a1.67 1.67 0 0 1-1.67 1.67h-10L2.5 17.5V4.17A1.67 1.67 0 0 1 4.17 2.5h11.66a1.67 1.67 0 0 1 1.67 1.67z"/></svg>',
    admin_sidebar_context: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="8.33"/><path d="M10 13.33V10"/><path d="M10 6.67h0.01"/></svg>',
    admin_sidebar_data_controls: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 1.67v16.66"/><path d="M14.17 4.17H7.92a2.92 2.92 0 0 0 0 5.83h4.58a2.92 2.92 0 0 1 0 5.83H5"/></svg>',
    admin_sidebar_files: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11.67 1.67H5A1.67 1.67 0 0 0 3.33 3.34v13.33A1.67 1.67 0 0 0 5 18.34h10a1.67 1.67 0 0 0 1.67-1.67v-10z"/><path d="M11.67 1.67v5h5"/></svg>',
    admin_sidebar_users: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13.33 17.5v-1.67a3.33 3.33 0 0 0-3.33-3.33H5a3.33 3.33 0 0 0-3.33 3.33v1.67"/><circle cx="7.5" cy="5.83" r="3.33"/><path d="M18.33 17.5v-1.67a3.33 3.33 0 0 0-2.5-3.23"/><path d="M13.33 2.61a3.33 3.33 0 0 1 0 6.46"/></svg>',
    admin_sidebar_connections: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7.5 10a2.5 2.5 0 0 0 2.5 2.5h3.33a2.5 2.5 0 0 0 0-5H10a2.5 2.5 0 0 0-2.5 2.5Z"/><path d="M12.5 10A2.5 2.5 0 0 0 10 7.5H6.67a2.5 2.5 0 0 0 0 5H10a2.5 2.5 0 0 0 2.5-2.5Z"/></svg>',
    admin_sidebar_leaderboard: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 1.67 7.5 6.67l-5.83 0.83 4.16 4.17-0.83 5.83 5-2.5 5 2.5-0.83-5.83 4.16-4.17-5.83-0.83-2.5-5z"/></svg>',
    admin_sidebar_compliance: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 18.33s6.67-3.33 6.67-8.33V4.17L10 1.67 3.33 4.17V10c0 5 6.67 8.33 6.67 8.33z"/><path d="m7.5 10 1.67 1.67 3.33-3.34"/></svg>',
    admin_sidebar_access_windows: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="8.33"/><path d="M10 5v5l3.33 1.67"/></svg>',
    admin_sidebar_preferences: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.33 17.5v-5.83"/><path d="M3.33 8.33V2.5"/><path d="M10 17.5V10"/><path d="M10 6.67V2.5"/><path d="M16.67 17.5v-4.17"/><path d="M16.67 10V2.5"/><path d="M0.83 11.67h5"/><path d="M7.5 6.67h5"/><path d="M14.17 13.33h5"/></svg>',
    admin_sidebar_security: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 18.33s6.67-3.33 6.67-8.33V4.17L10 1.67 3.33 4.17V10c0 5 6.67 8.33 6.67 8.33z"/></svg>',
    admin_sidebar_limits: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 16.67V8.33"/><path d="M15 16.67V3.33"/><path d="M5 16.67v-3.34"/></svg>',
    admin_sidebar_permissions: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17.5 1.67 15.83 3.33m-6.34 6.34a4.58 4.58 0 1 1-6.48 6.48 4.58 4.58 0 0 1 6.48-6.48zm0 0 3.43-3.42m0 0 2.5 2.5 2.91-2.92-2.5-2.5m-2.91 2.92 2.91-2.92"/></svg>',
    admin_sidebar_default: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="8.33"/><path d="M10 6.67V10"/><path d="M10 13.33h0.01"/></svg>',

    arrow_top: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M9.293 16.5V5.207L4 10.5a.707.707 0 0 1-1-1L9.5 3a.707.707 0 0 1 1 0L17 9.5a.707.707 0 0 1-1 1l-5.293-5.293V16.5a.707.707 0 0 1-1.414 0"/></svg>',
    arrow_top_right: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M4.5 15.5a.707.707 0 0 1 0-1l9.293-9.293H7.5a.707.707 0 1 1 0-1.414h8a.707.707 0 0 1 .707.707v8a.707.707 0 1 1-1.414 0V6.207L5.5 15.5a.707.707 0 0 1-1 0"/></svg>',
    arrow_down: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M10.707 3.5v11.293L16 9.5a.707.707 0 0 1 1 1L10.5 17a.707.707 0 0 1-1 0L3 10.5a.707.707 0 1 1 1-1l5.293 5.293V3.5a.707.707 0 1 1 1.414 0"/></svg>',
    arrow_right: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M3.5 9.293h11.293L9.5 4a.707.707 0 0 1 1-1L17 9.5a.707.707 0 0 1 0 1L10.5 17a.707.707 0 0 1-1-1l5.293-5.293H3.5a.707.707 0 0 1 0-1.414"/></svg>',
    send_now: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M3.5 9.293h9.586L8.793 5a.707.707 0 1 1 1-1l5.5 5.5a.707.707 0 0 1 0 1l-5.5 5.5a.707.707 0 1 1-1-1l4.293-4.293H3.5a.707.707 0 1 1 0-1.414"/><rect x="16.293" y="3.5" width="1.414" height="13" rx=".707"/></svg>',
    school: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" class="icon" aria-hidden="true"><path d="M10.37 3.35c2.35 1.4 4.84 2.95 7.19 4.34a.75.75 0 0 1 .18.67v5.63a.75.75 0 0 1-1.5 0V9.28l-5.87 3.36a.75.75 0 0 1-.74 0l-7-4a.75.75 0 0 1 0-1.3l7-4a.75.75 0 0 1 .74 0ZM10 4.86 4.51 8 10 11.14 15.49 8zM5.25 9.6h1.5v3.9a3.25 1.75 0 0 0 6.5 0V9.6h1.5v3.9a4.75 3.25 0 0 1-9.5 0z"/></svg>',
    business: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M4.5 6.5h2V5a2 2 0 0 1 2-2h3a2 2 0 0 1 2 2v1.5h2a2 2 0 0 1 2 2V15a2 2 0 0 1-2 2h-11a2 2 0 0 1-2-2V8.5a2 2 0 0 1 2-2m0 1.5a.5.5 0 0 0-.5.5V15a.5.5 0 0 0 .5.5h11a.5.5 0 0 0 .5-.5V8.5a.5.5 0 0 0-.5-.5zM12 6.5V5a.5.5 0 0 0-.5-.5h-3A.5.5 0 0 0 8 5v1.5z"/></svg>',
    night: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M17.67 11.107A7.75 7.75 0 1 1 8.893 2.33a.75.75 0 0 1 .792 1.049 5.25 5.25 0 0 0 6.936 6.936.75.75 0 0 1 1.049.792m-1.792 1.021a6.25 6.25 0 1 1-8.006-8.006 6.75 6.75 0 0 0 8.006 8.006"/></svg>',
    file: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path fill-rule="evenodd" d="M10.25 2h-3.5A2.75 2.75 0 0 0 4 4.75v10.5A2.75 2.75 0 0 0 6.75 18h6.5A2.75 2.75 0 0 0 16 15.25v-7.5a.75.75 0 0 0-.22-.53l-5-5a.75.75 0 0 0-.53-.22m-3.5 1.5H9.5v4.25a.75.75 0 0 0 .75.75h4.25v6.75a1.25 1.25 0 0 1-1.25 1.25h-6.5a1.25 1.25 0 0 1-1.25-1.25V4.75A1.25 1.25 0 0 1 6.75 3.5M11 7V4.561L13.439 7Z"/></svg>',
    image_gen: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="2.5" width="15" height="15" rx="1.67"/><circle cx="7.5" cy="7.5" r="1.67"/><path d="M17.5 12.5l-2.57-2.57a1.67 1.67 0 0 0-2.36 0L5 17.5"/></svg>',
    code_execution: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m3.33 14.17 5-5-5-5M10 15.83h6.67"/></svg>',
    uptime: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18.33 10H15l-2.5 7.5-5-15L5 10H1.67"/></svg>',
    cost: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18.33 10H15l-2.5 7.5-5-15-2.5 7.5H1.67"/></svg>',
    layout: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1.67" y="1.67" width="6.66" height="6.66" rx=".83"/><rect x="11.67" y="1.67" width="6.66" height="6.66" rx=".83"/><rect x="1.67" y="11.67" width="6.66" height="6.66" rx=".83"/><rect x="11.67" y="11.67" width="6.66" height="6.66" rx=".83"/></svg>',
    server: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1.67" y="1.67" width="16.66" height="6.66" rx="1.67" ry="1.67"/><rect x="1.67" y="11.67" width="16.66" height="6.66" rx="1.67" ry="1.67"/><path d="M5 5h.01M5 15h.01"/></svg>',
    protection: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 18.33S16.67 15 16.67 10V4.17L10 1.67l-6.67 2.5V10c0 5 6.67 8.33 6.67 8.33"/><path d="m7.5 10 1.67 1.67 3.33-3.34"/></svg>',
    preferences: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor"><path d="M4.25 3.5a.75.75 0 0 1 1.5 0v9.75h.5a.75.75 0 0 1 0 1.5h-.5v1.75a.75.75 0 0 1-1.5 0v-1.75h-.5a.75.75 0 0 1 0-1.5h.5zm5 0a.75.75 0 0 1 1.5 0v1.75h.5a.75.75 0 0 1 0 1.5h-.5v9.75a.75.75 0 0 1-1.5 0V6.75h-.5a.75.75 0 0 1 0-1.5h.5zm5 0a.75.75 0 0 1 1.5 0v6.75h.5a.75.75 0 0 1 0 1.5h-.5v4.75a.75.75 0 0 1-1.5 0v-4.75h-.5a.75.75 0 0 1 0-1.5h.5z"/></svg>',
    notification: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6.67A5 5 0 0 0 5 6.67c0 5.83-2.5 7.5-2.5 7.5h15s-2.5-1.67-2.5-7.5"/><path d="M11.44 17.5a1.67 1.67 0 0 1-2.88 0"/></svg>', 
    sun: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="4.17"/><path d="M10 .83v1.67"/><path d="M10 17.5v1.67"/><path d="m3.52 3.52 1.18 1.18"/><path d="m15.3 15.3 1.18 1.18"/><path d="M.83 10H2.5"/><path d="M17.5 10h1.67"/><path d="m3.52 16.48 1.18-1.18"/><path d="m15.3 4.7 1.18-1.18"/></svg>', 
    moon: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 2.5a5 5 0 0 0 7.5 7.5 7.5 7.5 0 1 1-7.5-7.5Z"/></svg>',
    aura: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 1.67V5M10 15V18.33M4.11 4.11l2.36 2.36M13.53 13.53l2.36 2.36M1.67 10H5M15 10h3.33M4.11 15.89l2.36-2.36M13.53 6.47l2.36-2.36"/></svg>',
    lock: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="9.17" width="15" height="9.17" rx="1.67" ry="1.67"/><path d="M5.83 9.17V5.83a4.17 4.17 0 0 1 8.34 0v3.34"/></svg>',
    bars: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 16.67V8.33m5 8.34V3.33M5 16.67v-3.34"/></svg>',
    groups: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13.33 17.5v-1.67A3.33 3.33 0 0 0 10 12.5H5a3.33 3.33 0 0 0-3.33 3.33v1.67"/><circle cx="7.5" cy="5.83" r="3.33"/><path d="M18.33 17.5v-1.67a3.33 3.33 0 0 0-2.5-3.23m-2.5-9.99a3.33 3.33 0 0 1 0 6.46"/></svg>',
    video_gen: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1.67" y="5" width="11.66" height="10" rx="1.67"/><path d="m18.33 6.67-5 3.33 5 3.33V6.67Z"/></svg>',
    mobile: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4.17" y="1.67" width="11.66" height="16.66" rx="1.67" ry="1.67"/><path d="M10 15h.01"/></svg>',
    desktop: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="1.67" y="2.5" width="16.66" height="11.67" rx="1.67" ry="1.67"/><path d="M6.67 17.5h6.66"/><path d="M10 14.17v3.33"/></svg>',
    folder: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg" class="icon" aria-hidden="true"><path d="M4 3h3.5a1 1 0 0 1 .7.3l1.6 1.4a1 1 0 0 0 .7.3H16a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2"/></svg>',
    attachment: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m16.52 9.41-7.07 7.08a4.17 4.17 0 0 1-5.89-5.9l7.07-7.07a2.92 2.92 0 0 1 4.13 4.13l-7.07 7.08a1.67 1.67 0 0 1-2.36-2.36l7.08-7.07"/></svg>',
    open_window: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 10.83v5a1.67 1.67 0 0 1-1.67 1.67H4.17a1.67 1.67 0 0 1-1.67-1.67V6.67A1.67 1.67 0 0 1 4.17 5h5m3.33-2.5h5v5m-9.17 4.17L17.5 2.5"/></svg>',
    user_add: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13.33 17.5v-1.67a3.33 3.33 0 0 0-3.33-3.33H5a3.33 3.33 0 0 0-3.33 3.33v1.67"/><circle cx="7.5" cy="5.83" r="3.33"/><path d="M15.83 6.67v5"/><path d="M18.33 9.17h-5"/></svg>',
    play: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.17 2.5 15.83 10 4.17 17.5V2.5Z"/></svg>',
    pause: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><rect x="5.42" y="3.33" width="3.33" height="13.34" rx=".83"/><rect x="11.25" y="3.33" width="3.33" height="13.34" rx=".83"/></svg>',
    recording: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><circle cx="10" cy="10" r="4.17"/></svg>',
    playing: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><rect x="5" y="5" width="10" height="10" rx="1.67"/></svg>',
    eye: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M1.5 10c1.4-3.8 4.4-6.2 8.2-6.2s6.8 2.4 8.2 6.2c-1.4 3.8-4.4 6.2-8.2 6.2S2.9 13.8 1.5 10z"/><circle cx="9.7" cy="10" r="2.5"/></svg>',
    mermaid: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="10" cy="10" r="2.5"/><circle cx="4.17" cy="5" r="1.67"/><circle cx="15.83" cy="5" r="1.67"/><circle cx="4.17" cy="15" r="1.67"/><circle cx="15.83" cy="15" r="1.67"/><path d="M7.92 8.33 5.42 6.25M12.08 8.33l2.5-2.08M7.92 11.67l-2.5 2.08M12.08 11.67l2.5 2.08"/></svg>',
    csv: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="2.5" width="15" height="15" rx="1.67"/><path d="M2.5 7.5h15M2.5 12.5h15M7.5 2.5v15M12.5 2.5v15"/></svg>',
    addFolder: '<svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" xmlns="http://www.w3.org/2000/svg" class="icon" aria-hidden="true"><path d="M9 17H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.5a1 1 0 0 1 .7.3l1.6 1.4a1 1 0 0 0 .7.3H16a2 2 0 0 1 2 2v3.5M15 12v6m-3-3h6"/></svg>',
    audio_gen: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.75 7.5v5h2.5L10 15.63V4.38L6.25 7.5h-2.5z"/><path d="M13.75 6.88a4.38 4.38 0 0 1 0 6.25"/></svg>',
    sparkle: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20"><path fill="currentColor" fill-rule="evenodd" d="M6.83 7.5a.67.67 0 0 1 1.34 0 4.33 4.33 0 0 0 4.33 4.33.67.67 0 0 1 0 1.34 4.33 4.33 0 0 0-4.33 4.33.67.67 0 0 1-1.34 0 4.33 4.33 0 0 0-4.33-4.33.67.67 0 0 1 0-1.34A4.33 4.33 0 0 0 6.83 7.5m.67 2.67a5.67 5.67 0 0 1-2.33 2.33 5.67 5.67 0 0 1 2.33 2.33 5.67 5.67 0 0 1 2.33-2.33 5.67 5.67 0 0 1-2.33-2.33M13.33 3a.67.67 0 0 1 1.34 0A2.33 2.33 0 0 0 17 5.33a.67.67 0 0 1 0 1.34A2.33 2.33 0 0 0 14.67 9a.67.67 0 0 1-1.34 0A2.33 2.33 0 0 0 11 6.67a.67.67 0 0 1 0-1.34A2.33 2.33 0 0 0 13.33 3M14 5.11a3.7 3.7 0 0 1-.89.89 3.7 3.7 0 0 1 .89.89 3.7 3.7 0 0 1 .89-.89 3.7 3.7 0 0 1-.89-.89"/></svg>',
    // Provider and connection icons
    serper: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    you: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    perplexity: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    nebius: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    nvidia: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    openai: OMLORIX_PROVIDER_SERVICE_ICON,
    anthropic: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    google_aistudio: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    ollama: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    openrouter: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    alibaba: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    baidu: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    exa: OMLORIX_PROVIDER_SERVICE_ICON,
    lmstudio: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    meta: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    google: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    apple: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    microsoft: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    minimax: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    mistral: OMLORIX_PROVIDER_SERVICE_ICON,
    qwen: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    xai: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    tavily: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    duckduckgo: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    searxng: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    cloudflare: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    firecrawl: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    aiohttp: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    crawl4ai: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    amazon: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    elevenlabs: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    deepseek: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    claude: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    gemma: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    gemini: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    grok: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    kimi: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    github: OMLORIX_PROVIDER_SERVICE_ICON,
    gmail: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    google_calendar: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    google_drive: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    slack: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    notion: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    youtube: OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON,
    dashboard: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="2.5" width="5.833" height="7.5" rx=".833"/><rect x="11.667" y="2.5" width="5.833" height="4.167" rx=".833"/><rect x="11.667" y="10" width="5.833" height="7.5" rx=".833"/><rect x="2.5" y="13.333" width="5.833" height="4.167" rx=".833"/></svg>',
    llmModels: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 13.33V6.67a1.67 1.67 0 0 0-.83-1.45l-5.84-3.33a1.67 1.67 0 0 0-1.66 0L3.33 5.22a1.67 1.67 0 0 0-.83 1.45v6.66a1.67 1.67 0 0 0 .83 1.45l5.84 3.33a1.67 1.67 0 0 0 1.66 0l5.84-3.33a1.67 1.67 0 0 0 .83-1.45"/></svg>',
    rateLimits: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M3.86 15.36a7.33 7.33 0 1 1 12.28 0m-.84-.6 1.7 1.2m-7-4.6 4.5 2.2"/><circle cx="10" cy="11.36" r="1.33" fill="currentColor" stroke="none"/></svg>',
    statistics: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M15 16.667V8.333m-5 8.334V3.333M5 16.667v-5"/></svg>',
    database: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="10" cy="4.167" rx="7.5" ry="2.5"/><path d="M17.5 10c0 1.383-3.333 2.5-7.5 2.5S2.5 11.383 2.5 10"/><path d="M2.5 4.167v11.666c0 1.384 3.333 2.5 7.5 2.5s7.5-1.116 7.5-2.5V4.167"/></svg>',
    enterprise: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.33" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17v-6.5A1.5 1.5 0 0 1 4.5 9H8V4.5A1.5 1.5 0 0 1 9.5 3h6A1.5 1.5 0 0 1 17 4.5V17Zm5-8v8"/><path d="M11 17v-2.5a1.5 1.5 0 0 1 3 0V17M11 6h3m-3 3.5h3m-9.5 3h2"/></svg>',
    star: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="m10 1.667 2.575 5.216 5.758.842-4.166 4.058.983 5.734L10 14.808l-5.15 2.709.983-5.734-4.166-4.058 5.758-.842z"/></svg>',
    heart: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M17.367 3.842a4.583 4.583 0 0 0-6.483 0L10 4.725l-.883-.883a4.583 4.583 0 0 0-6.484 6.483l.883.883L10 17.692l6.483-6.484.884-.883a4.583 4.583 0 0 0 0-6.483"/></svg>',
    home: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M2.5 7.5 10 1.667 17.5 7.5v9.167a1.667 1.667 0 0 1-1.667 1.666H4.167A1.667 1.667 0 0 1 2.5 16.667Z"/><path d="M7.5 18.333V10h5v8.333"/></svg>',
    briefcase: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><rect x="1.667" y="5.833" width="16.667" height="11.667" rx="1.667" ry="1.667"/><path d="M13.333 17.5V4.167A1.667 1.667 0 0 0 11.667 2.5H8.333a1.667 1.667 0 0 0-1.666 1.667V17.5"/></svg>',
    book: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M3.333 16.25a2.083 2.083 0 0 1 2.084-2.083h11.25"/><path d="M5.417 1.667h11.25v16.666H5.417a2.083 2.083 0 0 1-2.084-2.083V3.75a2.083 2.083 0 0 1 2.084-2.083"/></svg>',
    camera: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M19.167 15.833A1.667 1.667 0 0 1 17.5 17.5h-15a1.667 1.667 0 0 1-1.667-1.667V6.667A1.667 1.667 0 0 1 2.5 5h3.333L7.5 2.5h5L14.167 5H17.5a1.667 1.667 0 0 1 1.667 1.667Z"/><circle cx="10" cy="10.833" r="3.333"/></svg>',
    checklist: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M7.5 4.167H5.833a1.667 1.667 0 0 0-1.666 1.666v10A1.667 1.667 0 0 0 5.833 17.5h8.334a1.667 1.667 0 0 0 1.666-1.667v-10a1.667 1.667 0 0 0-1.666-1.666H12.5m-5 0a1.667 1.667 0 0 0 1.667 1.666h1.666A1.667 1.667 0 0 0 12.5 4.167m-5 0A1.667 1.667 0 0 1 9.167 2.5h1.666A1.667 1.667 0 0 1 12.5 4.167m-5 7.5 1.667 1.666L12.5 10"/></svg>',
    list: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M6.667 5H17.5M6.667 10H17.5M6.667 15H17.5M2.5 5h.01m-.01 5h.01m-.01 5h.01"/></svg>',
    calendar: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="3.333" width="15" height="15" rx="1.667" ry="1.667"/><path d="M13.333 1.667V5M6.667 1.667V5M2.5 8.333h15"/></svg>',
    shoppingCart: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><circle cx="7.5" cy="17.5" r=".833"/><circle cx="16.667" cy="17.5" r=".833"/><path d="M.833.833h3.334L6.4 11.992a1.67 1.67 0 0 0 1.667 1.341h8.1a1.67 1.67 0 0 0 1.666-1.341L19.167 5H5"/></svg>',
    gift: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M16.667 10v8.333H3.333V10M1.667 5.833h16.667V10H1.667zM10 18.333v-12.5m0 0H6.25a2.083 2.083 0 0 1 0-4.166c2.917 0 3.75 4.166 3.75 4.166m0 0h3.75a2.083 2.083 0 0 0 0-4.166c-2.917 0-3.75 4.166-3.75 4.166"/></svg>',
    flag: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M3.333 12.5s.833-.833 3.334-.833 4.166 1.666 6.666 1.666 3.334-.833 3.334-.833v-10s-.834.833-3.334.833-4.166-1.666-6.666-1.666-3.334.833-3.334.833zm0 5.833V12.5"/></svg>',
    tag: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"><path d="M10.775 2.5H4.167A1.667 1.667 0 0 0 2.5 4.167v6.608a1.667 1.667 0 0 0 .488 1.179l5.546 5.546a1.667 1.667 0 0 0 2.357 0l6.609-6.609a1.667 1.667 0 0 0 0-2.357l-5.546-5.546a1.667 1.667 0 0 0-1.179-.488Z"/><circle cx="6.25" cy="6.25" r="1.25"/></svg>',
    
    
    
    splitScreen: '<svg width="20" height="20" viewBox="0 0 20 20" fill="currentColor" xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" clip-rule="evenodd" d="M5.33 2.67h9.34c1.47 0 2.66 1.19 2.66 2.66v9.34c0 1.47-1.19 2.66-2.66 2.66H5.33c-1.47 0-2.66-1.19-2.66-2.66V5.33c0-1.47 1.19-2.66 2.66-2.66m0 1.33C4.59 4 4 4.59 4 5.33v9.34c0 .74.59 1.33 1.33 1.33H8c.74 0 1.33-.59 1.33-1.33V5.33C9.33 4.59 8.74 4 8 4zM12 4c-.74 0-1.33.59-1.33 1.33v9.34c0 .74.59 1.33 1.33 1.33h2.67c.74 0 1.33-.59 1.33-1.33V5.33C16 4.59 15.41 4 14.67 4z"/></svg>',
    
    
    
    
    
    wrapSvgBody(body, { viewBox = '0 0 20 20', width = '20', height = '20', fill = 'none', stroke = 'currentColor', strokeWidth = '1.35', className = '', ariaHidden = true } = {}) {
        const widthAttr = width ? ` width="${width}"` : '';
        const heightAttr = height ? ` height="${height}"` : '';
        const classAttr = className ? ` class="${className}"` : '';
        const hiddenAttr = ariaHidden ? ' aria-hidden="true"' : '';
        const strokeAttr = stroke ? ` stroke="${stroke}" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round"` : '';
        return `<svg viewBox="${viewBox}" fill="${fill}"${strokeAttr}${widthAttr}${heightAttr}${classAttr}${hiddenAttr}>${body || ''}</svg>`;
    },

    createSvgElement(markup, className = '') {
        const template = document.createElement('template');
        template.innerHTML = String(markup || '').trim();
        const svg = template.content.firstElementChild;
        if (svg && className) {
            svg.setAttribute('class', className);
        }
        return svg || document.createTextNode('');
    },

    /** Resolve a top-level or dotted icon registry key. */
    resolveIcon(name) {
        const parts = String(name || '').replace(/^Icons\./, '').split('.').filter(Boolean);
        let value = Icons;
        for (const part of parts) {
            value = value && value[part];
        }
        const markup = typeof value === 'string' ? value.trim() : '';
        return /^<(?:svg|img|span)\b/i.test(markup) ? markup : '';
    },

    /** Return trusted registry markup with presentation attributes applied. */
    withSvgAttributes(iconOrName, attributes = {}) {
        const rawValue = String(iconOrName || '').trim();
        const markup = /^<(?:svg|img|span)\b/i.test(rawValue)
            ? rawValue
            : Icons.resolveIcon(iconOrName);
        const svg = Icons.createSvgElement(markup);
        if (!svg || typeof svg.setAttribute !== 'function') return '';

        for (const [name, rawValue] of Object.entries(attributes || {})) {
            if (rawValue === undefined || rawValue === null || rawValue === false) continue;
            const value = rawValue === true ? '' : String(rawValue);
            if (name === 'class') {
                const classes = `${svg.getAttribute('class') || ''} ${value}`.trim();
                if (classes) svg.setAttribute('class', classes);
            } else if (name === 'style') {
                const styles = [svg.getAttribute('style'), value].filter(Boolean).join(';');
                if (styles) svg.setAttribute('style', styles);
            } else {
                svg.setAttribute(name, value);
            }
        }
        return svg.outerHTML;
    },

    /** Update an existing SVG without replacing its stable DOM reference. */
    setSvgContents(target, iconOrName) {
        if (!target || typeof target.setAttribute !== 'function') return false;
        const markup = /<svg\b/i.test(String(iconOrName || ''))
            ? String(iconOrName)
            : Icons.resolveIcon(iconOrName);
        if (!/^<svg\b/i.test(String(markup || '').trim())) return false;
        const source = Icons.createSvgElement(markup);
        if (!source || typeof source.getAttributeNames !== 'function') return false;

        const preserved = new Set(['id', 'class', 'width', 'height', 'style', 'aria-label']);
        for (const name of target.getAttributeNames()) {
            if (!preserved.has(name)) target.removeAttribute(name);
        }
        for (const name of source.getAttributeNames()) {
            if (!preserved.has(name)) target.setAttribute(name, source.getAttribute(name));
        }
        target.replaceChildren(...Array.from(source.childNodes, (node) => node.cloneNode(true)));
        return true;
    },

    /** Replace declarative HTML placeholders with trusted elements from this registry. */
    renderInlineIcons(root = document) {
        if (!root || typeof root.querySelectorAll !== 'function') return 0;
        const placeholders = [];
        if (typeof root.matches === 'function' && root.matches('[data-omlorix-icon]')) {
            placeholders.push(root);
        }
        placeholders.push(...root.querySelectorAll('[data-omlorix-icon]'));

        let rendered = 0;
        for (const placeholder of placeholders) {
            const name = placeholder.getAttribute('data-omlorix-icon');
            const markup = Icons.resolveIcon(name);
            if (!markup) {
                console.error(`Unknown Omlorix icon: ${name}`);
                continue;
            }
            const svg = Icons.createSvgElement(markup);
            if (!svg || typeof svg.setAttribute !== 'function') continue;
            for (const attribute of placeholder.attributes) {
                if (attribute.name === 'data-omlorix-icon') continue;
                if (attribute.name === 'class') {
                    const classes = `${svg.getAttribute('class') || ''} ${attribute.value}`.trim();
                    if (classes) svg.setAttribute('class', classes);
                } else if (attribute.name === 'style') {
                    const styles = [svg.getAttribute('style'), attribute.value].filter(Boolean).join(';');
                    if (styles) svg.setAttribute('style', styles);
                } else {
                    svg.setAttribute(attribute.name, attribute.value);
                }
            }
            placeholder.replaceWith(svg);
            rendered += 1;
        }
        return rendered;
    },

    /** Build the non-interactive fallback image used for unavailable slides. */
    createSlidePlaceholder(label) {
        const escapedLabel = String(label || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&apos;');
        return `<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080"><rect width="100%" height="100%" fill="#111318"/><text x="50%" y="50%" fill="#ffffff" font-family="sans-serif" font-size="48" text-anchor="middle">${escapedLabel}</text></svg>`;
    },
}

// Semantic 20 px icons migrated from page-local SVG markup.
// Each UI glyph uses the same square viewport, currentColor fill, rounded geometry, and about 2.67 px optical padding.
Icons.key = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M7.333 9.333a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm0 2.667a1.333 1.333 0 1 0 0 2.666 1.333 1.333 0 0 0 0-2.666Z\"/><path d=\"m9.55 10.17 5.74-5.74a1.333 1.333 0 0 1 1.886 1.886l-1.11 1.11-.943-.943-.943.943.943.943-.943.943-.943-.943-2.74 2.74-1.937-.937Z\"/></svg>";
Icons.proxy = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M8.333 10a1.667 1.667 0 1 1 3.334 0 1.667 1.667 0 0 1-3.334 0ZM3.333 9.333h3.334a.667.667 0 1 1 0 1.334H3.333a.667.667 0 1 1 0-1.334Zm10 0h3.334a.667.667 0 1 1 0 1.334h-3.334a.667.667 0 1 1 0-1.334ZM4.057 5.529C7.34 2.246 12.66 2.246 15.943 5.529L15 6.472c-2.762-2.762-7.238-2.762-10 0l-.943-.943Zm0 8.942L5 13.528c2.762 2.762 7.238 2.762 10 0l.943.943c-3.283 3.283-8.603 3.283-11.886 0Z\"/></svg>";
Icons.checkCircle =     "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667a7.333 7.333 0 1 1 0 14.666 7.333 7.333 0 0 1 0-14.666ZM10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12Z\"/><path d=\"m5.3 9.53 1.17-1.17L9 10.89l4.53-4.53 1.17 1.17L9 13.23 5.3 9.53Z\"/></svg>";
Icons.shieldPlus = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667 16.667 5.333v4.6c0 3.66-2.4 6.4-6.667 7.4-4.267-1-6.667-3.74-6.667-7.4v-4.6L10 2.667Zm0 1.433L4.667 6.233v3.7c0 2.9 1.773 4.93 5.333 5.9 3.56-.97 5.333-3 5.333-5.9v-3.7L10 4.1Z\"/><path d=\"M9.333 7.333h1.334v2h2v1.334h-2v2H9.333v-2h-2V9.333h2v-2Z\"/></svg>";
Icons.storageDrive = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M5.527 3.333h8.946c.758 0 1.45.428 1.789 1.106l1.071 2.142v7.752a2 2 0 0 1-2 2H4.667a2 2 0 0 1-2-2V6.581l1.071-2.142a2 2 0 0 1 1.789-1.106ZM4 7.333h12v1.334H4V7.333Zm1.667 4a.833.833 0 1 0 0 1.667.833.833 0 0 0 0-1.667Zm3.333 0A.833.833 0 1 0 9 13a.833.833 0 0 0 0-1.667Z\"/></svg>";
Icons.chevronsLeft = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"m9.36 5.333.94.94L6.573 10l3.727 3.727-.94.94-4.2-4.2a.667.667 0 0 1 0-.934l4.2-4.2Zm6 0 .94.94L12.573 10l3.727 3.727-.94.94-4.2-4.2a.667.667 0 0 1 0-.934l4.2-4.2Z\"/></svg>";
Icons.shieldOff = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667 16.667 5.333v4.6c0 3.66-2.4 6.4-6.667 7.4-4.267-1-6.667-3.74-6.667-7.4v-4.6L10 2.667Zm0 1.433L4.667 6.233v3.7c0 2.9 1.773 4.93 5.333 5.9 3.56-.97 5.333-3 5.333-5.9v-3.7L10 4.1Z\"/><path d=\"m2.667 3.61.943-.943 13.723 13.723-.943.943L2.667 3.61Z\"/></svg>";
Icons.mapPin = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667A5.333 5.333 0 0 1 15.333 8c0 3.973-3.493 7.333-4.78 8.4a.86.86 0 0 1-1.106 0C8.16 15.333 4.667 11.973 4.667 8A5.333 5.333 0 0 1 10 2.667ZM10 5a2.333 2.333 0 1 0 0 4.667A2.333 2.333 0 0 0 10 5Z\"/></svg>";
Icons.smile = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667a7.333 7.333 0 1 1 0 14.666 7.333 7.333 0 0 1 0-14.666ZM10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12Z\"/><path d=\"M6.667 7.333a.833.833 0 1 1 0 1.667.833.833 0 0 1 0-1.667Zm6.666 0a.833.833 0 1 1 0 1.667.833.833 0 0 1 0-1.667Zm-6.72 4.334h1.474c.39.82 1.017 1.266 1.913 1.266s1.523-.446 1.913-1.266h1.474C12.95 13.28 11.77 14.267 10 14.267s-2.95-.987-3.387-2.6Z\"/></svg>";
Icons.panelLayout = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M4.667 2.667h10.666a2 2 0 0 1 2 2v10.666a2 2 0 0 1-2 2H4.667a2 2 0 0 1-2-2V4.667a2 2 0 0 1 2-2ZM4 4.667C4 4.298 4.298 4 4.667 4h10.666c.369 0 .667.298.667.667v10.666a.667.667 0 0 1-.667.667H4.667A.667.667 0 0 1 4 15.333V4.667Z\"/><path d=\"M3.333 7.333h13.334v1.334H3.333V7.333Zm4 1.334h1.334v8H7.333v-8Z\"/></svg>";
Icons.currencyDollar = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path transform=\"translate(.7 .225) scale(.815)\" d=\"M11.8 10.9c-2.27-.59-3-1.2-3-2.15 0-1.09 1.01-1.85 2.7-1.85 1.78 0 2.44.85 2.5 2.1h2.21c-.07-1.72-1.12-3.3-3.21-3.81V3h-3v2.16c-1.94.42-3.5 1.68-3.5 3.61 0 2.31 1.91 3.46 4.7 4.13 2.5.6 3 1.48 3 2.41 0 .69-.49 1.79-2.7 1.79-2.06 0-2.87-.92-2.98-2.1h-2.2c.12 2.19 1.76 3.42 3.68 3.83V21h3v-2.15c1.95-.37 3.5-1.5 3.5-3.55 0-2.84-2.43-3.81-4.7-4.4Z\"/></svg>";
Icons.callDuration = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.333 9.333h3.334a.667.667 0 1 1 0 1.334H3.333a.667.667 0 1 1 0-1.334Zm10 0h3.334a.667.667 0 1 1 0 1.334h-3.334a.667.667 0 1 1 0-1.334ZM7.333 6.667a.667.667 0 0 1 1.334 0v6.666a.667.667 0 1 1-1.334 0V6.667Zm4 0a.667.667 0 0 1 1.334 0v6.666a.667.667 0 1 1-1.334 0V6.667Z\"/></svg>";
Icons.sidebarNavigation = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M4.667 3.333h10.666a2 2 0 0 1 2 2v9.334a2 2 0 0 1-2 2H4.667a2 2 0 0 1-2-2V5.333a2 2 0 0 1 2-2Zm0 1.334a.667.667 0 0 0-.667.666v9.334c0 .368.298.666.667.666h10.666a.667.667 0 0 0 .667-.666V5.333a.667.667 0 0 0-.667-.666H4.667Z\"/><path d=\"M7.333 4h1.334v12H7.333V4Zm-2 2.333a.667.667 0 1 1 0 1.334.667.667 0 0 1 0-1.334Zm0 3a.667.667 0 1 1 0 1.334.667.667 0 0 1 0-1.334Zm0 3a.667.667 0 1 1 0 1.334.667.667 0 0 1 0-1.334Z\"/></svg>";
Icons.sidebarPanel = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" aria-hidden=\"true\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M5.67 2.67h8.66a3 3 0 0 1 3 3v8.66a3 3 0 0 1-3 3H5.67a3 3 0 0 1-3-3V5.67a3 3 0 0 1 3-3m0 1.33A1.67 1.67 0 0 0 4 5.67v8.66A1.67 1.67 0 0 0 5.67 16h1.66V4zm3 0v12h5.66A1.67 1.67 0 0 0 16 14.33V5.67A1.67 1.67 0 0 0 14.33 4z\"/></svg>";
Icons.temporaryChat = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.33 16.67 4.8 12.4A6.2 6.2 0 1 1 7.6 15.2Z\" stroke-dasharray=\"5.5 3.3\" stroke-dashoffset=\"2.75\"/></svg>";
Icons.save = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M4.167 2.667h8.654c.309 0 .605.122.823.34l2.682 2.682c.218.218.341.514.341.823v8.821A2.333 2.333 0 0 1 14.333 17.667H5.667a2.333 2.333 0 0 1-2.334-2.334v-11.5c0-.644.523-1.166 1.167-1.166h-.333ZM4.667 4v11.333c0 .553.447 1 1 1h8.666c.553 0 1-.447 1-1V6.667h-2.666A1.667 1.667 0 0 1 11 5V4H4.667Zm7.666.276V5c0 .184.15.333.334.333h.723l-1.057-1.057ZM7.333 10c0-.46.374-.833.834-.833h3.666c.46 0 .834.373.834.833v3.333c0 .46-.374.834-.834.834H8.167a.833.833 0 0 1-.834-.834V10Zm1.334.5v2.333h2.666V10.5H8.667Z\"/></svg>";
Icons.palette = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667a7.333 7.333 0 0 0 0 14.666h.667a1.667 1.667 0 0 0 1.48-2.433c-.42-.807.16-1.9 1.073-1.9H14a3.333 3.333 0 0 0 3.333-3.333c0-3.867-3.333-7-7.333-7ZM6.333 6a.833.833 0 1 0 0 1.667.833.833 0 0 0 0-1.667ZM10 4.667a.833.833 0 1 0 0 1.666.833.833 0 0 0 0-1.666Zm3.667 2a.833.833 0 1 0 0 1.666.833.833 0 0 0 0-1.666ZM5.333 10a.833.833 0 1 0 0 1.667.833.833 0 0 0 0-1.667Z\"/></svg>";
Icons.filePlus = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M5.333 2.667h6L16.667 8v7.333a2 2 0 0 1-2 2H5.333a2 2 0 0 1-2-2V4.667a2 2 0 0 1 2-2Zm0 1.333a.667.667 0 0 0-.666.667v10.666c0 .369.298.667.666.667h9.334a.667.667 0 0 0 .666-.667V8.667h-2.666a2 2 0 0 1-2-2V4H5.333Zm6.667.943V6.667c0 .368.298.666.667.666h1.723L12 4.943Z\"/><path d=\"M9.333 9h1.334v2h2v1.333h-2v2H9.333v-2h-2V11h2V9Z\"/></svg>";
Icons.generalPreferences = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.333 5.333h4.334a2 2 0 0 1 3.666 0h5.334a.667.667 0 1 1 0 1.334h-5.334a2 2 0 0 1-3.666 0H3.333a.667.667 0 1 1 0-1.334Zm0 4h8.334a2 2 0 0 1 3.666 0h1.334a.667.667 0 1 1 0 1.334h-1.334a2 2 0 0 1-3.666 0H3.333a.667.667 0 1 1 0-1.334Zm0 4h2.334a2 2 0 0 1 3.666 0h7.334a.667.667 0 1 1 0 1.334H9.333a2 2 0 0 1-3.666 0H3.333a.667.667 0 1 1 0-1.334ZM9.5 4.667a.667.667 0 1 0 0 1.333.667.667 0 0 0 0-1.333Zm4 4a.667.667 0 1 0 0 1.333.667.667 0 0 0 0-1.333Zm-6 4a.667.667 0 1 0 0 1.333.667.667 0 0 0 0-1.333Z\"/></svg>";
Icons.appearance = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M9.333 2.667a.667.667 0 1 1 1.334 0v1.166a.667.667 0 1 1-1.334 0V2.667Zm0 13.5a.667.667 0 1 1 1.334 0v1.166a.667.667 0 1 1-1.334 0v-1.166ZM2.667 9.333h1.166a.667.667 0 1 1 0 1.334H2.667a.667.667 0 1 1 0-1.334Zm13.5 0h1.166a.667.667 0 1 1 0 1.334h-1.166a.667.667 0 1 1 0-1.334ZM4.815 3.872l.825.825a.667.667 0 1 1-.943.943l-.825-.825a.667.667 0 1 1 .943-.943Zm9.545 9.545.825.825a.667.667 0 1 1-.943.943l-.825-.825a.667.667 0 1 1 .943-.943Zm.825-8.602-.825.825a.667.667 0 1 1-.943-.943l.825-.825a.667.667 0 1 1 .943.943ZM5.64 14.36l-.825.825a.667.667 0 1 1-.943-.943l.825-.825a.667.667 0 1 1 .943.943ZM10 5.333a4.667 4.667 0 1 1 0 9.334 4.667 4.667 0 0 1 0-9.334Z\"/></svg>";
Icons.artificialAnalysis = OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON;
Icons.shieldAlert = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667 16.667 5.333v4.6c0 3.66-2.4 6.4-6.667 7.4-4.267-1-6.667-3.74-6.667-7.4v-4.6L10 2.667Zm0 1.433L4.667 6.233v3.7c0 2.9 1.773 4.93 5.333 5.9 3.56-.97 5.333-3 5.333-5.9v-3.7L10 4.1Z\"/><path d=\"M9.333 7h1.334v4.667H9.333V7Zm0 6h1.334v1.333H9.333V13Z\"/></svg>";
Icons.passkey = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M10 2.667a5.333 5.333 0 1 1 0 10.666 5.333 5.333 0 0 1 0-10.666ZM10 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8Zm0 2.333a1.667 1.667 0 1 0 0 3.334 1.667 1.667 0 0 0 0-3.334Z\"/><path d=\"M9.333 12.667h1.334v3.666h2.666v1.334H6.667v-1.334h2.666v-3.666Z\"/></svg>";
Icons.email = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M4.667 3.333h10.666a2 2 0 0 1 2 2v9.334a2 2 0 0 1-2 2H4.667a2 2 0 0 1-2-2V5.333a2 2 0 0 1 2-2Zm0 1.334a.667.667 0 0 0-.667.666v9.334c0 .368.298.666.667.666h10.666a.667.667 0 0 0 .667-.666V5.333a.667.667 0 0 0-.667-.666H4.667Z\"/><path d=\"m3.11 5.887.78-1.107L10 9.087l6.11-4.307.78 1.107-6.507 4.59a.667.667 0 0 1-.766 0L3.11 5.887Z\"/></svg>";
Icons.chip = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M6.667 4.667h6.666a2 2 0 0 1 2 2v6.666a2 2 0 0 1-2 2H6.667a2 2 0 0 1-2-2V6.667a2 2 0 0 1 2-2ZM6 6.667C6 6.298 6.298 6 6.667 6h6.666c.369 0 .667.298.667.667v6.666a.667.667 0 0 1-.667.667H6.667A.667.667 0 0 1 6 13.333V6.667Z\"/><path d=\"M8 8h4v4H8V8ZM7.333 2.667h1.334V5H7.333V2.667Zm4 0h1.334V5h-1.334V2.667Zm-4 12.333h1.334v2.333H7.333V15Zm4 0h1.334v2.333h-1.334V15ZM2.667 7.333H5v1.334H2.667V7.333Zm0 4H5v1.334H2.667v-1.334Zm12.333-4h2.333v1.334H15V7.333Zm0 4h2.333v1.334H15v-1.334Z\"/></svg>";
Icons.verticalAlign = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.333 2.667h13.334a.667.667 0 1 1 0 1.333H3.333a.667.667 0 1 1 0-1.333Zm0 13.333h13.334a.667.667 0 1 1 0 1.333H3.333a.667.667 0 1 1 0-1.333ZM7.333 6.333c0-.552.448-1 1-1h3.334c.552 0 1 .448 1 1v7.334c0 .552-.448 1-1 1H8.333c-.552 0-1-.448-1-1V6.333Z\"/></svg>";
Icons.verticalAlignTop = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.333 2.667h13.334a.667.667 0 1 1 0 1.333H3.333a.667.667 0 1 1 0-1.333ZM7.333 5.333c0-.552.448-1 1-1h3.334c.552 0 1 .448 1 1v8.334c0 .552-.448 1-1 1H8.333c-.552 0-1-.448-1-1V5.333Z\"/></svg>";
Icons.verticalAlignMiddle = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.333 2.667h13.334a.667.667 0 1 1 0 1.333H3.333a.667.667 0 1 1 0-1.333Zm0 13.333h13.334a.667.667 0 1 1 0 1.333H3.333a.667.667 0 1 1 0-1.333ZM7.333 6.333c0-.552.448-1 1-1h3.334c.552 0 1 .448 1 1v7.334c0 .552-.448 1-1 1H8.333c-.552 0-1-.448-1-1V6.333Z\"/></svg>";
Icons.verticalAlignBottom = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.333 16h13.334a.667.667 0 1 1 0 1.333H3.333a.667.667 0 1 1 0-1.333ZM7.333 6.333c0-.552.448-1 1-1h3.334c.552 0 1 .448 1 1v8.334c0 .552-.448 1-1 1H8.333c-.552 0-1-.448-1-1V6.333Z\"/></svg>";
Icons.textSpacing = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M3.333 5.333h7.334a.667.667 0 1 1 0 1.334H3.333a.667.667 0 1 1 0-1.334Zm0 4h5.334a.667.667 0 1 1 0 1.334H3.333a.667.667 0 1 1 0-1.334Zm0 4h7.334a.667.667 0 1 1 0 1.334H3.333a.667.667 0 1 1 0-1.334Zm11.334-7.056-1.195 1.195-.944-.944 2.334-2.333a.667.667 0 0 1 .943 0l2.333 2.333-.943.944L16 6.277v7.446l1.195-1.195.943.944-2.333 2.333a.667.667 0 0 1-.943 0l-2.334-2.333.944-.944 1.195 1.195V6.277Z\"/></svg>";
Icons.eyeOff = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M2.667 10c1.6-3.333 4.167-5 7.333-5s5.733 1.667 7.333 5c-1.6 3.333-4.167 5-7.333 5s-5.733-1.667-7.333-5ZM4.2 10c1.333 2.444 3.267 3.667 5.8 3.667S14.467 12.444 15.8 10C14.467 7.556 12.533 6.333 10 6.333S5.533 7.556 4.2 10Z\"/><path d=\"M10 7.667a2.333 2.333 0 1 1 0 4.666 2.333 2.333 0 0 1 0-4.666ZM2.667 3.61l.943-.943 13.723 13.723-.943.943L2.667 3.61Z\"/></svg>";
Icons.externalLink = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M4.667 5.333h6v1.334h-6a.667.667 0 0 0-.667.666v8c0 .369.298.667.667.667h8a.667.667 0 0 0 .666-.667v-6h1.334v6a2 2 0 0 1-2 2h-8a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z\"/><path d=\"M11.333 2.667h6v6H16v-3.4l-6.195 6.195-.943-.943 6.195-6.196h-3.724V2.667Z\"/></svg>";
Icons.search = "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"20\" height=\"20\" viewBox=\"0 0 20 20\" fill=\"currentColor\" stroke-width=\"1.33\" stroke-linecap=\"round\" stroke-linejoin=\"round\" aria-hidden=\"true\" focusable=\"false\"><path fill-rule=\"evenodd\" clip-rule=\"evenodd\" d=\"M8.667 2.667a6 6 0 1 1 0 12 6 6 0 0 1 0-12Zm0 1.333a4.667 4.667 0 1 0 0 9.333A4.667 4.667 0 0 0 8.667 4Z\"/><path d=\"m12.471 13.414.943-.943 3.919 3.919-.943.943-3.919-3.919Z\"/></svg>";

// Keep the legacy key used by the chat attachment workflow, but deliberately
// resolve it to Omlorix artwork rather than redistributing the Google Drive mark.
Icons.chatFilesGoogleDrive = OMLORIX_NEUTRAL_EXTERNAL_SERVICE_ICON;

// Every surface that represents a managed connection must resolve its logo
// through this single map. In particular, chat connector mentions and the
// Connections workspace should never drift into showing different artwork.
Icons.connectionProviderIconKeys = Object.freeze({
    github: 'github',
    notion: 'notion',
    slack: 'slack',
    gmail: 'gmail',
    google_calendar: 'google_calendar',
    google_drive: 'google_drive',
});

/** Return the preset icon key used by managed connection cards. */
Icons.getConnectionProviderIconKey = (provider) => {
    const normalizedProvider = String(provider || '').trim().toLowerCase();
    return Icons.connectionProviderIconKeys[normalizedProvider] || '';
};

const folderIconOptions = {
    // Folder
    folder: Icons.folder,
    // Archive
    archive: Icons.archive,
    // Text document 
    document: Icons.text_document,
    // Image
    image: Icons.image_gen, 
    // Video 
    video: Icons.video_gen,
    // Music
    music: Icons.music,
    // Code
    code: Icons.code,
    // Star
    star: Icons.star,
    // Heart
    heart: Icons.heart,
    // Home
    home: Icons.home,
    // Briefcase
    briefcase: Icons.briefcase,
    // Book
    book: Icons.book,
    // Camera
    camera: Icons.camera,
    // Download
    download: Icons.download,
    // Lock
    lock: Icons.lock, 
    // Shield
    secure: Icons.protection,
    // Lightning 
    quick: Icons.lightning
}


const todoIconOptions = {
    checklist: Icons.checklist,
    list: Icons.list,
    star: Icons.star,
    heart: Icons.heart,
    home: Icons.home,
    briefcase: Icons.briefcase,
    book: Icons.book,
    calendar: Icons.calendar, 
    shopping_cart: Icons.shoppingCart,
    gift: Icons.gift,
    music: Icons.music,
    camera: Icons.camera,
    map_pin: Icons.pin,
    flag: Icons.flag,
    lightning: Icons.lightning,
    sun: Icons.sun,
}

const workspaceIconPickerOptions = [
    { id: 'folder', name: 'Folder', iconKey: 'folder', svg: Icons.folder },
    { id: 'archive', name: 'Archive', iconKey: 'archive', svg: Icons.archive },
    { id: 'document', name: 'Document', iconKey: 'text_document', svg: Icons.text_document },
    { id: 'image', name: 'Image', iconKey: 'image_gen', svg: Icons.image_gen },
    { id: 'video', name: 'Video', iconKey: 'video_gen', svg: Icons.video_gen },
    { id: 'music', name: 'Music', iconKey: 'music', svg: Icons.music },
    { id: 'code', name: 'Code', iconKey: 'code', svg: Icons.code },
    { id: 'star', name: 'Star', iconKey: 'star', svg: Icons.star },
    { id: 'heart', name: 'Heart', iconKey: 'heart', svg: Icons.heart },
    { id: 'home', name: 'Home', iconKey: 'home', svg: Icons.home },
    { id: 'briefcase', name: 'Briefcase', iconKey: 'briefcase', svg: Icons.briefcase },
    { id: 'book', name: 'Book', iconKey: 'book', svg: Icons.book },
    { id: 'camera', name: 'Camera', iconKey: 'camera', svg: Icons.camera },
    { id: 'download', name: 'Download', iconKey: 'download', svg: Icons.download },
    { id: 'lock', name: 'Lock', iconKey: 'lock', svg: Icons.lock },
    { id: 'secure', name: 'Secure', iconKey: 'protection', svg: Icons.protection },
    { id: 'quick', name: 'Quick', iconKey: 'lightning', svg: Icons.lightning },
    { id: 'checklist', name: 'Checklist', iconKey: 'checklist', svg: Icons.checklist },
    { id: 'list', name: 'List', iconKey: 'list', svg: Icons.list },
    { id: 'calendar', name: 'Calendar', iconKey: 'calendar', svg: Icons.calendar },
    { id: 'shopping_cart', name: 'Shopping cart', iconKey: 'shoppingCart', svg: Icons.shoppingCart },
    { id: 'gift', name: 'Gift', iconKey: 'gift', svg: Icons.gift },
    { id: 'map_pin', name: 'Map pin', iconKey: 'pin', svg: Icons.pin },
    { id: 'flag', name: 'Flag', iconKey: 'flag', svg: Icons.flag },
    { id: 'lightning', name: 'Lightning', iconKey: 'lightning', svg: Icons.lightning },
    { id: 'sun', name: 'Sun', iconKey: 'sun', svg: Icons.sun },
    { id: 'tool', name: 'Tool', iconKey: 'tool', svg: Icons.tool },
    { id: 'sparkles', name: 'Sparkles', iconKey: 'lightning', svg: Icons.lightning },
]

Icons.workspaceIconPickerOptions = workspaceIconPickerOptions;
Icons.folderIconOptions = folderIconOptions;
Icons.todoIconOptions = todoIconOptions;

if (typeof globalThis !== 'undefined') {
    globalThis.Icons = Icons;
    globalThis.folderIconOptions = folderIconOptions;
    globalThis.todoIconOptions = todoIconOptions;
    globalThis.workspaceIconPickerOptions = workspaceIconPickerOptions;
}

const todoSortOptions = [
  { id: 'manual', nameKey: 'todos_sort_manual', name: 'Manual', icon: Icons.list },
  { id: 'date-asc', nameKey: 'todos_sort_date_oldest', name: 'Date (Oldest)', icon: Icons.arrow_down },
  { id: 'date-desc', nameKey: 'todos_sort_date_newest', name: 'Date (Newest)', icon: Icons.arrow_top },
  { id: 'due-date', nameKey: 'todos_sort_due_date', name: 'Due date', icon: Icons.calendar },
  { id: 'alpha-asc', nameKey: 'todos_sort_alpha_asc', name: 'A → Z', icon: Icons.arrow_down },
  { id: 'alpha-desc', nameKey: 'todos_sort_alpha_desc', name: 'Z → A', icon: Icons.arrow_top },
  { id: 'priority', nameKey: 'todos_sort_priority', name: 'Priority', icon: Icons.layers},
]

Icons.todoSortOptions = todoSortOptions;

if (typeof globalThis !== 'undefined') {
    globalThis.todoSortOptions = todoSortOptions;
}

/**
 * Hardcoded SVG markup for the Markdown editor toolbar.
 *
 * Each icon remains self-contained so consumers can use the value directly
 * without relying on a runtime SVG-building helper.
 */
const markdownEditorIcons = {
    undo: Icons.undo,
    redo: Icons.redo,
    bold: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M4.67 3.42a.75.75 0 0 1 .75-.75h4.91c2.75 0 4.34 1.42 4.34 3.67 0 1.28-.6 2.3-1.63 2.96 1.47.65 2.3 1.85 2.3 3.5 0 2.58-1.95 4.53-4.85 4.53H5.42a.75.75 0 0 1-.75-.75Zm1.5.75v4.46h4.05c1.87 0 2.95-.78 2.95-2.21 0-1.47-1.02-2.25-2.95-2.25Zm0 5.96v5.7h4.28c2.13 0 3.39-1.04 3.39-2.88 0-1.8-1.24-2.82-3.43-2.82Z"/></svg>',
    italic: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M8.12 2.67h7a.75.75 0 0 1 0 1.5H12.7L9.45 15.83h2.42a.75.75 0 0 1 0 1.5h-7a.75.75 0 0 1 0-1.5H7.9l3.25-11.66H8.12a.75.75 0 0 1 0-1.5"/></svg>',
    underline: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M5.25 3.42a.75.75 0 0 0-1.5 0V7.5a6.25 6.25 0 0 0 12.5 0V3.42a.75.75 0 0 0-1.5 0V7.5a4.75 4.75 0 0 1-9.5 0ZM4.17 15.83h11.66a.75.75 0 0 1 0 1.5H4.17a.75.75 0 0 1 0-1.5"/></svg>',
    strike: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><defs><mask id="a"><path fill="#fff" d="M0 0h20v20H0z"/><path d="M3.5 10h13" stroke="#000" stroke-width="3.5" fill="none" stroke-linecap="round"/></mask></defs><g fill="none" stroke="currentColor" stroke-linecap="round"><path d="M14.5 6c0-2-2-3-4.5-3S5.5 4 5.5 6s2 3 4.5 4 4.5 2 4.5 4-2 3-4.5 3-4.5-1-4.5-3" stroke-width="1.75" mask="url(#a)"/><path d="M3.5 10h13" stroke-width="1.5"/></g></svg>',
    code: Icons.code,
    sup: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="m3.5 10 5 5m0-5-5 5m8-10A2.25 2.25 0 0 1 16 5c0 2-2.5 3-4.5 4.5H16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"/></svg>',
    sub: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" aria-hidden="true"><path d="m3.5 5 5 5m0-5-5 5m8 3a2.25 2.25 0 0 1 4.5 0c0 2-2.5 3-4.5 4.5H16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"/></svg>',
    color: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4.5 14 10 3l5.5 11M7.25 8.5h5.5M4 17h12"/></svg>',
    highlight: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M11.5 2.5a1.5 1.5 0 0 1 2.12 0l2.88 2.88a1.5 1.5 0 0 1 0 2.12l-8 8h-5v-5zm.95.95-7.61 7.61v3.1h3.1l7.61-7.61zM3.5 17.33a.67.67 0 0 1 0-1.33h13a.67.67 0 0 1 0 1.33z"/></svg>',
    paint: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M12.5 3.5a1.5 1.5 0 0 1 2.12 0l1.88 1.88a1.5 1.5 0 0 1 0 2.12l-7 7-4-4zm.95.95L7.4 10.5l2.1 2.1 6.05-6.05zM5.5 10.5l4 4c-1.2 1.2-2.6 1.9-4.8 2.1a1.1 1.1 0 0 1-1.3-1.3c.2-2.2.9-3.6 2.1-4.8"/></svg>',
    quote: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M3.75 9c0-3.5 1.5-5.5 4.5-6.3a.67.67 0 0 1 .4 1.3c-1.9.7-3.1 2-3.5 4.2h1.6a1.5 1.5 0 0 1 1.5 1.5v3.8a1.5 1.5 0 0 1-1.5 1.5h-1.5a1.5 1.5 0 0 1-1.5-1.5zm1.34.5v4.16h1.82V9.5zm6.66-.5c0-3.5 1.5-5.5 4.5-6.3a.67.67 0 0 1 .4 1.3c-1.9.7-3.1 2-3.5 4.2h1.6a1.5 1.5 0 0 1 1.5 1.5v3.8a1.5 1.5 0 0 1-1.5 1.5h-1.5a1.5 1.5 0 0 1-1.5-1.5zm1.34.5v4.16h1.82V9.5z"/></svg>',
    list: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M2.97 4a.83.83 0 1 1 1.66 0 .83.83 0 0 1-1.66 0m3.83-.67a.67.67 0 0 0 0 1.34h9.7a.67.67 0 0 0 0-1.34zM2.97 10a.83.83 0 1 1 1.66 0 .83.83 0 0 1-1.66 0m3.83-.67a.67.67 0 0 0 0 1.34h9.7a.67.67 0 0 0 0-1.34zM2.97 16a.83.83 0 1 1 1.66 0 .83.83 0 0 1-1.66 0m3.83-.67a.67.67 0 0 0 0 1.34h9.7a.67.67 0 0 0 0-1.34z"/></svg>',
    ordered: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3.5 5.5 5 4v4M3.5 8h3m3.25-2h6.5M3.5 13a1.5 1.5 0 0 1 3 0c0 1.5-2 2-3 3h3m3.25-2h6.5"/></svg>',
    task: Icons.todo_management,
    outdent: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="m5.7 7.5-2 2a.707.707 0 0 0 0 1l2 2a.707.707 0 0 0 1-1l-.793-.793H7.5a.707.707 0 0 0 0-1.414H5.907L6.7 8.5a.707.707 0 0 0-1-1m10.593-4.207H3.707a.707.707 0 0 0 0 1.414h12.586a.707.707 0 0 0 0-1.414m0 4H9.707a.707.707 0 0 0 0 1.414h6.586a.707.707 0 0 0 0-1.414m0 4H9.707a.707.707 0 0 0 0 1.414h6.586a.707.707 0 0 0 0-1.414m0 4H3.707a.707.707 0 0 0 0 1.414h12.586a.707.707 0 0 0 0-1.414"/></svg>',
    indent: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M3.7 9.293h1.593L4.5 8.5a.707.707 0 0 1 1-1l2 2a.707.707 0 0 1 0 1l-2 2a.707.707 0 0 1-1-1l.793-.793H3.7a.707.707 0 0 1 0-1.414m12.593-6H3.707a.707.707 0 0 0 0 1.414h12.586a.707.707 0 0 0 0-1.414m0 4H9.707a.707.707 0 0 0 0 1.414h6.586a.707.707 0 0 0 0-1.414m0 4H9.707a.707.707 0 0 0 0 1.414h6.586a.707.707 0 0 0 0-1.414m0 4H3.707a.707.707 0 0 0 0 1.414h12.586a.707.707 0 0 0 0-1.414"/></svg>',
    alignLeft: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M3.5 3.25h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5m0 4h8a.75.75 0 0 1 0 1.5h-8a.75.75 0 0 1 0-1.5m0 4h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5m0 4h8a.75.75 0 0 1 0 1.5h-8a.75.75 0 0 1 0-1.5"/></svg>',
    alignCenter: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M3.5 3.25h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5m2.5 4h8a.75.75 0 0 1 0 1.5H6a.75.75 0 0 1 0-1.5m-2.5 4h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5m2.5 4h8a.75.75 0 0 1 0 1.5H6a.75.75 0 0 1 0-1.5"/></svg>',    
    alignRight: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path fill-rule="evenodd" clip-rule="evenodd" d="M3.5 3.25h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5m5 4h8a.75.75 0 0 1 0 1.5h-8a.75.75 0 0 1 0-1.5m-5 4h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5m5 4h8a.75.75 0 0 1 0 1.5h-8a.75.75 0 0 1 0-1.5"/></svg>',
    alignJustify: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" focusable="false"><path fill-rule="evenodd" clip-rule="evenodd" d="M3.5 3.25h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5ZM3.5 7.25h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5ZM3.5 11.25h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5ZM3.5 15.25h13a.75.75 0 0 1 0 1.5h-13a.75.75 0 0 1 0-1.5Z"></path></svg>',
    link: Icons.urlLink,
    image: Icons.image_gen,
    table: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M5 2.5h10A2.5 2.5 0 0 1 17.5 5v10a2.5 2.5 0 0 1-2.5 2.5H5A2.5 2.5 0 0 1 2.5 15V5A2.5 2.5 0 0 1 5 2.5M7 4H5a1 1 0 0 0-1 1v2h3Zm4.5 0h-3v3h3ZM16 5a1 1 0 0 0-1-1h-2v3h3ZM7 8.5H4v3h3Zm4.5 0h-3v3h3Zm4.5 0h-3v3h3ZM7 13H4v2a1 1 0 0 0 1 1h2Zm4.5 0h-3v3h3Zm4.5 0h-3v3h2a1 1 0 0 0 1-1Z"/></svg>',
    divider: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M16.293 9.293H3.707a.707.707 0 0 0 0 1.414h12.586a.707.707 0 0 0 0-1.414"/></svg>',
    more: Icons.ellipsis, 
    plus: Icons.plus,
    trash: Icons.trash,
    clear: '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true"><path d="M6.829 3.276a.707.707 0 0 1 1.342.448l-4 12a.707.707 0 0 1-1.342-.448Zm0 .448a.707.707 0 0 1 1.342-.448l4 12a.707.707 0 0 1-1.342.448ZM5.167 9.793h4.666a.707.707 0 0 1 0 1.414H5.167a.707.707 0 0 1 0-1.414M13 13a.707.707 0 0 1 1-1l4 4a.707.707 0 0 1-1 1Zm4-1a.707.707 0 0 1 1 1l-4 4a.707.707 0 0 1-1-1Z"/></svg>',
    check: Icons.check,
    omlorix: Icons.omlorix
};

Icons.markdownEditorIcons = markdownEditorIcons;

const featureIconBodies = {
    skillDefault: Icons.tool,
    note: Icons.text_document,
    prompt: Icons.chatFilesChooseChats,
    promptLightning: Icons.lightning,
    promptDocument: Icons.text_document,
    promptChat: Icons.chatFilesChooseChats,
    promptSettings: Icons.settings,
    check16: Icons.check,
    checkCircle24: Icons.todo, 
    modelCheck16: Icons.check,
    arrow16: Icons.chevronRight,
    fileUnsupported: Icons.file,
};

Icons.featureIconBodies = featureIconBodies;



const latexPdfStatusIcons = {
    error: Icons.close, 
    ready: Icons.check,
    compiling: Icons.loading_circle
};

Icons.latexPdfStatusIcons = latexPdfStatusIcons;

if (typeof globalThis !== 'undefined') {
    globalThis.featureIconBodies = featureIconBodies;
    globalThis.latexPdfStatusIcons = latexPdfStatusIcons;
}

if (typeof document !== 'undefined') {
    Icons.renderInlineIcons(document);
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => Icons.renderInlineIcons(document), { once: true });
    }
}
