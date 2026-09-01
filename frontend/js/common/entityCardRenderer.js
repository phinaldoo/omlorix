/**
 * Shared Project-style entity card renderer.
 *
 * Features provide their icon, menu actions, and feature-specific bottom
 * content while this module owns the repeated shell, accessible menu trigger,
 * dropdown wiring, and a dedicated primary action.
 */
(function initializeEntityCardRenderer(global) {
    'use strict';

    /** Escape text before it is inserted into a card template. */
    function escapeHtml(value = '') {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    let nextMenuId = 0;

    /** Create one card and bind all shared menu interactions. */
    function createCard({
        className = '',
        dataset = {},
        iconHtml,
        topExtraHtml = '',
        title,
        bottomExtraHtml = '',
        menuItems = [],
        moreOptionsLabel = 'More options',
        closeDropdowns = () => {},
        onClick,
    }) {
        const card = global.document.createElement('div');
        card.className = ['projects-content-main-element', className].filter(Boolean).join(' ');
        card.setAttribute('role', 'group');
        card.setAttribute('aria-label', String(title));
        Object.entries(dataset).forEach(([key, value]) => {
            card.dataset[key] = String(value);
        });

        const menuId = `entityCardMenu${++nextMenuId}`;
        const menuMarkup = menuItems.map((item) => `
            <div class="select-dropdown-item">
                <button type="button" role="menuitem" class="select-dropdown-button ${escapeHtml(item.className || '')}"
                    data-entity-action="${escapeHtml(item.action)}">
                    ${item.iconHtml || ''}<p>${escapeHtml(item.label)}</p>
                </button>
            </div>`).join('');
        card.innerHTML = `
            <div class="projects-content-main-element-top">
                ${iconHtml}
                ${topExtraHtml}
                <button type="button" class="project-ellipsis" aria-label="${escapeHtml(moreOptionsLabel)}"
                    aria-haspopup="menu" aria-expanded="false" aria-controls="${escapeHtml(menuId)}">
                    ${global.Icons?.ellipsis || ''}
                </button>
                <div class="select-dropdown" id="${escapeHtml(menuId)}" role="menu">${menuMarkup}</div>
            </div>
            <button type="button" class="projects-content-main-element-bottom entity-card-primary-action"
                aria-label="${escapeHtml(title)}">
                <p class="project-title">${escapeHtml(title)}</p>
                ${bottomExtraHtml}
            </button>`;

        const trigger = card.querySelector('.project-ellipsis');
        const dropdown = card.querySelector('.select-dropdown');
        const primaryAction = card.querySelector('.entity-card-primary-action');
        const getMenuItems = () => Array.from(dropdown?.querySelectorAll('[role="menuitem"]:not(:disabled)') || []);

        /** Keep the popup state and focus behavior consistent for every close path. */
        const setMenuOpen = (open, { focus = 'first', restoreFocus = false } = {}) => {
            dropdown?.classList.toggle('open', open);
            trigger?.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) {
                const items = getMenuItems();
                const target = focus === 'last' ? items[items.length - 1] : items[0];
                target?.focus();
            } else if (restoreFocus) {
                trigger?.focus();
            }
        };

        trigger?.addEventListener('click', (event) => {
            event.stopPropagation();
            const shouldOpen = !dropdown?.classList.contains('open');
            closeDropdowns();
            setMenuOpen(shouldOpen);
        });
        trigger?.addEventListener('keydown', (event) => {
            if (!['ArrowDown', 'ArrowUp'].includes(event.key)) return;
            event.preventDefault();
            event.stopPropagation();
            closeDropdowns();
            setMenuOpen(true, { focus: event.key === 'ArrowUp' ? 'last' : 'first' });
        });
        dropdown?.addEventListener('keydown', (event) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                setMenuOpen(false, { restoreFocus: true });
                return;
            }

            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            const items = getMenuItems();
            const currentIndex = items.indexOf(event.target);
            if (!items.length || currentIndex < 0) return;
            event.preventDefault();
            const nextIndex = event.key === 'Home'
                ? 0
                : event.key === 'End'
                    ? items.length - 1
                    : (currentIndex + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
            items[nextIndex].focus();
        });

        menuItems.forEach((item) => {
            card.querySelector(`[data-entity-action="${item.action}"]`)?.addEventListener('click', async (event) => {
                event.stopPropagation();
                setMenuOpen(false);
                closeDropdowns();
                await item.onSelect?.(event);
            });
        });

        primaryAction?.addEventListener('click', async (event) => {
            await onClick?.(event);
        });
        card.addEventListener('click', async (event) => {
            // The bordered wrapper is the visual card, so its icon row,
            // spacing, and padding must activate the same primary action as
            // the title button. Keep the actual controls independent and let
            // the primary button retain native keyboard behavior.
            if (event.target.closest('.entity-card-primary-action, .project-ellipsis, .select-dropdown')) {
                return;
            }
            await onClick?.(event);
        });
        return card;
    }

    global.EntityCardRenderer = Object.freeze({ createCard, escapeHtml });
})(window);
