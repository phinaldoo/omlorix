(() => {
    'use strict';

    if (typeof window !== 'undefined' && window.createDropdownController) {
        return;
    }

    const controllers = new Set();
    const panelNavigators = new WeakMap();
    let controllerCounter = 0;
    const PANEL_HEIGHT_ANIMATION_CLASS = 'is-panel-height-animating';
    const PANEL_HEIGHT_ANIMATION_FALLBACK_MS = 500;

    const DEFAULT_FOCUSABLE_SELECTOR = [
        'a[href]',
        'button:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        'textarea:not([disabled])',
        '[tabindex]',
    ].join(', ');

    function toArray(value) {
        if (!value) {
            return [];
        }
        if (Array.isArray(value)) {
            return value.filter(Boolean);
        }
        if (typeof NodeList !== 'undefined' && value instanceof NodeList) {
            return Array.from(value).filter(Boolean);
        }
        if (typeof HTMLCollection !== 'undefined' && value instanceof HTMLCollection) {
            return Array.from(value).filter(Boolean);
        }
        return [value].filter(Boolean);
    }

    function resolveElements(value) {
        return toArray(value)
            .flatMap((candidate) => {
                if (typeof candidate === 'string') {
                    return Array.from(document.querySelectorAll(candidate));
                }
                return [candidate];
            })
            .filter((candidate) => candidate && typeof candidate.contains === 'function');
    }

    function normalizeClassNames(classNames) {
        if (!classNames) {
            return [];
        }
        return toArray(classNames)
            .flatMap((className) => String(className).split(/\s+/))
            .map((className) => className.trim())
            .filter(Boolean);
    }

    function toggleClasses(elements, classNames, isOpen) {
        const classes = normalizeClassNames(classNames);
        if (!classes.length) {
            return;
        }
        resolveElements(elements).forEach((element) => {
            classes.forEach((className) => {
                element.classList.toggle(className, isOpen);
            });
        });
    }

    function containsTarget(elements, target) {
        if (!target) {
            return false;
        }
        return resolveElements(elements).some((element) => element.contains(target));
    }

    function setExpanded(elements, isOpen) {
        resolveElements(elements).forEach((element) => {
            element.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });
    }

    function setHidden(elements, isOpen) {
        resolveElements(elements).forEach((element) => {
            element.setAttribute('aria-hidden', isOpen ? 'false' : 'true');
        });
    }

    function setInert(elements, isOpen) {
        resolveElements(elements).forEach((element) => {
            element.inert = !isOpen;
        });
    }

    function setFocusableState(container, isOpen, selector) {
        const target = resolveElements(container)[0];
        if (!target || typeof target.querySelectorAll !== 'function') {
            return;
        }

        target.querySelectorAll(selector || DEFAULT_FOCUSABLE_SELECTOR).forEach((element) => {
            if (isOpen) {
                const previousTabIndex = element.dataset.dropdownPreviousTabIndex;
                if (previousTabIndex === undefined) {
                    element.removeAttribute('tabindex');
                } else if (previousTabIndex === '') {
                    element.removeAttribute('tabindex');
                    delete element.dataset.dropdownPreviousTabIndex;
                } else {
                    element.tabIndex = Number(previousTabIndex);
                    delete element.dataset.dropdownPreviousTabIndex;
                }
                return;
            }

            if (element.dataset.dropdownPreviousTabIndex === undefined) {
                element.dataset.dropdownPreviousTabIndex = element.hasAttribute('tabindex')
                    ? element.getAttribute('tabindex')
                    : '';
            }
            element.tabIndex = -1;
        });
    }

    function focusTarget(target, controller) {
        if (!target) {
            return;
        }

        const element = typeof target === 'function' ? target(controller) : target;
        const resolved = typeof element === 'string'
            ? document.querySelector(element)
            : element;

        if (resolved && typeof resolved.focus === 'function') {
            resolved.focus();
        }
    }

    function closeDropdownControllers({ group = null, except = null, reason = 'external' } = {}) {
        controllers.forEach((controller) => {
            if (controller === except) {
                return;
            }
            if (group && controller.group !== group) {
                return;
            }
            controller.close({ reason });
        });
    }

    function getElementRect(element) {
        if (!element || typeof element.getBoundingClientRect !== 'function') {
            return null;
        }

        const rect = element.getBoundingClientRect();
        const top = Number(rect?.top);
        const right = Number(rect?.right);
        const bottom = Number(rect?.bottom);
        const left = Number(rect?.left);
        if (![top, right, bottom, left].every(Number.isFinite)) {
            return null;
        }

        return {
            top,
            right,
            bottom,
            left,
            width: Number.isFinite(Number(rect.width)) ? Number(rect.width) : Math.max(0, right - left),
            height: Number.isFinite(Number(rect.height)) ? Number(rect.height) : Math.max(0, bottom - top),
        };
    }

    function readConfiguredHorizontalOrigin(dropdown) {
        const inlineOrigin = String(dropdown?.style?.transformOrigin || '').toLowerCase();
        if (inlineOrigin.includes('left')) return 'left';
        if (inlineOrigin.includes('right')) return 'right';

        const getStyle = window.getComputedStyle || globalThis.getComputedStyle;
        if (typeof getStyle !== 'function' || !dropdown) {
            return null;
        }

        const computedStyle = getStyle(dropdown);
        const customOrigin = String(
            computedStyle?.getPropertyValue?.('--select-dropdown-transform-origin') || '',
        ).toLowerCase();
        if (customOrigin.includes('left')) return 'left';
        if (customOrigin.includes('right')) return 'right';
        return null;
    }

    function normalizeMenuPlacement(value) {
        const placement = String(value || '').toLowerCase();
        if (['top', 'up', 'above'].includes(placement)) return 'top';
        if (['bottom', 'down', 'below'].includes(placement)) return 'bottom';
        return null;
    }

    function normalizeHorizontalOrigin(value) {
        const alignment = String(value || '').toLowerCase();
        if (['left', 'start'].includes(alignment)) return 'left';
        if (['right', 'end'].includes(alignment)) return 'right';
        return null;
    }

    /**
     * Resolve the physical corner nearest the trigger from which a shared
     * dropdown should grow. Geometry wins over declared alignment so menus
     * that are flipped or clamped to a viewport edge still animate correctly.
     */
    function prepareDropdownOpeningAnimation(trigger, dropdown, options = {}) {
        if (!trigger || !dropdown?.classList?.contains('select-dropdown')) {
            return null;
        }

        const requestedOrigin = String(options.origin || '').toLowerCase();
        const validRequestedOrigin = /^(top|bottom)-(left|right)$/.test(requestedOrigin)
            ? requestedOrigin
            : null;
        const triggerRect = getElementRect(trigger);
        const dropdownRect = getElementRect(dropdown);
        const hasMeasurableGeometry = Boolean(
            triggerRect
            && dropdownRect
            && (triggerRect.width > 0 || triggerRect.height > 0)
            && (dropdownRect.width > 0 || dropdownRect.height > 0),
        );

        let menuPlacement = normalizeMenuPlacement(options.placement);
        if (!menuPlacement && hasMeasurableGeometry) {
            const triggerCenterY = (triggerRect.top + triggerRect.bottom) / 2;
            const dropdownCenterY = (dropdownRect.top + dropdownRect.bottom) / 2;
            menuPlacement = dropdownCenterY < triggerCenterY ? 'top' : 'bottom';
        }
        menuPlacement ||= normalizeMenuPlacement(dropdown.dataset?.verticalPlacement);
        if (!menuPlacement && dropdown.classList.contains('upward')) {
            menuPlacement = 'top';
        }
        menuPlacement ||= 'bottom';

        let horizontalOrigin = null;
        if (hasMeasurableGeometry) {
            const triggerCenterX = (triggerRect.left + triggerRect.right) / 2;
            const anchorX = Math.min(
                Math.max(triggerCenterX, dropdownRect.left),
                dropdownRect.right,
            );
            horizontalOrigin = Math.abs(anchorX - dropdownRect.left)
                <= Math.abs(dropdownRect.right - anchorX)
                ? 'left'
                : 'right';
        }
        horizontalOrigin ||= normalizeHorizontalOrigin(options.align || options.alignment);
        horizontalOrigin ||= readConfiguredHorizontalOrigin(dropdown);
        horizontalOrigin ||= 'right';

        const origin = validRequestedOrigin
            || `${menuPlacement === 'top' ? 'bottom' : 'top'}-${horizontalOrigin}`;
        const [verticalOrigin] = origin.split('-');
        const originCss = origin.replace('-', ' ');
        const closedOffsetY = verticalOrigin === 'bottom' ? '8px' : '-8px';

        dropdown.dataset.dropdownAnimationOrigin = origin;
        dropdown.style.transformOrigin = originCss;
        if (typeof dropdown.style.setProperty === 'function') {
            dropdown.style.setProperty('--select-dropdown-transform-origin', originCss);
            dropdown.style.setProperty('--select-dropdown-animation-offset-y', closedOffsetY);
        } else {
            dropdown.style['--select-dropdown-transform-origin'] = originCss;
            dropdown.style['--select-dropdown-animation-offset-y'] = closedOffsetY;
        }
        return origin;
    }

    function resolveOpeningTrigger(triggerElements, detail = {}) {
        const eventTarget = detail.event?.currentTarget || detail.event?.target;
        if (eventTarget) {
            const matchingTrigger = triggerElements.find((trigger) => (
                trigger === eventTarget || trigger.contains?.(eventTarget)
            ));
            if (matchingTrigger) {
                return matchingTrigger;
            }
        }

        return triggerElements.find((trigger) => {
            const rect = getElementRect(trigger);
            return rect && (rect.width > 0 || rect.height > 0);
        }) || triggerElements[0] || null;
    }

    /**
     * Position a shared dropdown beside a trigger while keeping the complete
     * menu above or below the trigger and inside the viewport.
     */
    function positionDropdownAtTrigger(trigger, dropdown, options = {}) {
        if (!trigger || !dropdown) {
            return null;
        }

        const spacing = Number.isFinite(options.spacing) ? options.spacing : 8;
        const viewportMargin = Number.isFinite(options.viewportMargin) ? options.viewportMargin : 8;
        const align = options.align === 'start' ? 'start' : 'end';
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth;
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight;
        const triggerRect = trigger.getBoundingClientRect();
        const maximumWidth = Math.max(0, viewportWidth - viewportMargin * 2);

        dropdown.style.maxWidth = `${maximumWidth}px`;
        dropdown.style.maxHeight = '';
        dropdown.style.overflowY = '';
        dropdown.style.overflowX = '';

        // offset dimensions are unaffected by the shared dropdown's closed
        // scale transform, keeping the final open position pixel-accurate.
        const naturalHeight = dropdown.offsetHeight;
        const roomBelow = Math.max(0, viewportHeight - triggerRect.bottom - spacing - viewportMargin);
        const roomAbove = Math.max(0, triggerRect.top - spacing - viewportMargin);
        const openUpward = naturalHeight > roomBelow && roomAbove > roomBelow;
        const availableHeight = openUpward ? roomAbove : roomBelow;

        if (naturalHeight > availableHeight) {
            dropdown.style.maxHeight = `${availableHeight}px`;
            dropdown.style.overflowY = 'auto';
            dropdown.style.overflowX = 'hidden';
        }

        const menuWidth = dropdown.offsetWidth;
        const menuHeight = dropdown.offsetHeight;
        const proposedLeft = align === 'start'
            ? triggerRect.left
            : triggerRect.right - menuWidth;
        const maximumLeft = Math.max(viewportMargin, viewportWidth - menuWidth - viewportMargin);
        const left = Math.min(Math.max(viewportMargin, proposedLeft), maximumLeft);
        const top = openUpward
            ? triggerRect.top - menuHeight - spacing
            : triggerRect.bottom + spacing;

        Object.assign(dropdown.style, {
            position: 'fixed',
            left: `${left}px`,
            top: `${Math.max(viewportMargin, top)}px`,
            right: 'auto',
            bottom: 'auto',
        });
        dropdown.classList.toggle('upward', openUpward);
        prepareDropdownOpeningAnimation(trigger, dropdown, {
            placement: openUpward ? 'top' : 'bottom',
            align,
        });

        return openUpward ? 'top' : 'bottom';
    }

    function escapeHtml(value = '') {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function createDropdownController(options = {}) {
        const triggerElements = resolveElements(options.trigger || options.triggers);
        const dropdownElements = resolveElements(options.dropdown || options.menu || options.panel);
        const rootElements = resolveElements(options.root || options.container);
        const insideElements = [
            ...rootElements,
            ...triggerElements,
            ...dropdownElements,
            ...resolveElements(options.inside || options.insideElements),
        ];
        const expandedElements = options.expandedElements === undefined
            ? triggerElements
            : resolveElements(options.expandedElements);
        const controllerId = options.id || `dropdown-controller-${++controllerCounter}`;
        const dropdownOpenClass = options.dropdownOpenClass === undefined
            ? (options.openClass === undefined ? 'open' : options.openClass)
            : options.dropdownOpenClass;
        const triggerOpenClass = options.triggerOpenClass || '';
        const rootOpenClass = options.rootOpenClass || '';
        const closeOnOutsideClick = options.closeOnOutsideClick !== false;
        const closeOnFocusOutside = options.closeOnFocusOutside === true;
        const closeOnEscape = options.closeOnEscape !== false;
        const restoreFocusTarget = options.restoreFocusTarget || triggerElements[0] || null;
        const focusableContainer = options.focusableContainer === undefined
            ? dropdownElements[0]
            : options.focusableContainer;
        const outsideEvents = options.outsideEvents || ['click'];
        const bindTrigger = options.bindTrigger !== false;
        let isOpen = false;
        let escapeRegistration = null;

        const controller = {
            id: controllerId,
            group: options.group || null,
            triggerElements,
            dropdownElements,
            rootElements,
            isOpen: () => isOpen,
            open: (detail = {}) => setOpen(true, detail),
            close: (detail = {}) => setOpen(false, detail),
            toggle: (detail = {}) => setOpen(!isOpen, detail),
            destroy,
        };

        function syncState(open) {
            toggleClasses(dropdownElements, dropdownOpenClass, open);
            toggleClasses(triggerElements, triggerOpenClass, open);
            toggleClasses(rootElements, rootOpenClass, open);
            toArray(options.openTargets).forEach((target) => {
                toggleClasses(target.element || target.elements || target, target.className || target.classNames, open);
            });

            setExpanded(expandedElements, open);

            if (options.manageAriaHidden !== false) {
                setHidden(options.ariaHiddenElements || dropdownElements, open);
            }

            if (options.inert === true) {
                setInert(options.inertElements || dropdownElements, open);
            }

            if (options.manageFocusable === true) {
                setFocusableState(focusableContainer, open, options.focusableSelector);
            }
        }

        function bindDocumentListeners() {
            if (closeOnOutsideClick) {
                outsideEvents.forEach((eventName) => {
                    document.addEventListener(eventName, handleOutsideInteraction, options.useCapture === true);
                });
            }
            if (closeOnFocusOutside) {
                document.addEventListener('focusin', handleFocusOutside, true);
            }
            if (closeOnEscape) {
                if (typeof window.registerEscapeHandler === 'function') {
                    escapeRegistration = window.registerEscapeHandler({
                        id: `${controllerId}-escape`,
                        priority: Number.isFinite(options.escapePriority) ? options.escapePriority : 80,
                        isActive: () => isOpen,
                        close: () => setOpen(false, { reason: 'escape', restoreFocus: true }),
                    });
                } else {
                    document.addEventListener('keydown', handleKeydown, true);
                }
            }
        }

        function unbindDocumentListeners() {
            outsideEvents.forEach((eventName) => {
                document.removeEventListener(eventName, handleOutsideInteraction, options.useCapture === true);
            });
            document.removeEventListener('focusin', handleFocusOutside, true);
            document.removeEventListener('keydown', handleKeydown, true);
            if (escapeRegistration) {
                if (typeof escapeRegistration.unregister === 'function') {
                    escapeRegistration.unregister();
                } else if (typeof window.unregisterEscapeHandler === 'function') {
                    window.unregisterEscapeHandler(escapeRegistration.id);
                }
                escapeRegistration = null;
            }
        }

        function setOpen(nextOpen, detail = {}) {
            const shouldOpen = Boolean(nextOpen);
            if (isOpen === shouldOpen) {
                if (!shouldOpen && detail.restoreFocus) {
                    focusTarget(detail.restoreFocusTarget || restoreFocusTarget, controller);
                }
                return isOpen;
            }

            const lifecycleDetail = {
                controller,
                isOpen: shouldOpen,
                reason: detail.reason || (shouldOpen ? 'open' : 'close'),
                event: detail.event,
            };

            if (shouldOpen && options.group && options.closeOthersOnOpen !== false) {
                closeDropdownControllers({ group: options.group, except: controller, reason: 'group' });
            }

            if (shouldOpen && typeof options.onBeforeOpen === 'function' && options.onBeforeOpen(lifecycleDetail) === false) {
                return isOpen;
            }
            if (!shouldOpen && typeof options.onBeforeClose === 'function' && options.onBeforeClose(lifecycleDetail) === false) {
                return isOpen;
            }

            if (shouldOpen && options.prepareAnimation !== false) {
                const openingTrigger = resolveOpeningTrigger(triggerElements, detail);
                dropdownElements.forEach((dropdown) => {
                    prepareDropdownOpeningAnimation(openingTrigger, dropdown, {
                        placement: typeof options.animationPlacement === 'function'
                            ? options.animationPlacement(dropdown, openingTrigger, controller)
                            : options.animationPlacement,
                        align: typeof options.animationAlign === 'function'
                            ? options.animationAlign(dropdown, openingTrigger, controller)
                            : options.animationAlign,
                    });
                });
            }

            isOpen = shouldOpen;
            syncState(isOpen);

            if (isOpen) {
                bindDocumentListeners();
                if (typeof options.onOpen === 'function') {
                    options.onOpen(lifecycleDetail);
                }
                if (options.focusOnOpen) {
                    window.setTimeout(() => {
                        if (isOpen) {
                            focusTarget(options.focusOnOpen, controller);
                        }
                    }, Number.isFinite(options.focusDelay) ? options.focusDelay : 0);
                }
            } else {
                unbindDocumentListeners();
                if (typeof options.onClose === 'function') {
                    options.onClose(lifecycleDetail);
                }
                if (detail.restoreFocus || options.restoreFocusOnClose === true) {
                    focusTarget(detail.restoreFocusTarget || restoreFocusTarget, controller);
                }
            }

            if (typeof options.onToggle === 'function') {
                options.onToggle(lifecycleDetail);
            }

            return isOpen;
        }

        function handleTriggerClick(event) {
            if (options.preventTriggerDefault !== false) {
                event.preventDefault();
            }
            if (options.stopTriggerPropagation !== false) {
                event.stopPropagation();
            }
            controller.toggle({
                event,
                reason: 'trigger',
                restoreFocus: isOpen,
            });
        }

        function handleOutsideInteraction(event) {
            if (!isOpen) {
                return;
            }
            if (containsTarget(insideElements, event.target)) {
                return;
            }
            if (typeof options.shouldCloseOnOutsideInteraction === 'function'
                && options.shouldCloseOnOutsideInteraction(event, controller) === false) {
                return;
            }
            setOpen(false, { event, reason: 'outside' });
        }

        function handleFocusOutside(event) {
            if (!isOpen || containsTarget(insideElements, event.target)) {
                return;
            }
            setOpen(false, { event, reason: 'focusout' });
        }

        function handleKeydown(event) {
            if (event.key !== 'Escape' || !isOpen) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            setOpen(false, { event, reason: 'escape', restoreFocus: true });
        }

        function destroy() {
            setOpen(false, { reason: 'destroy' });
            triggerElements.forEach((trigger) => {
                trigger.removeEventListener('click', handleTriggerClick);
            });
            controllers.delete(controller);
        }

        controllers.add(controller);

        if (bindTrigger) {
            triggerElements.forEach((trigger) => {
                trigger.addEventListener('click', handleTriggerClick);
            });
        }

        syncState(false);

        return controller;
    }

    function measureDropdownPanelContent(section) {
        if (!section) {
            return 0;
        }

        const getStyle = window.getComputedStyle || globalThis.getComputedStyle;
        const sectionStyle = typeof getStyle === 'function' ? getStyle(section) : null;
        const sectionTop = section.offsetTop || 0;
        const visibleChildren = Array.from(section.children || []).filter((child) => (
            !sectionStyle || getStyle(child).display !== 'none'
        ));
        const contentBottom = visibleChildren.reduce((bottom, child) => (
            Math.max(bottom, (child.offsetTop || 0) + (child.offsetHeight || 0))
        ), sectionTop + (Number.parseFloat(sectionStyle?.paddingTop) || 0));
        return Math.ceil(
            contentBottom
            - sectionTop
            + (Number.parseFloat(sectionStyle?.paddingBottom) || 0),
        );
    }

    /**
     * Add in-place panel navigation to a shared dropdown. Panels, triggers,
     * animated height, focus, ARIA state, back actions, and Escape handling
     * stay consistent for every dropdown that opts into this helper.
     */
    function createDropdownPanelNavigator(options = {}) {
        const dropdown = resolveElements(options.dropdown || options.root)[0];
        if (!dropdown) {
            return null;
        }

        panelNavigators.get(dropdown)?.destroy();

        dropdown.classList.add('select-dropdown-panel-menu');
        const panels = resolveElements(
            options.panels || dropdown.querySelectorAll('[data-dropdown-panel]'),
        );
        if (!panels.length) {
            return null;
        }

        const mainPanel = String(options.mainPanel || 'main');
        const panelByName = new Map(panels.map((panel) => {
            panel.classList.add('select-dropdown-panel');
            return [String(panel.dataset.dropdownPanel || ''), panel];
        }));
        const triggers = resolveElements(
            options.triggers || dropdown.querySelectorAll('[data-dropdown-open-panel]'),
        );
        let activePanel = panelByName.has(mainPanel)
            ? mainPanel
            : panelByName.keys().next().value;
        const navigatorId = options.id || dropdown.id || `dropdown-panel-navigator-${++controllerCounter}`;
        let heightAnimationEndHandler = null;
        let heightAnimationTimer = null;

        function addTriggerChevron(trigger) {
            if (options.addChevrons === false
                || trigger.querySelector?.('[data-dropdown-panel-chevron]')) {
                return;
            }
            const chevron = document.createElement('span');
            chevron.className = 'select-dropdown-panel-chevron';
            chevron.dataset.dropdownPanelChevron = '';
            chevron.setAttribute('aria-hidden', 'true');
            chevron.innerHTML = window.Icons?.chatFilesChevron || '';
            trigger.appendChild(chevron);
        }

        function resolvePanelHeight(panelName, panel) {
            const customHeight = options.getPanelHeight?.(panelName, panel, navigator);
            if (Number.isFinite(customHeight)) {
                return customHeight;
            }

            const fixedHeight = Number.parseFloat(panel.dataset.dropdownPanelHeight);
            if (Number.isFinite(fixedHeight)) {
                return fixedHeight;
            }

            const header = panel.querySelector?.(':scope > [data-dropdown-panel-header], :scope > .select-dropdown-panel-header');
            const content = panel.querySelector?.(':scope > [data-dropdown-panel-content], :scope > .select-dropdown-panel-scroll');
            const getStyle = window.getComputedStyle || globalThis.getComputedStyle;
            const dropdownStyle = typeof getStyle === 'function' ? getStyle(dropdown) : null;
            const borderHeight = (Number.parseFloat(dropdownStyle?.borderTopWidth) || 0)
                + (Number.parseFloat(dropdownStyle?.borderBottomWidth) || 0);
            const measuredHeight = (header?.offsetHeight || 0)
                + measureDropdownPanelContent(content)
                + borderHeight;
            return measuredHeight || panel.scrollHeight || panel.offsetHeight || 0;
        }

        function cancelRenderedHeightTransition() {
            if (typeof dropdown.getAnimations !== 'function') {
                return;
            }
            dropdown.getAnimations().forEach((animation) => {
                if (animation.transitionProperty === 'height') {
                    animation.cancel();
                }
            });
        }

        function clearHeightAnimation({ cancelTransition = false } = {}) {
            if (heightAnimationEndHandler) {
                dropdown.removeEventListener('transitionend', heightAnimationEndHandler);
                dropdown.removeEventListener('transitioncancel', heightAnimationEndHandler);
                heightAnimationEndHandler = null;
            }
            if (heightAnimationTimer !== null) {
                window.clearTimeout?.(heightAnimationTimer);
                heightAnimationTimer = null;
            }
            if (cancelTransition) {
                cancelRenderedHeightTransition();
            }
            dropdown.classList.remove(PANEL_HEIGHT_ANIMATION_CLASS);
        }

        function scheduleHeightAnimationCleanup() {
            heightAnimationEndHandler = (event) => {
                if (event.target !== dropdown || event.propertyName !== 'height') {
                    return;
                }
                clearHeightAnimation();
            };
            dropdown.addEventListener('transitionend', heightAnimationEndHandler);
            dropdown.addEventListener('transitioncancel', heightAnimationEndHandler);
            heightAnimationTimer = window.setTimeout?.(
                () => clearHeightAnimation(),
                PANEL_HEIGHT_ANIMATION_FALLBACK_MS,
            ) ?? null;
        }

        function readRenderedHeight() {
            const getStyle = window.getComputedStyle || globalThis.getComputedStyle;
            const renderedHeight = typeof getStyle === 'function'
                ? Number.parseFloat(getStyle(dropdown)?.height)
                : Number.NaN;
            if (Number.isFinite(renderedHeight) && renderedHeight > 0) {
                return renderedHeight;
            }
            const inlineHeight = Number.parseFloat(dropdown.style.height);
            return Number.isFinite(inlineHeight) ? inlineHeight : 0;
        }

        function applyHeight(height, { animate = false } = {}) {
            if (!(height > 0)) {
                clearHeightAnimation({ cancelTransition: true });
                return false;
            }

            const nextHeight = Math.ceil(height);
            const renderedHeight = readRenderedHeight();
            const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches === true;
            const shouldAnimate = Boolean(
                animate
                && dropdown.classList.contains('open')
                && !reducedMotion
                && renderedHeight > 0
                && Math.abs(renderedHeight - nextHeight) >= 1,
            );

            clearHeightAnimation({ cancelTransition: true });
            if (!shouldAnimate) {
                dropdown.style.height = `${nextHeight}px`;
                return false;
            }

            // Commit the currently rendered height with transitions disabled,
            // then opt into height motion only for this panel-to-panel change.
            dropdown.style.height = `${renderedHeight}px`;
            void dropdown.offsetHeight;
            dropdown.classList.add(PANEL_HEIGHT_ANIMATION_CLASS);
            void dropdown.offsetHeight;
            scheduleHeightAnimationCleanup();
            dropdown.style.height = `${nextHeight}px`;
            return true;
        }

        function syncHeight(panelName = activePanel, detail = {}) {
            const panel = panelByName.get(panelName);
            if (!panel) {
                return 0;
            }
            const minimumHeight = Number.isFinite(options.minHeight)
                ? options.minHeight
                : (Number.parseFloat(dropdown.dataset.dropdownPanelMinHeight) || 0);
            const naturalHeight = Math.max(minimumHeight, resolvePanelHeight(panelName, panel));
            const configuredMaximum = typeof options.maxHeight === 'function'
                ? options.maxHeight(panelName, panel, navigator)
                : options.maxHeight;
            const maximumHeight = Number.isFinite(configuredMaximum)
                ? configuredMaximum
                : Number.parseFloat(dropdown.dataset.dropdownPanelMaxHeight);
            const height = Number.isFinite(maximumHeight)
                ? Math.min(naturalHeight, maximumHeight)
                : naturalHeight;
            const animated = applyHeight(height, { animate: detail.animate === true });
            options.onHeightChange?.({ panelName, panel, height, animated, navigator });
            return height;
        }

        function focusPanel(panelName, panel) {
            const target = options.getFocusTarget?.(panelName, panel, navigator)
                || panel.querySelector?.('[autofocus], input:not([disabled]), button:not([disabled]), [tabindex]:not([tabindex="-1"])');
            const schedule = window.requestAnimationFrame || window.setTimeout;
            schedule?.(() => target?.focus?.({ preventScroll: true }));
        }

        function open(panelName, detail = {}) {
            const nextPanelName = panelByName.has(String(panelName))
                ? String(panelName)
                : mainPanel;
            const panel = panelByName.get(nextPanelName);
            if (!panel || options.onBeforeNavigate?.({
                panelName: nextPanelName,
                previousPanelName: activePanel,
                panel,
                navigator,
            }) === false) {
                return false;
            }

            const previousPanelName = activePanel;
            activePanel = nextPanelName;
            panels.forEach((candidate) => {
                const candidateName = String(candidate.dataset.dropdownPanel || '');
                const isMain = candidateName === mainPanel;
                const isActive = candidateName === activePanel;
                candidate.classList.toggle('is-active', isActive);
                candidate.classList.toggle('is-behind', isMain && activePanel !== mainPanel);
                candidate.setAttribute('aria-hidden', isActive ? 'false' : 'true');
                candidate.inert = !isActive;
            });
            triggers.forEach((trigger) => {
                trigger.setAttribute(
                    'aria-expanded',
                    String(trigger.dataset.dropdownOpenPanel) === activePanel ? 'true' : 'false',
                );
            });
            syncHeight(activePanel, {
                animate: detail.animateHeight !== false
                    && previousPanelName !== activePanel
                    && dropdown.classList.contains('open'),
            });

            if (detail.notify !== false) {
                options.onNavigate?.({
                    panelName: activePanel,
                    previousPanelName,
                    panel,
                    navigator,
                });
            }
            if (detail.focus !== false) {
                focusPanel(activePanel, panel);
            }
            return true;
        }

        function reset(detail = {}) {
            return open(mainPanel, detail);
        }

        function handleClick(event) {
            const trigger = event.target?.closest?.('[data-dropdown-open-panel]');
            if (trigger && dropdown.contains(trigger)) {
                event.preventDefault();
                event.stopPropagation();
                open(trigger.dataset.dropdownOpenPanel);
                return;
            }

            const backButton = event.target?.closest?.('[data-dropdown-panel-back]');
            if (backButton && dropdown.contains(backButton)) {
                event.preventDefault();
                event.stopPropagation();
                reset();
            }
        }

        function handleKeydown(event) {
            if (event.key !== 'Escape' || activePanel === mainPanel) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            reset();
        }

        function destroy() {
            clearHeightAnimation({ cancelTransition: true });
            dropdown.removeEventListener('click', handleClick);
            dropdown.removeEventListener('keydown', handleKeydown);
            if (panelNavigators.get(dropdown) === navigator) {
                panelNavigators.delete(dropdown);
            }
        }

        const navigator = {
            dropdown,
            panels,
            mainPanel,
            get activePanel() {
                return activePanel;
            },
            open,
            reset,
            syncHeight,
            destroy,
        };

        panelNavigators.set(dropdown, navigator);

        triggers.forEach((trigger) => {
            addTriggerChevron(trigger);
            trigger.setAttribute('aria-haspopup', 'true');
            const panelName = String(trigger.dataset.dropdownOpenPanel || '');
            const panel = panelByName.get(panelName);
            if (panel) {
                if (!panel.id) {
                    panel.id = `${navigatorId}-${panelName}-panel`;
                }
                trigger.setAttribute('aria-controls', panel.id);
            }
        });
        dropdown.addEventListener('click', handleClick);
        dropdown.addEventListener('keydown', handleKeydown);
        reset({ focus: false, notify: false });
        return navigator;
    }

    /**
     * Open a transient shared menu from item data. The shared component owns
     * its markup, positioning, focus behavior, and cleanup.
     */
    function openDropdownMenu({
        trigger,
        items = [],
        ariaLabel = '',
        onSelect,
    } = {}) {
        if (!trigger || !Array.isArray(items) || !items.length) {
            return null;
        }

        const menu = document.createElement('div');
        menu.className = 'select-dropdown select-dropdown-portal';
        menu.setAttribute('role', 'menu');
        menu.setAttribute('aria-label', String(ariaLabel));

        const buttons = items.map((item) => {
            const wrapper = document.createElement('div');
            wrapper.className = 'select-dropdown-item';

            const button = document.createElement('button');
            button.type = 'button';
            button.className = [
                'select-dropdown-button',
                item.destructive ? 'select-dropdown-button-red' : '',
            ].filter(Boolean).join(' ');
            button.setAttribute(
                'role',
                typeof item.checked === 'boolean' ? 'menuitemradio' : 'menuitem',
            );
            if (typeof item.checked === 'boolean') {
                button.setAttribute('aria-checked', item.checked ? 'true' : 'false');
            }
            button.disabled = item.disabled === true;
            button.innerHTML = `${item.iconHtml || ''}<p>${escapeHtml(item.label)}</p>${
                item.checked ? (globalThis.Icons?.check || '') : ''
            }`;
            wrapper.appendChild(button);
            menu.appendChild(wrapper);
            return { button, item };
        });

        document.body.appendChild(menu);
        positionDropdownAtTrigger(trigger, menu);

        let controller;
        const closeForViewportChange = () => controller.close({ reason: 'viewport' });
        const cleanup = () => {
            window.removeEventListener?.('resize', closeForViewportChange);
            window.removeEventListener?.('scroll', closeForViewportChange, true);
        };

        controller = createDropdownController({
            group: 'shared-transient-menu',
            trigger,
            dropdown: menu,
            bindTrigger: false,
            focusOnOpen: () => buttons.find(({ button, item }) => item.checked && !button.disabled)?.button
                || buttons.find(({ button }) => !button.disabled)?.button,
            onClose: () => {
                cleanup();
                menu.remove();
                controller.destroy();
            },
        });

        buttons.forEach(({ button, item }) => {
            button.addEventListener('click', async (event) => {
                controller.close({ event, reason: 'selection' });
                await (item.onSelect || onSelect)?.(item, event);
            });
        });

        menu.addEventListener('keydown', (event) => {
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
            const enabledButtons = buttons
                .map(({ button }) => button)
                .filter((button) => !button.disabled);
            const currentIndex = enabledButtons.indexOf(event.target);
            if (!enabledButtons.length || currentIndex < 0) return;

            event.preventDefault();
            const nextIndex = event.key === 'Home'
                ? 0
                : event.key === 'End'
                    ? enabledButtons.length - 1
                    : (currentIndex + (event.key === 'ArrowDown' ? 1 : -1) + enabledButtons.length)
                        % enabledButtons.length;
            enabledButtons[nextIndex].focus();
        });

        window.addEventListener?.('resize', closeForViewportChange);
        window.addEventListener?.('scroll', closeForViewportChange, true);
        controller.open({ reason: 'open' });
        return controller;
    }

    window.createDropdownController = createDropdownController;
    window.createDropdownPanelNavigator = createDropdownPanelNavigator;
    window.getDropdownPanelNavigator = (dropdown) => panelNavigators.get(dropdown) || null;
    window.closeDropdownControllers = closeDropdownControllers;
    window.prepareDropdownOpeningAnimation = prepareDropdownOpeningAnimation;
    window.positionDropdownAtTrigger = positionDropdownAtTrigger;
    window.openDropdownMenu = openDropdownMenu;
})();
