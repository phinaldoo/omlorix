(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function createStatusModule({ previewStatus }) {
        function escapeHtml(str) {
            const div = document.createElement('div');
            div.textContent = str;
            return div.innerHTML;
        }
    
    
        function updateStatusClass(status, explicitKind = '') {
            if (!previewStatus) return;
            previewStatus.classList.remove('generating', 'complete', 'unsaved', 'error');
            const normalizedKind = String(explicitKind || '').trim().toLowerCase();
            if (normalizedKind === 'generating') {
                previewStatus.classList.add('generating');
                return;
            }
            if (normalizedKind === 'saved' || normalizedKind === 'complete') {
                previewStatus.classList.add('complete');
                return;
            }
            if (normalizedKind === 'unsaved') {
                previewStatus.classList.add('unsaved');
                return;
            }
            if (normalizedKind === 'failed' || normalizedKind === 'error') {
                previewStatus.classList.add('error');
                return;
            }
            const lowerStatus = String(status || '').toLowerCase();
            if (lowerStatus.includes('unsaved')) {
                previewStatus.classList.add('unsaved');
            } else if (lowerStatus.includes('failed') || lowerStatus.includes('error')) {
                previewStatus.classList.add('error');
            } else if (lowerStatus.includes('streaming') || lowerStatus.includes('writing') || lowerStatus.includes('loading') || lowerStatus.includes('saving')) {
                previewStatus.classList.add('generating');
            } else if (lowerStatus.includes('saved') || lowerStatus.includes('complete')) {
                previewStatus.classList.add('complete');
            }
        }
    

        return Object.freeze({ escapeHtml, updateStatusClass });
    }

    modules.status = Object.freeze({ create: createStatusModule });
})(globalThis);
