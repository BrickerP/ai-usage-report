import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  degradedSourceNotices,
  modelSeriesPoint,
  selectModelSeries,
  TOOLS,
} from '../src/lib/usage.ts'

const rows = [
  {
    date: '2026-07-29',
    oneapi_models: [
      { model: 'alpha', tokens: 50, cost: 5 },
      { model: 'beta', tokens: 40, cost: 4 },
      { model: 'gamma', tokens: 30, cost: 3 },
      { model: 'delta', tokens: 20, cost: 2 },
      { model: 'epsilon', tokens: 10, cost: 1 },
      { model: 'zeta', tokens: 5, cost: 0.5 },
      { model: 'Legacy unknown', tokens: 25, cost: 2.5 },
    ],
  },
  {
    date: '2026-07-30',
    oneapi_models: [
      { model: 'alpha', tokens: 50, cost: 5 },
      { model: 'beta', tokens: 40, cost: 4 },
      { model: 'gamma', tokens: 30, cost: 3 },
      { model: 'delta', tokens: 20, cost: 2 },
      { model: 'epsilon', tokens: 10, cost: 1 },
      { model: 'zeta', tokens: 5, cost: 0.5 },
      { model: 'Legacy unknown', tokens: 25, cost: 2.5 },
    ],
  },
]

const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const stylesheetSource = readFileSync(
  new URL('../src/index.css', import.meta.url),
  'utf8',
)

test('interaction controls expose explicit state semantics', () => {
  assert.match(appSource, /isDisabled=\{range\[0\] <= 0\}/)
  assert.match(appSource, /isDisabled=\{range\[1\] >= daily\.length - 1\}/)
  assert.match(appSource, /aria-expanded=\{isModelListOpen\}/)
  assert.match(appSource, /aria-controls="model-details-panel"/)
  assert.match(appSource, /aria-label="Back to all tools"/)
  assert.match(stylesheetSource, /\.chart-breadcrumb button:hover/)
  assert.match(stylesheetSource, /\.model-focus-note button:hover/)
})

test('fresh source status adds no page-level notice', () => {
  assert.deepEqual(
    degradedSourceNotices({
      cursor: { status: 'fresh' },
      oneapi: { status: 'fresh' },
    }),
    [],
  )
})

test('a stale One API notice says retained data is still shown', () => {
  const notices = degradedSourceNotices({
    oneapi: {
      status: 'stale',
      last_success_at: '2026-07-29T15:03:00+08:00',
      attempted_at: '2026-07-30T15:03:00+08:00',
      window_end: '2026-07-29',
      lag_days: 1,
      error: 'SECRET_SESSION_COOKIE at /Users/example/private/state.json',
    },
  })

  assert.equal(notices.length, 1)
  assert.equal(notices[0].status, 'stale')
  assert.match(notices[0].message, /One API is stale/)
  assert.match(notices[0].message, /previously saved data is retained and still shown/)
  assert.match(notices[0].message, /1 day behind/)
  assert.match(notices[0].message, /last successful refresh/)
  assert.doesNotMatch(notices[0].message, /SECRET_SESSION_COOKIE/)
  assert.doesNotMatch(notices[0].message, /\/Users\/example/)
})

test('a failed source notice identifies the failed refresh without diagnostics', () => {
  const notices = degradedSourceNotices({
    cursor: {
      status: 'failed',
      attempted_at: '2026-07-30T15:03:00+08:00',
      error: 'PRIVATE_TOKEN request timed out at /tmp/cursor-debug.log',
    },
  })

  assert.deepEqual(notices, [
    {
      id: 'cursor',
      status: 'failed',
      message: 'Cursor refresh failed. last attempted 2026-07-30.',
    },
  ])
  assert.doesNotMatch(notices[0].message, /PRIVATE_TOKEN/)
  assert.doesNotMatch(notices[0].message, /\/tmp\/cursor-debug\.log/)
})

test('source freshness details are bounded before rendering', () => {
  const notices = degradedSourceNotices({
    cursor: {
      status: 'stale',
      lag_days: Number.MAX_VALUE,
      window_end: `2026-07-29T${'/private/'.repeat(20)}`,
      last_success_at: '/Users/example/private/state.json',
      attempted_at: '2026-07-30T15:03:00+08:00',
    },
  })

  assert.equal(
    notices[0].message,
    'Cursor is stale. 999+ days behind; last attempted 2026-07-30.',
  )
  assert.doesNotMatch(notices[0].message, /private/)
})

