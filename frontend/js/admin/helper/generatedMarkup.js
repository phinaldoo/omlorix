function setAdminI18nText(element, key, fallback) {
    if (!element) {
        return;
    }
    if (key) {
        element.setAttribute('data-i18n', key);
    }
    // Dynamic admin markup can be rendered long after the global i18n pass ran,
    // so resolve the current translation immediately instead of leaving the
    // English fallback in place until a full page reload or later re-apply.
    element.textContent = key ? helperT(key, fallback ?? '') : (fallback ?? '');
}

function setAdminI18nAttr(element, attr, key, fallback) {
    if (!element) {
        return;
    }
    if (key) {
        const current = element.getAttribute('data-i18n-attr');
        const next = `${attr}:${key}`;
        element.setAttribute('data-i18n-attr', current ? `${current};${next}` : next);
    }
    if (fallback !== undefined) {
        // Attribute translations need the same immediate resolution as text nodes
        // so client-side admin navigation does not leave stale fallback labels.
        element.setAttribute(attr, key ? helperT(key, fallback) : fallback);
    }
}

function createAdminIcon(iconName, {
    className = '',
    width,
    height,
    strokeWidth,
} = {}) {
    const icon = getAdminRegistryIconMarkup(iconName);
    if (!icon) {
        return document.createTextNode('');
    }
    // Icons are stored as complete SVG strings, not structured objects
    // Parse the SVG string to extract the viewBox and body
    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = icon;
    const svg = tempDiv.querySelector('svg');
    if (!svg) {
        return document.createTextNode('');
    }
    
    // Clone the SVG to avoid modifying the original
    const clonedSvg = svg.cloneNode(true);
    
    // Apply optional overrides
    if (className) {
        clonedSvg.setAttribute('class', className);
    }
    if (width) {
        clonedSvg.setAttribute('width', width);
    }
    if (height) {
        clonedSvg.setAttribute('height', height);
    }
    if (strokeWidth) {
        clonedSvg.setAttribute('stroke-width', strokeWidth);
    }
    
    return clonedSvg;
}

function createAdminButtonLabel({ key, label }) {
    const span = document.createElement('span');
    setAdminI18nText(span, key, label);
    return span;
}

function createAdminTableHeader({ className, cells = [] } = {}) {
    const header = document.createElement('div');
    if (className) {
        header.className = className;
    }
    header.setAttribute('role', 'row');

    cells.forEach(({ className: cellClassName, text }) => {
        const cell = document.createElement('div');
        if (cellClassName) {
            cell.className = cellClassName;
        }
        cell.setAttribute('role', 'columnheader');
        cell.textContent = text || '';
        header.appendChild(cell);
    });

    return header;
}

function createAdminTableCell({ className, label, text, role = 'cell' } = {}) {
    const cell = document.createElement('div');
    if (className) {
        cell.className = className;
    }
    if (role) {
        cell.setAttribute('role', role);
    }
    if (label) {
        cell.dataset.label = label;
    }
    if (text !== undefined) {
        cell.textContent = text;
    }
    return cell;
}

function createAdminIconActionButton({
    className = 'action-btn',
    title,
    ariaLabel,
    icon,
    fallback = '',
    dataset = {},
} = {}) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    if (title) {
        button.title = title;
    }
    if (ariaLabel || title) {
        button.setAttribute('aria-label', ariaLabel || title);
    }
    Object.entries(dataset).forEach(([name, value]) => {
        if (value !== undefined && value !== null) {
            button.dataset[name] = value;
        }
    });
    button.innerHTML = icon || fallback;
    return button;
}

function createAdminPageActionButton({
    id,
    className = 'om-button border ghost',
    labelKey,
    label,
    icon = null,
    targetPage = null,
    ariaKey = null,
    ariaLabel = undefined,
    disabled = false,
}) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = className;
    if (id) {
        button.id = id;
    }
    if (targetPage) {
        button.dataset.adminTargetPage = targetPage;
    }
    if (ariaKey || ariaLabel !== undefined) {
        setAdminI18nAttr(button, 'aria-label', ariaKey, ariaLabel);
    }
    if (disabled) {
        button.disabled = true;
    }
    if (icon) {
        button.appendChild(createAdminIcon(icon));
    }
    button.appendChild(createAdminButtonLabel({ key: labelKey, label }));
    return button;
}

