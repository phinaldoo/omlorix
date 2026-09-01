(() => {
    const handlers = new Map();
    let sequence = 0;
    let pendingRequest = null;
    let bypassHandlerId = null;

    const t = (key, fallback) =>
        (typeof window.getTranslation === 'function'
            ? window.getTranslation(key, fallback ?? key)
            : fallback ?? key);

    const getDom = () => ({
        overlay: document.getElementById('deleteUnchangedOverlay'),
        title: document.getElementById('deleteUnchangedTitle'),
        subtitle: document.getElementById('deleteUnchangedSubtitle'),
        close: document.getElementById('deleteUnchangedClose'),
        cancel: document.getElementById('deleteUnchangedCancel'),
        confirm: document.getElementById('deleteUnchangedConfirm'),
    });

    const getDefaultCopy = () => ({
        title: t('modal_discard_changes_title', 'Discard changes?'),
        subtitle: t('modal_discard_changes_desc', 'You have unsaved changes. Are you sure you want to leave without saving?'),
        confirmLabel: t('modal_discard_btn', 'Discard changes'),
    });

    const getOrderedHandlers = () => (
        Array.from(handlers.values()).sort((a, b) => {
            if (b.priority !== a.priority) {
                return b.priority - a.priority;
            }
            return a.order - b.order;
        })
    );

    const resolveCopy = (handler, context) => {
        const copy = typeof handler?.getCopy === 'function'
            ? handler.getCopy(context)
            : null;
        return {
            ...getDefaultCopy(),
            ...(copy && typeof copy === 'object' ? copy : {}),
        };
    };

    const applyCopy = (copy) => {
        const dom = getDom();
        dom.title && (dom.title.textContent = copy.title || '');
        dom.subtitle && (dom.subtitle.textContent = copy.subtitle || '');
        const confirmLabel = dom.confirm?.querySelector('span');
        if (confirmLabel) {
            confirmLabel.textContent = copy.confirmLabel || '';
        } else if (dom.confirm) {
            dom.confirm.textContent = copy.confirmLabel || '';
        }
    };

    const resetCopy = () => {
        applyCopy(getDefaultCopy());
    };

    const register = (candidate) => {
        if (!candidate || typeof candidate !== 'object') {
            return null;
        }

        const { isActive, isDirty } = candidate;
        if (typeof isActive !== 'function' || typeof isDirty !== 'function') {
            return null;
        }

        if (typeof candidate.id === 'string' && handlers.has(candidate.id)) {
            handlers.delete(candidate.id);
        }

        const handlerId = typeof candidate.id === 'string'
            ? candidate.id
            : `unsaved-handler-${++sequence}`;

        handlers.set(handlerId, {
            id: handlerId,
            isActive,
            isDirty,
            discard: typeof candidate.discard === 'function' ? candidate.discard : null,
            getCopy: typeof candidate.getCopy === 'function' ? candidate.getCopy : null,
            priority: Number.isFinite(candidate.priority) ? candidate.priority : 0,
            order: ++sequence,
        });

        return {
            id: handlerId,
            unregister: () => unregister(handlerId),
        };
    };

    const unregister = (id) => {
        if (!id) {
            return;
        }
        handlers.delete(id);
        if (bypassHandlerId === id) {
            bypassHandlerId = null;
        }
    };

    const getMatchingHandler = ({ id, context } = {}) => {
        const orderedHandlers = getOrderedHandlers();
        if (id) {
            const handler = handlers.get(id);
            if (!handler) {
                return null;
            }
            try {
                return handler.isActive(context) && handler.isDirty(context) ? handler : null;
            } catch (error) {
                console.error('[unsavedChangesManager] Failed to evaluate handler', error);
                return null;
            }
        }

        for (const handler of orderedHandlers) {
            try {
                if (handler.isActive(context) && handler.isDirty(context)) {
                    return handler;
                }
            } catch (error) {
                console.error('[unsavedChangesManager] Failed to evaluate handler', error);
            }
        }

        return null;
    };

    const closeDialog = () => {
        const dom = getDom();
        dom.overlay?.classList.remove('active');
        if (dom.overlay) {
            dom.overlay.hidden = true;
        }
        pendingRequest = null;
        resetCopy();
    };

    const handleCancel = () => {
        const request = pendingRequest;
        closeDialog();
        request?.onCancel?.();
    };

    const scheduleBypassReset = (handlerId) => {
        if (!handlerId) {
            return;
        }
        const clear = () => {
            if (bypassHandlerId === handlerId) {
                bypassHandlerId = null;
            }
        };
        if (typeof queueMicrotask === 'function') {
            queueMicrotask(clear);
            return;
        }
        setTimeout(clear, 0);
    };

    const handleConfirm = () => {
        const request = pendingRequest;
        if (!request?.handler) {
            closeDialog();
            return;
        }

        bypassHandlerId = request.handler.id;

        try {
            request.handler.discard?.(request.context);
        } catch (error) {
            console.error('[unsavedChangesManager] Failed to discard pending changes', error);
        }

        closeDialog();
        request.onConfirm?.();
        scheduleBypassReset(request.handler.id);
    };

    const openDialog = (request) => {
        const dom = getDom();
        if (!dom.overlay) {
            bypassHandlerId = request.handler.id;
            try {
                request.handler.discard?.(request.context);
            } catch (error) {
                console.error('[unsavedChangesManager] Failed to discard pending changes', error);
            }
            request.onConfirm?.();
            scheduleBypassReset(request.handler.id);
            return false;
        }

        pendingRequest = request;
        applyCopy(resolveCopy(request.handler, request.context));
        dom.overlay.hidden = false;
        dom.overlay.classList.add('active');
        dom.confirm?.focus();
        return true;
    };

    const confirmIfNeeded = ({ id, onConfirm, onCancel, context } = {}) => {
        const handler = getMatchingHandler({ id, context });
        if (!handler) {
            onConfirm?.();
            return false;
        }

        if (bypassHandlerId && bypassHandlerId === handler.id) {
            bypassHandlerId = null;
            onConfirm?.();
            return false;
        }

        return openDialog({
            handler,
            onConfirm,
            onCancel,
            context,
        });
    };

    const hasActiveDirtyHandler = ({ id, context } = {}) => Boolean(getMatchingHandler({ id, context }));

    const bindControls = () => {
        const dom = getDom();
        if (dom.overlay && dom.overlay.dataset.unsavedBound !== 'true') {
            dom.overlay.addEventListener('click', (event) => {
                if (event.target === dom.overlay) {
                    handleCancel();
                }
            });
            dom.overlay.dataset.unsavedBound = 'true';
        }

        if (dom.close && dom.close.dataset.unsavedBound !== 'true') {
            dom.close.addEventListener('click', handleCancel);
            dom.close.dataset.unsavedBound = 'true';
        }

        if (dom.cancel && dom.cancel.dataset.unsavedBound !== 'true') {
            dom.cancel.addEventListener('click', handleCancel);
            dom.cancel.dataset.unsavedBound = 'true';
        }

        if (dom.confirm && dom.confirm.dataset.unsavedBound !== 'true') {
            dom.confirm.addEventListener('click', handleConfirm);
            dom.confirm.dataset.unsavedBound = 'true';
        }
    };

    const initializeControls = () => {
        bindControls();
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeControls, { once: true });
    } else {
        initializeControls();
    }
    if (typeof window.registerEscapeHandler === 'function') {
        window.registerEscapeHandler({
            id: 'admin-unsaved-changes-overlay',
            priority: 450,
            isActive: () => Boolean(pendingRequest),
            close: handleCancel,
        });
    }

    const api = {
        register,
        unregister,
        confirmIfNeeded,
        hasActiveDirtyHandler,
        isDialogOpen: () => Boolean(pendingRequest),
    };

    window.unsavedChangesManager = api;
    window.registerUnsavedChangesHandler = (handler) => register(handler);
    window.unregisterUnsavedChangesHandler = (id) => unregister(id);
})();
