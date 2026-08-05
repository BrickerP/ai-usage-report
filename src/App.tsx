import { Theme } from '@astryxdesign/core/theme'
import { neutralTheme } from '@astryxdesign/theme-neutral/built'
import { Heading } from '@astryxdesign/core/Heading'
import { Text } from '@astryxdesign/core/Text'
import { Card } from '@astryxdesign/core/Card'
import { Badge } from '@astryxdesign/core/Badge'
import { VStack } from '@astryxdesign/core/VStack'
import { HStack } from '@astryxdesign/core/HStack'
import { Button } from '@astryxdesign/core/Button'
import { IconButton } from '@astryxdesign/core/IconButton'
import { Icon } from '@astryxdesign/core/Icon'
import {
  SegmentedControl,
  SegmentedControlItem,
} from '@astryxdesign/core/SegmentedControl'
import { DateRangeInput } from '@astryxdesign/core/DateRangeInput'
import type { DateRange } from '@astryxdesign/core/DateRangeInput'
import type { ISODateString } from '@astryxdesign/core/Calendar'
import { EmptyState } from '@astryxdesign/core/EmptyState'
import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type { CSSProperties, KeyboardEvent as ReactKeyboardEvent } from 'react'
import { UsageSkyline } from './components/UsageSkyline'
import { UsageCharts } from './components/UsageCharts'
import { findRunRecord, modelSeriesColor } from './lib/chart'
import { summarizeLifetime } from './lib/chronicle'
import { fmtCompact, fmtUsd } from './lib/format'
import {
  buildReportViewSearch,
  deriveGuidedRunStep,
  explorationBackAction,
  indexRangeForDates,
  nextSeriesIndex,
  parseReportView,
  type GuidedRunStep,
  type ViewPreset,
} from './lib/interaction'
import {
  degradedSourceNotices,
  describeRange,
  indexForPreset,
  loadUsage,
  modelSeriesPoint,
  percentageTenths,
  selectModelSeries,
  summarizeRange,
  TOOLS,
  type DailyRow,
  type ModelSeriesSpec,
  type ToolId,
  type UsagePayload,
} from './lib/usage'

function fmtExactTokens(value: number) {
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 0,
  }).format(value)
}

function fmtExactUsd(value: number) {
  const digits = Math.abs(value) > 0 && Math.abs(value) < 0.01 ? 4 : 2
  return `$${value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`
}

function modelLabel(series: ModelSeriesSpec) {
  return series.kind === 'legacy' ? 'Unattributed (legacy)' : series.label
}

function compactTimestamp(value?: string | null) {
  if (!value) return '—'
  return value.slice(0, 16).replace('T', ' ')
}

