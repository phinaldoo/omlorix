(function (root) {
    'use strict';

    const modules = root.__omlorixCanvasWidgetModules ||= {};

    function parseCSV(csvText) {
        const lines = String(csvText || '').split(/\r?\n/).filter(line => line.trim());
        if (!lines.length) return { headers: [], rows: [] };

        const parseRow = (line) => {
            const result = [];
            let current = '';
            let inQuotes = false;
            for (let i = 0; i < line.length; i++) {
                const char = line[i];
                if (char === '"') {
                    if (inQuotes && line[i + 1] === '"') {
                        current += '"';
                        i++;
                    } else {
                        inQuotes = !inQuotes;
                    }
                } else if (char === ',' && !inQuotes) {
                    result.push(current.trim());
                    current = '';
                } else {
                    current += char;
                }
            }
            result.push(current.trim());
            return result;
        };

        const headers = parseRow(lines[0]);
        const rows = lines.slice(1).map(parseRow);
        return { headers, rows };
    }

    function renderCSVInto(target, csvText) {
        if (!target) return;
        target.setAttribute('data-raw-content', String(csvText ?? ''));
        target.innerHTML = '';
        target.classList.remove('canvas-markdown-render');
        target.classList.add('canvas-csv-render');

        const { headers, rows } = parseCSV(csvText);
        if (!headers.length) {
            target.textContent = csvText || '(Empty CSV)';
            return;
        }

        const table = document.createElement('table');
        table.className = 'canvas-csv-table';
        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        headers.forEach(h => {
            const th = document.createElement('th');
            th.textContent = h;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        table.appendChild(thead);

        const tbody = document.createElement('tbody');
        rows.forEach(row => {
            const tr = document.createElement('tr');
            headers.forEach((_, i) => {
                const td = document.createElement('td');
                td.textContent = row[i] ?? '';
                tr.appendChild(td);
            });
            tbody.appendChild(tr);
        });
        table.appendChild(tbody);

        const wrapper = document.createElement('div');
        wrapper.className = 'canvas-csv-table-wrapper';
        wrapper.appendChild(table);
        target.appendChild(wrapper);
    }

    modules.csv = Object.freeze({ parseCSV, renderCSVInto });
})(globalThis);
