const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

class FakeElement {
    constructor(id, tagName = 'DIV', hidden = false) {
        this.id = id;
        this.tagName = tagName;
        this.hidden = hidden;
        this.innerHTML = '';
        this.src = '';
        this.style = { display: '' };
        this.clickCount = 0;
        this.listeners = new Map();
    }

    addEventListener(type, listener) {
        this.listeners.set(type, listener);
    }

    click() {
        this.clickCount += 1;
        this.listeners.get('click')?.({ preventDefault() {} });
    }
}

function flushPromises() {
    return new Promise((resolve) => setImmediate(resolve));
}

/**
 * Load the browser script with a small DOM that models the profile views.
 * The fake image loader is controlled by the test so the loading state can be
 * inspected before the profile picture becomes available.
 */
function loadProfilePictureRuntime() {
    const elements = new Map();
    ['sidebarProfilePicture', 'profilePicture'].forEach((id) => {
        elements.set(id, new FakeElement(id, 'IMG', true));
    });
    ['sidebarProfileInitials', 'profileInitials'].forEach((id) => {
        elements.set(id, new FakeElement(id));
    });
    elements.set('sidebarProfileAvatarSkeleton', new FakeElement('sidebarProfileAvatarSkeleton'));
    elements.set('sidebarProfileNameSkeleton', new FakeElement('sidebarProfileNameSkeleton'));
    elements.set('sidebarName', new FakeElement('sidebarName', 'P', true));
    elements.set('profilePictureInput', new FakeElement('profilePictureInput', 'INPUT'));
    elements.set('editProfilePicture', new FakeElement('editProfilePicture', 'BUTTON'));
    elements.set('deleteProfilePicIcon', new FakeElement('deleteProfilePicIcon', 'SPAN'));

    const pendingImages = [];
    class FakeImage {
        set src(value) {
            this.source = value;
            pendingImages.push(this);
        }
    }

    const document = {
        readyState: 'complete',
        getElementById(id) {
            return elements.get(id) || null;
        },
        querySelector() {
            return null;
        },
        addEventListener() {},
    };

    const window = {
        activeUserProfile: {},
        addEventListener() {},
        authedFetch: async () => ({
            ok: true,
            blob: async () => ({ size: 10 }),
        }),
    };

    const context = {
        console,
        CustomEvent: class CustomEvent {},
        document,
        Image: FakeImage,
        URL: {
            createObjectURL: () => 'blob:profile-picture',
            revokeObjectURL() {},
        },
        Icons: {
            trash: '<svg data-shared-icon="trash"></svg>',
        },
        window,
    };
    context.globalThis = context;

    const source = fs.readFileSync(path.join(__dirname, 'profilePicture.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'profilePicture.js' });

    return { elements, pendingImages, window };
}

test('sidebar starts with hidden avatar fallbacks and visible loading skeletons', () => {
    const html = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
    assert.match(html, /<span class="sidebar-profile-avatar"[^>]*>/);
    assert.match(html, /<img id="sidebarProfilePicture"[^>]*\shidden(?:\s|>)/);
    assert.match(html, /<span id="sidebarProfileInitials"[^>]*\shidden(?:\s|>)/);
    assert.match(html, /id="sidebarProfileAvatarSkeleton"/);
    assert.match(html, /id="sidebarProfileNameSkeleton"/);
    assert.match(html, /<p id="sidebarName"[^>]*\shidden(?:\s|>)/);
});

test('profile picture delete button renders the shared trash icon', () => {
    const runtime = loadProfilePictureRuntime();

    assert.equal(
        runtime.elements.get('deleteProfilePicIcon').innerHTML,
        '<svg data-shared-icon="trash"></svg>',
    );
});

test('profile picture chooser is exposed as a translated native button', () => {
    const html = fs.readFileSync(path.join(__dirname, '../../index.html'), 'utf8');
    const styles = fs.readFileSync(path.join(__dirname, '../../css/userSettings/style.css'), 'utf8');

    assert.match(
        html,
        /<button class="profile-picture-overlay" id="editProfilePicture" type="button" aria-label="Change profile picture" data-i18n-attr="aria-label:us_profile_picture_change">/,
    );
    assert.doesNotMatch(html, /<div[^>]+id="editProfilePicture"/);
    assert.match(html, /<span class="profile-picture-edit" aria-hidden="true">/);
    assert.match(styles, /\.profile-picture-overlay:focus-visible\s*\{/);
});

test('profile picture button opens the existing file chooser', () => {
    const runtime = loadProfilePictureRuntime();

    runtime.elements.get('editProfilePicture').click();

    assert.equal(runtime.elements.get('profilePictureInput').clickCount, 1);
});

test('profile picture change label is translated in every supported locale', () => {
    const i18nRoot = path.join(__dirname, '../../i18n');
    const locales = fs.readdirSync(i18nRoot, { withFileTypes: true })
        .filter((entry) => entry.isDirectory())
        .map((entry) => entry.name);

    for (const locale of locales) {
        const dictionary = JSON.parse(
            fs.readFileSync(path.join(i18nRoot, locale, 'index.json'), 'utf8'),
        );
        assert.equal(
            typeof dictionary.us_profile_picture_change,
            'string',
            `${locale} is missing us_profile_picture_change`,
        );
        assert.ok(
            dictionary.us_profile_picture_change.trim(),
            `${locale} has an empty us_profile_picture_change`,
        );
    }
});

test('sidebar shows no avatar until the fetched profile picture has loaded', async () => {
    const runtime = loadProfilePictureRuntime();
    const sidebarImage = runtime.elements.get('sidebarProfilePicture');
    const sidebarInitials = runtime.elements.get('sidebarProfileInitials');
    const avatarSkeleton = runtime.elements.get('sidebarProfileAvatarSkeleton');
    const nameSkeleton = runtime.elements.get('sidebarProfileNameSkeleton');
    const sidebarName = runtime.elements.get('sidebarName');

    const applying = runtime.window.initProfilePicture({
        first_name: 'Ada',
        last_name: 'Lovelace',
        has_profile_picture: true,
    });

    // Allow the mocked fetch and blob promises to reach the controlled image load.
    await flushPromises();

    assert.equal(runtime.pendingImages.length, 1);
    assert.equal(sidebarImage.hidden, true);
    assert.equal(avatarSkeleton.hidden, false);
    assert.equal(nameSkeleton.hidden, false);
    assert.equal(sidebarName.hidden, true);

    runtime.pendingImages[0].onload();
    await applying;

    assert.equal(sidebarImage.hidden, false);
    assert.equal(sidebarImage.style.display, 'block');
    assert.equal(sidebarImage.src, 'blob:profile-picture');
    assert.equal(sidebarInitials.hidden, true);
    assert.equal(sidebarInitials.style.display, 'none');
    assert.equal(avatarSkeleton.hidden, true);
    assert.equal(nameSkeleton.hidden, true);
    assert.equal(sidebarName.hidden, false);
});

test('a profile image decode failure shows initials in the sidebar', async () => {
    const runtime = loadProfilePictureRuntime();
    const applying = runtime.window.initProfilePicture({
        first_name: 'Ada',
        last_name: 'Lovelace',
        has_profile_picture: true,
    });

    await flushPromises();
    runtime.pendingImages[0].onerror();
    await applying;

    const sidebarImage = runtime.elements.get('sidebarProfilePicture');
    const sidebarInitials = runtime.elements.get('sidebarProfileInitials');
    const settingsInitials = runtime.elements.get('profileInitials');
    const avatarSkeleton = runtime.elements.get('sidebarProfileAvatarSkeleton');
    const nameSkeleton = runtime.elements.get('sidebarProfileNameSkeleton');
    const sidebarName = runtime.elements.get('sidebarName');
    assert.equal(sidebarImage.hidden, true);
    assert.equal(sidebarImage.style.display, 'none');
    assert.equal(sidebarInitials.hidden, false);
    assert.equal(sidebarInitials.style.display, 'flex');
    assert.equal(sidebarInitials.innerHTML, 'AL');
    assert.equal(settingsInitials.hidden, false);
    assert.equal(settingsInitials.innerHTML, 'AL');
    assert.equal(avatarSkeleton.hidden, true);
    assert.equal(nameSkeleton.hidden, true);
    assert.equal(sidebarName.hidden, false);
});