test('an unknown internal source id is not rendered', () => {
  const notices = degradedSourceNotices({
    '/Users/example/private/collector': {
      status: 'failed',
      error: 'PRIVATE_TOKEN',
    },
  })

  assert.equal(notices[0].message, 'Data source refresh failed.')
  assert.doesNotMatch(notices[0].message, /Users|PRIVATE_TOKEN/)
})

test('model drilldown keeps the top four, legacy, and a conserving other group', () => {
  const result = selectModelSeries(rows, 'oneapi')

  assert.equal(result.modelCount, 6)
  assert.equal(result.hasLegacy, true)
  assert.deepEqual(
    result.series.map((series) => series.label),
    ['alpha', 'beta', 'gamma', 'delta', 'Legacy unknown', 'Other · 2'],
  )
  assert.deepEqual(result.series.at(-1).models, ['epsilon', 'zeta'])
  assert.equal(
    result.series.reduce((sum, series) => sum + series.tokens, 0),
    360,
  )
  assert.equal(
    result.fullModels.reduce((sum, model) => sum + model.tokens, 0),
    360,
  )
  assert.equal(
    result.series.reduce((sum, series) => sum + series.cost, 0),
    36,
  )
})

test('a single remaining model keeps its real name instead of becoming other one', () => {
  const result = selectModelSeries(
    [
      {
        date: '2026-07-30',
        claude_models: [
          { model: 'a', tokens: 50, cost: 5 },
          { model: 'b', tokens: 40, cost: 4 },
          { model: 'c', tokens: 30, cost: 3 },
          { model: 'd', tokens: 20, cost: 2 },
          { model: 'e', tokens: 10, cost: 1 },
        ],
      },
    ],
    'claude',
  )

  assert.equal(result.modelCount, 5)
  assert.equal(result.hasLegacy, false)
  assert.deepEqual(
    result.series.map((series) => series.label),
    ['a', 'b', 'c', 'd', 'e'],
  )
})

test('pinning a small model promotes it without losing any totals', () => {
  const result = selectModelSeries(rows, 'oneapi', 'zeta')

  assert.deepEqual(
    result.series.map((series) => series.label),
    ['alpha', 'beta', 'gamma', 'zeta', 'Legacy unknown', 'Other · 2'],
  )
  assert.deepEqual(result.series.at(-1).models, ['delta', 'epsilon'])
  assert.equal(
    result.series.reduce((sum, series) => sum + series.tokens, 0),
    360,
  )
})

test('an aggregated series keeps exact daily tokens and cost', () => {
  const result = selectModelSeries(rows, 'oneapi')
  const other = result.series.at(-1)

  assert.deepEqual(modelSeriesPoint(rows[0], 'oneapi', other), {
    tokens: 15,
    cost: 1.5,
  })
  assert.deepEqual(modelSeriesPoint(rows[0], 'oneapi', result.series[0]), {
    tokens: 50,
    cost: 5,
  })
})

test('normalized model names keep their daily chart values', () => {
  const normalizedRows = [
    {
      date: '2026-07-30',
      oneapi_models: [{ model: ' zeta ', tokens: 5, cost: 0.5 }],
    },
  ]
  const result = selectModelSeries(normalizedRows, 'oneapi')

  assert.equal(result.series[0].label, 'zeta')
  assert.deepEqual(
    modelSeriesPoint(normalizedRows[0], 'oneapi', result.series[0]),
    { tokens: 5, cost: 0.5 },
  )
})

test('published usage conserves every tool across common ranges', () => {
  const payload = JSON.parse(
    readFileSync(new URL('../public/usage.json', import.meta.url), 'utf8'),
  )
  const ranges = [
    payload.daily.slice(-7),
    payload.daily.slice(-30),
    payload.daily,
  ]

  for (const rangeRows of ranges) {
    for (const tool of TOOLS) {
      const selection = selectModelSeries(rangeRows, tool.id)
      for (const row of rangeRows) {
        const point = selection.series.reduce(
          (total, series) => {
            const value = modelSeriesPoint(row, tool.id, series)
            total.tokens += value.tokens
            total.cost += value.cost
            return total
          },
          { tokens: 0, cost: 0 },
        )
        assert.equal(point.tokens, Number(row[tool.tokenKey]) || 0)
        assert.ok(
          Math.abs(point.cost - (Number(row[tool.costKey]) || 0)) < 1e-8,
          `${tool.id} cost mismatch on ${row.date}`,
        )
      }
    }
  }
})
