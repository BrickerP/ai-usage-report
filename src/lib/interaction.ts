import type { ToolId } from './usage'

export function nextSeriesIndex(
  key: string,
  currentIndex: number,
  count: number,
): number | null {
  if (count <= 0 || currentIndex < 0 || currentIndex >= count) return null
  if (key === 'Home') return 0
  if (key === 'End') return count - 1
  if (key === 'ArrowLeft') return (currentIndex - 1 + count) % count
  if (key === 'ArrowRight') return (currentIndex + 1) % count
  return null
}

export type ViewPreset = '7' | '30' | '90' | 'all'

export type ReportViewState = {
  tool: ToolId | null
  model: string | null
  preset: ViewPreset | null
  from: string | null
  to: string | null
}

const TOOL_IDS = new Set<ToolId>(['codex', 'claude', 'cursor', 'oneapi'])
const PRESETS = new Set<ViewPreset>(['7', '30', '90', 'all'])
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

export function parseReportView(search: string): ReportViewState {
  const params = new URLSearchParams(search)
  const rawTool = params.get('tool') as ToolId | null
  const tool = rawTool && TOOL_IDS.has(rawTool) ? rawTool : null
  const rawModel = params.get('model')?.trim() ?? ''
  const model =
    tool && rawModel && rawModel.length <= 160 ? rawModel : null
  const rawPreset = params.get('range') as ViewPreset | null
  const preset = rawPreset && PRESETS.has(rawPreset) ? rawPreset : null
  const rawFrom = params.get('from')
  const rawTo = params.get('to')
  const hasDateRange =
    Boolean(rawFrom && rawTo) &&
    ISO_DATE.test(rawFrom ?? '') &&
    ISO_DATE.test(rawTo ?? '')

  return {
    tool,
    model,
    preset: hasDateRange ? null : preset,
    from: hasDateRange ? rawFrom : null,
    to: hasDateRange ? rawTo : null,
  }
}

export function buildReportViewSearch(
  currentSearch: string,
  view: ReportViewState,
): string {
  const params = new URLSearchParams(currentSearch)

  if (view.tool) params.set('tool', view.tool)
  else params.delete('tool')

  if (view.tool && view.model) params.set('model', view.model)
  else params.delete('model')

  if (view.from && view.to) {
    params.delete('range')
    params.set('from', view.from)
    params.set('to', view.to)
  } else {
    params.delete('from')
    params.delete('to')
    if (view.preset && view.preset !== '30') {
      params.set('range', view.preset)
    } else {
      params.delete('range')
    }
  }

  const value = params.toString()
  return value ? `?${value}` : ''
}

export function indexRangeForDates(
  dates: string[],
  from: string,
  to: string,
): [number, number] | null {
  if (!dates.length || !ISO_DATE.test(from) || !ISO_DATE.test(to)) return null
  const first = dates[0]
  const last = dates[dates.length - 1]
  const boundedFrom = from < first ? first : from > last ? last : from
  const boundedTo = to < first ? first : to > last ? last : to
  const start = boundedFrom <= boundedTo ? boundedFrom : boundedTo
  const end = boundedFrom <= boundedTo ? boundedTo : boundedFrom
  const i0 = dates.findIndex((date) => date >= start)
  let i1 = dates.length - 1
  for (let index = dates.length - 1; index >= 0; index -= 1) {
    if (dates[index] <= end) {
      i1 = index
      break
    }
  }
  return [Math.max(0, i0), Math.max(0, i1)]
}

export type ExplorationBackAction =
  | 'close-details'
  | 'clear-model'
  | 'clear-tool'

export function explorationBackAction(state: {
  detailsOpen: boolean
  modelFocused: boolean
  toolSelected: boolean
}): ExplorationBackAction | null {
  if (state.detailsOpen) return 'close-details'
  if (state.modelFocused) return 'clear-model'
  if (state.toolSelected) return 'clear-tool'
  return null
}
