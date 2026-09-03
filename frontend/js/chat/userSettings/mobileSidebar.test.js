const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const USER_SETTINGS_INIT_PATH = path.join(__dirname, 'init.js');
const USER_SETTINGS_CSS_PATH = path.join(__dirname, '..', '..', '..', 'css', 'userSettings', 'style.css');
const SHARED_ELEMENTS_CSS_PATH = path.join(__dirname, '..', '..', '..', 'css', 'common', 'elements.css');
const INDEX_PATH = path.join(__dirname, '..', '..', '..', 'index.html');

test('phone settings opens navigation while the tablet drawer starts closed', () => {
    const initSource = fs.readFileSync(USER_SETTINGS_INIT_PATH, 'utf8');
    const cssSource = fs.readFileSync(USER_SETTINGS_CSS_PATH, 'utf8');
    const sharedElementsCssSource = fs.readFileSync(SHARED_ELEMENTS_CSS_PATH, 'utf8');
    const indexSource = fs.readFileSync(INDEX_PATH, 'utf8');

    // The JavaScript behavior and the CSS drawer layout must change at the same
    // viewport width so there is no intermediate layout with hidden navigation.
    assert.match(initSource, /USER_SETTINGS_SIDEBAR_DRAWER_MEDIA_QUERY = '\(max-width: 1024px\)'/);
    assert.match(initSource, /USER_SETTINGS_MOBILE_SHEET_MEDIA_QUERY = '\(max-width: 640px\)'/);
    assert.match(cssSource, /@media \(max-width: 1024px\)[\s\S]*?\.us-sidebar\s*\{[\s\S]*?position: absolute[\s\S]*?width: min\(280px, 80%\)[\s\S]*?height: 100%/);
    assert.match(cssSource, /@media \(max-width: 1024px\)[\s\S]*?\.us-sidebar-backdrop\s*\{\s*position: absolute;/);
    assert.match(cssSource, /@media \(max-width: 640px\)[\s\S]*?\.us-sidebar\s*\{[\s\S]*?position: absolute/);
    assert.match(cssSource, /\.us-container\.us-mobile-navigation-open \.us-main-content\s*\{\s*display: none/);
    assert.match(indexSource, /class="us-mobile-sheet-drag-handle" id="userSettingsDragHandle"[^>]*role="button"[^>]*tabindex="0"/);
    assert.match(indexSource, /class="us-mobile-sheet-drag-handle-bar"/);
    assert.match(sharedElementsCssSource, /\.model-select-drag-handle,[\s\S]*?\.us-mobile-sheet-drag-handle/);
    assert.match(initSource, /Math\.min\(100, Math\.max\(48, sheetHeight \* 0\.18\)\)/);
    assert.match(initSource, /addEventListener\('pointerdown'[\s\S]*?addEventListener\('touchstart'/);
    assert.match(indexSource, /class="om-button" id="userSettingsNavToggle"[^>]* hidden/);
    assert.match(indexSource, /class="us-settings-nav-back-icon" aria-hidden="true"/);
    assert.match(initSource, /userSettingsNavToggle\.hidden = !isUserSettingsSidebarDrawer\(\)/);
    assert.match(cssSource, /grid-template-columns: 42px minmax\(0, 1fr\) 42px/);
    assert.match(cssSource, /#userSettingsNavToggle,[\s\S]*?#userSettingsHeaderCloseButton\s*\{[\s\S]*?width: 42px;[\s\S]*?height: 42px;/);
    assert.match(cssSource, /#userSettingsNavToggle\s*\{\s*grid-row: 1;\s*grid-column: 1;/);
    assert.match(cssSource, /#userSettingsHeaderCloseButton\s*\{\s*grid-row: 1;\s*grid-column: 3;/);
    assert.match(
        cssSource,
        /\.us-nav-item\.active\s*\{\s*background: transparent;\s*\}\s*@media \(hover: hover\) and \(pointer: fine\)\s*\{\s*\.us-nav-item:hover\s*\{\s*background: var\(--hover\);/,
    );
    assert.match(initSource, /chevron\.innerHTML = window\.Icons\?\.chevronRight/);
    assert.match(initSource, /userSettingsBackIcon\.innerHTML = window\.Icons\?\.chevronLeft/);

    const showViewSource = initSource
        .split('function showUserSettingsView() {', 2)[1]
        .split('\n}', 1)[0];

    assert.match(showViewSource, /userSettingsView\.hidden = false;[\s\S]*showUserSettingsNavigationOnOpen\(\)/);
    assert.match(initSource, /function showUserSettingsNavigationOnOpen\(\)[\s\S]*isUserSettingsMobileSheet\(\)[\s\S]*openUserSettingsSidebar\(\)[\s\S]*closeUserSettingsSidebar\(\)/);
    assert.match(initSource, /const focusTarget = isUserSettingsMobileSheet\(\)[\s\S]*navItems\.find[\s\S]*userSettingsHeaderTitle/);
    assert.match(initSource, /handleNavItemClick\(event\)[\s\S]*activeUserSettingsOpenInvocation\.userSelectedSectionDuringSettingsLoad = true/);
    assert.match(initSource, /handleNavItemClick\(event\)[\s\S]*setActiveSection\(section\);[\s\S]*closeUserSettingsSidebar\(\)/);
    assert.match(initSource, /const invocation = \{\s*userSelectedSectionDuringSettingsLoad: false/);
    assert.match(initSource, /const data = await fetchUserSettingsInit\(\);\s*if \(activeUserSettingsOpenInvocation !== invocation\)/);
    assert.match(
        initSource,
        /await window\.initializeSidebarButtonSettings\(data\);\s*}\s*if \(activeUserSettingsOpenInvocation !== invocation\)/,
    );
    assert.match(
        initSource,
        /activeUserSettingsOpenInvocation === invocation\s*&& !invocation\.userSelectedSectionDuringSettingsLoad[\s\S]*setActiveSection\(initialSection/,
    );
});

test('user settings uses the shared responsive modal shell without hiding chat', () => {
    const initSource = fs.readFileSync(USER_SETTINGS_INIT_PATH, 'utf8');
    const cssSource = fs.readFileSync(USER_SETTINGS_CSS_PATH, 'utf8');
    const indexSource = fs.readFileSync(INDEX_PATH, 'utf8');

    assert.match(indexSource, /class="[^"]*search-modal-overlay[^"]*user-settings-overlay" id="userSettingsView" aria-hidden="true" hidden/);
    assert.match(indexSource, /class="us-container search-modal shared-modal shared-modal--large shared-modal--fixed" role="dialog" aria-modal="true" aria-labelledby="userSettingsTitle"/);
    assert.match(indexSource, /<main class="us-main-content shared-modal-body">[\s\S]*?<header class="us-settings-header shared-modal-header shared-modal-header--main">/);
    assert.match(indexSource, /<h1 class="shared-modal-title" id="userSettingsTitle"/);
    assert.match(indexSource, /id="userSettingsHeaderCloseButton"[^>]*class="[^"]*shared-modal-close|class="[^"]*shared-modal-close[^"]*" id="userSettingsHeaderCloseButton"/);
    assert.match(cssSource, /\.user-settings-overlay\s*\{[\s\S]*?--shared-modal-z-index:\s*1200/);
    assert.match(
        cssSource,
        /\.user-settings-overlay \.us-container\.shared-modal\s*\{[^}]*background:\s*var\(--user-settings-surface\)/,
        'the settings shell should use the opaque settings surface',
    );
    assert.match(
        cssSource,
        /\.us-settings-header\s*\{[^}]*background:\s*var\(--user-settings-surface\)/,
        'the sticky header should use the same opaque settings surface',
    );
    assert.doesNotMatch(indexSource, /id="userSettingsSidebarCloseButton"/);
    assert.doesNotMatch(indexSource, /id="userSettings(?:Name|Email|ProfilePicture|ProfileInitials)"/);
    assert.match(indexSource, /data-us-page="profile"[\s\S]*class="us-settings-section us-profile-logout-section"[\s\S]*id="userSettingsLogoutButton"/);
    assert.doesNotMatch(initSource, /chatView\.style\.display = 'none'/);
    assert.doesNotMatch(initSource, /userSettingsView\.style\.display/);
    assert.match(initSource, /const isUserSettingsViewVisible = \(\) => Boolean\(userSettingsView && !userSettingsView\.hidden\)/);
    assert.match(initSource, /chatView\.inert = true/);
    assert.match(initSource, /event\.target === userSettingsView[\s\S]*closeUserSettings\(\)/);
    assert.match(cssSource, /--shared-modal-width: 1120px/);
    assert.doesNotMatch(cssSource, /height: min\(92dvh, 760px\)/);
    assert.doesNotMatch(initSource, /us-opening|us-closing/);
    assert.doesNotMatch(cssSource, /\.us-container\.us-opening|\.us-container\.us-closing/);
});
