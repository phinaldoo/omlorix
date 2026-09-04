const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');


class FakeElement {
    constructor(tagName = 'div') {
        this.tagName = tagName.toUpperCase();
        this.children = [];
        this.listeners = {};
        this.attributes = {};
        this.dataset = {};
        this.style = {};
        this.hidden = false;
        this.disabled = false;
        this.checked = false;
        this.value = '';
        this.parentNode = null;
        this.className = '';
        this.classList = {
            add: (name) => this._setClass(name, true),
            toggle: (name, force) => {
                const enabled = force === undefined
                    ? !this.classList.contains(name)
                    : force;
                this._setClass(name, enabled);
                return enabled;
            },
            contains: (name) => this.className.split(/\s+/).includes(name),
        };
    }

    _setClass(name, enabled) {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(' ');
    }

    appendChild(child) {
        this.children.push(child);
        child.parentNode = this;
        return child;
    }

    addEventListener(type, listener) {
        (this.listeners[type] ||= []).push(listener);
    }

    async dispatch(type, event = {}) {
        for (const listener of this.listeners[type] || []) {
            await listener({ target: this, preventDefault() {}, ...event });
        }
    }

    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name]; }
    removeAttribute(name) { delete this.attributes[name]; }
    focus() { this.focused = true; }

    set innerHTML(value) {
        this._innerHTML = String(value);
        this.children = [];
    }
    get innerHTML() { return this._innerHTML || ''; }
    set textContent(value) { this._textContent = String(value ?? ''); }
    get textContent() { return this._textContent || ''; }
    get options() { return this.tagName === 'SELECT' ? this.children : undefined; }

    closest(selector) {
        let cursor = this;
        const className = selector.startsWith('.') ? selector.slice(1) : null;
        while (cursor) {
            if (className && cursor.classList.contains(className)) return cursor;
            cursor = cursor.parentNode;
        }
        return null;
    }

    querySelectorAll(selector) {
        const wanted = new Set(selector.split(',').map((value) => value.trim().toUpperCase()));
        const matches = [];
        const visit = (node) => {
            for (const child of node.children) {
                if (wanted.has(child.tagName)) matches.push(child);
                visit(child);
            }
        };
        visit(this);
        return matches;
    }
}


function jsonResponse(payload, status = 200) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: (name) => name === 'content-type' ? 'application/json' : null },
        json: async () => payload,
    };
}


