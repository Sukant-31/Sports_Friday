import test, { before, after } from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

let server, MatchTile, statusText;
before(async () => {
  server = await createServer({
    server: { middlewareMode: true, hmr: { port: 0 } }, appType: 'custom',
  });
  ({ default: MatchTile } = await server.ssrLoadModule('/src/components/MatchTile.jsx'));
  ({ statusText } = await server.ssrLoadModule('/src/pages/MatchDetail.jsx'));
});
after(async () => { await server?.close(); });

function tileBadge(match) {
  const html = renderToStaticMarkup(React.createElement(MemoryRouter, null,
    React.createElement(MatchTile, { match: {
      id: 'fixture', home_team: 'Home', away_team: 'Away', home_score: 0,
      away_score: 0, events: [], ...match,
    } })));
  return html.match(/<span class="badge(?: live)?">(.*?)<\/span><\/div>/)?.[1]
    .replace(/<[^>]*>/g, '').replace(/&#x27;/g, "'").trim();
}

const statuses = [
  ['INT', 'interrupted', 'Interrupted'],
  ['TBD', 'time_to_be_defined', 'Time to be defined'],
  ['AWD', 'awarded', 'Awarded'],
  ['WO', 'walkover', 'Walkover'],
  ['SUSP', 'suspended', 'Suspended'],
  ['PST', 'postponed', 'Postponed'],
  ['CANC', 'cancelled', 'Cancelled'],
  ['ABD', 'abandoned', 'Abandoned'],
  ['unknown', 'unknown', 'Unknown'],
  ['missing', undefined, 'Unknown'],
  ['null', null, 'Unknown'],
  ['unrecognized', 'future_status', 'Unknown'],
];

for (const [code, status, label] of statuses) {
  for (const starts_at of [null, '2026-10-05T12:00:00Z']) {
    test(`${code} displays ${label} in tile and detail ${starts_at ? 'with' : 'without'} kickoff`, () => {
      const match = { status, starts_at, minute: 45 };
      assert.equal(tileBadge(match), label);
      assert.equal(statusText(match), label);
    });
  }
}

for (const minute of [undefined, null, 0, 45]) {
  test(`live labels preserve minute ${minute}`, () => {
    const match = { status: 'live', minute };
    assert.equal(tileBadge(match), `LIVE${minute != null ? ` ${minute}'` : ''}`);
    assert.equal(statusText(match), `LIVE${minute != null ? ` · ${minute}'` : ''}`);
  });
}

test('finished labels remain FT in tiles and Full time in details', () => {
  const match = { status: 'finished', starts_at: '2026-10-05T12:00:00Z', minute: 90 };
  assert.equal(tileBadge(match), 'FT');
  assert.equal(statusText(match), 'Full time');
});

test('scheduled labels preserve kickoff formatting and fallback', () => {
  const starts_at = '2026-10-05T12:00:00Z';
  assert.equal(tileBadge({ status: 'scheduled', starts_at }),
    `KO ${new Date(starts_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`);
  assert.equal(statusText({ status: 'scheduled', starts_at }),
    `Kick-off ${new Date(starts_at).toLocaleString([], {
      weekday: 'short', hour: '2-digit', minute: '2-digit',
    })}`);
  assert.equal(tileBadge({ status: 'scheduled' }), 'Scheduled');
  assert.equal(statusText({ status: 'scheduled' }), 'Scheduled');
});
