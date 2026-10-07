"""Allowlisted, self-contained tool inputs used by the browser proof."""
from pathlib import Path

PROOF = Path(__file__).resolve().parent
EXAMPLES = {
    'parallelism': {
        'title': 'The shape of a faster response',
        'summary': 'Illustrative model, not a benchmark. Increasing parallel workers shortens processing time until coordination overhead dominates. The table provides the values shown in the chart.',
    },
    'charts': {
        'title': 'How conversations change over time',
        'question': 'Show model usage over time and the busiest hours. Let me explore the numbers.',
        'intro': 'Compare the share of conversations, switch to absolute counts, and inspect any hour in the weekly pattern.',
        'after': 'These are illustrative conversation counts, not Omlorix telemetry. Both charts use embedded data and run locally.',
        'summary': 'Illustrative data: Atlas grows from 34% to 66% of conversations over 12 weeks. The activity heatmap is busiest on Thursday at 15:00. Switch between share and counts or inspect a week, day, and hour using the labeled controls.',
    },
    'treemap': {
        'title': 'Explore the Omlorix codebase',
        'question': 'Map the codebase by size and recent changes. Let me zoom into folders and filter out tests.',
        'intro': 'Every rectangle is a source file. Area shows lines of code; color shows how many of the last 100 commits touched it.',
        'after': 'This is a measured snapshot of Omlorix, including application code and tests. Vendor bundles are excluded.',
        'summary': 'Measured code snapshot at commit 36df43ebc: backend/app, backend/tests, and frontend/js, excluding vendor bundles. Area is physical lines; churn is commits touching each file in the last 100 commits. Use the folder selector, test filter, color mode, or ranked file buttons to explore.',
    },
    'map': {
        'title': 'A world of conversations',
        'question': 'Show an interactive world map. Let me select countries, zoom, and compare activity with latency.',
        'intro': 'Select a highlighted country or choose it from the list. Zoom in to inspect a region, then switch the measure.',
        'after': 'The geography is real; the activity and latency values are explicitly illustrative. The map works without a tile service or network access.',
        'summary': 'Natural Earth country geometry with illustrative activity and latency for eight countries. Germany has 2,400 conversations and 180 ms median latency. Country selection, zoom controls, metric switching, and a data table provide multiple ways to inspect the map.',
    },
}


def example_content(name):
    file = PROOF / ('parallelism.html' if name == 'parallelism' else f'examples/{name}.html')
    content = file.read_text()
    for marker, data_file in [('__WORLD_DATA__', 'countries-110m.json'), ('__CODE_DATA__', 'code-snapshot.json')]:
        if marker in content:
            content = content.replace(marker, (PROOF / 'examples' / data_file).read_text().replace('<', '\\u003c'))
    return content
