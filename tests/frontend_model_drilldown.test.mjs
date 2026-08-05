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
  deriveGuidedRunStep,
  explorationBackAction,
  indexRangeForDates,
  nextSeriesIndex,
  parseReportView,
} from '../src/lib/interaction.ts'
import {
  findPeakGroup,
  findRecordDayIndex,
  findRunRecord,
  stackSegment,
} from '../src/lib/chart.ts'

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
const skylineSource = readFileSync(
  new URL('../src/components/UsageSkyline.tsx', import.meta.url),
  'utf8',
)
const mobile500Start = stylesheetSource.lastIndexOf('@media (max-width: 500px)')
const mobile500Source = stylesheetSource.slice(
  mobile500Start,
  stylesheetSource.indexOf('@media', mobile500Start + 1),
)

function jsxTextContent(source) {
  return [...source.matchAll(/>([^<>{}]*)</g)]
    .map((match) => match[1].trim())
    .filter(Boolean)
    .join('\n')
}

test('hero and endless run derive their totals from published data', () => {
  const heroScores = appSource.match(/type="display-2"/g) ?? []

  assert.match(appSource, /summarizeLifetime\(daily\)/)
  assert.match(appSource, /\{fmtHeroTokens\(lifetime\.recordedTokens\)\}/)
  assert.equal(heroScores.length, 1)
  assert.match(appSource, /TOTAL RECORDED TOKENS/)
  assert.match(appSource, /lifetime\.cacheRatio/)
  assert.match(appSource, /fmtCompact\(lifetime\.cacheTokens\)/)
  assert.match(appSource, /className="skyline-desktop"/)
  assert.match(appSource, /className="skyline-compact"/)
  assert.match(appSource, /USAGE HISTORY/)
  assert.match(appSource, /THE ENDLESS RUN/)
  assert.match(appSource, /Daily recorded tokens/)
  assert.match(appSource, /<UsageSkyline[\s\S]{0,240}daily=\{daily\}/)
  assert.match(appSource, /compact/)
  assert.match(appSource, /id="explore"/)
  assert.match(appSource, /id="endless-run"/)
  assert.doesNotMatch(appSource, /75\.2B|68\.8B|91\.5%/)
})

test('pixel platformer language is original and avoids borrowed game assets', () => {
  const visitorCopy = jsxTextContent(appSource)

  assert.match(appSource, /THE ENDLESS RUN/)
  assert.match(appSource, /LOADOUT STATION/)
  assert.match(appSource, /MODEL GATE/)
  assert.match(appSource, /LIFETIME ARCHIVE/)
  assert.match(appSource, /Exact ledger/i)
  assert.match(appSource, /command-(?:runner|cursor)/)
  assert.doesNotMatch(
    `${appSource}\n${stylesheetSource}`,
    /mario|luigi|goomba|koopa|question[-_ ]?block|coin[-_ ]?sprite|warp[-_ ]?pipe/i,
  )
  assert.doesNotMatch(
    visitorCopy,
    /\b(?:XP|LEVEL|SCORE)\b|reward|Beijing|二环/i,
  )
})

