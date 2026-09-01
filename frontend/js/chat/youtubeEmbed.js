/**
 * YouTube Embed Module
 * Handles YouTube video embedding with GDPR-compliant cookie consent
 */

const YouTubeEmbed = (function() {
    'use strict';

    const CONSENT_STORAGE_KEY = 'youtube_cookie_consent';
    const YOUTUBE_REGEX = /(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})(?:[?&][^\s]*)?/gi;
    const YOUTUBE_LINK_REGEX = /(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:watch\?v=|embed\/|v\/|shorts\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})(?:[?&][^\s]*)?/i;

    const YOUTUBE_ICON_SVG = Icons.youtube;

    const PLAY_ICON_SVG = Icons.play;

    /**
     * Check if user has consented to YouTube cookies
     */
    function hasConsent() {
        try {
            return localStorage.getItem(CONSENT_STORAGE_KEY) === 'true';
        } catch (e) {
            return false;
        }
    }

    /**
     * Save consent preference
     */
    function saveConsent(consented) {
        try {
            if (consented) {
                localStorage.setItem(CONSENT_STORAGE_KEY, 'true');
            } else {
                localStorage.removeItem(CONSENT_STORAGE_KEY);
            }
        } catch (e) {
            console.warn('Failed to save YouTube consent preference:', e);
        }
    }

    /**
     * Extract YouTube video ID from URL
     */
    function extractVideoId(url) {
        const match = url.match(YOUTUBE_LINK_REGEX);
        return match ? match[1] : null;
    }

    /**
     * Check if a URL is a YouTube URL
     */
    function isYouTubeUrl(url) {
        return YOUTUBE_LINK_REGEX.test(url);
    }

    /**
     * Get translation string with fallback
     */
    function t(key, fallback) {
        if (typeof window !== 'undefined' && typeof window.t === 'function') {
            const translated = window.t(key);
            return translated !== key ? translated : fallback;
        }
        return fallback;
    }

    /**
     * Create the consent placeholder element
     */
    function createConsentPlaceholder(videoId, originalUrl) {
        const container = document.createElement('div');
        container.className = 'youtube-embed-container';
        container.dataset.videoId = videoId;
        container.dataset.originalUrl = originalUrl;

        const placeholder = document.createElement('div');
        placeholder.className = 'youtube-consent-placeholder';

        const content = document.createElement('div');
        content.className = 'youtube-consent-content';

        // Icon
        const iconWrapper = document.createElement('div');
        iconWrapper.className = 'youtube-consent-icon';
        iconWrapper.innerHTML = YOUTUBE_ICON_SVG;
        content.appendChild(iconWrapper);

        // Title
        const title = document.createElement('p');
        title.className = 'youtube-consent-title';
        title.textContent = t('youtube_consent_title', 'YouTube-Video');
        content.appendChild(title);

        // Description
        const description = document.createElement('p');
        description.className = 'youtube-consent-description';
        description.textContent = t('youtube_consent_description', 
            'Mit dem Laden des Videos akzeptierst du die Datenschutzerklärung von YouTube. Dabei werden Cookies gesetzt.');
        content.appendChild(description);

        // Actions container
        const actions = document.createElement('div');
        actions.className = 'youtube-consent-actions';

        // Remember checkbox
        const checkboxLabel = document.createElement('label');
        checkboxLabel.className = 'youtube-consent-checkbox-label';
        
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = 'youtube-remember-' + videoId;
        
        const checkboxText = document.createElement('span');
        checkboxText.textContent = t('youtube_consent_remember', 'Einstellung merken');
        
        checkboxLabel.appendChild(checkbox);
        checkboxLabel.appendChild(checkboxText);
        actions.appendChild(checkboxLabel);

        // Accept button
        const acceptBtn = document.createElement('button');
        acceptBtn.className = 'youtube-consent-btn youtube-consent-btn-primary';
        acceptBtn.innerHTML = PLAY_ICON_SVG + '<span>' + t('youtube_consent_accept', 'Video laden') + '</span>';
        acceptBtn.addEventListener('click', () => {
            const shouldRemember = checkbox.checked;
            if (shouldRemember) {
                saveConsent(true);
            }
            loadVideo(container, videoId);
        });
        actions.appendChild(acceptBtn);

        content.appendChild(actions);
        placeholder.appendChild(content);
        container.appendChild(placeholder);

        return container;
    }

    /**
     * Create the YouTube iframe embed
     */
    function createVideoEmbed(videoId) {
        const wrapper = document.createElement('div');
        wrapper.className = 'youtube-embed-wrapper';

        // Add loading indicator
        const loading = document.createElement('div');
        loading.className = 'youtube-embed-loading';
        wrapper.appendChild(loading);

        const iframe = document.createElement('iframe');
        // Use youtube-nocookie.com for enhanced privacy
        iframe.src = `https://www.youtube-nocookie.com/embed/${videoId}?rel=0&modestbranding=1`;
        iframe.title = t('youtube_video_player_title', 'YouTube video player');
        iframe.allow = 'accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share';
        iframe.allowFullscreen = true;
        iframe.loading = 'lazy';

        // Remove loading indicator when iframe loads
        iframe.addEventListener('load', () => {
            loading.remove();
        });

        wrapper.appendChild(iframe);
        return wrapper;
    }

    /**
     * Load video into container (replace consent placeholder)
     */
    function loadVideo(container, videoId) {
        if (!container || !videoId) return;

        // Clear existing content
        container.innerHTML = '';

        // Add video embed
        const embed = createVideoEmbed(videoId);
        container.appendChild(embed);
    }

    /**
     * Process a single YouTube URL and return embed element
     */
    function createEmbed(url) {
        const videoId = extractVideoId(url);
        if (!videoId) {
            return null;
        }

        if (hasConsent()) {
            // User already consented, show video directly
            const container = document.createElement('div');
            container.className = 'youtube-embed-container';
            container.dataset.videoId = videoId;
            container.dataset.originalUrl = url;
            container.appendChild(createVideoEmbed(videoId));
            return container;
        } else {
            // Show consent placeholder
            return createConsentPlaceholder(videoId, url);
        }
    }

    /**
     * Process markdown-rendered HTML and replace YouTube links with embeds
     * This is called after markdown rendering to enhance YouTube links
     */
    function processRenderedContent(element) {
        if (!element) return;

        // Find all links that point to YouTube
        const links = element.querySelectorAll('a[href]');
        
        links.forEach(link => {
            const href = link.getAttribute('href');
            if (!href || !isYouTubeUrl(href)) return;

            // Check if this link is already processed or inside a code block
            if (link.closest('.youtube-embed-container') || 
                link.closest('pre') || 
                link.closest('code')) {
                return;
            }

            if (link.dataset.youtubeEmbedProcessed === 'true') {
                return;
            }

            // Check if the link text is just the URL (not custom text)
            const linkText = link.textContent.trim();
            const isPlainUrl = isYouTubeUrl(linkText) || linkText === href;

            if (isPlainUrl) {
                // Replace link with embed
                const embed = createEmbed(href);
                if (embed) {
                    // If link is the only child of a paragraph, replace the paragraph
                    const parent = link.parentElement;
                    if (parent && parent.tagName === 'P' && 
                        parent.childNodes.length === 1) {
                        parent.replaceWith(embed);
                    } else {
                        link.replaceWith(embed);
                    }
                }
            } else {
                const embed = createEmbed(href);
                if (embed) {
                    const blockContainer = link.closest('p, li, div, section, article, blockquote') || link.parentElement;
                    if (blockContainer && blockContainer.parentNode) {
                        blockContainer.parentNode.insertBefore(embed, blockContainer.nextSibling);
                    } else if (link.parentNode) {
                        link.parentNode.insertBefore(embed, link.nextSibling);
                    } else {
                        element.appendChild(embed);
                    }
                    link.dataset.youtubeEmbedProcessed = 'true';
                }
            }
        });

        // Also look for plain text YouTube URLs that weren't linkified
        // This handles cases where the URL wasn't auto-linked
        const walker = document.createTreeWalker(
            element,
            NodeFilter.SHOW_TEXT,
            {
                acceptNode: function(node) {
                    // Skip if inside code, pre, or already processed
                    if (node.parentElement.closest('pre, code, .youtube-embed-container, a')) {
                        return NodeFilter.FILTER_REJECT;
                    }
                    if (YOUTUBE_LINK_REGEX.test(node.textContent)) {
                        return NodeFilter.FILTER_ACCEPT;
                    }
                    return NodeFilter.FILTER_REJECT;
                }
            }
        );

        const textNodesToProcess = [];
        let currentNode;
        while (currentNode = walker.nextNode()) {
            textNodesToProcess.push(currentNode);
        }

        textNodesToProcess.forEach(textNode => {
            const text = textNode.textContent;
            const match = text.match(YOUTUBE_LINK_REGEX);
            
            if (match) {
                const url = match[0];
                const embed = createEmbed(url);
                
                if (embed) {
                    const beforeText = text.substring(0, match.index);
                    const afterText = text.substring(match.index + url.length);
                    
                    const fragment = document.createDocumentFragment();
                    
                    if (beforeText) {
                        fragment.appendChild(document.createTextNode(beforeText));
                    }
                    
                    fragment.appendChild(embed);
                    
                    if (afterText) {
                        fragment.appendChild(document.createTextNode(afterText));
                    }
                    
                    textNode.replaceWith(fragment);
                }
            }
        });
    }

    /**
     * Initialize all YouTube embeds on page (for existing content)
     */
    function initializeExistingEmbeds() {
        // Find any containers that need initialization
        const containers = document.querySelectorAll('.youtube-embed-container[data-video-id]:not([data-initialized])');
        
        containers.forEach(container => {
            container.dataset.initialized = 'true';
            const videoId = container.dataset.videoId;
            
            if (hasConsent() && !container.querySelector('iframe')) {
                loadVideo(container, videoId);
            }
        });
    }

    /**
     * Revoke consent (for settings page)
     */
    function revokeConsent() {
        saveConsent(false);
    }

    /**
     * Get consent status (for settings page)
     */
    function getConsentStatus() {
        return hasConsent();
    }

    // Public API
    return {
        hasConsent,
        saveConsent,
        revokeConsent,
        getConsentStatus,
        extractVideoId,
        isYouTubeUrl,
        createEmbed,
        processRenderedContent,
        initializeExistingEmbeds,
        YOUTUBE_REGEX,
        YOUTUBE_LINK_REGEX
    };
})();

// Export for global access
if (typeof window !== 'undefined') {
    window.YouTubeEmbed = YouTubeEmbed;
}
