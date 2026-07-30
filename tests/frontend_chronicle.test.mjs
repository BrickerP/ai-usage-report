import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildChroniclePeriods,
  summarizeLifetime,
} from '../src/lib/chronicle.ts'

test('lifetime summary derives totals, cache ratio, and recorded bounds from daily rows', () => {
  const summary = summarizeLifetime([
    {
      date: '2026-08-02',
      codex_tokens: 90,
      claude_tokens: 10,
      codex_cache_read: 20,
    },
    {
      date: '2026-07-27',
      cursor_tokens: 200,
      oneapi_tokens: 100,
      cursor_cache_write: 30,
      cursor_cache_read: 20,
      oneapi_cache_read: 30,
    },
    {
      date: '2026-07-30',
      claude_tokens: 100,
      claude_cache_create: 10,
      claude_cache_read: 10,
    },
  ])

  assert.deepEqual(summary, {
    recordedTokens: 500,
    cacheTokens: 120,
    cacheRatio: 0.24,
    recordedDays: 3,
    firstDate: '2026-07-27',
    lastDate: '2026-08-02',
  })
})

test('daily chronicle rows aggregate into labeled Monday-to-Sunday weeks', () => {
  const rows = [
    {
      date: '2026-08-03',
      codex_tokens: 40,
      claude_tokens: 10,
    },
    {
      date: '2026-08-02',
      codex_tokens: 60,
      cursor_tokens: 20,
    },
    {
      date: '2026-08-07',
      claude_tokens: 30,
      oneapi_tokens: 20,
    },
  ]

  const days = buildChroniclePeriods(rows, { granularity: 'day' })
  assert.deepEqual(
    days.map(({ startDate, endDate, label, totalTokens }) => ({
      startDate,
      endDate,
      label,
      totalTokens,
    })),
    [
      {
        startDate: '2026-08-02',
        endDate: '2026-08-02',
        label: 'Aug 2, 2026',
        totalTokens: 80,
      },
      {
        startDate: '2026-08-03',
        endDate: '2026-08-03',
        label: 'Aug 3, 2026',
        totalTokens: 50,
      },
      {
        startDate: '2026-08-07',
        endDate: '2026-08-07',
        label: 'Aug 7, 2026',
        totalTokens: 50,
      },
    ],
  )

  const weeks = buildChroniclePeriods(rows, { granularity: 'week' })
  assert.deepEqual(
    weeks.map(({ startDate, endDate, label, totalTokens }) => ({
      startDate,
      endDate,
      label,
      totalTokens,
    })),
    [
      {
        startDate: '2026-07-27',
        endDate: '2026-08-02',
        label: 'Jul 27, 2026 – Aug 2, 2026',
        totalTokens: 80,
      },
      {
        startDate: '2026-08-03',
        endDate: '2026-08-09',
        label: 'Aug 3, 2026 – Aug 9, 2026',
        totalTokens: 100,
      },
    ],
  )
})

test('chronicle segments carry existing tool colors and selected-tool emphasis', () => {
  const [period] = buildChroniclePeriods(
    [
      {
        date: '2026-08-03',
        codex_tokens: 40,
        claude_tokens: 30,
        cursor_tokens: 20,
        oneapi_tokens: 10,
      },
    ],
    { granularity: 'week', selectedTool: 'claude' },
  )

  assert.deepEqual(
    period.segments.map(({ toolId, color, tokens, emphasis }) => ({
      toolId,
      color,
      tokens,
      emphasis,
    })),
    [
      {
        toolId: 'codex',
        color: '#2563eb',
        tokens: 40,
        emphasis: 'muted',
      },
      {
        toolId: 'claude',
        color: '#c2410c',
        tokens: 30,
        emphasis: 'selected',
      },
      {
        toolId: 'cursor',
        color: '#0d9488',
        tokens: 20,
        emphasis: 'muted',
      },
      {
        toolId: 'oneapi',
        color: '#7c3aed',
        tokens: 10,
        emphasis: 'muted',
      },
    ],
  )
})
