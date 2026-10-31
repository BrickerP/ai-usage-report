import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DEFAULT_REPORT_VIEW,
  buildReport,
  buildReportSearch,
  parseReportView,
  shiftReportView,
} from '../src/lib/report.ts'
import { analyseEditorialPattern } from '../src/lib/pattern.ts'

function dayAt(offset) {
  return new Date(Date.UTC(2026, 0, 1 + offset)).toISOString().slice(0, 10)
}

function dailyRow(date, overrides = {}) {
  const row = {
    date,
    codex_tokens: 0,
    claude_tokens: 0,
    cursor_tokens: 0,
    oneapi_tokens: 0,
    codex_cost: 0,
    claude_cost: 0,
    cursor_cost: 0,
    oneapi_cost: 0,
    total_tokens: 0,
    total_cost: 0,
    codex_input: 0,
    codex_cache_read: 0,
    codex_output: 0,
    codex_reasoning: 0,
    claude_input: 0,
    claude_cache_create: 0,
    claude_cache_read: 0,
    claude_output: 0,
    cursor_input: 0,
    cursor_cache_write: 0,
    cursor_cache_read: 0,
    cursor_output: 0,
    oneapi_input: 0,
    oneapi_output: 0,
    oneapi_cache_read: 0,
    oneapi_cache_write: 0,
    codex_models: [],
    claude_models: [],
    cursor_models: [],
    oneapi_models: [],
    ...overrides,
  }
  row.total_tokens =
    row.codex_tokens + row.claude_tokens + row.cursor_tokens + row.oneapi_tokens
  row.total_cost =
    row.codex_cost + row.claude_cost + row.cursor_cost + row.oneapi_cost
  return row
}

function usagePayload(daily) {
  return {
    generated_at: '2026-10-31T15:09:47+08:00',
    timezone: 'Asia/Shanghai',
    machines: ['fixture'],
    source_status: {
      codex: { status: 'fresh', window_end: daily.at(-1)?.date ?? null },
      oneapi: {
        status: 'stale',
        window_end: daily.at(-1)?.date ?? null,
        lag_days: 1,
      },
    },
    daily,
  }
}

test('a 90-day calendar keeps an unpublished day null and reports 89 recorded rows', () => {
  const missingDate = dayAt(40)
  const rows = Array.from({ length: 90 }, (_, index) =>
    dailyRow(dayAt(index), { cursor_tokens: index + 1 }),
  ).filter((row) => row.date !== missingDate)

  const report = buildReport(usagePayload(rows), {
    ...DEFAULT_REPORT_VIEW,
    preset: '90',
  })

  assert.equal(report.range.calendarDays, 90)
  assert.equal(report.range.recordedDays, 89)
  assert.equal(report.daily.length, 90)
  assert.deepEqual(
    report.daily.find((day) => day.date === missingDate),
    {
      date: missingDate,
      published: false,
      tokens: null,
      cost: null,
      values: [null, null, null, null],
      breakdown: report.series.map((series) => ({
        id: series.id,
        label: series.label,
        tokens: null,
        cost: null,
      })),
      modelCoverage: 'not-applicable',
    },
  )
})

test('focused model distinguishes unavailable model coverage from a published zero', () => {
  const first = dailyRow('2026-01-01', {
    oneapi_tokens: 10,
    oneapi_cost: 1,
    oneapi_models: [{ model: 'focus', tokens: 10, cost: 1 }],
  })
  const unavailable = dailyRow('2026-01-02', {
    oneapi_tokens: 20,
    oneapi_cost: 2,
  })
  delete unavailable.oneapi_models
  const zero = dailyRow('2026-01-03', {
    oneapi_models: [],
  })

  const report = buildReport(usagePayload([first, unavailable, zero]), {
    ...DEFAULT_REPORT_VIEW,
    tool: 'oneapi',
    model: 'focus',
    day: '2026-01-02',
  })

  assert.deepEqual(
    report.daily.map((day) => [day.date, day.tokens, day.cost, day.modelCoverage]),
    [
      ['2026-01-01', 10, 1, 'complete'],
      ['2026-01-02', null, null, 'unavailable'],
      ['2026-01-03', 0, 0, 'complete'],
    ],
  )
  assert.equal(report.pattern.signature.name, 'History still forming')
  assert.equal(report.pattern.signature.evidence, '1 of 3 dates unavailable')
  assert.equal(report.view.day, '2026-01-02')
  assert.equal(report.selectedDay.tokens, null)
  assert.equal(report.scope.tokens, 10)
  assert.equal(report.scope.cost, 1)
})

