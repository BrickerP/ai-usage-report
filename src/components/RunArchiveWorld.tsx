import { useId, useMemo } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'
import type { RunLevel, RunLevelPoint } from '../lib/runLevel'

type Props = {
  level: RunLevel
  selectedDay: string | null
  onSelectedDayChange: (day: string) => void
  scopeLabel: string
}

type WorldPoint = RunLevelPoint & {
  x: number
  groundHeight: number
  groundY: number
  skyHeight: number
  skyY: number
}

const VIEWBOX_WIDTH = 960
const VIEWBOX_HEIGHT = 280
const WORLD_PADDING = 72
const GROUND_BASELINE = 224
const GROUND_BAND_HEIGHT = 86
const SKY_BASELINE = 102
const SKY_BAND_HEIGHT = 64
const RUNNER_LOCK_X = VIEWBOX_WIDTH * 0.32
const DAY_MS = 86_400_000

const exactInteger = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 0,
})

const readableDate = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: '2-digit',
  year: 'numeric',
  timeZone: 'UTC',
})

function formatDate(date: string): string {
  const parsed = new Date(`${date}T00:00:00Z`)
  return Number.isNaN(parsed.valueOf())
    ? date
    : readableDate.format(parsed).toUpperCase()
}

function formatTokens(value: number | null): string {
  return value === null ? 'UNAVAILABLE' : exactInteger.format(value)
}

function formatCost(value: number | null): string {
  if (value === null) return 'UNAVAILABLE'
  const digits = Math.abs(value) > 0 && Math.abs(value) < 0.01 ? 4 : 2
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

function pointStateLabel(state: RunLevelPoint['state']): string {
  if (state === 'active') return 'ACTIVE DATE'
  if (state === 'zero') return 'QUIET DATE'
  return 'UNAVAILABLE'
}

function pointStateAnnouncement(state: RunLevelPoint['state']): string {
  if (state === 'active') return 'active'
  if (state === 'zero') return 'quiet'
  return 'unavailable'
}

function signatureSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-')
}

function dateOrdinal(value: string): number | null {
  const ordinal = Date.parse(`${value}T00:00:00Z`)
  return Number.isNaN(ordinal) ? null : ordinal
}

function landmarkNames(level: RunLevel, pointIndex: number): string[] {
  const names: string[] = []
  if (level.landmarks.firstSeen?.index === pointIndex) names.push('First Seen')
  if (level.landmarks.record?.index === pointIndex) names.push('Model Record')
  if (level.landmarks.lastSeen?.index === pointIndex) names.push('Last Seen')
  if (level.landmarks.archiveEdge?.index === pointIndex) names.push('Archive Edge')
  return names
}

function maxPointValue(
  points: ReadonlyArray<RunLevelPoint>,
  key: 'groundTokens' | 'skyTokens',
): number {
  return points.reduce((maximum, point) => {
    const value = point[key]
    return value === null ? maximum : Math.max(maximum, value)
  }, 0)
}

function nearestPointIndex(points: ReadonlyArray<WorldPoint>, worldX: number): number {
  if (!points.length) return -1
  let nearestIndex = 0
  let nearestDistance = Math.abs(points[0].x - worldX)

  for (let index = 1; index < points.length; index += 1) {
    const distance = Math.abs(points[index].x - worldX)
    if (distance < nearestDistance) {
      nearestIndex = index
      nearestDistance = distance
    }
  }

  return nearestIndex
}

