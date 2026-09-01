// fertig
function toggleThinking(button) {
    const thinkingContainer = button.closest('.assistant-thinking');
    if (!thinkingContainer) {
        return;
    }
    
    // Toggle collapsed class
    thinkingContainer.classList.toggle('collapsed');
    
    // Determine current state
    const isCollapsed = thinkingContainer.classList.contains('collapsed');
    if (button instanceof HTMLElement) {
        button.setAttribute('aria-expanded', String(!isCollapsed));
    }
    
    // Smooth scroll if expanding and component is partially out of view
    if (!isCollapsed) {
        setTimeout(() => {
            const rect = thinkingContainer.getBoundingClientRect();
            const viewportHeight = window.innerHeight;
            
            if (rect.bottom > viewportHeight * 0.8) {
                thinkingContainer.scrollIntoView({
                    behavior: 'smooth',
                    block: 'nearest'
                });
            }
        }, 100);
    }
}


// Attach listeners
function initializeThinking() {
    const headers = document.querySelectorAll('.assistant-thinking-header');
    let contentCounter = 0;
    headers.forEach((header) => {
        const container = header.closest('.assistant-thinking');
        if (!container) return;
        const content = container.querySelector('.assistant-thinking-content');
        if (content && !content.id) {
            contentCounter += 1;
            const baseId = container.id ? `${container.id}-content` : `assistant-thinking-content-${contentCounter}`;
            let contentId = baseId;
            while (document.getElementById(contentId)) {
                contentCounter += 1;
                contentId = `assistant-thinking-content-${contentCounter}`;
            }
            content.id = contentId;
        }
        if (content && content.id) {
            header.setAttribute('aria-controls', content.id);
        }
        const isCollapsed = container.classList.contains('collapsed');
        header.setAttribute('aria-expanded', String(!isCollapsed));
        if (header.tagName !== 'BUTTON') {
            if (!header.hasAttribute('role')) {
                header.setAttribute('role', 'button');
            }
            if (header.tabIndex < 0) {
                header.tabIndex = 0;
            }
        }
        if (header.dataset.thinkingBound !== 'true') {
            const onClick = () => toggleThinking(header);
            const onKeydown = (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    if (header.tagName !== 'BUTTON') {
                        event.preventDefault();
                    }
                    toggleThinking(header);
                }
            };
            header.addEventListener('click', onClick);
            header.addEventListener('keydown', onKeydown);
            header.dataset.thinkingBound = 'true';
        }
    });
}

// Initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeThinking);
} else {
    initializeThinking();
}