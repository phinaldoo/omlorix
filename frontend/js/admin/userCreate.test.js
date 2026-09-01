const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const userCreateSource = fs.readFileSync(path.join(__dirname, 'userCreate.js'), 'utf8');
const adminHtml = fs.readFileSync(path.join(__dirname, '..', '..', 'admin.html'), 'utf8');

/**
 * Return a named function body without letting assertions match unrelated code.
 */
function getFunctionSource(functionName, nextFunctionName) {
    const start = userCreateSource.indexOf(`    async function ${functionName}()`);
    const end = userCreateSource.indexOf(`    function ${nextFunctionName}(`, start);

    assert.notEqual(start, -1, `Missing ${functionName}`);
    assert.notEqual(end, -1, `Missing boundary after ${functionName}`);
    return userCreateSource.slice(start, end);
}

test('bulk import keeps one-time passwords visible after a successful upload', () => {
    const submitSource = getFunctionSource('submitBulkUpload', 'renderBulkResults');

    assert.match(submitSource, /renderBulkResults\(result\);/);
    assert.match(submitSource, /focusBulkResults\(\);/);
    assert.doesNotMatch(submitSource, /activateAdminPage/);
});

test('bulk import result is focusable and announced as a status', () => {
    const resultStart = adminHtml.indexOf('id="userCreateBulkResults"');
    assert.notEqual(resultStart, -1, 'Missing bulk import result');

    const resultEnd = adminHtml.indexOf('></div>', resultStart);
    const resultMarkup = adminHtml.slice(resultStart, resultEnd);

    assert.match(resultMarkup, /role="status"/);
    assert.match(resultMarkup, /aria-live="polite"/);
    assert.match(resultMarkup, /tabindex="-1"/);
    assert.match(resultMarkup, /hidden/);
    assert.match(userCreateSource, /dom\.bulk\.results\.focus\(\{ preventScroll: true \}\);/);
    assert.match(userCreateSource, /window\.scrollAdminPaginatedListToStart\(dom\.bulk\.results\);/);
});
