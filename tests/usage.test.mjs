import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import {
  degradedSourceNotices, describeRange, indexForPreset, modelSeriesMembers,
  modelSeriesPoint, percentageTenths, selectModelSeries, summarizeRange, TOOLS,
} from '../src/lib/usage.ts'
import {
  buildReportViewSearch, explorationBackAction, indexRangeForDates,
  nextSeriesIndex, parseReportView,
} from '../src/lib/interaction.ts'

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


test('presets select natural calendar windows and disclose missing records', () => {
  const dates = [
    { date: '2026-04-30' },
    { date: '2026-05-03' },
    { date: '2026-07-25' },
    { date: '2026-07-29' },
    { date: '2026-07-31' },
  ]

  assert.deepEqual(indexForPreset(dates, '7'), [2, 4])
  assert.deepEqual(indexForPreset(dates, '30'), [2, 4])
  assert.deepEqual(indexForPreset(dates, '90'), [1, 4])
  assert.deepEqual(describeRange(dates.slice(2), '7'), {
    start: '2026-07-25',
    end: '2026-07-31',
    calendarDays: 7,
    recordedDays: 3,
  })
})

test('Codex reasoning is a labelled subset of output and never double-counts', () => {
  const result = summarizeRange([
    {
      date: '2026-07-31',
      codex_tokens: 130,
      codex_input: 10,
      codex_cache_read: 20,
      codex_output: 100,
      codex_reasoning: 40,
    },
  ])
  const codex = result.byTool.find((tool) => tool.id === 'codex')

  assert.deepEqual(
    codex.parts.map((part) => [part.label, part.value]),
    [
      ['Input', 10],
      ['Cache read', 20],
      ['Output (non-reasoning)', 60],
      ['Reasoning (of output)', 40],
    ],
  )
  assert.equal(
    codex.parts.reduce((sum, part) => sum + part.value, 0),
    codex.tokens,
  )
})

test('display percentages use stable largest remainders and total 100.0%', () => {
  const thirds = percentageTenths([1, 1, 1])

  assert.deepEqual(thirds, [334, 333, 333])
  assert.equal(thirds.reduce((sum, share) => sum + share, 0), 1000)
  assert.deepEqual(percentageTenths([0, 0, Number.NaN]), [0, 0, 0])
})

test('series keyboard navigation wraps and supports boundary keys', () => {
  assert.equal(nextSeriesIndex('ArrowRight', 0, 4), 1)
  assert.equal(nextSeriesIndex('ArrowRight', 3, 4), 0)
  assert.equal(nextSeriesIndex('ArrowLeft', 0, 4), 3)
  assert.equal(nextSeriesIndex('ArrowLeft', 2, 4), 1)
  assert.equal(nextSeriesIndex('Home', 2, 4), 0)
  assert.equal(nextSeriesIndex('End', 1, 4), 3)
  assert.equal(nextSeriesIndex('Enter', 1, 4), null)
  assert.equal(nextSeriesIndex('ArrowRight', -1, 4), null)
  assert.equal(nextSeriesIndex('ArrowRight', 0, 0), null)
})

test('report view state round-trips tool, model, preset, and selected day', () => {
  const search = buildReportViewSearch('?keep=yes', {
    tool: 'oneapi',
    model: 'deepseek/v4 flash',
    preset: '7',
    from: null,
    to: null,
    day: '2026-07-29',
  })

  assert.equal(
    search,
    '?keep=yes&tool=oneapi&model=deepseek%2Fv4+flash&range=7&day=2026-07-29',
  )
  assert.deepEqual(parseReportView(search), {
    tool: 'oneapi',
    model: 'deepseek/v4 flash',
    preset: '7',
    from: null,
    to: null,
    day: '2026-07-29',
  })
})

test('custom dates override presets and clamp to the available timeline', () => {
  const view = parseReportView(
    '?range=90&from=2026-07-02&to=2026-07-20&tool=claude',
  )
  assert.deepEqual(view, {
    tool: 'claude',
    model: null,
    preset: null,
    from: '2026-07-02',
    to: '2026-07-20',
    day: null,
  })
  assert.deepEqual(
    indexRangeForDates(
      ['2026-07-10', '2026-07-11', '2026-07-12'],
      '2026-01-01',
      '2026-12-31',
    ),
    [0, 2],
  )
})

