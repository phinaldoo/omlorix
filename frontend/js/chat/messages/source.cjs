const fs = require('node:fs');
const path = require('node:path');

const STREAM_MESSAGE_FILE_NAMES = [
    'shared.js',
    'tool-config.js',
    'subagents.js',
    'accessibility.js',
    'file-handling.js',
    'user-messages.js',
    'generated-media.js',
    'assistant-content.js',
    'widgets.js',
    'reasoning.js',
    'tools.js',
    'actions.js',
    'completion.js',
    'delete-message.js',
    'edit-message.js',
];

const STREAM_MESSAGE_SCRIPT_URLS = STREAM_MESSAGE_FILE_NAMES.map(
    (fileName) => `/js/chat/messages/${fileName}`,
);

function readStreamMessagesSource() {
    return STREAM_MESSAGE_FILE_NAMES
        .map((fileName) => fs.readFileSync(path.join(__dirname, fileName), 'utf8'))
        .join('');
}

module.exports = {
    STREAM_MESSAGE_FILE_NAMES,
    STREAM_MESSAGE_SCRIPT_URLS,
    readStreamMessagesSource,
};