function createHarness(detail, options = {}) {
    const ids = [
        'managedGroupsPage', 'managedGroupsNavItem', 'managedGroupsSearch', 'managedGroupsList',
        'managedGroupsEmpty', 'managedGroupsDetail', 'managedGroupsTitle', 'managedGroupsPath',
        'managedGroupsMeta', 'managedGroupContextEnabled',
        'managedGroupContext', 'managedGroupAllowTemporaryChat',
        'managedGroupAllowFileUploads', 'managedGroupMaxFiles', 'managedGroupMaxFileSize',
        'managedGroupTempEnabled', 'managedGroupTempMaxActive',
        'managedGroupTempCredentialLength',
        'managedGroupEnableProjects', 'managedGroupEnableTodo', 'managedGroupEnableNotes',
        'managedGroupEnableMemories', 'managedGroupMemoryModel', 'managedGroupEnableSkills', 'managedGroupEnablePrompts',
        'managedGroupEnableBookmarks', 'managedGroupEnableAgents', 'managedGroupEnableAutomations',
        'managedGroupAllowProjectShare', 'managedGroupAllowTodoShare', 'managedGroupAllowNotesShare',
        'managedGroupAllowSkillShare', 'managedGroupAllowPromptShare', 'managedGroupAllowBookmarkShare',
        'managedGroupAllowAgentShare', 'managedGroupAllowChatShare', 'managedGroupAllowArtifactShare',
        'managedGroupsSaveSettings',
        'managedGroupsPromotionUser', 'managedGroupsPromotionRole', 'managedGroupsPromoteMember',
        'managedGroupsManagersList', 'managedGroupsMembersList', 'managedGroupsMembersMore',
        'managedGroupsTempCount',
        'managedGroupsTempExpiryHours', 'managedGroupsCreateTemp', 'managedGroupsTempCredentials',
        'managedGroupsTempExpiryHelp',
        'managedGroupsTemporaryList',
    ];
    const elements = new Map(ids.map((id) => [id, new FakeElement(id.includes('Button') ? 'button' : 'div')]));
    for (const buttonId of ['managedGroupsSaveSettings', 'managedGroupsPromoteMember', 'managedGroupsCreateTemp']) {
        elements.get(buttonId).tagName = 'BUTTON';
    }
    for (const selectId of ['managedGroupMemoryModel', 'managedGroupsPromotionUser', 'managedGroupsPromotionRole']) {
        elements.get(selectId).tagName = 'SELECT';
    }
    const checkboxIds = [
        'managedGroupContextEnabled', 'managedGroupAllowTemporaryChat',
        'managedGroupAllowFileUploads',
        'managedGroupTempEnabled', 'managedGroupEnableProjects', 'managedGroupEnableTodo',
        'managedGroupEnableNotes', 'managedGroupEnableMemories', 'managedGroupEnableSkills',
        'managedGroupEnablePrompts', 'managedGroupEnableBookmarks', 'managedGroupEnableAgents',
        'managedGroupEnableAutomations', 'managedGroupAllowProjectShare',
        'managedGroupAllowTodoShare', 'managedGroupAllowNotesShare', 'managedGroupAllowSkillShare',
        'managedGroupAllowPromptShare', 'managedGroupAllowBookmarkShare',
        'managedGroupAllowAgentShare', 'managedGroupAllowChatShare',
        'managedGroupAllowArtifactShare',
    ];
    for (const checkboxId of checkboxIds) {
        elements.get(checkboxId).tagName = 'INPUT';
        elements.get(checkboxId).type = 'checkbox';
    }

    const managerForm = new FakeElement('div'); managerForm.className = 'managed-groups-inline-form';
    managerForm.appendChild(elements.get('managedGroupsPromotionUser'));
    managerForm.appendChild(elements.get('managedGroupsPromotionRole'));
    managerForm.appendChild(elements.get('managedGroupsPromoteMember'));
    const temporaryForm = new FakeElement('div'); temporaryForm.className = 'managed-groups-inline-form';
    temporaryForm.appendChild(elements.get('managedGroupsTempCount'));
    temporaryForm.appendChild(elements.get('managedGroupsTempExpiryHours'));
    temporaryForm.appendChild(elements.get('managedGroupsCreateTemp'));

    const tabKeys = ['settings', 'managers', 'members', 'temporary'];
    const tabBadges = new Map();
    const tabs = tabKeys.map((key) => {
        const tab = new FakeElement('button');
        tab.dataset.managedTab = key;
        if (key !== 'settings') {
            const badge = new FakeElement('span');
            badge.className = 'managed-groups-tab-count';
            badge.hidden = true;
            tab.appendChild(badge);
            tabBadges.set(key, badge);
            tab.querySelector = (selector) => selector === '.managed-groups-tab-count' ? badge : null;
        }
        return tab;
    });
    const panels = tabKeys.map((key) => {
        const panel = new FakeElement('section');
        panel.dataset.managedPanel = key;
        return panel;
    });
    for (const checkboxId of checkboxIds) panels[0].appendChild(elements.get(checkboxId));
    panels[0].appendChild(elements.get('managedGroupMemoryModel'));
    panels[0].appendChild(elements.get('managedGroupsSaveSettings'));
    panels[1].appendChild(managerForm);
    panels[2].appendChild(elements.get('managedGroupsMembersList'));
    panels[3].appendChild(temporaryForm);

    const document = {
        getElementById: (id) => elements.get(id) || null,
        querySelectorAll: (selector) => selector === '.managed-groups-tab' ? tabs : selector === '.managed-groups-panel' ? panels : [],
        querySelector: (selector) => {
            const match = selector.match(/\[data-managed-panel="([^"]+)"\]/);
            return match ? panels.find((panel) => panel.dataset.managedPanel === match[1]) : null;
        },
        createElement: (tagName) => new FakeElement(tagName),
    };
    const requests = [];
    const requestLog = [];
    const window = {
        getTranslation: (_key, fallback) => fallback,
        notifyError() {},
        notifySuccess() {},
        showDeleteConfirm: async () => true,
        authedFetch: async (url, fetchOptions = {}) => {
            requests.push(url);
            requestLog.push({ url, options: fetchOptions });
            if (url === '/api/v1/group-management/groups') {
                return jsonResponse({
                    groups: options.groups || [{ id: 'group-1', name: 'Group', path: ['Group'], role: detail.group.role }],
                });
            }
            if (url.includes('/manager-candidates')) {
                const candidatePayload = typeof options.candidateResponse === 'function'
                    ? options.candidateResponse(url)
                    : options.candidateResponse;
                return jsonResponse(candidatePayload || {
                    items: [], offset: 0, limit: 500, total: 0, has_more: false,
                }, options.candidateStatus || 200);
            }
            if (url.includes('/temporary-accounts') && fetchOptions.method === 'POST') {
                return jsonResponse(options.createTempResponse || { created: [] });
            }
            if (url.includes('/manager-promotions') && fetchOptions.method === 'POST') {
                return jsonResponse({ role: 'manager' });
            }
            if (options.detailResponse) return options.detailResponse(url);
            return jsonResponse(detail);
        },
    };
    const context = {
        console,
        document,
        Intl,
        URL,
        URLSearchParams,
        window,
        setTimeout,
        clearTimeout,
    };
    context.globalThis = context;
    vm.runInNewContext(
        fs.readFileSync(path.join(__dirname, 'managedGroups.js'), 'utf8'),
        context,
        { filename: 'managedGroups.js' },
    );
    return { elements, managerForm, temporaryForm, tabs, tabBadges, panels, requests, requestLog, window };
}