function fmtHeroTokens(value: number) {
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

function fmtRatio(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

type GuidedStepKey = Exclude<GuidedRunStep, 'free' | 'complete'>

const GUIDED_STEPS: Array<{
  key: GuidedStepKey
  label: string
  description: string
}> = [
  {
    key: 'choose-tool',
    label: 'LOADOUT',
    description: 'Select one tool from the recorded archive.',
  },
  {
    key: 'choose-model',
    label: 'MODEL',
    description: 'Focus one exact model in the model gate.',
  },
  {
    key: 'reveal-record',
    label: 'RECORD',
    description: 'Reveal the record for the current visible range.',
  },
]

function ReportApp() {
  const [payload, setPayload] = useState<UsagePayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [preset, setPreset] = useState<ViewPreset>('all')
  const [range, setRange] = useState<[number, number]>([0, -1])
  const [selectedTool, setSelectedTool] = useState<ToolId | null>(null)
  const [pinnedModel, setPinnedModel] = useState<string | null>(null)
  const [guidedActive, setGuidedActive] = useState(false)
  const [runComplete, setRunComplete] = useState(false)
  const [isModelListOpen, setIsModelListOpen] = useState(false)
  const [isReportDetailsOpen, setIsReportDetailsOpen] = useState(false)
  const [isRangeDetailsOpen, setIsRangeDetailsOpen] = useState(false)
  const [isViewHydrated, setIsViewHydrated] = useState(false)
  const chartSectionRef = useRef<HTMLDivElement>(null)
  const guidedRunRef = useRef<HTMLElement>(null)
  const loadoutStationRef = useRef<HTMLDivElement>(null)
  const runResultRef = useRef<HTMLElement>(null)
  const modelDetailsToggleRef = useRef<HTMLButtonElement>(null)
  const modelDetailsPanelRef = useRef<HTMLDivElement>(null)
  const wasModelListOpen = useRef(false)

  useEffect(() => {
    let cancelled = false
    loadUsage()
      .then((data) => {
        if (cancelled) return
        setPayload(data)
        setRange(indexForPreset(data.daily, 'all'))
        setPreset('all')
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || String(err))
      })
    return () => {
      cancelled = true
    }
  }, [loadAttempt])

  useEffect(() => {
    if (!payload || isViewHydrated) return
    const savedView = parseReportView(window.location.search)
    const nextPreset = savedView.preset ?? 'all'
    let nextRange = indexForPreset(payload.daily, nextPreset)

    if (savedView.from && savedView.to) {
      nextRange =
        indexRangeForDates(
          payload.daily.map((row) => row.date),
          savedView.from,
          savedView.to,
        ) ?? nextRange
      setPreset('all')
      setIsRangeDetailsOpen(true)
    } else {
      setPreset(nextPreset)
    }
    setRange(nextRange)

    if (savedView.tool) {
      setSelectedTool(savedView.tool)
      const knownModel = savedView.model
        ? savedView.model !== 'Legacy unknown' &&
          selectModelSeries(payload.daily, savedView.tool).fullModels.some(
            (model) => model.model === savedView.model,
          )
        : false
      setPinnedModel(knownModel ? savedView.model : null)
    }
    setIsViewHydrated(true)
  }, [isViewHydrated, payload])

  useEffect(() => {
    const wasOpen = wasModelListOpen.current
    wasModelListOpen.current = isModelListOpen
    if (wasOpen === isModelListOpen) return

    const focusTarget = isModelListOpen
      ? modelDetailsPanelRef.current
      : modelDetailsToggleRef.current
    if (!focusTarget) return

    window.requestAnimationFrame(() => {
      focusTarget.focus({ preventScroll: true })
    })
  }, [isModelListOpen])

  useEffect(() => {
    if (!runComplete) return
    window.requestAnimationFrame(() => {
      const result = runResultRef.current
      if (!result) return
      const reduceMotion = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      ).matches
      result.scrollIntoView({
        behavior: reduceMotion ? 'auto' : 'smooth',
        block: 'center',
      })
      result.focus({ preventScroll: true })
    })
  }, [runComplete])

  const daily = useMemo(() => payload?.daily ?? [], [payload])
  const visible = useMemo(() => {
    if (!daily.length || range[1] < range[0]) return [] as DailyRow[]
    return daily.slice(range[0], range[1] + 1)
  }, [daily, range])

  const summary = useMemo(() => summarizeRange(visible), [visible])
  const lifetime = useMemo(() => summarizeLifetime(daily), [daily])
  const lifetimePeak = useMemo(
    () =>
      daily.reduce<DailyRow | null>(
        (peak, row) =>
          !peak || row.total_tokens > peak.total_tokens ? row : peak,
        null,
      ),
    [daily],
  )
  const lifetimeToolCount = useMemo(
    () =>
      summarizeRange(daily).byTool.filter((tool) => tool.tokens > 0).length,
    [daily],
  )
  const activeTool = selectedTool
    ? TOOLS.find((tool) => tool.id === selectedTool) ?? null
    : null
  const activeToolSummary = selectedTool
    ? summary.byTool.find((tool) => tool.id === selectedTool) ?? null
    : null
  const modelSelection = useMemo(
    () =>
      selectedTool
        ? selectModelSeries(visible, selectedTool, pinnedModel)
        : null,
    [pinnedModel, selectedTool, visible],
  )
  const pinnedModelUsage = pinnedModel
    ? modelSelection?.fullModels.find((model) => model.model === pinnedModel)
    : undefined
  const modelShareTenths = useMemo(() => {
    const models = modelSelection?.fullModels ?? []
    const shares = percentageTenths(models.map((model) => model.tokens))
    return new Map(
      models.map((model, index) => [model.model, shares[index]] as const),
    )
  }, [modelSelection])

  const guidedStep = deriveGuidedRunStep({
    guidedActive,
    runComplete,
    selectedTool,
    focusedModel: pinnedModel,
  })
  const focusedRunSeries = useMemo(
    () =>
      selectedTool && pinnedModel
        ? modelSelection?.series.find(
            (series) =>
              series.kind === 'model' &&
              series.models.length === 1 &&
              series.models[0] === pinnedModel,
          ) ?? null
        : null,
    [modelSelection, pinnedModel, selectedTool],
  )
  const runMetrics = useMemo(() => {
    if (!activeTool || !selectedTool || !pinnedModel || !focusedRunSeries) {
      return null
    }
    const points = visible.map((row) =>
      modelSeriesPoint(row, selectedTool, focusedRunSeries),
    )
    const dates = visible.map((row) => row.date)
    const peak = findRunRecord(
      points.map((point) => point.tokens),
      dates,
    )
    return {
      scope: `${activeTool.label} / ${pinnedModel}`,
      range: visible.length
        ? `${visible[0].date} — ${visible.at(-1)?.date ?? '—'}`
        : '—',
      totalTokens: points.reduce((total, point) => total + point.tokens, 0),
      estimatedCost: points.reduce((total, point) => total + point.cost, 0),
      peak,
    }
  }, [activeTool, focusedRunSeries, pinnedModel, selectedTool, visible])

  const dateRangeValue: DateRange | null = useMemo(() => {
    if (!visible.length) return null
    return {
      start: visible[0].date as ISODateString,
      end: visible[visible.length - 1].date as ISODateString,
    }
  }, [visible])

  useEffect(() => {
    if (!payload || !isViewHydrated || !visible.length) return
    const isFullRange = range[0] === 0 && range[1] === daily.length - 1
    const isCustomRange = preset === 'all' && !isFullRange
    const nextSearch = buildReportViewSearch(window.location.search, {
      tool: selectedTool,
      model: selectedTool ? pinnedModel : null,
      preset: isCustomRange ? null : preset,
      from: isCustomRange ? visible[0].date : null,
      to: isCustomRange ? visible[visible.length - 1].date : null,
    })
    const nextUrl = `${window.location.pathname}${nextSearch}${window.location.hash}`
    window.history.replaceState(window.history.state, '', nextUrl)
  }, [
    daily.length,
    isViewHydrated,
    payload,
    pinnedModel,
    preset,
    range,
    selectedTool,
    visible,
  ])

  const applyPreset = useCallback(
    (next: ViewPreset) => {
      setRunComplete(false)
      setPreset(next)
      setRange(indexForPreset(daily, next))
      setIsRangeDetailsOpen(false)
    },
    [daily],
  )

  const nudge = useCallback(
    (dir: -1 | 1) => {
      if (!daily.length || range[1] < range[0]) return
      setRunComplete(false)
      const width = range[1] - range[0]
      let i0 = range[0] + dir
      let i1 = range[1] + dir
      if (i0 < 0) {
        i0 = 0
        i1 = width
      }
      if (i1 > daily.length - 1) {
        i1 = daily.length - 1
        i0 = Math.max(0, i1 - width)
      }
      setRange([i0, i1])
      setPreset('all')
    },
    [daily, range],
  )

  const onDatesChange = useCallback(
    (value: DateRange | null) => {
      if (!value?.start || !value?.end || !daily.length) return
      setRunComplete(false)
      let i0 = daily.findIndex((r) => r.date >= value.start)
      let i1 = -1
      for (let i = daily.length - 1; i >= 0; i -= 1) {
        if (daily[i].date <= value.end) {
          i1 = i
          break
        }
      }
      if (i0 < 0) i0 = 0
      if (i1 < 0) i1 = daily.length - 1
      if (i1 < i0) [i0, i1] = [i1, i0]
      setRange([i0, i1])
      setPreset('all')
    },
    [daily],
  )

  const focusChartSection = useCallback((scrollToChart = false) => {
    window.requestAnimationFrame(() => {
      const chartSection = chartSectionRef.current
      if (!chartSection) return
      if (scrollToChart) {
        const reduceMotion = window.matchMedia(
          '(prefers-reduced-motion: reduce)',
        ).matches
        chartSection.scrollIntoView({
          behavior: reduceMotion ? 'auto' : 'smooth',
          block: 'start',
        })
      }
      chartSection.focus({ preventScroll: true })
    })
  }, [])

  const focusGuidedRun = useCallback(() => {
    window.requestAnimationFrame(() => {
      const guide = guidedRunRef.current
      if (!guide) return
      const reduceMotion = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      ).matches
      guide.scrollIntoView({
        behavior: reduceMotion ? 'auto' : 'smooth',
        block: 'center',
      })
      guide.focus({ preventScroll: true })
    })
  }, [])

  const focusLoadout = useCallback(() => {
    window.requestAnimationFrame(() => {
      const loadout = loadoutStationRef.current
      if (!loadout) return
      const reduceMotion = window.matchMedia(
        '(prefers-reduced-motion: reduce)',
      ).matches
      loadout.scrollIntoView({
        behavior: reduceMotion ? 'auto' : 'smooth',
        block: 'center',
      })
      loadout.focus({ preventScroll: true })
    })
  }, [])

  const selectTool = useCallback(
    (toolId: ToolId, scrollToChart = false) => {
      setRunComplete(false)
      setSelectedTool(toolId)
      setPinnedModel(null)
      setIsModelListOpen(false)
      focusChartSection(scrollToChart)
    },
    [focusChartSection],
  )

  const returnToTools = useCallback(() => {
    setRunComplete(false)
    setSelectedTool(null)
    setPinnedModel(null)
    setIsModelListOpen(false)
    focusChartSection()
  }, [focusChartSection])

  const clearModelFocus = useCallback(() => {
    setRunComplete(false)
    setPinnedModel(null)
    focusChartSection()
  }, [focusChartSection])

  const stepBackInChart = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      if (event.key !== 'Escape') return
      const action = explorationBackAction({
        detailsOpen: isModelListOpen,
        modelFocused: Boolean(pinnedModel),
        toolSelected: Boolean(selectedTool),
      })
      if (!action) return
      event.preventDefault()
      event.stopPropagation()
      if (action === 'close-details') {
        setIsModelListOpen(false)
      } else if (action === 'clear-model') {
        clearModelFocus()
      } else {
        returnToTools()
      }
    },
    [
      clearModelFocus,
      isModelListOpen,
      pinnedModel,
      returnToTools,
      selectedTool,
    ],
  )

  const toggleModelFocus = useCallback(
    (model: string) => {
      setRunComplete(false)
      if (pinnedModel === model) clearModelFocus()
      else setPinnedModel(model)
      setIsModelListOpen(false)
    },
    [clearModelFocus, pinnedModel],
  )

  const retryLoad = useCallback(() => {
    setRunComplete(false)
    setError(null)
    setPayload(null)
    setIsViewHydrated(false)
    setLoadAttempt((attempt) => attempt + 1)
  }, [])

  const moveSeriesFocus = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const buttons = Array.from(
        event.currentTarget.querySelectorAll<HTMLButtonElement>(
          ':scope > button:not(:disabled)',
        ),
      )
      const currentIndex = buttons.indexOf(
        document.activeElement as HTMLButtonElement,
      )
      const nextIndex = nextSeriesIndex(
        event.key,
        currentIndex,
        buttons.length,
      )
      if (nextIndex === null) return
      event.preventDefault()
      buttons[nextIndex]?.focus()
    },
    [],
  )

  const startGuidedRun = useCallback(() => {
    setRunComplete(false)
    setGuidedActive(true)
    focusGuidedRun()
  }, [focusGuidedRun])

  const freeRoam = useCallback(() => {
    setRunComplete(false)
    setGuidedActive(false)
  }, [])

  const replayRun = useCallback(() => {
    setRunComplete(false)
    setSelectedTool(null)
    setPinnedModel(null)
    setIsModelListOpen(false)
    setGuidedActive(true)
    focusGuidedRun()
  }, [focusGuidedRun])

  const openModelGate = useCallback(() => {
    if (!selectedTool) return
    setIsModelListOpen(true)
    focusChartSection(true)
  }, [focusChartSection, selectedTool])

  const revealRunRecord = useCallback(() => {
    if (!selectedTool || !pinnedModel) return
    setRunComplete(true)
  }, [pinnedModel, selectedTool])

  const guidedCtaLabel =
    guidedStep === 'choose-tool'
      ? 'Go to loadout'
      : guidedStep === 'choose-model'
        ? 'Open model gate'
        : guidedStep === 'reveal-record'
          ? 'Reveal run record'
          : guidedStep === 'complete'
            ? 'Run another loadout'
            : null
  const guidedCtaAction =
    guidedStep === 'choose-tool'
      ? focusLoadout
      : guidedStep === 'choose-model'
        ? openModelGate
        : guidedStep === 'reveal-record'
          ? revealRunRecord
          : guidedStep === 'complete'
            ? replayRun
            : null

  if (error) {
    return (
      <div className="page report-state-page">
        <Card variant="muted" padding={4} className="report-state-frame">
          <div
            className="report-state-card"
            role="alert"
            aria-live="assertive"
          >
            <Badge label="Data unavailable" variant="neutral" />
            <p className="state-kicker">AI USAGE / ERROR</p>
            <Heading level={1}>Usage data could not be loaded</Heading>
            <Text color="secondary">{error}</Text>
            <Button
              label="Retry loading usage data"
              variant="primary"
              onClick={retryLoad}
            >
              Retry loading data
            </Button>
          </div>
        </Card>
      </div>
    )
  }

  if (!payload) {
    return (
      <div className="page">
        <div
          className="report-state-card chronicle-loading-state"
          role="status"
          aria-live="polite"
          aria-busy="true"
        >
          <span className="visually-hidden">
            Loading the latest published usage data.
          </span>
          <p className="state-kicker">AI USAGE / LOADING</p>
          <div className="loading-meta skeleton-block" aria-hidden="true" />
          <div className="loading-hero skeleton-block" aria-hidden="true" />
          <div className="loading-copy skeleton-block" aria-hidden="true" />
          <div className="loading-skyline" aria-hidden="true">
            {Array.from({ length: 36 }, (_, index) => (
              <span
                key={index}
                style={{ height: `${24 + ((index * 37) % 68)}%` }}
              />
            ))}
          </div>
          <div className="loading-legend skeleton-block" aria-hidden="true" />
        </div>
      </div>
    )
  }

  const rangeDescription = describeRange(visible, preset)
  const spanLabel = rangeDescription
    ? `${rangeDescription.start} — ${rangeDescription.end} · ${rangeDescription.calendarDays} calendar ${rangeDescription.calendarDays === 1 ? 'day' : 'days'} · ${rangeDescription.recordedDays} recorded ${rangeDescription.recordedDays === 1 ? 'day' : 'days'}`
    : '—'

  const fullSpan =
    payload.timeline_meta?.span ||
    `${daily[0]?.date || '—'} — ${daily.at(-1)?.date || '—'}`
  const degradedSources = degradedSourceNotices(payload.source_status)
  const baseSelectionStatus = activeTool
    ? pinnedModel
      ? `Viewing ${activeTool.label} models. Focused on ${pinnedModel}.`
      : `Viewing ${activeTool.label} models.`
    : 'Viewing all tools.'
  const guidedAnnouncement = guidedActive
    ? runComplete
      ? ' Guided run complete. Recorded run revealed.'
      : guidedStep === 'choose-tool'
        ? ' Guided step: LOADOUT — choose a tool.'
        : guidedStep === 'choose-model'
          ? ' Guided step: MODEL — focus an exact model.'
          : ' Guided step: RECORD — reveal the run record.'
    : ''
  const selectionStatus = `${baseSelectionStatus}${guidedAnnouncement}`
  const guidedStepKey =
    guidedStep === 'complete' || guidedStep === 'free'
        ? null
        : guidedStep
  const guidedStepIndex =
    guidedStep === 'complete'
      ? GUIDED_STEPS.length
      : guidedStepKey
        ? GUIDED_STEPS.findIndex((step) => step.key === guidedStepKey)
        : -1

  return (
    <div
      className="page endless-run-shell"
      data-guided-step={guidedStep}
      style={
        {
          '--route-accent': activeTool?.hex ?? '#ffc84a',
        } as CSSProperties
      }
    >
      <div
        className="selection-status visually-hidden"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {selectionStatus}
      </div>
      <div className="data-world" aria-hidden="true">
        <span data-terrain="archive-grid" />
        <span data-signal="usage-pulse" />
        <span data-cursor="range-position" />
        <span data-save="lifetime-record" />
      </div>
      <VStack gap={6}>
        <header className="chronicle-header">
          <div className="chronicle-topline">
            <div className="save-file-id">
              <span className="save-file-light" aria-hidden="true" />
              <p className="chronicle-kicker">AI USAGE / THE ENDLESS RUN</p>
              <span className="save-file-state">LIVE ARCHIVE</span>
            </div>
            <div className="chronicle-update">
              <span>Updated {compactTimestamp(payload.generated_at)}</span>
              {degradedSources.length ? (
                <button
                  type="button"
                  className="source-health-notice"
                  aria-expanded={isReportDetailsOpen}
                  aria-controls="report-details-panel"
                  onClick={() => setIsReportDetailsOpen(true)}
                >
                  <span className="source-health-marker" aria-hidden="true" />
                  {degradedSources.length}{' '}
                  {degradedSources.length === 1 ? 'source' : 'sources'} delayed
                </button>
              ) : null}
              <button
                type="button"
                className="report-details-trigger"
                aria-expanded={isReportDetailsOpen}
                aria-controls="report-details-panel"
                onClick={() => setIsReportDetailsOpen((open) => !open)}
              >
                {isReportDetailsOpen ? 'Hide details' : 'Report details'}
              </button>
            </div>
          </div>

          <div className="lifetime-hero">
            <p className="hero-metric-label">AI USAGE</p>
            <Heading level={1} type="display-2">
              {fmtHeroTokens(lifetime.recordedTokens)}
            </Heading>
            <p className="hero-label">TOTAL RECORDED TOKENS</p>
            <p className="hero-span">
              {lifetime.firstDate || '—'} — {lifetime.lastDate || '—'}
              <span aria-hidden="true"> · </span>
              <span>{lifetime.recordedDays} recorded days</span>
            </p>
            <p className="hero-disclosure">
              <strong>{fmtCompact(lifetime.cacheTokens)}</strong> cache-reused ·{' '}
              <strong>{fmtRatio(lifetime.cacheRatio)}</strong> of recorded
              traffic. Usage history, not a measure of output or productivity.
            </p>
            <button
              type="button"
              className="start-run-link"
              onClick={startGuidedRun}
            >
              <span className="command-cursor" aria-hidden="true">&gt;</span>
              Start guided run
            </button>
          </div>

          {isReportDetailsOpen ? (
            <div id="report-details-panel" className="report-details-panel">
              <dl className="report-meta">
                <div>
                  <dt>Generated</dt>
                  <dd>{payload.generated_at || '—'}</dd>
                </div>
                <div>
                  <dt>Timezone</dt>
                  <dd>{payload.timezone || '—'}</dd>
                </div>
                <div>
                  <dt>Full span</dt>
                  <dd>{fullSpan}</dd>
                </div>
                {payload.machines?.length ? (
                  <div>
                    <dt>Machines</dt>
                    <dd>{payload.machines.join(', ')}</dd>
                  </div>
                ) : null}
              </dl>
              {degradedSources.length ? (
                <div className="report-methodology">
                  <strong>Source freshness</strong>
                  <p>
                    {degradedSources
                      .map((source) => source.message)
                      .join(' ')}
                  </p>
                </div>
              ) : null}
              <div className="report-methodology">
                <strong>Token breakdown</strong>
                <p>
                  {payload.notes?.token_breakdown ||
                    'Cards and tooltips show input, cache, and output tokens per tool.'}
                </p>
              </div>
              <div className="report-methodology">
                <strong>Cost estimate</strong>
                <p>
                  {payload.notes?.cost ||
                    'Codex/Claude from ccusage; Cursor from Dashboard API.'}
                </p>
              </div>
            </div>
          ) : null}
        </header>

        <section
          id="endless-run"
          className="skyline-section game-stage"
          aria-labelledby="skyline-heading"
        >
          <div className="section-heading">
            <div>
              <p className="section-kicker">USAGE HISTORY</p>
              <Heading level={2} id="skyline-heading">
                THE ENDLESS RUN
              </Heading>
            </div>
            <p>Daily recorded tokens across the selected archive. Choose a tool to inspect.</p>
          </div>
          <div className="skyline-desktop">
            <UsageSkyline
              daily={daily}
              selectedTool={selectedTool}
              onSelectTool={(toolId) => selectTool(toolId, true)}
            />
          </div>
          <div className="skyline-compact">
            <UsageSkyline
              daily={daily}
              selectedTool={selectedTool}
              compact
              onSelectTool={(toolId) => selectTool(toolId, true)}
            />
          </div>
        </section>

        <section
          id="explore"
          className="explore-section"
          aria-labelledby="explore-heading"
        >
          <div className="explore-heading">
            <div>
              <p className="section-kicker">PERSONAL ARCHIVE</p>
              <Heading level={2} id="explore-heading">
                Explore recorded usage
              </Heading>
              <Text color="secondary">
                Select a tool, inspect its model gate, and optionally reveal a run record.
              </Text>
            </div>
            <div className="explore-range-summary" aria-live="polite">
              <span>{spanLabel}</span>
              <strong>{fmtCompact(summary.tokens)} recorded tokens</strong>
            </div>
          </div>

          {guidedActive ? (
            <aside
              id="guided-run"
              ref={guidedRunRef}
              className="checkpoint-log guided-run-strip"
              aria-labelledby="guided-run-heading"
              tabIndex={-1}
            >
              <div>
                <p className="section-kicker">OPTIONAL GUIDE</p>
                <Heading level={3} id="guided-run-heading">
                  Guided run
                </Heading>
              </div>
              <ol className="guided-run-steps" aria-label="Guided run steps">
                {GUIDED_STEPS.map((step, index) => (
                  <li
                    key={step.key}
                    aria-current={
                      guidedStepKey === step.key ? 'step' : undefined
                    }
                    data-step-state={
                      guidedStepKey === step.key
                        ? 'current'
                        : index < guidedStepIndex
                          ? 'complete'
                          : 'upcoming'
                    }
                  >
                    <strong>{step.label}</strong>
                    <span>{step.description}</span>
                  </li>
                ))}
              </ol>
              <HStack gap={2}>
                {guidedCtaLabel && guidedCtaAction ? (
                  <Button
                    label={guidedCtaLabel}
                    variant="primary"
                    size="sm"
                    onClick={guidedCtaAction}
                  >
                    {guidedCtaLabel}
                  </Button>
                ) : null}
                <Button
                  label="Free roam"
                  variant="ghost"
                  size="sm"
                  onClick={freeRoam}
                >
                  Free roam
                </Button>
              </HStack>
            </aside>
          ) : null}

          <div
            className="loadout-heading"
            ref={loadoutStationRef}
            tabIndex={-1}
          >
            <div>
              <p className="section-kicker">LOADOUT STATION</p>
              <Heading level={3}>Choose your tool</Heading>
            </div>
            <span className="loadout-status">
              {activeTool ? `${activeTool.label} equipped` : 'All tools ready'}
            </span>
          </div>

          <div className="tool-grid" aria-label="Explore tools">
          {summary.byTool.map((tool) => (
            <Card
              key={tool.id}
              variant={tool.color}
              padding={3}
              className={`loadout-card${selectedTool === tool.id ? ' is-equipped' : ''}`}
            >
              <VStack gap={2}>
                <HStack justify="between" align="center">
                  <Heading level={3}>{tool.label}</Heading>
                  <Badge label={fmtUsd(tool.cost)} variant="neutral" />
                </HStack>
                <Heading level={2}>{fmtCompact(tool.tokens)}</Heading>
                <Text size="sm" color="secondary">
                  recorded tokens in range
                </Text>
                <div className="breakdown-grid">
                  {tool.parts.map((part) => (
                    <Fragment key={`${tool.id}-${part.label}`}>
                      <Text size="sm" color="secondary">
                        {part.label}
                      </Text>
                      <Text size="sm" justify="end">
                        {fmtCompact(part.value)}
                      </Text>
                    </Fragment>
                  ))}
                </div>
                {tool.models.length ? (
                  <div className="tool-card-action">
                    <Text size="sm" color="secondary" weight="semibold">
                      {
                        tool.models.filter(
                          (model) => model.model !== 'Legacy unknown',
                        ).length
                      }{' '}
                      models
                    </Text>
                    <Button
                      label={`${selectedTool === tool.id ? 'Viewing' : 'View'} ${tool.label} models`}
                      variant="ghost"
                      size="sm"
                      className={`model-drill-trigger${selectedTool === tool.id ? ' is-active' : ''}`}
                      aria-pressed={selectedTool === tool.id}
                      data-active={selectedTool === tool.id ? 'true' : 'false'}
                      style={{ '--tool-color': tool.hex } as CSSProperties}
                      onClick={() => selectTool(tool.id, true)}
                      icon={
                        selectedTool === tool.id ? (
                          <span
                            className="model-drill-state-icon"
                            aria-hidden="true"
                          >
                            ✓
                          </span>
                        ) : undefined
                      }
                      endContent={
                        selectedTool === tool.id ? undefined : (
                          <span
                            className="model-drill-arrow"
                            aria-hidden="true"
                          >
                            ›
                          </span>
                        )
                      }
                    >
                      {selectedTool === tool.id ? 'Equipped' : 'Equip'}
                    </Button>
                  </div>
                ) : null}
              </VStack>
            </Card>
          ))}
          </div>

          <div className="explore-time-controls">
          <Heading level={3}>Run window</Heading>
          <div className="controls-row">
            <SegmentedControl
              label="Range preset"
              value={preset}
              onChange={(v) => applyPreset(v as ViewPreset)}
              size="md"
            >
              <SegmentedControlItem value="7" label="7 days" />
              <SegmentedControlItem value="30" label="30 days" />
              <SegmentedControlItem value="90" label="90 days" />
              <SegmentedControlItem value="all" label="All" />
            </SegmentedControl>

            <Button
              label={
                isRangeDetailsOpen
                  ? 'Hide custom date controls'
                  : 'Show custom date controls'
              }
              variant="ghost"
              size="sm"
              className="range-details-trigger"
              aria-expanded={isRangeDetailsOpen}
              aria-controls="range-details-panel"
              onClick={() => setIsRangeDetailsOpen((open) => !open)}
            >
              {isRangeDetailsOpen ? 'Hide custom dates' : 'Custom dates'}
            </Button>
          </div>
          {isRangeDetailsOpen ? (
            <div id="range-details-panel" className="range-details-panel">
              <DateRangeInput
                label="Dates"
                value={dateRangeValue}
                onChange={onDatesChange}
                min={daily[0]?.date as ISODateString | undefined}
                max={daily.at(-1)?.date as ISODateString | undefined}
                size="md"
                numberOfMonths={1}
              />

              <HStack gap={2} align="end">
                <IconButton
                  label="Earlier"
                  icon={<Icon icon="chevronLeft" />}
                  isDisabled={range[0] <= 0}
                  onClick={() => nudge(-1)}
                />
                <IconButton
                  label="Later"
                  icon={<Icon icon="chevronRight" />}
                  isDisabled={range[1] >= daily.length - 1}
                  onClick={() => nudge(1)}
                />
                <Button
                  label="Reset all time"
                  variant="secondary"
                  size="md"
                  isDisabled={
                    preset === 'all' &&
                    range[0] === 0 &&
                    range[1] === daily.length - 1
                  }
                  onClick={() => applyPreset('all')}
                />
              </HStack>
            </div>
          ) : null}
          </div>

          <div className="model-gate-heading">
            <div>
              <p className="section-kicker">MODEL GATE</p>
              <Heading level={3}>
                {activeTool ? `${activeTool.label} models` : 'All tools'}
              </Heading>
            </div>
            <span className="gate-state">
              {pinnedModel ? `Focused · ${pinnedModel}` : 'Select an exact model'}
            </span>
          </div>

          <Card padding={3} className="exact-ledger-card">
          {daily.length ? (
            <div
              id="usage-explorer-chart"
              className="chart-panel"
              ref={chartSectionRef}
              role="region"
              aria-label="Usage chart exploration"
              aria-keyshortcuts="Escape"
              tabIndex={-1}
              onKeyDown={stepBackInChart}
            >
              <div className="chart-heading">
                <div className="chart-title-block">
                  {activeTool ? (
                    <div className="chart-breadcrumb">
                      <button
                        type="button"
                        aria-label="Back to all tools"
                        onClick={returnToTools}
                      >
                        <span aria-hidden="true">←</span> All tools
                      </button>
                      <span aria-hidden="true">/</span>
                      <span aria-current="page">{activeTool.label}</span>
                    </div>
                  ) : null}
                  <div className="chart-title-row">
                    <Heading level={3}>
                      {activeTool
                        ? `${activeTool.label} models`
                        : 'Run history'}
                    </Heading>
                    {activeTool && pinnedModel ? (
                      <div className="chart-focus-state">
                        <span>
                          Focused · <strong>{pinnedModel}</strong>
                          {!pinnedModelUsage ? ' · No usage in range' : ''}
                        </span>
                        <button type="button" onClick={clearModelFocus}>
                          Clear
                        </button>
                      </div>
                    ) : null}
                  </div>
                  {!activeTool ? (
                    <Text size="sm" color="secondary">
                      Equip a tool to open its model gate.
                    </Text>
                  ) : !pinnedModel ? (
                    <Text size="sm" color="secondary">
                      {spanLabel}
                    </Text>
                  ) : null}
                </div>

                {activeTool && modelSelection ? (
                  <div className="chart-toolbar">
                    <Button
                      label={
                        isModelListOpen
                          ? 'Hide model details'
                          : `Show all ${modelSelection.modelCount} ${activeTool.label} models`
                      }
                      variant="secondary"
                      size="sm"
                      ref={modelDetailsToggleRef}
                      aria-expanded={isModelListOpen}
                      aria-controls="model-details-panel"
                      onClick={() => setIsModelListOpen((open) => !open)}
                    >
                      {isModelListOpen
                        ? 'Hide model details'
                        : `All ${modelSelection.modelCount} models`}
                    </Button>
                  </div>
                ) : null}
              </div>

              <div
                className={`chart-series-key ${
                  activeTool ? 'is-model' : 'is-tool'
                }`}
                role="group"
                aria-label={
                  activeTool
                    ? `${activeTool.label} model series`
                    : 'Select a tool'
                }
                aria-describedby="series-keyboard-help"
                onKeyDown={moveSeriesFocus}
              >
                <span id="series-keyboard-help" className="visually-hidden">
                  Use Left and Right Arrow to move between series. Use Home and
                  End to jump to the first or last series. Use Escape to close
                  details, clear model focus, or return to all tools.
                </span>
                {activeTool && modelSelection
                  ? modelSelection.series.map((series, index) => {
                      const canFocus =
                        series.kind === 'model' && series.models.length === 1
                      const isFocused =
                        canFocus && pinnedModel === series.models[0]
                      return (
                        <button
                          type="button"
                          className="series-key-button"
                          key={series.id}
                          aria-pressed={canFocus ? isFocused : undefined}
                          aria-label={`${modelLabel(series)}, ${fmtCompact(series.tokens)} tokens${
                            canFocus
                              ? isFocused
                                ? ', focused; activate to clear focus'
                                : ', activate to focus'
                              : ', open model details'
                          }`}
                          onClick={() => {
                            if (series.kind === 'other') {
                              setIsModelListOpen(true)
                            } else if (series.kind === 'legacy') {
                              setIsModelListOpen(true)
                            } else if (canFocus) {
                              toggleModelFocus(series.models[0])
                            }
                          }}
                        >
                          <span
                            className="series-swatch"
                            style={{
                              backgroundColor: modelSeriesColor(
                                activeTool.hex,
                                index,
                                series.kind,
                              ),
                            }}
                            aria-hidden="true"
                          />
                          <span>{modelLabel(series)}</span>
                        </button>
                      )
                    })
                  : summary.byTool.map((tool) => (
                      <button
                        type="button"
                        className="series-key-button"
                        key={tool.id}
                        aria-label={`View ${tool.label} models, ${fmtCompact(tool.tokens)} tokens`}
                        onClick={() => selectTool(tool.id)}
                      >
                        <span
                          className="series-swatch"
                          style={{ backgroundColor: tool.hex }}
                          aria-hidden="true"
                        />
                        <span>{tool.label}</span>
                      </button>
                    ))}
              </div>

              <UsageCharts
                daily={visible}
                selectedTool={selectedTool}
                focusedModel={pinnedModel}
                modelSelection={modelSelection}
                onSelectTool={(toolId) => selectTool(toolId)}
                onOpenModelList={() => setIsModelListOpen(true)}
              />

              {activeTool &&
              activeToolSummary &&
              modelSelection &&
              isModelListOpen ? (
                <div
                  id="model-details-panel"
                  className="model-detail-panel"
                  role="region"
                  aria-live="polite"
                  aria-labelledby="model-details-heading"
                  ref={modelDetailsPanelRef}
                  tabIndex={-1}
                >
                  <div className="model-detail-header">
                    <div>
                      <Heading level={4} id="model-details-heading">
                        {activeTool.label} model details
                      </Heading>
                      <Text size="sm" color="secondary">
                        Exact totals for {spanLabel}. Select an identified model
                        to keep it visible in the chart.
                      </Text>
                    </div>
                    <Button
                      label="Close model details"
                      variant="ghost"
                      size="sm"
                      onClick={() => setIsModelListOpen(false)}
                    >
                      Close
                    </Button>
                  </div>

                  <div className="model-detail-columns" aria-hidden="true">
                    <span>Model</span>
                    <span>Tokens</span>
                    <span>Share</span>
                    <span>Spend</span>
                  </div>
                  <div className="model-detail-list">
                    {modelSelection.fullModels.map((model) => {
                      const isLegacy = model.model === 'Legacy unknown'
                      const share = (
                        (modelShareTenths.get(model.model) ?? 0) / 10
                      ).toFixed(1)
                      const isFocused = pinnedModel === model.model
                      return (
                        <button
                          type="button"
                          className="model-detail-row"
                          key={model.model}
                          aria-pressed={isLegacy ? undefined : isFocused}
                          aria-label={`${isLegacy ? 'Unattributed legacy' : model.model}, ${fmtExactTokens(model.tokens)} tokens, ${share} percent, ${fmtExactUsd(model.cost)} spend${isLegacy ? '' : ', focus in chart'}`}
                          onClick={() => {
                            if (!isLegacy) toggleModelFocus(model.model)
                          }}
                          disabled={isLegacy}
                        >
                          <span className="model-detail-name">
                            {isLegacy
                              ? 'Unattributed (legacy)'
                              : model.model}
                            {!isLegacy && isFocused ? (
                              <span className="model-focused-label">
                                Focused
                              </span>
                            ) : null}
                          </span>
                          <span data-label="Tokens">
                            {fmtExactTokens(model.tokens)}
                          </span>
                          <span data-label="Share">{share}%</span>
                          <span data-label="Spend">{fmtExactUsd(model.cost)}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              ) : null}
            </div>
          ) : (
            <div
              className="chart-empty-state"
              role="status"
              aria-live="polite"
            >
              <EmptyState
                title="No usage data yet"
                description="The report loaded successfully, but it contains no daily rows."
              />
              <Button
                label="Reload usage data"
                variant="secondary"
                onClick={retryLoad}
              >
                Reload data
              </Button>
            </div>
          )}
          </Card>

          {runComplete ? (
            <section
              ref={runResultRef}
              className="checkpoint-log run-complete-card"
              aria-labelledby="run-complete-heading"
              tabIndex={-1}
            >
              <div className="checkpoint-heading">
                <div>
                  <p className="section-kicker">RUN COMPLETE</p>
                  <Heading level={3} id="run-complete-heading">
                    Recorded run
                  </Heading>
                </div>
              </div>
              <dl className="checkpoint-grid">
                <div>
                  <dt>Scope</dt>
                  <dd>{runMetrics?.scope ?? '—'}</dd>
                </div>
                <div>
                  <dt>Range</dt>
                  <dd>{runMetrics?.range ?? '—'}</dd>
                </div>
                <div>
                  <dt>Total recorded tokens</dt>
                  <dd>{fmtExactTokens(runMetrics?.totalTokens ?? 0)}</dd>
                </div>
                <div>
                  <dt>Estimated cost</dt>
                  <dd>{fmtExactUsd(runMetrics?.estimatedCost ?? 0)}</dd>
                </div>
                <div>
                  <dt>Peak exact tokens</dt>
                  <dd>
                    {runMetrics?.peak
                      ? fmtExactTokens(runMetrics.peak.value)
                      : 'NO RECORDED USAGE'}
                  </dd>
                  <span>{runMetrics?.peak?.date ?? '—'}</span>
                </div>
              </dl>
              <HStack gap={2}>
                <Button
                  label="Replay"
                  variant="primary"
                  size="sm"
                  onClick={replayRun}
                >
                  Replay
                </Button>
                <Button
                  label="Free roam"
                  variant="ghost"
                  size="sm"
                  onClick={freeRoam}
                >
                  Free roam
                </Button>
              </HStack>
            </section>
          ) : (
            <section
              className="checkpoint-log"
              aria-labelledby="lifetime-archive-heading"
            >
              <div className="checkpoint-heading">
                <div>
                  <p className="section-kicker">LIFETIME ARCHIVE</p>
                  <Heading level={3} id="lifetime-archive-heading">
                    Lifetime archive
                  </Heading>
                </div>
              </div>
              <dl className="checkpoint-grid">
                <div>
                  <dt>Peak recorded day</dt>
                  <dd>{fmtExactTokens(lifetimePeak?.total_tokens ?? 0)}</dd>
                  <span>{lifetimePeak?.date ?? '—'}</span>
                </div>
                <div>
                  <dt>Days recorded</dt>
                  <dd>{fmtExactTokens(lifetime.recordedDays)}</dd>
                  <span>{lifetime.firstDate ?? '—'} → {lifetime.lastDate ?? '—'}</span>
                </div>
                <div>
                  <dt>Tools recorded</dt>
                  <dd>{lifetimeToolCount}</dd>
                  <span>Real usage across the lifetime data</span>
                </div>
              </dl>
            </section>
          )}

          <footer className="exact-ledger">
            <span className="command-cursor" aria-hidden="true">&gt;</span>
            <div>
              <strong>Exact ledger</strong>
              <span>Tooltips, model details, costs, and token parts retain exact published values.</span>
            </div>
            <a
              href="#endless-run"
              onClick={(event) => {
                event.preventDefault()
                startGuidedRun()
              }}
            >
              Start guided run ↑
            </a>
          </footer>
        </section>
      </VStack>
    </div>
  )
}

export default function App() {
  return (
    <Theme theme={neutralTheme} mode="light">
      <ReportApp />
    </Theme>
  )
}
