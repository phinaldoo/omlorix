(() => {
    const handlers = new Map();
    let sequence = 0;

    const getOrderedHandlers = () => (
        Array.from(handlers.values()).sort((a, b) => {
            if (b.priority !== a.priority) {
                return b.priority - a.priority;
            }
            return a.order - b.order;
        })
    );

    const register = (candidate) => {
        if (!candidate || typeof candidate !== 'object') {
            return null;
        }

        const { isActive, close } = candidate;
        if (typeof isActive !== 'function' || typeof close !== 'function') {
            return null;
        }

        if (typeof candidate.id === 'string' && handlers.has(candidate.id)) {
            handlers.delete(candidate.id);
        }

        const handlerId = typeof candidate.id === 'string'
            ? candidate.id
            : `escape-handler-${++sequence}`;

        const entry = {
            id: handlerId,
            isActive,
            close,
            priority: Number.isFinite(candidate.priority) ? candidate.priority : 0,
            order: ++sequence
        };

        handlers.set(handlerId, entry);
        return { id: handlerId, unregister: () => unregister(handlerId) };
    };

    const unregister = (id) => {
        if (!id) {
            return;
        }
        handlers.delete(id);
    };

    const handleKeydown = (event) => {
        if (event.key !== 'Escape') {
            return;
        }

        const orderedHandlers = getOrderedHandlers();
        for (const handler of orderedHandlers) {
            let active = false;
            try {
                active = Boolean(handler.isActive());
            } catch (error) {
                console.error('[escapeManager] Failed to evaluate handler state', error);
                continue;
            }

            if (!active) {
                continue;
            }

            event.preventDefault();
            if (typeof event.stopImmediatePropagation === 'function') {
                event.stopImmediatePropagation();
            }
            event.stopPropagation();

            try {
                handler.close();
            } catch (error) {
                console.error('[escapeManager] Failed to close handler target', error);
            }
            break;
        }
    };

    document.addEventListener('keydown', handleKeydown, true);

    const api = { register, unregister };
    window.escapeManager = api;
    window.registerEscapeHandler = (handler) => register(handler);
    window.unregisterEscapeHandler = (id) => unregister(id);

    if (Array.isArray(window.__escapeManagerQueue)) {
        window.__escapeManagerQueue.forEach(register);
        window.__escapeManagerQueue = [];
    }
})();