/**
 * The page never auto-selects a group anymore, so tests that exercise the
 * detail view click the first group card — mirroring the user journey.
 */
async function selectFirstGroup(harness) {
    const firstGroupButton = harness.elements.get('managedGroupsList').children[0];
    await firstGroupButton.dispatch('click');
    return firstGroupButton;
}


function managerDetail() {
    const featureSettings = {
        projects: { enable_projects: true, allow_project_share: true },
        todo: { enabled_todo: true, allow_todo_list_share: true },
        notes: { enabled_notes: true, allow_notes_share: true },
        memories: { enabled_memories: true, memory_model_id: 'memory-model' },
        skills: { enabled_skills: true, allow_skill_share: true },
        prompts: { enabled_prompts: true, allow_prompt_share: true },
        bookmarks: { enabled_bookmarks: true, allow_bookmark_share: true },
        agents: { allow_agents: true, allow_agent_share: true },
        automations: { enabled_automations: true },
        sharing: { enable_chat_sharing: true, enable_artifact_sharing: true },
    };
    return {
        group: { id: 'group-1', name: 'Group', path: ['Group'], role: 'manager', capabilities: ['view_group', 'view_members', 'manage_settings', 'manage_temporary_accounts'] },
        settings: { context: {}, chat: {}, files: {}, temporary_accounts: { enabled: false }, ...structuredClone(featureSettings) },
        managers: [{ role: 'owner', user: { id: 'owner-1', email: 'owner@example.com', first_name: 'Owner', last_name: '' } }],
        members: [{ id: 'member-1', email: 'member@example.com', first_name: 'Group', last_name: 'Member', status: 'active' }],
        temporary_accounts: [],
        memory_model_options: [
            { value: 'memory-model', label: 'Memory Mini' },
            { value: 'other-model', label: 'Other Model' },
        ],
        pagination: {
            managers: { has_more: false },
            members: { has_more: false },
            temporary_accounts: { has_more: false },
        },
    };
}