export function RunArchiveWorld({
  level,
  selectedDay,
  onSelectedDayChange,
  scopeLabel,
}: Props) {
  const id = useId().replace(/:/g, '')
  const headingId = `run-archive-heading-${id}`
  const descriptionId = `run-archive-description-${id}`
  const sliderId = `run-archive-slider-${id}`
  const sliderHintId = `run-archive-slider-hint-${id}`
  const unavailablePatternId = `run-archive-unavailable-${id}`
  const requestedDay =
    selectedDay && level.points.some((point) => point.date === selectedDay)
      ? selectedDay
      : level.defaultDay
  const requestedIndex = level.points.findIndex(
    (point) => point.date === requestedDay,
  )
  const selectedIndex = Math.max(0, requestedIndex)
  const selectedPoint = level.points[selectedIndex] ?? null
  const selectedLandmarks = selectedPoint
    ? landmarkNames(level, selectedPoint.index)
    : []
  const signatureClass = signatureSlug(level.signature.name)

  const world = useMemo(() => {
    const pointCount = level.points.length
    const firstOrdinal = pointCount ? dateOrdinal(level.points[0].date) : null
    const calendarOffsets = level.points.map((point, index) => {
      const ordinal = dateOrdinal(point.date)
      return ordinal === null || firstOrdinal === null
        ? index
        : Math.max(0, Math.round((ordinal - firstOrdinal) / DAY_MS))
    })
    const maximumOffset = calendarOffsets[calendarOffsets.length - 1] ?? 0
    const dayStep = Math.max(
      28,
      (VIEWBOX_WIDTH - WORLD_PADDING * 2) / Math.max(1, maximumOffset),
    )
    const width = Math.max(
      VIEWBOX_WIDTH,
      WORLD_PADDING * 2 + maximumOffset * dayStep,
    )
    const groundMaximum = Math.max(
      1,
      level.split.enabled ? level.split.floor : 0,
      maxPointValue(level.points, 'groundTokens'),
    )
    const skyMaximum = Math.max(
      1,
      level.split.skyMax,
      maxPointValue(level.points, 'skyTokens'),
    )
    const points: WorldPoint[] = level.points.map((point, index) => {
      const groundValue = point.groundTokens ?? 0
      const skyValue = point.skyTokens ?? 0
      const groundHeight =
        point.state === 'active'
          ? Math.max(4, (groundValue / groundMaximum) * GROUND_BAND_HEIGHT)
          : 0
      const skyHeight =
        skyValue > 0
          ? Math.max(3, (skyValue / skyMaximum) * SKY_BAND_HEIGHT)
          : 0

      return {
        ...point,
        x: WORLD_PADDING + calendarOffsets[index] * dayStep,
        groundHeight,
        groundY: GROUND_BASELINE - groundHeight,
        skyHeight,
        skyY: SKY_BASELINE - skyHeight,
      }
    })
    const route = points
      .map(
        (point, index) =>
          `${index === 0 ? 'M' : 'L'} ${point.x} ${point.groundY}`,
      )
      .join(' ')

    return { width, dayStep, points, route }
  }, [level.points, level.split])

  const selectedWorldPoint = world.points[selectedIndex] ?? null
  const cameraOffset = selectedWorldPoint
    ? Math.max(
        0,
        Math.min(world.width - VIEWBOX_WIDTH, selectedWorldPoint.x - RUNNER_LOCK_X),
      )
    : 0
  const barWidth = Math.max(7, Math.min(18, world.dayStep * 0.58))
  const lastPointIndex = Math.max(0, level.points.length - 1)

  const sliderValueText = selectedPoint
    ? [
        selectedPoint.date,
        selectedPoint.tokens === null
          ? 'tokens unavailable'
          : `${formatTokens(selectedPoint.tokens)} tokens`,
        selectedPoint.cost === null
          ? 'cost unavailable'
          : `${formatCost(selectedPoint.cost)} cost`,
        pointStateAnnouncement(selectedPoint.state),
        ...selectedLandmarks,
      ].join(', ')
    : 'No published dates'

  const selectNearestDate = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!world.points.length || event.pointerType === 'touch') return
    const bounds = event.currentTarget.getBoundingClientRect()
    if (!bounds.width) return
    const viewX =
      ((event.clientX - bounds.left) / bounds.width) * VIEWBOX_WIDTH
    const pointIndex = nearestPointIndex(world.points, viewX + cameraOffset)
    const point = world.points[pointIndex]
    if (point) onSelectedDayChange(point.date)
  }

  const recordPoint = level.landmarks.record
    ? world.points[level.landmarks.record.index]
    : null
  const recordLabelOnLeft = Boolean(recordPoint && recordPoint.x > world.width / 2)

  return (
    <section
      className={`run-archive-world run-archive-world--${signatureClass}`}
      data-coverage={level.coverageComplete ? 'complete' : 'incomplete'}
      data-run-day={selectedPoint?.date ?? ''}
      data-run-signature={signatureClass}
      role="region"
      aria-labelledby={headingId}
      aria-describedby={descriptionId}
    >
      <header className="run-archive-world__hud">
        <div className="run-archive-world__identity">
          <p className="section-kicker">RUN THE ARCHIVE</p>
          <h3 id={headingId}>{scopeLabel}</h3>
        </div>
        <div
          className="run-archive-world__signature"
          data-forming={level.signature.forming ? 'true' : 'false'}
        >
          <span>RUN SIGNATURE</span>
          <strong>{level.signature.name}</strong>
          <small>{level.signature.evidence}</small>
        </div>
      </header>

      <p id={descriptionId} className="visually-hidden">
        This world maps the focused model&apos;s recorded daily history. Horizontal
        position is date and terrain height is exact daily tokens. Run Signature
        describes this personal history pattern, not model capability.
      </p>

      <div
        className="run-archive-world__viewport"
        data-selected-state={selectedPoint?.state ?? 'unavailable'}
        onPointerDown={selectNearestDate}
        aria-hidden="true"
      >
        <svg
          className="run-archive-world__scene"
          viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          focusable="false"
        >
          <defs>
            <pattern
              id={unavailablePatternId}
              width="8"
              height="8"
              patternUnits="userSpaceOnUse"
            >
              <path
                className="run-archive-world__hatch-line"
                d="M-2 2 L2 -2 M0 8 L8 0 M6 10 L10 6"
                fill="none"
                stroke="currentColor"
              />
            </pattern>
          </defs>

          <g
            className="run-archive-world__camera"
            transform={`translate(${-cameraOffset} 0)`}
          >
            <path
              className="run-archive-world__survey-line"
              d={`M 0 ${GROUND_BASELINE} H ${world.width}`}
            />
            {level.split.enabled ? (
              <path
                className="run-archive-world__sky-line"
                d={`M 0 ${SKY_BASELINE} H ${world.width}`}
              />
            ) : null}

            {world.points.map((point) => {
              const isRecord = level.landmarks.record?.index === point.index
              const terrainClass = [
                'run-archive-world__day',
                `run-archive-world__day--${point.state}`,
                isRecord ? 'run-archive-world__day--record' : '',
              ]
                .filter(Boolean)
                .join(' ')

              return (
                <g
                  className={terrainClass}
                  data-day={point.date}
                  key={`${point.date}-${point.index}`}
                >
                  {point.state === 'unavailable' ? (
                    <rect
                      className="run-archive-world__unavailable"
                      x={point.x - barWidth / 2}
                      y={GROUND_BASELINE - 22}
                      width={barWidth}
                      height={22}
                      fill={`url(#${unavailablePatternId})`}
                    />
                  ) : point.state === 'zero' ? (
                    <path
                      className="run-archive-world__quiet-mark"
                      d={`M ${point.x - barWidth / 2} ${GROUND_BASELINE} h ${barWidth}`}
                    />
                  ) : (
                    <rect
                      className="run-archive-world__ground-terrain"
                      x={point.x - barWidth / 2}
                      y={point.groundY}
                      width={barWidth}
                      height={point.groundHeight}
                    />
                  )}

                  {point.skyHeight > 0 ? (
                    <>
                      <path
                        className="run-archive-world__split-connector"
                        d={`M ${point.x} ${SKY_BASELINE + 4} V ${GROUND_BASELINE - GROUND_BAND_HEIGHT - 4}`}
                      />
                      <path
                        className="run-archive-world__split-mark"
                        d={`M ${point.x - 7} 119 l 5 -5 M ${point.x + 1} 119 l 5 -5`}
                      />
                      <rect
                        className="run-archive-world__sky-terrain"
                        x={point.x - barWidth / 2}
                        y={point.skyY}
                        width={barWidth}
                        height={point.skyHeight}
                      />
                    </>
                  ) : null}
                </g>
              )
            })}

            <path
              className="run-archive-world__route-trace"
              data-grammar={signatureClass}
              d={world.route}
            />

            {signatureClass === 'pulsar'
              ? world.points
                  .filter((point) => point.state === 'active')
                  .map((point) => (
                    <circle
                      className="run-archive-world__pulse"
                      cx={point.x}
                      cy={point.skyHeight > 0 ? point.skyY : point.groundY}
                      r="8"
                      key={`pulse-${point.date}`}
                    />
                  ))
              : null}

            {signatureClass === 'sprinter' && selectedWorldPoint ? (
              <g className="run-archive-world__afterimage">
                <path
                  d={`M ${selectedWorldPoint.x - 42} ${selectedWorldPoint.groundY - 15} h 24`}
                />
                <path
                  d={`M ${selectedWorldPoint.x - 32} ${selectedWorldPoint.groundY - 8} h 18`}
                />
              </g>
            ) : null}

            {signatureClass === 'history-still-forming' ? (
              <rect
                className="run-archive-world__forming-fog"
                x="0"
                y="24"
                width={world.width}
                height={GROUND_BASELINE - 24}
              />
            ) : null}

            {level.landmarks.firstSeen ? (
              <g
                className="run-archive-world__landmark run-archive-world__landmark--first"
                transform={`translate(${world.points[level.landmarks.firstSeen.index]?.x ?? 0} 0)`}
              >
                <path d={`M 0 178 V ${GROUND_BASELINE + 8}`} />
                <circle cy="178" r="4" />
                <text x="8" y="174">FIRST SEEN</text>
              </g>
            ) : null}

            {recordPoint ? (
              <g
                className="run-archive-world__landmark run-archive-world__landmark--record"
                transform={`translate(${recordPoint.x} 0)`}
              >
                <path d={`M 0 18 V ${GROUND_BASELINE + 8}`} />
                <path d="M -7 25 L 0 18 L 7 25 L 0 32 Z" />
                <text
                  x={recordLabelOnLeft ? -12 : 12}
                  y="22"
                  textAnchor={recordLabelOnLeft ? 'end' : 'start'}
                >
                  MODEL RECORD
                </text>
                <text
                  className="run-archive-world__record-value"
                  x={recordLabelOnLeft ? -12 : 12}
                  y="38"
                  textAnchor={recordLabelOnLeft ? 'end' : 'start'}
                >
                  {formatTokens(level.landmarks.record?.tokens ?? null)} TOKENS
                </text>
              </g>
            ) : null}

            {level.landmarks.lastSeen ? (
              <g
                className="run-archive-world__landmark run-archive-world__landmark--last"
                transform={`translate(${world.points[level.landmarks.lastSeen.index]?.x ?? 0} 0)`}
              >
                <path d={`M 0 154 V ${GROUND_BASELINE + 8}`} />
                <path d="M -5 154 h 10 l -5 -8 Z" />
                <text x="8" y="150">LAST SEEN</text>
              </g>
            ) : null}

            {level.landmarks.archiveEdge ? (
              <g
                className="run-archive-world__landmark run-archive-world__landmark--edge"
                transform={`translate(${world.points[level.landmarks.archiveEdge.index]?.x ?? 0} 0)`}
              >
                <path d={`M 0 190 V ${GROUND_BASELINE + 8}`} />
                <path d="M -5 190 h 10 M -5 196 h 10" />
                <text x="8" y="186">ARCHIVE EDGE</text>
              </g>
            ) : null}

            {selectedWorldPoint ? (
              <g
                className="run-archive-world__runner"
                data-state={selectedWorldPoint.state}
                transform={`translate(${selectedWorldPoint.x} ${Math.min(GROUND_BASELINE - 18, selectedWorldPoint.groundY - 18)})`}
              >
                <path className="run-archive-world__runner-guide" d="M 0 -250 V 20" />
                <rect x="-11" y="-12" width="22" height="16" />
                <path d="M -7 8 h 5 v 5 M 7 8 h -5 v 5" />
                <text x="0" y="0" textAnchor="middle">&gt;_</text>
              </g>
            ) : null}
          </g>
        </svg>
      </div>

      <output
        htmlFor={sliderId}
        className="run-archive-world__readout"
        data-landmark={selectedLandmarks.length ? 'true' : 'false'}
        data-state={selectedPoint?.state ?? 'unavailable'}
      >
        <time dateTime={selectedPoint?.date}>
          {selectedPoint ? formatDate(selectedPoint.date) : 'NO PUBLISHED DATE'}
        </time>
        <strong>
          {selectedPoint === null || selectedPoint.tokens === null
            ? 'TOKENS UNAVAILABLE'
            : `${formatTokens(selectedPoint.tokens)} TOKENS`}
        </strong>
        <span>
          {selectedPoint === null || selectedPoint.cost === null
            ? 'COST UNAVAILABLE'
            : formatCost(selectedPoint.cost)}{' '}
          ·{' '}
          {selectedPoint ? pointStateLabel(selectedPoint.state) : 'UNAVAILABLE'}
          {selectedLandmarks.length
            ? ` · ${selectedLandmarks.join(' · ').toUpperCase()}`
            : ''}
        </span>
      </output>

      <div className="run-archive-world__navigator">
        <label htmlFor={sliderId}>Explore recorded dates</label>
        <div className="run-archive-world__minimap">
          <svg
            viewBox={`0 0 ${world.width} 32`}
            preserveAspectRatio="none"
            aria-hidden="true"
            focusable="false"
          >
            <path d={`M 0 25 H ${world.width}`} />
            {world.points.map((point) => (
              <rect
                className={`run-archive-world__minimap-day run-archive-world__minimap-day--${point.state}`}
                x={point.x - Math.max(2, barWidth / 4)}
                y={point.state === 'active' ? 8 : 19}
                width={Math.max(4, barWidth / 2)}
                height={point.state === 'active' ? 17 : 6}
                key={`minimap-${point.date}-${point.index}`}
              />
            ))}
            {recordPoint ? (
              <path
                className="run-archive-world__minimap-record"
                d={`M ${recordPoint.x} 2 l 5 5 -5 5 -5 -5 Z`}
              />
            ) : null}
            {selectedWorldPoint ? (
              <path
                className="run-archive-world__minimap-runner"
                d={`M ${selectedWorldPoint.x} 0 V 32`}
              />
            ) : null}
          </svg>
          <input
            id={sliderId}
            className="run-archive-world__slider"
            type="range"
            min={0}
            max={lastPointIndex}
            step={1}
            value={Math.min(selectedIndex, lastPointIndex)}
            disabled={!level.points.length}
            aria-valuetext={sliderValueText}
            aria-describedby={sliderHintId}
            onChange={(event) => {
              const point = level.points[Number(event.currentTarget.value)]
              if (point) onSelectedDayChange(point.date)
            }}
          />
        </div>
        <p id={sliderHintId}>Drag the date runner or use arrow keys</p>
      </div>
    </section>
  )
}
