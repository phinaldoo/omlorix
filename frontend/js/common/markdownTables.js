(function () {
    'use strict';

    function isPotentialTableRow(line) {
        const value = String(line || '').trim();
        if (!value.startsWith('|')) {
            return false;
        }
        const pipes = value.match(/\|/g);
        return Array.isArray(pipes) && pipes.length >= 2;
    }

    function isTableSeparatorRow(line) {
        const value = String(line || '').trim();
        if (!value || !value.includes('|')) {
            return false;
        }
        if (!/^[:|\-\t ]+$/.test(value)) {
            return false;
        }
        const dashesOnly = value.replace(/[|:\t ]/g, '');
        return dashesOnly.length >= 3 && /^-+$/.test(dashesOnly);
    }

    function findNextNonEmptyLineIndex(lines, startIndex) {
        for (let index = startIndex; index < lines.length; index += 1) {
            if (String(lines[index] || '').trim() !== '') {
                return index;
            }
        }
        return -1;
    }

    function preprocessTablesInLists(text) {
        if (!text) {
            return text;
        }

        return String(text).replace(
            /^([ \t]*(?:[-*+]|\d+\.)[ \t]+.+)\n(\|[^\n]+\|[ \t]*\n\|[-:| \t]+\|)/gm,
            '$1\n\n$2'
        );
    }

    function normalizeBrokenTableBlocks(text) {
        if (!text) {
            return text;
        }

        const lines = String(text).split(/\r?\n/);
        const normalizedLines = [];
        let index = 0;

        while (index < lines.length) {
            const headerLine = lines[index];
            if (!isPotentialTableRow(headerLine)) {
                normalizedLines.push(headerLine);
                index += 1;
                continue;
            }

            const separatorIndex = findNextNonEmptyLineIndex(lines, index + 1);
            if (separatorIndex === -1 || !isTableSeparatorRow(lines[separatorIndex])) {
                normalizedLines.push(headerLine);
                index += 1;
                continue;
            }

            normalizedLines.push(headerLine);
            normalizedLines.push(lines[separatorIndex]);
            index = separatorIndex + 1;

            while (index < lines.length) {
                const currentLine = lines[index];

                if (isPotentialTableRow(currentLine)) {
                    normalizedLines.push(currentLine);
                    index += 1;
                    continue;
                }

                if (String(currentLine || '').trim() === '') {
                    const nextContentIndex = findNextNonEmptyLineIndex(lines, index + 1);
                    if (nextContentIndex !== -1 && isPotentialTableRow(lines[nextContentIndex])) {
                        index = nextContentIndex;
                        continue;
                    }
                }

                break;
            }
        }

        return normalizedLines.join('\n');
    }

    function normalizeMarkdownForRender(text) {
        const withListTableSpacing = preprocessTablesInLists(text);
        return normalizeBrokenTableBlocks(withListTableSpacing);
    }

    window.ChatMarkdownUtils = window.ChatMarkdownUtils || {};
    window.ChatMarkdownUtils.normalizeMarkdownForRender = normalizeMarkdownForRender;
})();
