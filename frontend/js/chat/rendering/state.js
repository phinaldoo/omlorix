let markdownRendererInstance = null;
let markdownEventsInitialized = false;
let markdownMutationObserver = null;
let mermaidRenderCounter = 0;
let activeCodeBlockPreviewModal = null;
const MAX_KATEX_RENDER_ATTEMPTS = 6;
const KATEX_RENDER_RETRY_DELAY = 150;
const IMPLICIT_MATH_MAX_SEGMENT_LENGTH = 260;
const IMPLICIT_MATH_INDICATOR_REGEX = /(\\[A-Za-z]+|[A-Za-z0-9}\]]\s*[_^]|[_^]\{|\\left|\\right|\\sum|\\prod|\\frac|\\int|\\sqrt|\\log|\\exp|\\pi|\\theta|\\lambda)/;
const IMPLICIT_MATH_EXCLUDED_TAGS = ['CODE', 'PRE', 'SAMP', 'KBD', 'VAR'];
const IMPLICIT_MATH_PREFIX_ALLOWED = /[\s,.;:!?={[(+\-*/<>]/;
const VEGA_PREVIEW_MAX_SPEC_LENGTH = 250000;
const VEGA_EXTERNAL_RESPONSE_MAX_LENGTH = 10000000;
const VEGA_EXTERNAL_REQUEST_TIMEOUT_MS = 15000;
const VEGA_RESOURCE_KEYS = new Set(['url', 'href', 'src']);
const VEGA_EXTERNAL_CONSENT_STORAGE_KEY = 'omlorix:vega-preview:external-resource-consent:v1';
const VEGA_MAX_SAVED_EXTERNAL_CONSENTS = 200;
const VEGA_EXTERNAL_RESOURCE_ENDPOINT = '/api/v1/chats/code-preview/vega/resource';

const LATEX_PLACEHOLDER_PREFIX = 'LATEXPLACEHOLDER';
const LATEX_PLACEHOLDER_SUFFIX = 'ENDLATEX';
const MARKDOWN_CODE_PLACEHOLDER_PREFIX = 'MARKDOWNCODEPLACEHOLDER';
const MARKDOWN_CODE_PLACEHOLDER_SUFFIX = 'ENDMARKDOWNCODE';
let latexPlaceholderCounter = 0;

const MARKDOWN_COPY_SVG = Icons.copy;
const MARKDOWN_DOWNLOAD_SVG = Icons.download;
const MARKDOWN_RUN_SVG = Icons.play;
const MARKDOWN_COLLAPSE_SVG = Icons.chevronTop;
const MARKDOWN_EXPAND_SVG = Icons.chevron;
const MARKDOWN_DONE_SVG = Icons.check;
const MARKDOWN_EDIT_SVG = Icons.edit;
const MARKDOWN_PREVIEW_SVG = Icons.eye;
const MARKDOWN_CODE_SVG = Icons.code;
const MARKDOWN_RELOAD_SVG = Icons.redo_circle;
const MARKDOWN_ZOOM_IN_SVG = Icons.zoomIn;
const MARKDOWN_ZOOM_OUT_SVG = Icons.zoomOut;
const MARKDOWN_RESET_ZOOM_SVG = Icons.refresh;
const MARKDOWN_EXPAND_PREVIEW_SVG = Icons.expand;
const MARKDOWN_CLOSE_SVG = Icons.close;
const MARKDOWN_EXTERNAL_CONTENT_SVG = Icons.globe;
const MARKDOWN_SETTINGS_SVG = Icons.settings;
const CODE_BLOCK_HTML_PREVIEW_MESSAGE_TYPE = 'omlorix-code-block-preview-height';
const VISUALIZATION_HOST_REQUEST_MESSAGE_TYPE = 'omlorix:visualization-request';
const VISUALIZATION_HOST_RESPONSE_MESSAGE_TYPE = 'omlorix:visualization-response';
const VISUALIZATION_THEME_MESSAGE_TYPE = 'omlorix:visualization-theme';
const VISUALIZATION_MAX_FOLLOWUP_LENGTH = 4000;
const VISUALIZATION_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024;
// These runtime assets deliberately keep stable filenames because the build
// cannot rewrite URLs embedded in JavaScript. Keep an explicit query version
// so a previously cached failure or an older runtime is never reused after an
// asset-contract update.
const VISUALIZATION_RUNTIME_ASSET_VERSION = '1';
const VISUALIZATION_RUNTIME_ASSET_PATHS = Object.freeze({
    css: `/css/chat/visualization-runtime.css?v=${VISUALIZATION_RUNTIME_ASSET_VERSION}`,
    d3: `/js/vendor/d3.min.js?v=${VISUALIZATION_RUNTIME_ASSET_VERSION}`,
    topojson: `/js/vendor/topojson-client.min.js?v=${VISUALIZATION_RUNTIME_ASSET_VERSION}`,
    lucide: `/js/vendor/lucide.min.js?v=${VISUALIZATION_RUNTIME_ASSET_VERSION}`,
});
const CODE_EXECUTION_MARKDOWN_ENDPOINT = '/api/v1/chats/code-execution/markdown/python';
const CODE_BLOCK_DEFAULT_VIEW = 'preview';
const COLLAPSE_CLICK_SUPPRESSION_WINDOW_MS = 250;
const CODE_BLOCK_COLLAPSE_ANIMATION_DURATION_MS = 320;
let codeBlockPreviewMessageListenerInitialized = false;
let visualizationThemeObserverInitialized = false;
let visualizationRuntimeCssPromise = null;
let visualizationRuntimeLibrariesPromise = null;
let vegaExternalConsentCounter = 0;

