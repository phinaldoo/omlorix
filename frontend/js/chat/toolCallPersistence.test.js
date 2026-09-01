const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { readStreamMessagesSource } = require('./messages/source.cjs');


test('persisted tool calls render canonical arguments and retain imported aliases', () => {
    const transcriptSource = fs.readFileSync(
        path.join(__dirname, 'chatTranscriptRenderer.js'),
        'utf8'
    );
    const adminSource = fs.readFileSync(
        path.join(__dirname, '..', 'admin_chats', 'script.js'),
        'utf8'
    );
    const streamSource = readStreamMessagesSource();

    assert.match(transcriptSource, /block\.meta\?\.arguments/);
    assert.match(transcriptSource, /block\.meta\?\.tool_args/);
    assert.match(transcriptSource, /block\.meta\?\.args/);
    assert.match(transcriptSource, /block\.meta \?\? null/);

    assert.match(adminSource, /meta\.arguments \?\? meta\.tool_args \?\? meta\.args/);
    assert.match(
        streamSource,
        /tool_meta\.id \?\? tool_meta\.tool_call_id \?\? tool_meta\.call_id \?\? tool_meta\.tool_use_id/
    );
    assert.match(transcriptSource, /deepResearchActivityByRunId/);
    assert.match(transcriptSource, /meta\.deep_research_activity/);
    assert.match(transcriptSource, /deep_research_activity: activity/);
    assert.match(streamSource, /widgetType === 'deep_research'/);
    assert.match(streamSource, /hydrateWidget\(widget, activity\)/);
});