test('all and tool scopes retain an inspected day without serializing it', () => {
  const rows = [
    dailyRow('2026-01-01', { codex_tokens: 10 }),
    dailyRow('2026-01-02', { codex_tokens: 20 }),
  ]
  const all = buildReport(usagePayload(rows), {
    ...DEFAULT_REPORT_VIEW,
    day: '2026-01-01',
  })
  const tool = buildReport(usagePayload(rows), {
    ...DEFAULT_REPORT_VIEW,
    tool: 'codex',
    day: '2026-01-01',
  })

  assert.equal(all.selectedDay.date, '2026-01-01')
  assert.equal(tool.selectedDay.date, '2026-01-01')
  assert.equal(all.view.day, '2026-01-01')
  assert.equal(tool.view.day, '2026-01-01')
  assert.equal(buildReportSearch('', tool.view).includes('day='), false)
})

test('invalid custom dates and an empty ledger collapse safely', () => {
  const rows = [dailyRow('2026-01-01'), dailyRow('2026-01-03')]
  const invalid = buildReport(usagePayload(rows), {
    ...DEFAULT_REPORT_VIEW,
    preset: 'custom',
    from: 'not-a-date',
    to: '2026-01-03',
  })
  const empty = buildReport(usagePayload([]), DEFAULT_REPORT_VIEW)

  assert.equal(invalid.range.preset, 'all')
  assert.equal(invalid.range.start, '2026-01-01')
  assert.equal(invalid.range.end, '2026-01-03')
  assert.equal(empty.range.start, null)
  assert.equal(empty.range.end, null)
  assert.equal(empty.daily.length, 0)
  assert.equal(empty.scope.tokens, null)
  assert.equal(empty.pattern, null)
})

test('custom ranges preserve requested calendar bounds even with no published rows', () => {
  const payload = usagePayload([
    dailyRow('2026-01-09', { codex_tokens: 9 }),
    dailyRow('2026-01-12', { codex_tokens: 12 }),
  ])
  const gapOnly = buildReport(payload, {
    ...DEFAULT_REPORT_VIEW,
    preset: 'custom',
    from: '2026-01-10',
    to: '2026-01-11',
  })
  const leadingGap = buildReport(payload, {
    ...DEFAULT_REPORT_VIEW,
    preset: 'custom',
    from: '2026-01-10',
    to: '2026-01-12',
  })

  assert.equal(gapOnly.range.preset, 'custom')
  assert.equal(gapOnly.range.start, '2026-01-10')
  assert.equal(gapOnly.range.end, '2026-01-11')
  assert.equal(gapOnly.range.recordedDays, 0)
  assert.equal(gapOnly.scope.availableDays, 0)
  assert.equal(gapOnly.scope.tokens, null)
  assert.equal(gapOnly.scope.cost, null)
  assert.equal(gapOnly.scope.cache, null)
  assert.equal(gapOnly.scope.cacheShare, null)
  assert.ok(gapOnly.tools.every((tool) => tool.availableDays === 0))
  assert.ok(gapOnly.tools.every((tool) => tool.tokens === null))
  assert.ok(gapOnly.tools.every((tool) => tool.cost === null))
  assert.deepEqual(
    gapOnly.daily.map((day) => [day.date, day.published, day.tokens]),
    [
      ['2026-01-10', false, null],
      ['2026-01-11', false, null],
    ],
  )
  assert.equal(leadingGap.range.start, '2026-01-10')
  assert.equal(leadingGap.range.end, '2026-01-12')
  assert.equal(leadingGap.range.recordedDays, 1)
  assert.equal(leadingGap.scope.availableDays, 1)
  assert.equal(leadingGap.scope.tokens, 12)
  assert.equal(leadingGap.scope.cost, 0)
  assert.deepEqual(
    leadingGap.daily.map((day) => day.date),
    ['2026-01-10', '2026-01-11', '2026-01-12'],
  )
})

test('model groups retain top four, Other, legacy, and unclassified tool tokens', () => {
  const models = [
    ['a', 40],
    ['b', 20],
    ['c', 10],
    ['d', 5],
    ['e', 3],
    ['f', 2],
    ['Legacy unknown', 5],
  ].map(([model, tokens]) => ({ model, tokens, cost: Number(tokens) / 10 }))
  const row = dailyRow('2026-01-01', {
    oneapi_tokens: 100,
    oneapi_cost: 10,
    oneapi_models: models,
  })
  const report = buildReport(usagePayload([row]), {
    ...DEFAULT_REPORT_VIEW,
    tool: 'oneapi',
  })

  assert.equal(report.models.top.length, 4)
  assert.equal(report.models.full.length, 7)
  assert.equal(report.models.legacy.name, 'Legacy unknown')
  assert.deepEqual(report.models.other.names, ['e', 'f'])
  assert.equal(report.series.at(-1).id, 'model:unclassified')
  assert.equal(
    report.daily[0].values.reduce((sum, value) => sum + (value ?? 0), 0),
    100,
  )
  assert.equal(
    report.daily[0].breakdown.reduce(
      (sum, item) => sum + (item.tokens ?? 0),
      0,
    ),
    100,
  )
})