function createAdminActionRow({
    titleKey,
    title,
    descKey,
    description,
    buttonLabelKey,
    buttonLabel,
    targetPage,
    buttonId,
    buttonClassName = 'om-button border submit',
    ariaKey,
    ariaLabel,
    buttonAriaKey,
    buttonAriaLabel,
}) {
    const row = document.createElement('div');
    row.className = 'settings-row';
    row.setAttribute('role', 'group');
    if (ariaKey || ariaLabel !== undefined) {
        setAdminI18nAttr(row, 'aria-label', ariaKey, ariaLabel);
    }

    const left = document.createElement('div');
    left.className = 'settings-row-left';

    const titleEl = document.createElement('p');
    titleEl.className = 'settings-row-title';
    setAdminI18nText(titleEl, titleKey, title);
    left.appendChild(titleEl);

    const descEl = document.createElement('p');
    descEl.className = 'settings-row-desc';
    setAdminI18nText(descEl, descKey, description);
    left.appendChild(descEl);

    const right = document.createElement('div');
    right.className = 'settings-row-right';
    right.appendChild(createAdminPageActionButton({
        id: buttonId,
        className: buttonClassName,
        labelKey: buttonLabelKey,
        label: buttonLabel,
        targetPage,
        ariaKey: buttonAriaKey,
        ariaLabel: buttonAriaLabel,
    }));

    row.append(left, right);
    return row;
}

function createAdminActionSection({
    className = 'settings-section',
    titleId,
    titleKey,
    title,
    descriptionKey,
    description,
    rows = [],
}) {
    const section = document.createElement('section');
    section.className = className;
    if (titleId) {
        section.setAttribute('aria-labelledby', titleId);
    }

    const header = document.createElement('div');
    header.className = 'settings-section-header';

    const headerTitle = document.createElement('h3');
    headerTitle.className = 'settings-section-title';
    if (titleId) {
        headerTitle.id = titleId;
    }
    setAdminI18nText(headerTitle, titleKey, title);
    header.appendChild(headerTitle);

    const headerDesc = document.createElement('p');
    headerDesc.className = 'settings-section-description';
    setAdminI18nText(headerDesc, descriptionKey, description);
    header.appendChild(headerDesc);

    const body = document.createElement('div');
    body.className = 'settings-section-body';
    rows.forEach((row) => body.appendChild(createAdminActionRow(row)));

    section.append(header, body);
    return section;
}

function renderAdminServiceConnectionsSettingsRow(target, {
    descriptionKey = 'service_connections_settings_row_desc',
    description = 'Manage the shared service connections table used by this tool.',
} = {}) {
    const mount = typeof target === 'string' ? document.getElementById(target) : target;
    if (!mount) {
        return;
    }

    mount.classList.add('settings-section');

    const body = document.createElement('div');
    body.className = 'settings-section-body';
    body.appendChild(createAdminActionRow({
        titleKey: 'service_connections_settings_row_title',
        title: 'Service Connections',
        descKey: descriptionKey,
        description,
        buttonLabelKey: 'service_connections_settings_row_button',
        buttonLabel: 'Open connections table',
        buttonAriaKey: 'service_connections_settings_row_button_aria',
        buttonAriaLabel: 'Open Service Connections',
        targetPage: 'service-connections',
        buttonClassName: 'om-button border cancel',
    }));

    mount.replaceChildren(body);
}

function createAdminPageShell({
    key,
    titleKey,
    title,
    subtitleKey,
    subtitle,
    backButton = null,
    statusId = null,
    content = [],
}) {
    const page = document.createElement('div');
    page.className = 'page';
    page.id = `page-${key}`;
    page.hidden = true;

    const header = document.createElement('div');
    header.className = 'page-header';

    const headerTop = document.createElement('div');
    headerTop.className = 'page-header-top';

    const titleEl = document.createElement('div');
    titleEl.className = 'title';
    setAdminI18nText(titleEl, titleKey, title);
    headerTop.appendChild(titleEl);

    if (backButton) {
        const actions = document.createElement('div');
        actions.className = 'page-header-actions';
        actions.appendChild(createAdminPageActionButton({
            id: backButton.id,
            className: 'om-button border ghost',
            labelKey: backButton.labelKey,
            label: backButton.label,
            icon: 'chevronLeft',
            targetPage: backButton.targetPage || null,
            ariaKey: backButton.ariaKey,
            ariaLabel: backButton.ariaLabel,
        }));
        headerTop.appendChild(actions);
    }

    header.appendChild(headerTop);

    const subtitleEl = document.createElement('p');
    subtitleEl.className = 'page-subtitle';
    setAdminI18nText(subtitleEl, subtitleKey, subtitle);
    header.appendChild(subtitleEl);
    page.appendChild(header);

    content.forEach(({ id, className = '' }) => {
        const node = document.createElement('div');
        node.id = id;
        if (className) {
            node.className = className;
        }
        page.appendChild(node);
    });

    return page;
}

