(function () {
    function getContentRangeTotal(response) {
        const contentRange = String(response?.headers?.get('Content-Range') || '');
        const match = contentRange.match(/\/(\d+)$/);
        if (!match) return 0;
        const total = Number(match[1]);
        return Number.isFinite(total) && total > 0 ? total : 0;
    }

    function isTextResponseTruncated(response, maxBytes) {
        const total = getContentRangeTotal(response);
        if (total > maxBytes) return true;
        if (total > 0) return false;
        const contentLength = Number(response?.headers?.get('Content-Length') || 0);
        return response?.status === 206 && contentLength >= maxBytes;
    }

    async function readTextPreviewContent(response, maxBytes) {
        let truncated = isTextResponseTruncated(response, maxBytes);
        const decoder = new TextDecoder('utf-8', { fatal: false });

        if (!response.body || typeof response.body.getReader !== 'function') {
            const blob = await response.blob();
            truncated = truncated || blob.size > maxBytes;
            const slice = truncated ? blob.slice(0, maxBytes) : blob;
            return {
                text: await slice.text(),
                truncated,
            };
        }

        const reader = response.body.getReader();
        let bytesRead = 0;
        let text = '';

        try {
            while (bytesRead < maxBytes) {
                const { done, value } = await reader.read();
                if (done) {
                    text += decoder.decode();
                    return { text, truncated };
                }

                const chunk = value instanceof Uint8Array ? value : new Uint8Array(value);
                const remaining = maxBytes - bytesRead;
                if (chunk.byteLength > remaining) {
                    text += decoder.decode(chunk.slice(0, remaining), { stream: true });
                    truncated = true;
                    await reader.cancel();
                    text += decoder.decode();
                    return { text, truncated };
                }

                text += decoder.decode(chunk, { stream: true });
                bytesRead += chunk.byteLength;
            }

            truncated = true;
            await reader.cancel();
            text += decoder.decode();
            return { text, truncated };
        } finally {
            try {
                reader.releaseLock?.();
            } catch (_) {
                // Older browsers may not expose releaseLock consistently.
            }
        }
    }

    window.TextPreview = { isTextResponseTruncated, readTextPreviewContent };
})();
