const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('sidebar inlines the fetched SVG so currentColor follows the application theme', async () => {
    const importedSvg = {
        classList: {
            values: [],
            add(value) {
                this.values.push(value);
            },
        },
        attributes: new Map([
            ['width', '32'],
            ['height', '32'],
        ]),
        setAttribute(name, value) {
            this.attributes.set(name, value);
        },
        removeAttribute(name) {
            this.attributes.delete(name);
        },
    };
    const logoButton = {
        child: 'instance-name-must-not-be-replaced',
    };
    const logoHost = {
        innerHTML: '',
        replaceChildren(child) {
            this.child = child;
        },
    };
    const instanceName = {};

    let domContentLoaded;
    let applicationNameUpdated;
    const fetchCalls = [];
    const window = {
        applicationName: 'Fallback name',
        getApplicationName: () => 'Team Omlorix',
        addEventListener(eventName, listener) {
            if (eventName === 'app:applicationNameUpdated') {
                applicationNameUpdated = listener;
            }
        },
    };

    const context = {
        window,
        document: {
            getElementById(id) {
                return {
                    sidebarHeaderLogoButton: logoButton,
                    sidebarHeaderLogo: logoHost,
                    sidebarHeaderInstanceName: instanceName,
                }[id] || null;
            },
            addEventListener(eventName, listener) {
                if (eventName === 'DOMContentLoaded') {
                    domContentLoaded = listener;
                }
            },
            importNode(svgElement, deep) {
                assert.equal(svgElement.localName, 'svg');
                assert.equal(deep, true);
                return importedSvg;
            },
        },
        Icons: {
            omlorix: '<svg data-default-icon="omlorix"></svg>',
        },
        DOMParser: class DOMParser {
            parseFromString(source, mimeType) {
                assert.match(source, /currentColor/);
                assert.equal(mimeType, 'image/svg+xml');
                return {
                    documentElement: { localName: 'svg' },
                    querySelector: () => null,
                };
            }
        },
        fetch: async (url, options) => {
            fetchCalls.push({ url, options });
            return {
                ok: true,
                headers: {
                    get: () => 'image/svg+xml; charset=utf-8',
                },
                text: async () => '<svg><path fill="currentColor" /></svg>',
            };
        },
        Image: class Image {},
        URL: {
            createObjectURL: () => 'blob:icon',
            revokeObjectURL() {},
        },
    };

    const source = fs.readFileSync(path.join(__dirname, 'sidebarIcon.js'), 'utf8');
    vm.runInNewContext(source, context, { filename: 'sidebarIcon.js' });
    await domContentLoaded();

    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0].url, '/api/v1/settings/icon/get');
    assert.equal(fetchCalls[0].options.credentials, 'same-origin');
    assert.equal(logoHost.child, importedSvg);
    assert.equal(logoButton.child, 'instance-name-must-not-be-replaced');
    assert.equal(instanceName.textContent, 'Team Omlorix');
    assert.equal(instanceName.title, 'Team Omlorix');

    applicationNameUpdated({ detail: { applicationName: 'Renamed instance' } });
    assert.equal(instanceName.textContent, 'Renamed instance');
    assert.equal(instanceName.title, 'Renamed instance');
    assert.deepEqual(importedSvg.classList.values, []);
    assert.equal(importedSvg.attributes.get('aria-hidden'), 'true');
    assert.equal(importedSvg.attributes.has('width'), false);
    assert.equal(importedSvg.attributes.has('height'), false);
});

test('sidebar renders PNG, JPEG, and WebP responses as regular images', async (t) => {
    for (const contentType of ['image/png', 'image/jpeg', 'image/webp']) {
        await t.test(contentType, async () => {
            const rasterBlob = { size: 128, type: contentType };
            const revokedUrls = [];
            const logoButton = {
                child: 'instance-name-must-not-be-replaced',
            };
            const logoHost = {
                innerHTML: '',
                replaceChildren(child) {
                    this.child = child;
                },
            };
            const instanceName = {};

            let domContentLoaded;
            let requestedImage;
            class FakeImage {
                constructor() {
                    requestedImage = this;
                }

                set src(value) {
                    this.source = value;
                }
            }

            const context = {
                window: {
                    getApplicationName: () => 'Omlorix',
                    addEventListener() {},
                },
                document: {
                    getElementById(id) {
                        return {
                            sidebarHeaderLogoButton: logoButton,
                            sidebarHeaderLogo: logoHost,
                            sidebarHeaderInstanceName: instanceName,
                        }[id] || null;
                    },
                    addEventListener(eventName, listener) {
                        if (eventName === 'DOMContentLoaded') {
                            domContentLoaded = listener;
                        }
                    },
                    importNode() {
                        throw new Error('Raster responses must not be imported as SVG.');
                    },
                },
                Icons: {
                    omlorix: '<svg data-default-icon="omlorix"></svg>',
                },
                DOMParser: class DOMParser {
                    constructor() {
                        throw new Error('Raster responses must not be parsed as SVG.');
                    }
                },
                fetch: async () => ({
                    ok: true,
                    headers: {
                        get: () => contentType,
                    },
                    blob: async () => rasterBlob,
                }),
                Image: FakeImage,
                URL: {
                    createObjectURL(blob) {
                        assert.equal(blob, rasterBlob);
                        return `blob:${contentType}`;
                    },
                    revokeObjectURL(url) {
                        revokedUrls.push(url);
                    },
                },
            };

            const source = fs.readFileSync(path.join(__dirname, 'sidebarIcon.js'), 'utf8');
            vm.runInNewContext(source, context, { filename: 'sidebarIcon.js' });

            const loading = domContentLoaded();
            await new Promise((resolve) => setImmediate(resolve));
            assert.ok(requestedImage);
            assert.equal(requestedImage.source, `blob:${contentType}`);
            assert.equal(requestedImage.className, undefined);

            requestedImage.onload();
            await loading;

            assert.equal(logoHost.child, requestedImage);
            assert.equal(logoButton.child, 'instance-name-must-not-be-replaced');
            assert.equal(instanceName.textContent, 'Omlorix');
            assert.deepEqual(revokedUrls, [`blob:${contentType}`]);
        });
    }
});
