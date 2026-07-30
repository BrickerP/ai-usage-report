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
import { modelSeriesColor } from './lib/chart'
import { summarizeLifetime } from './lib/chronicle'
import { fmtCompact, fmtUsd } from './lib/format'
import {
  buildReportViewSearch,
  explorationBackAction,
  indexRangeForDates,
  nextSeriesIndex,
  parseReportView,
  type ViewPreset,
} from './lib/interaction'
import {
  degradedSourceNotices,
  indexForPreset,
  loadUsage,
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

function ReportApp() {
  const [payload, setPayload] = useState<UsagePayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadAttempt, setLoadAttempt] = useState(0)
  const [preset, setPreset] = useState<ViewPreset>('all')
  const [range, setRange] = useState<[number, number]>([0, -1])
  const [selectedTool, setSelectedTool] = useState<ToolId | null>(null)
  const [pinnedModel, setPinnedModel] = useState<string | null>(null)
  const [isModelListOpen, setIsModelListOpen] = useState(false)
  const [isReportDetailsOpen, setIsReportDetailsOpen] = useState(false)
  const [isRangeDetailsOpen, setIsRangeDetailsOpen] = useState(false)
  const [isViewHydrated, setIsViewHydrated] = useState(false)
  const chartSectionRef = useRef<HTMLDivElement>(null)
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

  const daily = useMemo(() => payload?.daily ?? [], [payload])
  const visible = useMemo(() => {
    if (!daily.length || range[1] < range[0]) return [] as DailyRow[]
    return daily.slice(range[0], range[1] + 1)
  }, [daily, range])

  const summary = useMemo(() => summarizeRange(visible), [visible])
  const lifetime = useMemo(() => summarizeLifetime(daily), [daily])
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
      setPreset(next)
      setRange(indexForPreset(daily, next))
      setIsRangeDetailsOpen(false)
    },
    [daily],
  )

  const nudge = useCallback(
    (dir: -1 | 1) => {
      if (!daily.length || range[1] < range[0]) return
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

  const selectTool = useCallback(
    (toolId: ToolId, scrollToChart = false) => {
      setSelectedTool(toolId)
      setPinnedModel(null)
      setIsModelListOpen(false)
      focusChartSection(scrollToChart)
    },
    [focusChartSection],
  )

  const returnToTools = useCallback(() => {
    setSelectedTool(null)
    setPinnedModel(null)
    setIsModelListOpen(false)
    focusChartSection()
  }, [focusChartSection])

  const clearModelFocus = useCallback(() => {
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
      if (pinnedModel === model) clearModelFocus()
      else setPinnedModel(model)
      setIsModelListOpen(false)
    },
    [clearModelFocus, pinnedModel],
  )

  const retryLoad = useCallback(() => {
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

  if (error) {
    return (
      <div className="page report-state-page">
        <Card variant="muted" padding={4}>
          <div
            className="report-state-card"
            role="alert"
            aria-live="assertive"
          >
            <Badge label="Report unavailable" variant="neutral" />
            <Heading level={1}>Could not load usage data</Heading>
            <Text color="secondary">{error}</Text>
            <Button
              label="Retry loading usage data"
              variant="primary"
              onClick={retryLoad}
            >
              Retry
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
            Loading the latest published usage chronicle.
          </span>
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

  const spanLabel =
    visible.length > 0
      ? `${visible[0].date} — ${visible[visible.length - 1].date} · ${visible.length} day(s)`
      : '—'

  const fullSpan =
    payload.timeline_meta?.span ||
    `${daily[0]?.date || '—'} — ${daily.at(-1)?.date || '—'}`
  const degradedSources = degradedSourceNotices(payload.source_status)
  const selectionStatus = activeTool
    ? pinnedModel
      ? `Viewing ${activeTool.label} models. Focused on ${pinnedModel}.`
      : `Viewing ${activeTool.label} models.`
    : 'Viewing all tools.'

  return (
    <div className="page">
      <div
        className="selection-status visually-hidden"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        {selectionStatus}
      </div>
      <VStack gap={6}>
        <header className="chronicle-header">
          <div className="chronicle-topline">
            <p className="chronicle-kicker">BRICKERP / AI USAGE CHRONICLE</p>
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
            <Heading level={1} type="display-2">
              {fmtHeroTokens(lifetime.recordedTokens)}
            </Heading>
            <p className="hero-label">recorded tokens</p>
            <p className="hero-span">
              {lifetime.firstDate || '—'} — {lifetime.lastDate || '—'}
              <span aria-hidden="true"> · </span>
              <span>{lifetime.recordedDays} recorded days</span>
            </p>
            <p className="hero-disclosure">
              Includes {fmtCompact(lifetime.cacheTokens)} cached context (
              {fmtRatio(lifetime.cacheRatio)} of recorded traffic). This is
              usage activity, not a measure of output or productivity.
            </p>
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

        <section className="skyline-section" aria-labelledby="skyline-heading">
          <div className="section-heading">
            <div>
              <p className="section-kicker">ALL TIME</p>
              <Heading level={2} id="skyline-heading">
                <span className="skyline-daily-label">Daily Skyline</span>
                <span className="skyline-weekly-label">Weekly Skyline</span>
              </Heading>
            </div>
            <p>
              <span className="skyline-daily-label">
                Daily recorded token traffic, stacked by tool.
              </span>
              <span className="skyline-weekly-label">
                Natural-week totals on small screens, stacked by tool.
              </span>{' '}
              Select a tool to continue into its model history.
            </p>
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
              <p className="section-kicker">DETAILS</p>
              <Heading level={2} id="explore-heading">
                Explore
              </Heading>
              <Text color="secondary">
                Choose a tool, inspect its models, then focus one series. The
                chart, URL, and accessible status stay in sync.
              </Text>
            </div>
            <div className="explore-range-summary" aria-live="polite">
              <span>{spanLabel}</span>
              <strong>{fmtCompact(summary.tokens)} recorded tokens</strong>
            </div>
          </div>

          <div className="tool-grid" aria-label="Explore tools">
          {summary.byTool.map((tool) => (
            <Card key={tool.id} variant={tool.color} padding={3}>
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
                      {selectedTool === tool.id ? 'Viewing' : 'View models'}
                    </Button>
                  </div>
                ) : null}
              </VStack>
            </Card>
          ))}
          </div>

          <div className="explore-time-controls">
          <Heading level={3}>Time range</Heading>
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

          <Card padding={3}>
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
                        : 'Daily usage'}
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
                      Choose a tool to inspect its models.
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
                      const total = activeToolSummary.tokens
                      const share = total > 0 ? (model.tokens / total) * 100 : 0
                      const isFocused = pinnedModel === model.model
                      return (
                        <button
                          type="button"
                          className="model-detail-row"
                          key={model.model}
                          aria-pressed={isLegacy ? undefined : isFocused}
                          aria-label={`${isLegacy ? 'Unattributed legacy' : model.model}, ${fmtExactTokens(model.tokens)} tokens, ${share.toFixed(1)} percent, ${fmtExactUsd(model.cost)} spend${isLegacy ? '' : ', focus in chart'}`}
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
                          <span data-label="Share">{share.toFixed(1)}%</span>
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