test('managers can save every feature access and sharing control without broader policy fields', async () => {
    const detail = managerDetail();
    detail.settings.automations.enabled_automations = false;
    const harness = createHarness(detail);
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    assert.equal(harness.elements.get('managedGroupEnableProjects').checked, true);
    assert.equal(harness.elements.get('managedGroupAllowProjectShare').checked, true);
    assert.equal(harness.elements.get('managedGroupMemoryModel').value, 'memory-model');
    assert.equal(harness.elements.get('managedGroupMemoryModel').disabled, false);
    assert.equal(harness.elements.get('managedGroupEnableAutomations').disabled, false,
        'managers can edit the selected group without a parent-policy ceiling');

    const projects = harness.elements.get('managedGroupEnableProjects');
    projects.checked = false;
    await projects.dispatch('change');
    assert.equal(harness.elements.get('managedGroupAllowProjectShare').disabled, true,
        'sharing is unavailable while its feature is disabled');
    harness.elements.get('managedGroupEnableSkills').checked = false;
    harness.elements.get('managedGroupAllowChatShare').checked = false;
    await harness.elements.get('managedGroupsSaveSettings').dispatch('click');

    const saveRequest = harness.requestLog.find((entry) => (
        entry.url.endsWith('/settings') && entry.options.method === 'PUT'
    ));
    assert.ok(saveRequest);
    const payload = JSON.parse(saveRequest.options.body);
    assert.deepEqual(payload.settings.projects, {
        enable_projects: false,
        allow_project_share: true,
    });
    assert.deepEqual(payload.settings.skills, {
        enabled_skills: false,
        allow_skill_share: true,
    });
    assert.deepEqual(payload.settings.sharing, {
        enable_chat_sharing: false,
        enable_artifact_sharing: true,
    });
    assert.equal(payload.settings.automations.enabled_automations, false);
    assert.deepEqual(payload.settings.memories, {
        enabled_memories: true,
        memory_model_id: 'memory-model',
    });
    assert.equal(payload.settings.data_controls, undefined);
    assert.equal(payload.settings.tools_mcp, undefined);
    assert.equal(payload.description, undefined);
});


test('disabling group memories also disables its memory-model selector', async () => {
    const harness = createHarness(managerDetail());
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const enabled = harness.elements.get('managedGroupEnableMemories');
    enabled.checked = false;
    await enabled.dispatch('change');

    assert.equal(harness.elements.get('managedGroupMemoryModel').disabled, true);
    assert.equal(
        harness.elements.get('managedGroupMemoryModel').getAttribute('aria-disabled'),
        'true',
    );
});


