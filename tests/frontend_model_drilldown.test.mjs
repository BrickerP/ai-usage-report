import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  degradedSourceNotices,
  modelSeriesPoint,
  selectModelSeries,
  TOOLS,
} from '../src/lib/usage.ts'
import {
  buildReportViewSearch,
  explorationBackAction,
  indexRangeForDates,
  nextSeriesIndex,
  parseReportView,
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

const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8')
const stylesheetSource = readFileSync(
  new URL('../src/index.css', import.meta.url),
  'utf8',
)
const chartsSource = readFileSync(
  new URL('../src/components/UsageCharts.tsx', import.meta.url),
  'utf8',
)

test('interaction controls expose explicit state semantics', () => {
  assert.match(appSource, /isDisabled=\{range\[0\] <= 0\}/)
  assert.match(appSource, /isDisabled=\{range\[1\] >= daily\.length - 1\}/)
  assert.match(
    appSource,
    /label="Reset 30d"[\s\S]{0,180}isDisabled=\{preset === '30'\}/,
  )
  assert.match(appSource, /aria-expanded=\{isModelListOpen\}/)
  assert.match(appSource, /aria-controls="model-details-panel"/)
  assert.match(
    appSource,
    /const modelDetailsToggleRef = useRef<HTMLButtonElement>\(null\)/,
  )
  assert.match(
    appSource,
    /const modelDetailsPanelRef = useRef<HTMLDivElement>\(null\)/,
  )
  assert.match(appSource, /focusTarget\.focus\(\{ preventScroll: true \}\)/)
  assert.match(appSource, /tabIndex=\{-1\}/)
  assert.match(appSource, /role="region"/)
  assert.match(appSource, /aria-labelledby="model-details-heading"/)
  assert.match(appSource, /if \(wasOpen === isModelListOpen\) return/)
  assert.match(
    appSource,
    /isFocused \? \([\s\S]*className="series-key-state"[\s\S]*Focused[\s\S]*\) : null/,
  )
  assert.match(stylesheetSource, /\.series-key-state/)
  assert.match(appSource, /className="selection-status visually-hidden"/)
  assert.match(appSource, /role="status"/)
  assert.match(appSource, /aria-live="polite"/)
  assert.match(appSource, /Viewing \$\{activeTool\.label\} models/)
  assert.match(
    appSource,
    /className="report-state-card"[\s\S]*role="alert"[\s\S]*aria-live="assertive"/,
  )
  assert.match(
    appSource,
    /className="report-state-card"[\s\S]*role="status"[\s\S]*aria-live="polite"[\s\S]*aria-busy="true"/,
  )
  assert.match(appSource, /aria-label="Back to all tools"/)
  assert.match(stylesheetSource, /\.chart-breadcrumb button:hover/)
  assert.match(stylesheetSource, /\.model-focus-note button:hover/)
  assert.match(appSource, /focusedModel=\{pinnedModel\}/)
  assert.match(chartsSource, /focusedModel: string \| null/)
  assert.match(chartsSource, /const focusedSeries = activeTool && focusedModel/)
  assert.match(chartsSource, /opacity: isDimmed \? 0\.22 : 1/)
  assert.match(chartsSource, /focusedSeries\?\.label \?\? activeTool\.label/)
  assert.match(
    appSource,
    /Other model tokens stay visible for context; spend follows this model/,
  )
  assert.match(
    appSource,
    /Other model tokens are dimmed for context · spend shows[\s\n]+this model/,
  )
  assert.match(chartsSource, /const focusedAccessibleSeries =/)
  assert.match(
    chartsSource,
    /other model tokens are dimmed and spend follows the focused model/,
  )
  assert.match(
    chartsSource,
    /description: activeTool[\s\S]{0,220}focusedSeries[\s\S]{0,220}spend follows the focused model/,
  )
  assert.match(
    chartsSource,
    /activeTool && focusedAccessibleSeries[\s\S]{0,240}modelSeriesPoint\([\s\S]{0,180}\)\.cost/,
  )
  assert.match(appSource, /aria-describedby="series-keyboard-help"/)
  assert.match(appSource, /onKeyDown=\{moveSeriesFocus\}/)
  assert.match(appSource, /const \[loadAttempt, setLoadAttempt\] = useState\(0\)/)
  assert.match(appSource, /\}, \[loadAttempt\]\)/)
  assert.match(appSource, /savedView\.model !== 'Legacy unknown'/)
  assert.match(
    appSource,
    /label="Retry loading usage data"[\s\S]{0,160}onClick=\{retryLoad\}/,
  )
  assert.match(
    appSource,
    /const retryLoad = useCallback\(\(\) => \{[\s\S]{0,180}setIsViewHydrated\(false\)/,
  )
  assert.match(
    appSource,
    /title="No usage data yet"[\s\S]{0,260}label="Reload usage data"/,
  )
  assert.match(stylesheetSource, /@media \(prefers-reduced-motion: reduce\)/)
  assert.match(appSource, /aria-keyshortcuts="Escape"/)
  assert.match(appSource, /onKeyDown=\{stepBackInChart\}/)
  assert.match(
    appSource,
    /const clearModelFocus = useCallback\(\(\) => \{[\s\S]{0,120}focusChartSection\(\)/,
  )
  assert.match(
    appSource,
    /action === 'clear-model'[\s\S]{0,80}clearModelFocus\(\)/,
  )
  assert.match(
    appSource,
    /onClick=\{clearModelFocus\}[\s\S]{0,80}Clear focus/,
  )
  assert.match(
    appSource,
    /label="Reset chart to all tools"[\s\S]{0,160}onClick=\{returnToTools\}/,
  )
  assert.match(appSource, /behavior: reduceMotion \? 'auto' : 'smooth'/)
  assert.match(chartsSource, /animation: !reduceMotion/)
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

test('report view state round-trips valid tool, model, and preset values', () => {
  const search = buildReportViewSearch('?keep=yes', {
    tool: 'oneapi',
    model: 'deepseek/v4 flash',
    preset: '7',
    from: null,
    to: null,
  })

  assert.equal(
    search,
    '?keep=yes&tool=oneapi&model=deepseek%2Fv4+flash&range=7',
  )
  assert.deepEqual(parseReportView(search), {
    tool: 'oneapi',
    model: 'deepseek/v4 flash',
    preset: '7',
    from: null,
    to: null,
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

test('invalid report view values safely collapse to defaults', () => {
  assert.deepEqual(
    parseReportView('?tool=private&model=secret&range=365&from=no&to=also-no'),
    {
      tool: null,
      model: null,
      preset: null,
      from: null,
      to: null,
    },
  )
  assert.equal(
    buildReportViewSearch('?tool=oneapi&model=old&range=7', {
      tool: null,
      model: null,
      preset: '30',
      from: null,
      to: null,
    }),
    '',
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