test('invalid report view values and malformed days safely collapse to defaults', () => {
  assert.deepEqual(
    parseReportView(
      '?tool=private&model=secret&range=365&from=no&to=also-no&day=July-30',
    ),
    {
      tool: null,
      model: null,
      preset: null,
      from: null,
      to: null,
      day: null,
    },
  )
  assert.equal(
    buildReportViewSearch('?tool=oneapi&model=old&range=7&day=2026-07-01', {
      tool: null,
      model: null,
      preset: 'all',
      from: null,
      to: null,
      day: 'not-a-date',
    }),
    '',
  )
})

test('non-default 30-day range remains durable in the URL', () => {
  assert.equal(
    buildReportViewSearch('', {
      tool: null,
      model: null,
      preset: '30',
      from: null,
      to: null,
      day: null,
    }),
    '?range=30',
  )
})

test('Escape unwinds chart exploration one layer at a time', () => {
  assert.equal(
    explorationBackAction({
      detailsOpen: true,
      modelFocused: true,
      toolSelected: true,
    }),
    'close-details',
  )
  assert.equal(
    explorationBackAction({
      detailsOpen: false,
      modelFocused: true,
      toolSelected: true,
    }),
    'clear-model',
  )
  assert.equal(
    explorationBackAction({
      detailsOpen: false,
      modelFocused: false,
      toolSelected: true,
    }),
    'clear-tool',
  )
  assert.equal(
    explorationBackAction({
      detailsOpen: false,
      modelFocused: false,
      toolSelected: false,
    }),
    null,
  )
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
      label: 'Cursor',
      status: 'failed',
      retained: false,
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

test('compact source notices preserve every known source label', () => {
  const notices = degradedSourceNotices({
    codex: { status: 'stale' },
    claudecode: { status: 'failed' },
    cursor: { status: 'stale' },
    oneapi: { status: 'failed' },
  })

  assert.deepEqual(
    notices.map(({ label, retained }) => ({ label, retained })),
    [
      { label: 'Codex', retained: false },
      { label: 'Claude Code', retained: false },
      { label: 'Cursor', retained: false },
      { label: 'One API', retained: true },
    ],
  )
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

test('a pinned model with no usage in range remains a zero-value focused series', () => {
  const recentRows = [
    {
      date: '2026-07-30',
      oneapi_models: [{ model: 'grok', tokens: 12, cost: 0.5 }],
    },
  ]
  const result = selectModelSeries(
    recentRows,
    'oneapi',
    'deepseek-v4-flash',
  )
  const focused = result.series.find(
    (series) => series.label === 'deepseek-v4-flash',
  )

  assert.ok(focused)
  assert.equal(focused.tokens, 0)
  assert.equal(focused.cost, 0)
  assert.deepEqual(modelSeriesPoint(recentRows[0], 'oneapi', focused), {
    tokens: 0,
    cost: 0,
  })
  assert.equal(
    result.series.reduce((sum, series) => sum + series.tokens, 0),
    12,
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
  assert.deepEqual(modelSeriesMembers(rows[0], 'oneapi', other), [
    { model: 'epsilon', tokens: 10, cost: 1 },
    { model: 'zeta', tokens: 5, cost: 0.5 },
  ])
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

test('every tool declares what its recorded numbers cover', () => {
  for (const tool of TOOLS) {
    assert.ok(
      typeof tool.coverage === 'string' && tool.coverage.trim().length > 0,
      `${tool.id} is missing a coverage statement`,
    )
  }

  const codex = TOOLS.find((tool) => tool.id === 'codex')
  assert.ok(codex)
  assert.match(codex.coverage, /ephemeral/)
  assert.match(codex.coverage, /One API/)

  const summary = summarizeRange([
    { date: '2026-09-06', codex_tokens: 5, codex_cost: 1 },
  ])
  for (const tool of summary.byTool) {
    const declared = TOOLS.find((candidate) => candidate.id === tool.id)
    assert.equal(tool.coverage, declared.coverage)
  }
})
