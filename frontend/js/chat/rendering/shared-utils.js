function escapeHtml(text) {
    if (text === null || text === undefined) {
        return '';
    }

    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function renderMathWithRetry(element, attempt) {
    if (!element) {
        return;
    }
    const safeAttempt = Number.isFinite(attempt) ? attempt : 0;
    const renderer = resolveMathRenderer();

    if (renderer) {
        try {
            renderer(element, {
                delimiters: [
                    { left: '$$', right: '$$', display: true },
                    { left: '\\[', right: '\\]', display: true },
                    { left: '\\begin{equation}', right: '\\end{equation}', display: true },
                    { left: '\\begin{equation*}', right: '\\end{equation*}', display: true },
                    { left: '\\begin{align}', right: '\\end{align}', display: true },
                    { left: '\\begin{align*}', right: '\\end{align*}', display: true },
                    { left: '\\begin{alignat}', right: '\\end{alignat}', display: true },
                    { left: '\\begin{gather}', right: '\\end{gather}', display: true },
                    { left: '\\begin{CD}', right: '\\end{CD}', display: true },
                    { left: '$', right: '$', display: false },
                    { left: '\\(', right: '\\)', display: false }
                ],
                throwOnError: false
            });
        } catch (mathError) {
            console.error('KaTeX render error:', mathError);
        }
        return;
    }

    if (safeAttempt >= MAX_KATEX_RENDER_ATTEMPTS) {
        return;
    }

    setTimeout(() => {
        renderMathWithRetry(element, safeAttempt + 1);
    }, KATEX_RENDER_RETRY_DELAY);
}