function createAdminToolCard({
    targetPage,
    icon,
    badgeKey,
    badge,
    titleKey,
    title,
    descriptionKey,
    description,
    ctaKey,
    cta,
}) {
    const card = document.createElement('button');
    card.type = 'button';
    card.className = 'tool-card';
    card.dataset.targetPage = targetPage;
    card.setAttribute('role', 'listitem');

    const header = document.createElement('div');
    header.className = 'tool-card-header';

    const iconWrap = document.createElement('div');
    iconWrap.className = 'tool-card-icon';
    iconWrap.setAttribute('aria-hidden', 'true');
    iconWrap.appendChild(createAdminIcon(icon));
    header.appendChild(iconWrap);

    const badgeEl = document.createElement('span');
    badgeEl.className = 'tool-card-label';
    setAdminI18nText(badgeEl, badgeKey, badge);
    header.appendChild(badgeEl);

    const body = document.createElement('div');
    body.className = 'tool-card-body';

    const titleEl = document.createElement('h3');
    titleEl.className = 'tool-card-title';
    setAdminI18nText(titleEl, titleKey, title);
    body.appendChild(titleEl);

    const descEl = document.createElement('p');
    descEl.className = 'tool-card-description';
    setAdminI18nText(descEl, descriptionKey, description);
    body.appendChild(descEl);

    const footer = document.createElement('div');
    footer.className = 'tool-card-footer';
    footer.appendChild(createAdminButtonLabel({ key: ctaKey, label: cta }));
    footer.appendChild(createAdminIcon('arrowUpRight'));

    card.append(header, body, footer);
    return card;
}

function renderAdminSidebarNav() {
    const nav = document.getElementById('adminSidebarNav');
    if (!nav) {
        return;
    }
    nav.replaceChildren();
    ADMIN_NAV_CONFIG.forEach((group) => {
        const groupEl = document.createElement('div');
        groupEl.className = 'admin-nav-group';

        if (group.labelKey || group.label) {
            const label = document.createElement('div');
            label.className = 'admin-nav-label';
            setAdminI18nText(label, group.labelKey, group.label || '');
            groupEl.appendChild(label);
        }

        group.items.forEach((item) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'admin-nav-item sidebar-element';
            button.dataset.page = item.page;
            button.appendChild(createAdminIcon(item.icon));
            button.appendChild(createAdminButtonLabel({ key: item.labelKey, label: item.label }));
            groupEl.appendChild(button);
        });

        nav.appendChild(groupEl);
    });
}

function renderAdminToolCards() {
    const mount = document.getElementById('adminToolsGrid');
    if (!mount) {
        return;
    }
    mount.replaceChildren(...ADMIN_TOOL_CARD_CONFIG.map(createAdminToolCard));
}

function renderAdminGeneratedPages() {
    const toolMount = document.getElementById('adminGeneratedToolPages');
    if (toolMount) {
        toolMount.replaceChildren(...ADMIN_TOOL_PAGE_CONFIG.map((pageConfig) => createAdminPageShell({
            ...pageConfig,
            backButton: {
                id: pageConfig.backButtonId,
                labelKey: 'btn_back_to_tools',
                label: 'Back to Tools',
            },
        })));
    }

    const modelMount = document.getElementById('adminGeneratedModelPages');
    if (modelMount) {
        modelMount.replaceChildren(...ADMIN_MODEL_SUBPAGE_CONFIG.map((pageConfig) => createAdminPageShell({
            ...pageConfig,
            backButton: {
                labelKey: 'models_back_to_models',
                label: 'Back to Models',
                targetPage: 'models',
                ariaKey: 'models_back_to_models_aria',
                ariaLabel: 'Back to Models',
            },
        })));
    }
}

function renderAdminActionSections() {
    const securityMount = document.getElementById('securityActionSections');
    if (securityMount) {
        securityMount.replaceChildren(...ADMIN_SECURITY_ACTION_SECTIONS.map(createAdminActionSection));
    }

    const modelsRowsMount = document.getElementById('modelsVoiceSettingsRows');
    if (modelsRowsMount) {
        modelsRowsMount.replaceChildren(...ADMIN_MODELS_ACTION_ROWS.map(createAdminActionRow));
    }
}

function initializeAdminGeneratedMarkup() {
    renderAdminSidebarNav();
    renderAdminToolCards();
    renderAdminGeneratedPages();
    renderAdminActionSections();
}

window.createAdminPageShell = createAdminPageShell;
window.createAdminActionRow = createAdminActionRow;
window.createAdminActionSection = createAdminActionSection;
window.createAdminPageActionButton = createAdminPageActionButton;
window.createAdminTableHeader = createAdminTableHeader;
window.createAdminTableCell = createAdminTableCell;
window.createAdminIconActionButton = createAdminIconActionButton;
window.createAdminIcon = createAdminIcon;
window.renderAdminServiceConnectionsSettingsRow = renderAdminServiceConnectionsSettingsRow;
window.initializeAdminGeneratedMarkup = initializeAdminGeneratedMarkup;
initializeAdminGeneratedMarkup();

