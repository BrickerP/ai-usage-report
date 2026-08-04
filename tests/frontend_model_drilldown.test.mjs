import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  degradedSourceNotices,
  describeRange,
  indexForPreset,
  modelSeriesMembers,
  modelSeriesPoint,
  percentageTenths,
  selectModelSeries,
  summarizeRange,
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

test('chronicle hero and skyline are dynamic, all-time, and separate from Explore', () => {
  assert.match(appSource, /summarizeLifetime\(daily\)/)
  assert.match(appSource, /\{fmtHeroTokens\(lifetime\.recordedTokens\)\}/)
  assert.match(appSource, /recorded tokens/)
  assert.match(appSource, /lifetime\.cacheRatio/)
  assert.match(appSource, /className="skyline-desktop"/)
  assert.match(appSource, /className="skyline-compact"/)
  assert.match(appSource, /className="skyline-weekly-label"[\s\S]{0,120}Weekly Skyline/)
  assert.match(appSource, /Natural-week totals on small screens/)
  assert.match(stylesheetSource, /\.skyline-weekly-label/)
  assert.match(appSource, /<UsageSkyline[\s\S]{0,240}daily=\{daily\}/)
  assert.match(appSource, /compact/)
  assert.match(appSource, /id="explore"/)
  assert.match(appSource, /id="explore-heading"[\s\S]{0,80}Explore/)
  assert.doesNotMatch(appSource, /75\.2B|68\.8B|91\.5%/)
})

test('all time is the default view and the URL omits only the default preset', () => {
  assert.match(
    appSource,
    /const \[preset, setPreset\] = useState<ViewPreset>\('all'\)/,
  )
  assert.match(appSource, /setRange\(indexForPreset\(data\.daily, 'all'\)\)/)
  assert.match(appSource, /const nextPreset = savedView\.preset \?\? 'all'/)
  assert.match(appSource, /label="Reset all time"/)
  assert.match(
    appSource,
    /isDisabled=\{\s*preset === 'all' &&\s*range\[0\] === 0 &&\s*range\[1\] === daily\.length - 1\s*\}/,
  )
  assert.match(appSource, /onClick=\{\(\) => applyPreset\('all'\)\}/)
})

test('core interaction and data-state semantics remain explicit', () => {
  assert.match(appSource, /className="selection-status visually-hidden"/)
  assert.match(appSource, /Viewing \$\{activeTool\.label\} models/)
  assert.match(
    appSource,
    /className="report-state-card"[\s\S]*role="alert"[\s\S]*aria-live="assertive"/,
  )
  assert.match(
    appSource,
    /className="report-state-card"[\s\S]*role="status"[\s\S]*aria-live="polite"[\s\S]*aria-busy="true"/,
  )
  assert.match(
    appSource,
    /title="No usage data yet"[\s\S]{0,260}label="Reload usage data"/,
  )
  assert.match(appSource, /aria-label="Back to all tools"/)
  assert.match(appSource, /aria-keyshortcuts="Escape"/)
  assert.match(appSource, /onKeyDown=\{stepBackInChart\}/)
  assert.match(appSource, /aria-describedby="series-keyboard-help"/)
  assert.match(appSource, /onKeyDown=\{moveSeriesFocus\}/)
  assert.match(stylesheetSource, /@media \(prefers-reduced-motion: reduce\)/)
  assert.match(appSource, /behavior: reduceMotion \? 'auto' : 'smooth'/)
  assert.match(chartsSource, /animation: !reduceMotion/)
})

test('report context is concise by default and fully disclosed on demand', () => {
  assert.match(
    appSource,
    /const \[isReportDetailsOpen, setIsReportDetailsOpen\] = useState\(false\)/,
  )
  assert.match(appSource, /aria-expanded=\{isReportDetailsOpen\}/)
  assert.match(appSource, /aria-controls="report-details-panel"/)
  assert.match(appSource, /id="report-details-panel"/)
  assert.match(appSource, /payload\.notes\?\.token_breakdown/)
  assert.match(appSource, /payload\.notes\?\.cost/)
  assert.match(appSource, /className="source-health-notice"/)
  assert.match(appSource, /aria-live="polite"/)
  assert.match(appSource, /degradedSources[\s\S]{0,220}\.map\(\(source\) => source\.message\)/)
  const quietSourceStart = appSource.indexOf('className="source-health-notice"')
  const quietSourceMarkup = appSource.slice(
    quietSourceStart,
    appSource.indexOf('</button>', quietSourceStart),
  )
  assert.doesNotMatch(quietSourceMarkup, /role="status"|aria-live=/)
  assert.doesNotMatch(
    appSource,
    /<Text size="sm" color="secondary">\s*<Text weight="semibold">Token breakdown:/,
  )
})

