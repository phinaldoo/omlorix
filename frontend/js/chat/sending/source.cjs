const fs = require('node:fs');
const path = require('node:path');

const SEND_MESSAGE_SOURCE_PATHS = [
    '../composer/state-and-transport.js',
    '../rendering/state.js',
    '../rendering/preview-detection.js',
    '../rendering/mermaid-vega.js',
    '../rendering/code-editing-execution.js',
    '../rendering/visualization-bridge.js',
    '../rendering/visualization-renderers.js',
    '../rendering/mermaid-preview.js',
    '../rendering/preview-modals.js',
    '../rendering/preview-lifecycle.js',
    '../rendering/shared-utils.js',
    '../composer/attachments.js',
    '../rendering/markdown-parser.js',
    '../rendering/markdown-ui.js',
    '../messages/feedback.js',
    'history.js',
    'send.js',
    'regeneration.js',
];

const SEND_MESSAGE_SCRIPT_URLS = SEND_MESSAGE_SOURCE_PATHS.map(
    (sourcePath) => path.posix.normalize(`/js/chat/sending/${sourcePath}`),
);

function readSendMessageSource() {
    return SEND_MESSAGE_SOURCE_PATHS
        .map((sourcePath) => fs.readFileSync(path.join(__dirname, sourcePath), 'utf8'))
        .join('');
}

module.exports = {
    SEND_MESSAGE_SCRIPT_URLS,
    SEND_MESSAGE_SOURCE_PATHS,
    readSendMessageSource,
};