test('managed groups shows a read-only member roster without membership mutations', async () => {
    const scriptSource = fs.readFileSync(path.join(__dirname, 'managedGroups.js'), 'utf8');
    const indexSource = fs.readFileSync(path.join(__dirname, '../../../index.html'), 'utf8');

    for (const source of [scriptSource, indexSource]) {
        assert.doesNotMatch(source, /managedGroupsAddMember|managedGroupsMemberIdentifier/);
        assert.doesNotMatch(source, /\/members(?:\/|`|'|\")/);
        assert.doesNotMatch(source, /manage_members/);
    }
    assert.match(indexSource, /id="managedGroupsMembersList"/);
    assert.match(indexSource, /for="managedGroupsTempCount"/);
    assert.match(indexSource, /aria-describedby="managedGroupsTempCountHelp"/);
    assert.match(indexSource, /for="managedGroupsTempExpiryHours"/);
    assert.match(indexSource, /aria-describedby="managedGroupsTempExpiryHelp"/);

    const harness = createHarness(managerDetail());
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const memberCard = harness.elements.get('managedGroupsMembersList').children[0];
    assert.equal(memberCard.children[0].children[0].textContent, 'Group Member');
    assert.equal(memberCard.children.length, 1, 'read-only member cards must not render action controls');
});


test('member roster pagination appends every requested page', async () => {
    const firstPage = managerDetail();
    firstPage.pagination.members = { offset: 0, limit: 1, total: 2, has_more: true };
    const secondPage = {
        ...managerDetail(),
        members: [{
            id: 'member-2',
            email: 'second@example.com',
            first_name: 'Second',
            last_name: 'Member',
            status: 'active',
        }],
        pagination: {
            ...managerDetail().pagination,
            members: { offset: 1, limit: 1, total: 2, has_more: false },
        },
    };
    const harness = createHarness(firstPage, {
        detailResponse: async (url) => jsonResponse(
            url.includes('member_offset=1') ? secondPage : firstPage
        ),
    });
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);
    await harness.elements.get('managedGroupsMembersMore').dispatch('click');

    const cards = harness.elements.get('managedGroupsMembersList').children;
    assert.equal(cards.length, 2);
    assert.equal(cards[1].children[0].children[0].textContent, 'Second Member');
    assert.ok(harness.requests.some((url) => url.includes('member_offset=1')));
});


test('capabilities hide manager mutation controls and disabled temporary access blocks creation', async () => {
    const harness = createHarness(managerDetail());
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    assert.equal(harness.managerForm.hidden, true);
    assert.equal(harness.temporaryForm.hidden, false);
    assert.equal(harness.elements.get('managedGroupsCreateTemp').disabled, true);
    const managerCard = harness.elements.get('managedGroupsManagersList').children[0];
    assert.equal(managerCard.children.length, 1, 'manager cards must not render demotion or removal controls');
});


test('tab list supports arrow, Home, and End keyboard navigation', async () => {
    const harness = createHarness(managerDetail());
    await harness.tabs[0].dispatch('keydown', { key: 'ArrowRight' });
    assert.equal(harness.tabs[1].getAttribute('aria-selected'), 'true');
    assert.equal(harness.tabs[1].focused, true);

    await harness.tabs[1].dispatch('keydown', { key: 'End' });
    assert.equal(harness.tabs[3].getAttribute('aria-selected'), 'true');

    await harness.tabs[3].dispatch('keydown', { key: 'Home' });
    assert.equal(harness.tabs[0].getAttribute('aria-selected'), 'true');
});


test('owners can select group users and only promote them to higher roles', async () => {
    const detail = managerDetail();
    detail.group.role = 'owner';
    detail.group.capabilities.push('promote_members');
    detail.managers[0].user.status = 'active';
    const harness = createHarness(detail, {
        candidateResponse: {
            items: [
                { id: 'member-1', email: 'member@example.com', first_name: 'Group', last_name: 'Member', status: 'active', current_role: null, eligible: true },
                { id: 'coordinator-1', email: 'coordinator@example.com', first_name: 'Casey', last_name: 'Coordinator', status: 'active', current_role: 'coordinator', eligible: true },
                { id: 'owner-1', email: 'owner@example.com', first_name: 'Owner', last_name: '', status: 'active', current_role: 'owner', eligible: false },
                { id: 'inactive-1', email: 'inactive@example.com', first_name: 'Inactive', last_name: 'Member', status: 'inactive', current_role: null, eligible: false },
            ],
            offset: 0,
            limit: 500,
            total: 4,
            has_more: false,
        },
    });
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const userSelect = harness.elements.get('managedGroupsPromotionUser');
    const roleSelect = harness.elements.get('managedGroupsPromotionRole');
    assert.equal(userSelect.children.length, 5, 'placeholder plus every direct regular user');
    assert.equal(userSelect.children[3].disabled, true, 'an existing owner cannot be promoted further');
    assert.equal(userSelect.children[4].disabled, true, 'inactive users remain visible but disabled');

    userSelect.value = 'coordinator-1';
    await userSelect.dispatch('change');
    assert.deepEqual(roleSelect.children.map((option) => option.value), ['', 'manager', 'owner']);
    roleSelect.value = 'manager';
    await roleSelect.dispatch('change');
    assert.equal(harness.elements.get('managedGroupsPromoteMember').disabled, false);
    await harness.elements.get('managedGroupsPromoteMember').dispatch('click');

    const promotionRequest = harness.requestLog.find((entry) => (
        entry.url.endsWith('/manager-promotions') && entry.options.method === 'POST'
    ));
    assert.ok(promotionRequest);
    assert.deepEqual(JSON.parse(promotionRequest.options.body), {
        user_id: 'coordinator-1',
        role: 'manager',
    });
    const managerCard = harness.elements.get('managedGroupsManagersList').children[0];
    assert.equal(managerCard.children.length, 1, 'even owners cannot demote or remove a delegated role here');
});


test('promotion candidate failures do not hide an already loaded group', async () => {
    const detail = managerDetail();
    detail.group.role = 'owner';
    detail.group.capabilities.push('promote_members');
    const harness = createHarness(detail, { candidateStatus: 503 });
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    assert.equal(harness.elements.get('managedGroupsDetail').hidden, false);
    assert.equal(harness.elements.get('managedGroupsTitle').textContent, 'Group');
    assert.equal(harness.elements.get('managedGroupsPromotionUser').disabled, true);
});


test('coordinator assignments use the renamed role label', async () => {
    const detail = managerDetail();
    detail.managers = [{
        role: 'coordinator',
        user: {
            id: 'coordinator-1',
            email: 'coordinator@example.com',
            first_name: 'Casey',
            last_name: 'Coordinator',
            status: 'active',
        },
    }];
    const harness = createHarness(detail);
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const managerCard = harness.elements.get('managedGroupsManagersList').children[0];
    const subtitle = managerCard.children[0].children[1].textContent;
    assert.match(subtitle, /^Coordinator · Active · coordinator@example\.com$/);
});


test('temporary accounts show their scheduled deletion date', async () => {
    const detail = managerDetail();
    detail.temporary_accounts = [{
        id: 'temporary-1',
        email: 'temporary@example.com',
        status: 'expired',
        temporary_expires_at: '2026-07-01T08:00:00Z',
        deletion_scheduled_for: '2026-07-31T08:00:00Z',
    }];
    const harness = createHarness(detail);
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const temporaryCard = harness.elements.get('managedGroupsTemporaryList').children[0];
    const subtitle = temporaryCard.children[0].children[1].textContent;
    assert.match(subtitle, /Expired/);
    assert.match(subtitle, /Deletion scheduled/);
});


test('a slower previous group request cannot replace the current selection', async () => {
    const groupOne = managerDetail();
    const groupTwo = {
        ...managerDetail(),
        group: { ...managerDetail().group, id: 'group-2', name: 'Second group' },
    };
    let groupOneRequests = 0;
    let resolveSlowRequest;
    const slowResponse = new Promise((resolve) => { resolveSlowRequest = resolve; });
    const harness = createHarness(groupOne, {
        groups: [
            { id: 'group-1', name: 'First group', path: ['First group'], role: 'manager' },
            { id: 'group-2', name: 'Second group', path: ['Second group'], role: 'manager' },
        ],
        detailResponse: async (url) => {
            if (url.includes('group-1')) {
                groupOneRequests += 1;
                if (groupOneRequests > 1) return slowResponse;
                return jsonResponse(groupOne);
            }
            return jsonResponse(groupTwo);
        },
    });
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const groupButtons = harness.elements.get('managedGroupsList').children;
    const slowNavigation = groupButtons[0].dispatch('click');
    await groupButtons[1].dispatch('click');
    resolveSlowRequest(jsonResponse(groupOne));
    await slowNavigation;

    assert.equal(harness.elements.get('managedGroupsTitle').textContent, 'Second group');
});


test('no group is auto-selected until the user picks one from the list', async () => {
    const harness = createHarness(managerDetail());
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();

    assert.equal(harness.elements.get('managedGroupsDetail').hidden, true, 'detail stays hidden until selection');
    assert.equal(harness.elements.get('managedGroupsEmpty').style.display || '', '');
    const initialButtons = harness.elements.get('managedGroupsList').children;
    assert.ok(initialButtons.length > 0, 'groups are listed for selection');
    assert.ok(!initialButtons[0].classList.contains('active'), 'nothing is marked active');

    const clickedButton = await selectFirstGroup(harness);

    assert.equal(harness.elements.get('managedGroupsDetail').hidden, false);
    assert.equal(harness.elements.get('managedGroupsEmpty').style.display, 'none');
    const buttons = harness.elements.get('managedGroupsList').children;
    assert.ok(buttons[0].classList.contains('active'), 'the picked group is marked active');
    assert.equal(buttons[0].getAttribute('aria-current'), 'true');
    assert.equal(clickedButton.textContent, buttons[0].textContent);
});


test('tab badges reflect loaded collection totals and stay hidden when empty', async () => {
    const detail = managerDetail();
    detail.pagination = {
        managers: { offset: 0, limit: 50, total: 4, has_more: false },
        members: { offset: 0, limit: 50, total: 2, has_more: false },
        temporary_accounts: { offset: 0, limit: 50, total: 0, has_more: false },
    };
    const harness = createHarness(detail);
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    assert.equal(harness.tabBadges.get('managers').hidden, false);
    assert.equal(harness.tabBadges.get('managers').textContent, '4');
    assert.equal(harness.tabBadges.get('members').hidden, false);
    assert.equal(harness.tabBadges.get('members').textContent, '2');
    assert.equal(harness.tabBadges.get('temporary').hidden, true);
    assert.equal(harness.tabBadges.get('temporary').textContent, '');
});


test('created temporary credentials render as copyable rows', async () => {
    const detail = managerDetail();
    detail.settings.temporary_accounts.enabled = true;
    const harness = createHarness(detail, {
        createTempResponse: {
            created: [
                { email: 'temp_one@example.com', password: 'secret-one', expires_at: '2026-07-29T08:00:00Z' },
                { email: 'temp_two@example.com', password: 'secret-two', expires_at: '2026-07-29T08:00:00Z' },
            ],
        },
    });
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const createButton = harness.elements.get('managedGroupsCreateTemp');
    assert.equal(createButton.disabled, false);
    await createButton.dispatch('click');

    const box = harness.elements.get('managedGroupsTempCredentials');
    assert.equal(box.hidden, false);
    assert.equal(box.children.length, 3, 'head row plus one row per credential');

    const firstRow = box.children[1];
    const firstMain = firstRow.children[0];
    assert.equal(firstMain.children[0].textContent, 'temp_one@example.com');
    assert.equal(firstMain.children[1].textContent, 'secret-one');

    const copyButton = firstRow.children[1];
    assert.equal(copyButton.tagName, 'BUTTON');
    assert.equal(copyButton.children.length, 2, 'copy buttons render an icon span and a label span');
    assert.equal(box.children[0].children[2].tagName, 'BUTTON', 'head renders a copy-all action');
});


test('temporary expiry survives same-group refreshes and resets for another group', async () => {
    const groupOne = managerDetail();
    groupOne.settings.temporary_accounts.enabled = true;
    const groupTwo = {
        ...managerDetail(),
        group: { ...managerDetail().group, id: 'group-2', name: 'Second group' },
    };
    groupTwo.settings.temporary_accounts.enabled = true;
    const harness = createHarness(groupOne, {
        groups: [
            { id: 'group-1', name: 'First group', path: ['First group'], role: 'manager' },
            { id: 'group-2', name: 'Second group', path: ['Second group'], role: 'manager' },
        ],
        detailResponse: async (url) => jsonResponse(
            url.includes('group-2') ? groupTwo : groupOne
        ),
    });
    harness.window.ManagedGroupsSettings.setVisibility(true);
    await harness.window.ManagedGroupsSettings.load();
    await selectFirstGroup(harness);

    const expiryInput = harness.elements.get('managedGroupsTempExpiryHours');
    assert.equal(expiryInput.value, 8);
    expiryInput.value = 24;
    await harness.elements.get('managedGroupsCreateTemp').dispatch('click');
    assert.equal(expiryInput.value, 24);

    const groupButtons = harness.elements.get('managedGroupsList').children;
    await groupButtons[1].dispatch('click');
    assert.equal(expiryInput.value, 8);
});