test('token parts keep Codex reasoning separate from non-reasoning output', () => {
  const row = dailyRow('2026-01-01', {
    codex_tokens: 38,
    codex_cost: 0.00045678,
    codex_input: 10,
    codex_cache_read: 20,
    codex_output: 8,
    codex_reasoning: 3,
  })
  const report = buildReport(usagePayload([row]), {
    ...DEFAULT_REPORT_VIEW,
    tool: 'codex',
  })
  const parts = Object.fromEntries(
    report.parts.range.map((part) => [part.id, part.tokens]),
  )

  assert.equal(parts.input, 10)
  assert.equal(parts['cache-read'], 20)
  assert.equal(parts.output, 5)
  assert.equal(parts.reasoning, 3)
  assert.equal(parts.unclassified, 0)
  assert.equal(report.scope.cost, 0.00045678)
})

test('sub-nanodollar model cost residue does not create a false unclassified row', () => {
  const row = dailyRow('2026-01-01', {
    oneapi_tokens: 85,
    oneapi_cost: 8.500000000000002,
    oneapi_models: [
      { model: 'a', tokens: 40, cost: 4 },
      { model: 'b', tokens: 30, cost: 3 },
      { model: 'c', tokens: 15, cost: 1.5 },
    ],
  })
  const report = buildReport(usagePayload([row]), {
    ...DEFAULT_REPORT_VIEW,
    tool: 'oneapi',
  })

  assert.equal(
    report.daily[0].breakdown.some((item) => item.id === 'model:unclassified'),
    false,
  )
  assert.equal(report.scope.cost, 8.500000000000002)
})

test('Signature v1 classification and forming evidence match the legacy algorithm', () => {
  const pointsFor = (values, activeDates = values.map((_, index) => index + 1)) => {
    const byDay = new Map(
      activeDates.map((day, index) => [day, values[index]]),
    )
    const lastDay = Math.max(...activeDates)
    return Array.from({ length: lastDay }, (_, index) => {
      const tokens = byDay.get(index + 1) ?? 0
      return { date: dayAt(index), tokens, cost: tokens / 10 }
    })
  }
  const cases = [
    [pointsFor([100, 1, 50, 1, 5, 1, 1]), 'Pulsar', '3 pulse groups · 97% of tokens on pulse dates'],
    [pointsFor([50, 30, 5, 5, 5, 3, 2]), 'Sprinter', 'Top 2 dates hold 80% of tokens'],
    [pointsFor(Array.from({ length: 10 }, () => 10)), 'Marathon', '10 of 10 dates active · longest run 10 days'],
    [pointsFor([10, 10, 10, 10, 10, 10], [1, 3, 6, 9, 12, 15]), 'Hopper', '6 active runs · 40% density'],
    [pointsFor([1, 1, 1, 0, 5, 5, 5, 5]), 'Climber', '6.7× early half · 4 late active dates'],
    [pointsFor([10, 10, 10, 10, 10, 10], [1, 2, 3, 6, 7, 8]), 'Trailblazer', '6 of 8 dates active · longest run 3 days'],
    [pointsFor([1, 1, 1, 1, 1]), 'History still forming', '5 active dates · 6 needed'],
  ]

  for (const [points, name, evidence] of cases) {
    const result = analyseEditorialPattern(points)
    assert.equal(result.signature.name, name)
    assert.equal(result.signature.evidence, evidence)
  }
})

test('URL state round-trips model day while Earlier shifts to a custom window', () => {
  const rows = Array.from({ length: 10 }, (_, index) =>
    dailyRow(dayAt(index), {
      oneapi_tokens: 1,
      oneapi_models: [{ model: 'focus', tokens: 1, cost: 0.25 }],
    }),
  )
  const payload = usagePayload(rows)
  const current = buildReport(payload, {
    ...DEFAULT_REPORT_VIEW,
    preset: '7',
    tool: 'oneapi',
    model: 'focus',
    day: dayAt(9),
  })
  const earlierView = shiftReportView(payload, current.view, -1)
  const earlier = buildReport(payload, earlierView)
  const search = buildReportSearch('?keep=yes', earlier.view)
  const parsed = parseReportView(search)

  assert.equal(current.range.start, dayAt(3))
  assert.equal(earlier.range.preset, 'custom')
  assert.equal(earlier.range.start, dayAt(2))
  assert.equal(earlier.range.end, dayAt(8))
  assert.equal(parsed.preset, 'custom')
  assert.equal(parsed.from, dayAt(2))
  assert.equal(parsed.to, dayAt(8))
  assert.equal(parsed.tool, 'oneapi')
  assert.equal(parsed.model, 'focus')
  assert.equal(parsed.day, null)
})