test('visitor-facing chart language keeps the record but removes implementation jargon', () => {
  assert.match(chartsSource, /◆ RECORD/)
  assert.doesNotMatch(
    `${appSource}\n${chartsSource}`,
    /RECORD SKY|RECORD HORIZON|GROUND \/ TOKENS PER DAY/,
  )
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
  assert.match(stylesheetSource, /@media \(max-width:/)
  assert.match(stylesheetSource, /\.skyline-compact/)
  assert.match(appSource, /behavior: reduceMotion \? 'auto' : 'smooth'/)
  assert.match(chartsSource, /animation: !reduceMotion/)
})

test('guided run derives the complete five-state journey from interaction state', () => {
  assert.equal(
    deriveGuidedRunStep({
      guidedActive: false,
      runComplete: false,
      selectedTool: null,
      focusedModel: null,
    }),
    'free',
  )
  assert.equal(
    deriveGuidedRunStep({
      guidedActive: true,
      runComplete: false,
      selectedTool: null,
      focusedModel: null,
    }),
    'choose-tool',
  )
  assert.equal(
    deriveGuidedRunStep({
      guidedActive: true,
      runComplete: false,
      selectedTool: 'oneapi',
      focusedModel: null,
    }),
    'choose-model',
  )
  assert.equal(
    deriveGuidedRunStep({
      guidedActive: true,
      runComplete: false,
      selectedTool: 'oneapi',
      focusedModel: 'deepseek-v4-flash',
    }),
    'reveal-record',
  )
  assert.equal(
    deriveGuidedRunStep({
      guidedActive: true,
      runComplete: true,
      selectedTool: 'oneapi',
      focusedModel: 'deepseek-v4-flash',
    }),
    'complete',
  )
})

test('the endless data world exposes an optional guided run without fake game metrics', () => {
  const visitorCopy = jsxTextContent(appSource)

  assert.match(appSource, /className="page endless-run-shell"/)
  assert.match(appSource, /className="data-world"/)
  assert.match(appSource, /className="checkpoint-log guided-run-strip"/)
  assert.match(stylesheetSource, /\.endless-run-shell/)
  assert.match(stylesheetSource, /\.data-world/)
  assert.match(stylesheetSource, /\.guided-run-strip/)
  assert.match(appSource, /onClick=\{startGuidedRun\}[\s\S]{0,180}Start guided run/)
  const freeRoamControl = appSource.match(
    /label="Free roam"[\s\S]{0,180}onClick=\{([A-Za-z][A-Za-z0-9]*)\}/,
  )
  assert.ok(freeRoamControl)
  const freeRoamHandlerStart = appSource.indexOf(
    `const ${freeRoamControl[1]} =`,
  )
  const freeRoamHandler = appSource.slice(
    freeRoamHandlerStart,
    appSource.indexOf('\n  },', freeRoamHandlerStart),
  )
  assert.notEqual(freeRoamHandlerStart, -1)
  assert.match(freeRoamHandler, /setRunComplete\(false\)/)
  assert.match(freeRoamHandler, /setGuidedActive\(false\)/)
  assert.match(appSource, /label="Replay"/)
  assert.match(
    appSource,
    /<ol className="guided-run-steps" aria-label="Guided run steps">/,
  )
  assert.match(
    appSource,
    /const GUIDED_STEPS[\s\S]{0,600}key: 'choose-tool'[\s\S]{0,180}key: 'choose-model'[\s\S]{0,180}key: 'reveal-record'/,
  )
  assert.match(
    appSource,
    /GUIDED_STEPS\.map\(\(step, index\) =>[\s\S]{0,220}aria-current=\{[\s\S]{0,120}\? 'step' : undefined/,
  )
  assert.doesNotMatch(visitorCopy, /\b(?:XP|LEVEL|SCORE)\b|reward|Beijing|二环/i)
  assert.doesNotMatch(
    `${appSource}\n${stylesheetSource}`,
    /street-world|stage-route|data-run-state|const runState/,
  )
})

test('checkpoint tool count comes from the lifetime run', () => {
  assert.match(
    appSource,
    /const lifetimeToolCount = useMemo\([\s\S]{0,160}summarizeRange\(daily\)\.byTool\.filter\(\(tool\) => tool\.tokens > 0\)\.length/,
  )
  assert.match(appSource, /<dt>Tools recorded<\/dt>[\s\S]{0,80}<dd>\{lifetimeToolCount\}<\/dd>/)
})

test('tool selection announces once through the canonical atomic live region', () => {
  const atomicLiveRegions = appSource.match(/aria-atomic="true"/g) ?? []

  assert.equal(atomicLiveRegions.length, 1)
  assert.match(
    appSource,
    /className="selection-status visually-hidden"[\s\S]{0,100}role="status"[\s\S]{0,80}aria-live="polite"[\s\S]{0,80}aria-atomic="true"/,
  )
  assert.match(appSource, /className="loadout-status"(?![^>]*aria-live)[^>]*>/)
})

test('loading and failure states remain announced with direct usage language', () => {
  assert.match(appSource, /AI USAGE \/ LOADING/)
  assert.match(appSource, /AI USAGE \/ ERROR/)
  assert.match(appSource, /Usage data could not be loaded/)
  assert.match(
    appSource,
    /role="alert"[\s\S]{0,120}aria-live="assertive"/,
  )
  assert.match(
    appSource,
    /role="status"[\s\S]{0,140}aria-live="polite"[\s\S]{0,80}aria-busy="true"/,
  )
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
  assert.match(appSource, /selectedTool === tool\.id \? 'Equipped' : 'Equip'/)
  assert.match(appSource, /activeTool \? `\$\{activeTool\.label\} equipped` : 'All tools ready'/)
  assert.doesNotMatch(appSource, /tool\.models\.map\(/)
  assert.doesNotMatch(appSource, /className="model-row"/)
  assert.doesNotMatch(stylesheetSource, /\.model-row/)
})

test('desktop loadout reads as one continuous street station', () => {
  const stationStart = stylesheetSource.indexOf('.tool-grid { gap: 6px; }')
  const station = stylesheetSource.slice(
    stationStart,
    stylesheetSource.indexOf('.model-drill-trigger {', stationStart),
  )

  assert.notEqual(stationStart, -1)
  assert.match(station, /\.tool-grid > \* \{[\s\S]{0,260}box-shadow: inset/)
  assert.match(station, /\.tool-grid > \*::before \{[\s\S]{0,280}clip-path: polygon/)
  assert.match(
    station,
    /\.tool-grid > \*::after \{[\s\S]{0,420}repeating-linear-gradient[\s\S]{0,240}linear-gradient[\s\S]{0,220}repeating-linear-gradient/,
  )
  assert.match(station, /\.tool-grid > \* > \* \{[\s\S]{0,80}z-index: 1;/)
})

test('each loadout station keeps controls inside a distinct facade', () => {
  assert.match(appSource, /className=\{`loadout-card/)
  assert.match(appSource, /className=\{`model-drill-trigger/)
  for (const index of [1, 2, 3, 4]) {
    assert.match(
      stylesheetSource,
      new RegExp(`\\.tool-grid > :nth-child\\(${index}\\) \\{ --station-accent:`),
    )
  }
  for (const index of [2, 3, 4]) {
    const roofSelector = `.tool-grid > :nth-child(${index})::before {`
    const roofStart = stylesheetSource.indexOf(roofSelector)
    const roof = stylesheetSource.slice(
      roofStart,
      stylesheetSource.indexOf('}', roofStart) + 1,
    )
    const facadeSelector = `.tool-grid > :nth-child(${index})::after {`
    const facadeStart = stylesheetSource.indexOf(facadeSelector)
    const facade = stylesheetSource.slice(
      facadeStart,
      stylesheetSource.indexOf('}', facadeStart) + 1,
    )

    assert.notEqual(roofStart, -1)
    assert.match(roof, /clip-path: polygon/)
    assert.notEqual(facadeStart, -1)
    assert.match(facade, /background:/)
  }
  assert.match(
    stylesheetSource,
    /\.tool-grid > :nth-child\(1\)::before \{[\s\S]{0,180}background: #1b3040;[\s\S]{0,180}clip-path: polygon/,
  )
  assert.match(
    stylesheetSource,
    /\.tool-grid > :nth-child\(2\)::after \{[\s\S]{0,180}radial-gradient\(circle, #ef5548/,
  )
  assert.match(
    stylesheetSource,
    /\.tool-grid > :nth-child\(3\)::before \{[\s\S]{0,200}linear-gradient\(#55d6c2, #55d6c2\)/,
  )
  assert.match(
    stylesheetSource,
    /\.tool-grid > :nth-child\(4\)::after \{[\s\S]{0,200}repeating-linear-gradient\(to bottom[\s\S]{0,160}radial-gradient\(circle, #b594ff/,
  )
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
  assert.doesNotMatch(chartsSource, /name: '(?:TOKENS|SPEND) \/ DAY'/)
  assert.match(
    chartsSource,
    /RECORD: <strong>\$\{fmtExact\(recordDailyTokens\[list\[0\]\.dataIndex\]\)\}<\/strong> tokens/,
  )
  assert.doesNotMatch(chartsSource, /const rawModels =/)
  assert.doesNotMatch(chartsSource, /tool\.breakdown/)
})

test('multiple split peaks keep one truthful argmax record label', () => {
  const values = [100, 120, 110, 1_000, 900]
  const peakGroup = findPeakGroup(values)

  assert.deepEqual(peakGroup.peakIndices, [3, 4])
  assert.equal(findRecordDayIndex(values), 3)
  assert.match(
    chartsSource,
    /const recordDailyTokens = focusedSeries && activeTool[\s\S]{0,220}modelSeriesPoint\(row, activeTool\.id, focusedSeries\)\.tokens[\s\S]{0,80}: totalDayTokens/,
  )
  assert.match(
    chartsSource,
    /const peakGroup = findPeakGroup\(totalDayTokens\)[\s\S]{0,360}const recordDayIndex = findRecordDayIndex\(recordDailyTokens\)/,
  )
  assert.match(chartsSource, /id: 'record-beacon'[\s\S]{0,300}symbol: 'diamond' as const/)
  assert.match(
    chartsSource,
    /const recordBeaconInSky =[^;]+peakDaySet\.has\(recordDayIndex\)/,
  )
  assert.match(
    chartsSource,
    /xAxisIndex: recordBeaconXAxisIndex,[\s\S]{0,80}yAxisIndex: recordBeaconYAxisIndex/,
  )
  assert.match(
    chartsSource,
    /data: \[\[recordDayIndex, recordBeaconValue\]\]/,
  )
  assert.match(
    chartsSource,
    /formatter: \(\) => `◆ RECORD\\n\$\{fmtExact\(recordValue\)\}`/,
  )
})

test('focused record labels do not redefine or clip the tool-total terrain', () => {
  const toolTotals = [100, 120, 140, 1_000]
  const focusedModelTokens = [2, 3, 10, 4]
  const terrain = findPeakGroup(toolTotals)
  const terrainPeakDays = new Set(terrain.peakIndices)
  const focusedRecordDay = findRecordDayIndex(focusedModelTokens)
  const peakDayStack = [4, 996]
  const splitStack = peakDayStack.map((_value, index) => ({
    ground: stackSegment(peakDayStack, index, terrain.recordFloor, 'ground'),
    sky: stackSegment(peakDayStack, index, terrain.recordFloor, 'sky'),
  }))

  assert.deepEqual(terrain.peakIndices, [3])
  assert.equal(focusedRecordDay, 2)
  assert.equal(terrainPeakDays.has(focusedRecordDay), false)
  assert.equal(focusedModelTokens[focusedRecordDay], 10)
  assert.equal(toolTotals[focusedRecordDay], 140)
  assert.equal(
    splitStack.reduce((sum, segment) => sum + segment.ground, 0),
    terrain.recordFloor,
  )
  assert.equal(
    splitStack.reduce((sum, segment) => sum + segment.sky, 0),
    toolTotals[3] - terrain.recordFloor,
  )
  assert.equal(
    splitStack.reduce(
      (sum, segment) => sum + segment.ground + segment.sky,
      0,
    ),
    toolTotals[3],
  )
})

test('the exact record plaque stays visible at either chart edge', () => {
  const plaqueSource = chartsSource.slice(
    chartsSource.indexOf("id: 'record-beacon'"),
    chartsSource.indexOf("id: 'run-cursor'"),
  )

  assert.match(
    chartsSource,
    /const recordPlaquePosition =[\s\S]{0,100}recordDayIndex >= dates\.length \* 0\.72 \? 'left' : 'right'/,
  )
  assert.match(plaqueSource, /clip: false/)
  assert.match(plaqueSource, /symbol: 'diamond' as const/)
  assert.match(plaqueSource, /label: \{[\s\S]{0,80}show: true/)
  assert.match(plaqueSource, /position: recordPlaquePosition/)
  assert.match(plaqueSource, /overflow: 'none'/)
  assert.match(
    plaqueSource,
    /formatter: \(\) => `◆ RECORD\\n\$\{fmtExact\(recordValue\)\}`/,
  )
  assert.doesNotMatch(chartsSource, /id: 'record-label'/)
})

test('run record board follows the exact current chart scope without live noise', () => {
  const recordBoardStart = stylesheetSource.indexOf('.chart-run-record {')
  const recordBoardStyles = stylesheetSource.slice(
    recordBoardStart,
    stylesheetSource.indexOf('.chart-breadcrumb button', recordBoardStart),
  )

  assert.deepEqual(
    findRunRecord([12, 45, 45, 20], ['d1', 'd2', 'd3', 'd4']),
    { index: 1, date: 'd2', value: 45 },
  )
  assert.equal(findRunRecord([0, 0], ['d1', 'd2']), null)
  assert.equal(findRunRecord([10], []), null)

  assert.match(
    chartsSource,
    /const scopedDailyTokens = daily\.map\(\(row\) =>[\s\S]{0,420}modelSeriesPoint\(row, activeTool\.id, focusedAccessibleSeries\)\.tokens[\s\S]{0,300}num\(row, activeTool\.tokenKey\)[\s\S]{0,300}TOOLS\.reduce/,
  )
  assert.match(chartsSource, /className="chart-run-record"/)
  assert.match(chartsSource, /className="chart-run-record__title">RUN RECORD/)
  assert.match(
    chartsSource,
    /runRecord \? `\$\{fmtExact\(runRecord\.value\)\} TOKENS` : 'NO RECORDED USAGE'/,
  )
  assert.match(
    chartsSource,
    /data-record-state=\{runRecord \? 'recorded' : 'empty'\}/,
  )
  assert.match(chartsSource, /recordScope} · \{runRecord\?\.date/)
  assert.match(chartsSource, /aria-label=\{`Run record for \$\{recordScope\}`\}/)
  assert.doesNotMatch(chartsSource, /chart-run-record[\s\S]{0,500}aria-live=/)
  assert.match(
    recordBoardStyles,
    /\.chart-run-record \{[\s\S]{0,180}max-width: 100%[\s\S]{0,240}border: 2px solid[\s\S]{0,180}var\(--night-panel\)[\s\S]{0,180}clip-path: polygon/,
  )
  assert.match(
    recordBoardStyles,
    /\.chart-run-record__value \{[\s\S]{0,160}var\(--gold\)[\s\S]{0,120}font-variant-numeric: tabular-nums/,
  )
  assert.match(
    recordBoardStyles,
    /\.chart-run-record__meta \{[\s\S]{0,160}overflow-wrap: anywhere/,
  )
  assert.match(
    mobile500Source,
    /\.chart-run-record \{[\s\S]{0,100}width: 100%[\s\S]{0,140}grid-template-columns: auto minmax\(0, 1fr\)/,
  )
})

test('RUN COMPLETE is a data-backed ticket with a replay action', () => {
  const ticketStart = appSource.indexOf(
    'className="checkpoint-log run-complete-card"',
  )
  const ticket = appSource.slice(
    ticketStart,
    appSource.indexOf('</section>', ticketStart),
  )

  assert.notEqual(ticketStart, -1)
  assert.match(ticket, /RUN COMPLETE/)
  assert.match(ticket, /Recorded run/)
  assert.match(ticket, /runMetrics/)
  assert.match(ticket, /fmtExactTokens\(runMetrics\?\.[^)]+\)/)
  assert.match(ticket, /fmtExactUsd\(runMetrics\?\.[^)]+\)/)
  assert.match(ticket, /label="Replay"[\s\S]{0,160}onClick=\{replayRun\}/)
  assert.match(
    appSource,
    /visible\.map\([\s\S]{0,320}modelSeriesPoint\(/,
  )
  assert.match(
    appSource,
    /points\.reduce\([\s\S]{0,180}total \+ point\.tokens/,
  )
  assert.match(
    appSource,
    /points\.reduce\([\s\S]{0,180}total \+ point\.cost/,
  )
  assert.match(
    appSource,
    /findRunRecord\([\s\S]{0,220}points\.map\(\(point\) => point\.tokens\)[\s\S]{0,120}dates/,
  )
  assert.doesNotMatch(
    jsxTextContent(ticket),
    /\b(?:demo|mock|placeholder|score|reward|XP|level)\b/i,
  )
})

test('run record board reads as a finish-line gantry', () => {
  assert.match(
    stylesheetSource,
    /\.chart-run-record \{[\s\S]{0,100}width: min\(100%, 760px\);[\s\S]{0,260}min-height: 64px;/,
  )
  assert.match(
    stylesheetSource,
    /\.chart-run-record::before \{[\s\S]{0,260}border-top: 4px solid var\(--gold\)[\s\S]{0,120}border-inline: 6px solid var\(--pixel-border\)[\s\S]{0,180}radial-gradient\(circle at 7px 1px, var\(--gold\)/,
  )
  assert.match(
    stylesheetSource,
    /\.chart-run-record::after \{[\s\S]{0,220}bottom: 0[\s\S]{0,120}height: 5px[\s\S]{0,160}repeating-linear-gradient\(90deg, var\(--moon\) 0 10px, var\(--road\) 10px 20px\)/,
  )
  assert.match(
    stylesheetSource,
    /\.chart-run-record__title \{[\s\S]{0,160}font-size: 0\.68rem;/,
  )
  assert.match(
    stylesheetSource,
    /\.chart-run-record__value \{[\s\S]{0,100}font-size: 1\.18rem;[\s\S]{0,100}font-variant-numeric: tabular-nums/,
  )
})

test('chart arrival structures stay decorative behind truthful data', () => {
  assert.match(chartsSource, /className="chart-host"[\s\S]{0,100}role="img"/)
  assert.match(
    stylesheetSource,
    /\.chart-host \{[\s\S]{0,100}isolation: isolate;[\s\S]{0,140}overflow: hidden;/,
  )
  assert.match(
    stylesheetSource,
    /\.chart-host::before,\s*\.chart-host::after \{[\s\S]{0,120}z-index: 0;[\s\S]{0,80}pointer-events: none;[\s\S]{0,80}content: '';/,
  )
  assert.match(
    stylesheetSource,
    /\.chart-host > \* \{[\s\S]{0,80}z-index: 1;/,
  )
  assert.doesNotMatch(chartsSource, /arrival-series|arrival-value|arrival-data/)
})

test('run record board adds no explanatory metric labels', () => {
  const boardStart = chartsSource.indexOf('className="chart-run-record"')
  const boardMarkup = chartsSource.slice(
    boardStart,
    chartsSource.indexOf('</div>', boardStart),
  )

  assert.notEqual(boardStart, -1)
  assert.doesNotMatch(
    jsxTextContent(boardMarkup),
    /record horizon|record sky|ground|tokens per day|percentile|axis|scale/i,
  )
})

test('chart and model details share one record-wall platform', () => {
  const cardStart = appSource.indexOf('<Card padding={3} className="exact-ledger-card">')
  const card = appSource.slice(cardStart, appSource.indexOf('</Card>', cardStart))

  assert.notEqual(cardStart, -1)
  assert.match(card, /className="chart-panel"/)
  assert.match(card, /className="model-detail-panel"/)
  assert.match(
    stylesheetSource,
    /\.exact-ledger-card \{[\s\S]{0,420}linear-gradient\(rgba\(72, 97, 118, 0\.1\) 1px, transparent 1px\)[\s\S]{0,220}inset 0 -7px var\(--road\)/,
  )
  assert.match(
    stylesheetSource,
    /\.exact-ledger-card::before \{[\s\S]{0,320}clip-path: polygon/,
  )
  assert.match(
    stylesheetSource,
    /\.exact-ledger-card::after \{[\s\S]{0,260}repeating-linear-gradient\(90deg, var\(--moon-muted\)[\s\S]{0,100}var\(--road\)/,
  )
  assert.match(
    stylesheetSource,
    /\.exact-ledger-card > \* \{[\s\S]{0,80}z-index: 1;/,
  )
})

test('long ranges bound date-label density without exposing axis jargon', () => {
  assert.match(
    chartsSource,
    /const dateLabelInterval =[\s\S]{0,100}dates\.length > 12 \? Math\.ceil\(dates\.length \/ 6\) - 1 : 0/,
  )
  assert.match(
    chartsSource,
    /formatter: \(value: string\) => value\.slice\(5\),[\s\S]{0,80}interval: dateLabelInterval[\s\S]{0,60}hideOverlap: true/,
  )
  assert.doesNotMatch(
    chartsSource,
    /RECORD SKY|RECORD HORIZON|GROUND \/ TOKENS PER DAY|TOKENS \/ DAY|SPEND \/ DAY/,
  )
})

test('compact weekly skyline keeps peak and endpoint labels readable', () => {
  assert.match(
    skylineSource,
    /const COMPACT_LAYOUT = \{[\s\S]{0,240}width: 360,[\s\S]{0,80}height: 236,[\s\S]{0,80}plotTop: 32,[\s\S]{0,160}peakY: 18,[\s\S]{0,80}dateY: 224,[\s\S]{0,80}labelFontSize: 12/,
  )
  assert.match(
    skylineSource,
    /const layout = compact \? COMPACT_LAYOUT : WIDE_LAYOUT/,
  )
  assert.match(
    skylineSource,
    /viewBox=\{`0 0 \$\{layout\.width\} \$\{layout\.height\}`\}/,
  )
  assert.match(
    skylineSource,
    /className="usage-skyline__peak"[\s\S]{0,100}fontSize=\{layout\.labelFontSize\}[\s\S]{0,100}y=\{layout\.peakY\}/,
  )
  assert.match(
    skylineSource,
    /lastPeriod && lastPeriod\.key !== firstPeriod\.key[\s\S]{0,300}compactRangeLabel\(lastPeriod\.startDate, lastPeriod\.endDate\)/,
  )
  assert.match(
    mobile500Source,
    /\.skyline-section \.usage-skyline__peak,[\s\S]{0,100}\.skyline-section \.usage-skyline__date \{[^}]*opacity: 1;[^}]*font-weight: 900;[^}]*\}/,
  )
})

test('a low-tail ratio cannot suppress a valid upper-tail record split', () => {
  const peakGroup = findPeakGroup([1, 10, 11, 12, 100, 110])

  assert.deepEqual(peakGroup.peakIndices, [4, 5])
  assert.equal(peakGroup.recordFloor, 12 * 1.16)
})

test('a continuous normal distribution remains on one unsplit stage', () => {
  const peakGroup = findPeakGroup([100, 115, 130, 145, 160, 175, 190, 205])

  assert.deepEqual(peakGroup.peakIndices, [])
  assert.equal(peakGroup.skyMax, 0)
})

test('adjacent upper record-growth peaks stay in the same split group', () => {
  const values = [100, 110, 120, 130, 140, 150, 160, 170, 180, 500, 650, 800]
  const peakGroup = findPeakGroup(values)

  assert.deepEqual(peakGroup.peakIndices, [9, 10, 11])
  assert.equal(findRecordDayIndex(values), 11)
})

test('record ties resolve to the first truthful argmax', () => {
  assert.equal(findRecordDayIndex([]), -1)
  assert.equal(findRecordDayIndex([5, 10, 10, 8]), 1)
  assert.equal(findRecordDayIndex([10, 10]), 0)
})

test('every ground and sky series segment conserves its original value', () => {
  const values = [40, 30, 20, 10]

  for (const floor of [0, 25, 55, 100, 150]) {
    values.forEach((value, index) => {
      const ground = stackSegment(values, index, floor, 'ground')
      const sky = stackSegment(values, index, floor, 'sky')
      assert.equal(ground + sky, value)
    })
  }
})

test('a run with no positive usage renders no command cursor data', () => {
  assert.match(
    chartsSource,
    /const latestRunIndex = totalDayTokens\.reduce\([\s\S]{0,120}\(value > 0 \? index : latest\)[\s\S]{0,60}-1/,
  )
  assert.match(
    chartsSource,
    /latestRunIndex >= 0[\s\S]{0,100}\? \[\[latestRunIndex, terrainMax \* 0\.045\]\][\s\S]{0,40}: \[\]/,
  )
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
  assert.match(
    appSource,
    /const savedView = parseReportView\(window\.location\.search\)/,
  )
  assert.match(
    appSource,
    /const nextSearch = buildReportViewSearch\(window\.location\.search, \{/,
  )
  assert.match(
    appSource,
    /window\.history\.replaceState\(window\.history\.state, '', nextUrl\)/,
  )

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
