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
import type { CSSProperties } from 'react'
import { UsageCharts } from './components/UsageCharts'
import { modelSeriesColor } from './lib/chart'
import { fmtCompact, fmtUsd } from './lib/format'
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

type Preset = '7' | '30' | '90' | 'all'

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

function ReportApp() {
  const [payload, setPayload] = useState<UsagePayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [preset, setPreset] = useState<Preset>('30')
  const [range, setRange] = useState<[number, number]>([0, -1])
  const [selectedTool, setSelectedTool] = useState<ToolId | null>(null)
  const [pinnedModel, setPinnedModel] = useState<string | null>(null)
  const [isModelListOpen, setIsModelListOpen] = useState(false)
  const chartSectionRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let cancelled = false
    loadUsage()
      .then((data) => {
        if (cancelled) return
        setPayload(data)
        setRange(indexForPreset(data.daily, '30'))
        setPreset('30')
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message || String(err))
      })
    return () => {
      cancelled = true
    }
  }, [])

  const daily = useMemo(() => payload?.daily ?? [], [payload])
  const visible = useMemo(() => {
    if (!daily.length || range[1] < range[0]) return [] as DailyRow[]
    return daily.slice(range[0], range[1] + 1)
  }, [daily, range])

  const summary = useMemo(() => summarizeRange(visible), [visible])
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

  const applyPreset = useCallback(
    (next: Preset) => {
      setPreset(next)
      setRange(indexForPreset(daily, next))
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

  const selectTool = useCallback((toolId: ToolId, scrollToChart = false) => {
    setSelectedTool(toolId)
    setPinnedModel(null)
    setIsModelListOpen(false)
    if (scrollToChart) {
      window.requestAnimationFrame(() => {
        chartSectionRef.current?.scrollIntoView({
          behavior: 'smooth',
          block: 'start',
        })
      })
    }
  }, [])

  const returnToTools = useCallback(() => {
    setSelectedTool(null)
    setPinnedModel(null)
    setIsModelListOpen(false)
  }, [])

  const toggleModelFocus = useCallback((model: string) => {
    setPinnedModel((current) => (current === model ? null : model))
    setIsModelListOpen(false)
  }, [])

  if (error) {
    return (
      <div className="page">
        <EmptyState title="Could not load usage data" description={error} />
      </div>
    )
  }

  if (!payload) {
    return (
      <div className="page">
        <Text color="secondary">Loading usage report…</Text>
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

  return (
    <div className="page">
      <VStack gap={6}>
        <VStack gap={2}>
          <Badge label="Usage report" variant="neutral" />
          <Heading level={1} type="display-2">
            AI coding spend & tokens
          </Heading>
          <HStack gap={4} wrap="wrap">
            <Text size="sm" color="secondary">
              Generated {payload.generated_at || '—'}
            </Text>
            <Text size="sm" color="secondary">
              Timezone {payload.timezone || '—'}
            </Text>
            <Text size="sm" color="secondary">
              Full span {fullSpan}
            </Text>
            {payload.machines?.length ? (
              <Text size="sm" color="secondary">
                Machines {payload.machines.join(', ')}
              </Text>
            ) : null}
          </HStack>
          {degradedSources.length ? (
            <div
              className="source-health-notice"
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              <span className="source-health-marker" aria-hidden="true">
                !
              </span>
              <span>
                <span className="source-health-title">
                  Some usage sources are degraded.
                </span>{' '}
                {degradedSources.map((source) => source.message).join(' ')}
              </span>
            </div>
          ) : null}
        </VStack>

        <Card variant="muted" padding={4}>
          <VStack gap={2}>
            <Text size="sm" color="secondary">
              All tools combined
            </Text>
            <HStack gap={3} wrap="wrap" align="center">
              <Heading level={2}>{fmtCompact(summary.tokens)}</Heading>
              <Text color="secondary">tokens total</Text>
              <Text color="secondary">·</Text>
              <Heading level={2}>{fmtCompact(summary.cache)}</Heading>
              <Text color="secondary">cache tokens</Text>
              <Text color="secondary">·</Text>
              <Heading level={2}>{fmtUsd(summary.cost)}</Heading>
              <Text color="secondary">spend total</Text>
            </HStack>
            <Text size="sm" color="secondary">
              Totals for visible range: {spanLabel}
            </Text>
          </VStack>
        </Card>

        <Text size="sm" color="secondary">
          <Text weight="semibold">Token breakdown: </Text>
          {payload.notes?.token_breakdown ||
            'Cards and tooltips show input, cache, and output tokens per tool.'}{' '}
          <Text weight="semibold">Cost estimate: </Text>
          {payload.notes?.cost ||
            'Codex/Claude from ccusage; Cursor from Dashboard API.'}
        </Text>

        <div className="tool-grid">
          {summary.byTool.map((tool) => (
            <Card key={tool.id} variant={tool.color} padding={4}>
              <VStack gap={2}>
                <HStack justify="between" align="center">
                  <Heading level={3}>{tool.label}</Heading>
                  <Badge label={fmtUsd(tool.cost)} variant="neutral" />
                </HStack>
                <Heading level={2}>{fmtCompact(tool.tokens)}</Heading>
                <Text size="sm" color="secondary">
                  tokens in visible range
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
                  <div className="model-list">
                    <HStack justify="between" align="center" gap={2}>
                      <Text size="sm" color="secondary" weight="semibold">
                        Models ·{' '}
                        {
                          tool.models.filter(
                            (model) => model.model !== 'Legacy unknown',
                          ).length
                        }
                      </Text>
                      <Button
                        label={`${selectedTool === tool.id ? 'Viewing' : 'Explore'} ${tool.label} models`}
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
                          <span
                            className="model-drill-arrow"
                            aria-hidden="true"
                          >
                            ›
                          </span>
                        }
                      >
                        {selectedTool === tool.id ? 'Viewing' : 'Explore'}
                      </Button>
                    </HStack>
                    {tool.models.map((model) => (
                      <div
                        className="model-row"
                        key={`${tool.id}-${model.model}`}
                      >
                        <Text size="sm">
                          <span className="model-name" title={model.model}>
                            {model.model === 'Legacy unknown'
                              ? 'Unattributed (legacy)'
                              : model.model}
                          </span>
                        </Text>
                        <Text size="sm" justify="end">
                          {fmtCompact(model.tokens)}
                        </Text>
                        <Text size="sm" color="secondary" justify="end">
                          {fmtUsd(model.cost)}
                        </Text>
                      </div>
                    ))}
                  </div>
                ) : null}
              </VStack>
            </Card>
          ))}
        </div>

        <VStack gap={3}>
          <Heading level={3}>Time range</Heading>
          <div className="controls-row">
            <SegmentedControl
              label="Range preset"
              value={preset}
              onChange={(v) => applyPreset(v as Preset)}
              size="md"
            >
              <SegmentedControlItem value="7" label="7 days" />
              <SegmentedControlItem value="30" label="30 days" />
              <SegmentedControlItem value="90" label="90 days" />
              <SegmentedControlItem value="all" label="All" />
            </SegmentedControl>

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
                label="Reset 30d"
                variant="secondary"
                size="md"
                onClick={() => applyPreset('30')}
              />
            </HStack>
          </div>
        </VStack>

        <Card padding={3}>
          {daily.length ? (
            <div className="chart-panel" ref={chartSectionRef}>
              <div className="chart-heading">
                <div>
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
                  <Heading level={3}>
                    {activeTool
                      ? `${activeTool.label} models over time`
                      : 'Daily tokens by tool'}
                  </Heading>
                  <Text size="sm" color="secondary">
                    {activeTool
                      ? `Models and spend for ${spanLabel}.`
                      : 'Select a tool below to view its model mix. Spend follows the current view.'}
                  </Text>
                </div>

                {activeTool && modelSelection ? (
                  <div className="chart-toolbar">
                    <Badge
                      label={`${modelSelection.modelCount} identified model${modelSelection.modelCount === 1 ? '' : 's'}`}
                      variant="neutral"
                    />
                    {modelSelection.hasLegacy ? (
                      <Badge label="+ unattributed" variant="neutral" />
                    ) : null}
                    <Button
                      label={
                        isModelListOpen
                          ? 'Hide model details'
                          : `Show all ${modelSelection.modelCount} ${activeTool.label} models`
                      }
                      variant="secondary"
                      size="sm"
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
                aria-label={
                  activeTool
                    ? `${activeTool.label} model series`
                    : 'Select a tool'
                }
              >
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
                          <span className="series-key-value">
                            {fmtCompact(series.tokens)}
                          </span>
                        </button>
                      )
                    })
                  : summary.byTool.map((tool) => (
                      <button
                        type="button"
                        className="series-key-button"
                        key={tool.id}
                        onClick={() => selectTool(tool.id)}
                      >
                        <span
                          className="series-swatch"
                          style={{ backgroundColor: tool.hex }}
                          aria-hidden="true"
                        />
                        <span>{tool.label}</span>
                        <span className="series-key-value">
                          {fmtCompact(tool.tokens)}
                        </span>
                        <span aria-hidden="true">›</span>
                      </button>
                    ))}
              </div>

              {activeTool && pinnedModel ? (
                <div className="model-focus-note" role="status">
                  <span>
                    Focused: <strong>{pinnedModel}</strong>
                    {!pinnedModelUsage ? ' · No usage in this range' : ''}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPinnedModel(null)}
                  >
                    Clear focus
                  </button>
                </div>
              ) : null}

              <UsageCharts
                daily={visible}
                selectedTool={selectedTool}
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
                  aria-live="polite"
                >
                  <div className="model-detail-header">
                    <div>
                      <Heading level={4}>
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
            <EmptyState
              title="No daily rows"
              description="Run npm run collect to refresh public/usage.json"
            />
          )}
        </Card>
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