test('tool cards expose one model action without permanent inventories', () => {
  assert.match(appSource, /className="tool-card-action"/)
  assert.match(appSource, /aria-pressed=\{selectedTool === tool\.id\}/)
  assert.match(
    appSource,
    /onClick=\{\(\) => selectTool\(tool\.id, true\)\}/,
  )
  assert.doesNotMatch(appSource, /tool\.models\.map\(/)
  assert.doesNotMatch(appSource, /className="model-row"/)
  assert.doesNotMatch(stylesheetSource, /\.model-row/)
})

test('advanced time controls are disclosed without losing capability', () => {
  assert.match(
    appSource,
    /const \[isRangeDetailsOpen, setIsRangeDetailsOpen\] = useState\(false\)/,
  )
  assert.match(appSource, /aria-expanded=\{isRangeDetailsOpen\}/)
  assert.match(appSource, /aria-controls="range-details-panel"/)
  assert.match(appSource, /id="range-details-panel"/)
  assert.match(appSource, /setIsRangeDetailsOpen\(true\)/)
  assert.match(appSource, /onChange=\{onDatesChange\}/)
  assert.match(appSource, /isDisabled=\{range\[0\] <= 0\}/)
  assert.match(appSource, /isDisabled=\{range\[1\] >= daily\.length - 1\}/)
  assert.match(appSource, /label="Reset all time"/)
  assert.match(
    appSource,
    /isDisabled=\{\s*preset === 'all' &&\s*range\[0\] === 0 &&\s*range\[1\] === daily\.length - 1\s*\}/,
  )
  assert.match(appSource, /onClick=\{\(\) => applyPreset\('all'\)\}/)
})

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
  assert.match(appSource, /calendar \$\{[\s\S]{0,160}recorded/)
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
  assert.match(appSource, /percentageTenths\(models\.map\(\(model\) => model\.tokens\)\)/)
})

test('model details and focus preserve keyboard and accessible state', () => {
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
  assert.match(appSource, /aria-pressed=\{canFocus \? isFocused : undefined\}/)
  assert.match(appSource, /className="chart-focus-state"/)
  assert.match(appSource, /Focused · <strong>\{pinnedModel\}<\/strong>/)
  assert.match(
    appSource,
    /className="chart-focus-state"[\s\S]{0,360}onClick=\{clearModelFocus\}/,
  )
  assert.doesNotMatch(appSource, /className="model-focus-note"/)
  assert.doesNotMatch(appSource, /className="series-key-value"/)
  assert.doesNotMatch(appSource, /className="series-key-state"/)
  assert.doesNotMatch(appSource, /label="Reset chart to all tools"/)
  assert.match(appSource, /focusedModel=\{pinnedModel\}/)
  assert.match(chartsSource, /focusedModel: string \| null/)
  assert.match(chartsSource, /const focusedSeries = activeTool && focusedModel/)
  assert.match(chartsSource, /opacity: isDimmed \? 0\.22 : 1/)
  assert.match(chartsSource, /focusedSeries\?\.label \?\? activeTool\.label/)
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
  assert.match(
    appSource,
    /const clearModelFocus = useCallback\(\(\) => \{[\s\S]{0,120}focusChartSection\(\)/,
  )
  assert.match(
    appSource,
    /action === 'clear-model'[\s\S]{0,80}clearModelFocus\(\)/,
  )
})

test('tooltips follow plotted series instead of dumping raw detail', () => {
  assert.match(
    chartsSource,
    /const tooltipSeries = focusedSeries \? \[focusedSeries\] : specs/,
  )
  assert.match(chartsSource, /modelSeriesPoint\(row, activeTool\.id, spec\)/)
  assert.match(
    chartsSource,
    /spec\.kind === 'other'[\s\S]{0,220}modelSeriesMembers\(/,
  )
  assert.match(chartsSource, /Tool total:/)
  assert.match(chartsSource, /name: enablePeakCap \? 'Tokens \/ day \(peak capped\)' : 'Tokens \/ day'/)
  assert.match(chartsSource, /name: 'Spend \/ day'/)
  assert.doesNotMatch(chartsSource, /const rawModels =/)
  assert.doesNotMatch(chartsSource, /tool\.breakdown/)
})

test('peak-cap truncates stack segments proportionally and crowns the peak day', () => {
  assert.match(
    chartsSource,
    /const capTotalFor = \(dayIndex: number\): number => \{/,
  )
  assert.match(chartsSource, /total > cap\s*\n\s*\? cap \/ Math\.max\(total, 1\)\s*\n\s*: 1/)
  assert.match(chartsSource, /const peakDaySet = new Set\(peakDayIndices\)/)
  assert.match(chartsSource, /id: 'peak-crown'[\s\S]{0,180}symbol: 'rect' as const/)
  assert.match(chartsSource, /id: 'peak-crown-label'[\s\S]{0,1000}♛ \$\{fmtCompact\(value\)\}/)
  assert.match(chartsSource, /max: enablePeakCap \? \(peakCap \?\? 0\) \* 1\.18 : undefined/)
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
      preset: 'all',
      from: null,
      to: null,
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
