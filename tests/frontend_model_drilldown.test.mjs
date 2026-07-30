import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  modelSeriesPoint,
  selectModelSeries,
  TOOLS,
} from '../src/lib/usage.ts'

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
