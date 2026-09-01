const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function extractFunction(source, functionName) {
    const asyncStart = source.indexOf(`async function ${functionName}(`);
    const plainStart = source.indexOf(`function ${functionName}(`);
    const start = asyncStart >= 0 ? asyncStart : plainStart;
    assert.notEqual(start, -1, `expected ${functionName} in chatsHelper.js`);

    const bodyStart = source.indexOf('{', start);
    assert.notEqual(bodyStart, -1, `expected ${functionName} body`);

    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        const char = source[index];
        if (char === '{') {
            depth += 1;
        } else if (char === '}') {
            depth -= 1;
            if (depth === 0) {
                return source.slice(start, index + 1);
            }
        }
    }

    throw new Error(`Could not extract ${functionName}`);
}

function createChatContainer(activeChatId = 'chat-1') {
    const attributes = new Map();
    if (activeChatId) {
        attributes.set('data-chat-id', activeChatId);
    }

    return {
        setAttribute(name, value) {
            attributes.set(name, String(value));
        },
        getAttribute(name) {
            return attributes.has(name) ? attributes.get(name) : null;
        },
        removeAttribute(name) {
            attributes.delete(name);
        },
        hasAttribute(name) {
            return attributes.has(name);
        },
    };
}

function loadRestoreHelpers({ cachedChats = [], fetchPayload = null, loadProject = null } = {}) {
    const source = fs.readFileSync(path.join(__dirname, 'chatsHelper.js'), 'utf8');
    const chatContainer = createChatContainer('chat-1');
    const authedFetchCalls = [];

    const windowObject = {
        _projectSidebarSyncState: null,
        __pendingProjectSidebarRestore: null,
        authedFetch: async (url) => {
            authedFetchCalls.push(url);
            if (!fetchPayload) {
                return { ok: false };
            }
            return {
                ok: true,
                async json() {
                    return fetchPayload;
                },
            };
        },
        loadProject,
    };

    const context = {
        window: windowObject,
        document: {
            getElementById(id) {
                return id === 'chatContainer' ? chatContainer : null;
            },
        },
        console,
        readCachedChatList() {
            return cachedChats;
        },
        updateCachedChatListEntry(chat) {
            context.updatedChat = chat;
        },
        encodeURIComponent,
    };

    vm.runInNewContext(
        [
            extractFunction(source, 'findCachedChatById'),
            extractFunction(source, 'fetchChatDetailForProjectRestore'),
            extractFunction(source, 'queuePendingProjectSidebarRestore'),
            extractFunction(source, 'applyProjectSidebarStateForResolvedChat'),
            extractFunction(source, 'restoreProjectSidebarForChat'),
            extractFunction(source, 'syncProjectSidebarWithActiveChat'),
            'this.helpers = { restoreProjectSidebarForChat, applyProjectSidebarStateForResolvedChat, syncProjectSidebarWithActiveChat };',
        ].join('\n\n'),
        context,
        { filename: 'chatsHelper.js' },
    );

    return {
        helpers: context.helpers,
        chatContainer,
        windowObject,
        authedFetchCalls,
        getUpdatedChat: () => context.updatedChat,
    };
}

test('project sidebar restore queues pending project state before sidebar script is ready', async () => {
    const runtime = loadRestoreHelpers({
        fetchPayload: {
            id: 'chat-1',
            project_id: 'project-123',
        },
    });

    const result = await runtime.helpers.restoreProjectSidebarForChat('chat-1');

    assert.equal(result.project_id, 'project-123');
    assert.equal(runtime.chatContainer.getAttribute('data-project-id'), 'project-123');
    assert.deepEqual({ ...runtime.windowObject._projectSidebarSyncState }, {
        chatId: 'chat-1',
        projectId: 'project-123',
    });
    assert.deepEqual({ ...runtime.windowObject.__pendingProjectSidebarRestore }, {
        chatId: 'chat-1',
        projectId: 'project-123',
    });
    assert.equal(runtime.authedFetchCalls.length, 1);
    assert.deepEqual({ ...runtime.getUpdatedChat() }, {
        id: 'chat-1',
        project_id: 'project-123',
    });
});

test('project sidebar restore applies cached project chat immediately when loadProject is available', async () => {
    const loadCalls = [];
    const runtime = loadRestoreHelpers({
        cachedChats: [
            {
                id: 'chat-1',
                project_id: 'project-abc',
            },
        ],
        loadProject(projectId, chatId) {
            loadCalls.push({ projectId, chatId });
        },
    });

    const result = await runtime.helpers.restoreProjectSidebarForChat('chat-1');

    assert.equal(result.project_id, 'project-abc');
    assert.deepEqual(loadCalls, [{ projectId: 'project-abc', chatId: 'chat-1' }]);
    assert.equal(runtime.windowObject.__pendingProjectSidebarRestore, null);
});

test('project sidebar sync preserves restored project state when active chat is not in the current sidebar page', () => {
    const hideCalls = [];
    const runtime = loadRestoreHelpers({
        loadProject() {},
    });
    runtime.windowObject.hideProjectSidebar = () => {
        hideCalls.push('hide');
    };
    runtime.windowObject._projectSidebarSyncState = {
        chatId: 'chat-1',
        projectId: 'project-keep',
    };
    runtime.chatContainer.setAttribute('data-project-id', 'project-keep');

    runtime.helpers.syncProjectSidebarWithActiveChat([
        { id: 'chat-2', project_id: null },
        { id: 'chat-3', project_id: 'project-other' },
    ]);

    assert.deepEqual(hideCalls, []);
    assert.equal(runtime.chatContainer.getAttribute('data-project-id'), 'project-keep');
    assert.deepEqual({ ...runtime.windowObject._projectSidebarSyncState }, {
        chatId: 'chat-1',
        projectId: 'project-keep',
    });
});
