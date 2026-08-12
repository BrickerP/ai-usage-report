#!/usr/bin/env python3
"""Legacy single-file HTML and PNG report rendering."""
from __future__ import annotations

import base64
import html
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any


SCRIPTS_DIR = Path(__file__).resolve().parent


def safe_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def safe_float(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt_compact(value: Any) -> str:
    number = float(safe_float(value))
    sign = "-" if number < 0 else ""
    number = abs(number)
    for suffix, factor in (("T", 1_000_000_000_000), ("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if number >= factor:
            return f"{sign}{number / factor:.2f}{suffix}"
    return f"{sign}{number:.0f}"


def fmt_usd(value: Any) -> str:
    return f"${safe_float(value):,.2f}"


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


CARD_BREAKDOWNS: dict[str, list[tuple[str, str]]] = {
    "Codex": [
        ("input", "Input"),
        ("cache_read", "Cache read"),
        ("output", "Output (incl. reasoning)"),
        ("reasoning", "↳ Reasoning subset"),
    ],
    "Claude Code": [
        ("input", "Input"),
        ("cache_create", "Cache create"),
        ("cache_read", "Cache read"),
        ("output", "Output"),
    ],
    "Cursor": [
        ("input", "Input"),
        ("cache_write", "Cache write"),
        ("cache_read", "Cache read"),
        ("output", "Output"),
    ],
    "One API": [
        ("input", "Input"),
        ("cache_read", "Cache read"),
        ("cache_write", "Cache write"),
        ("output", "Output"),
    ],
}


def render_html(data: dict[str, Any], *, pricing_version: str) -> str:
    tools = data["tools"]
    colors = {
        "Codex": "#2563eb",
        "Claude Code": "#c2410c",
        "Cursor": "#0d9488",
        "One API": "#7c3aed",
    }
    daily_rows = data.get("daily_timeline_rows") if isinstance(data.get("daily_timeline_rows"), list) else []
    meta = data.get("timeline_meta") if isinstance(data.get("timeline_meta"), dict) else {}
    span = str(meta.get("span") or "unknown")
    payload_b64 = base64.b64encode(json.dumps(daily_rows, ensure_ascii=False).encode("utf-8")).decode("ascii")

    vendor_path = SCRIPTS_DIR / "vendor" / "echarts.min.js"
    if vendor_path.is_file():
        echarts_tag = "<script>\n" + vendor_path.read_text(encoding="utf-8") + "\n</script>"
    else:
        echarts_tag = (
            '<script src="https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js" '
            'crossorigin="anonymous"></script>'
        )

    series_map = {
        "Codex": ("codex", "codex_tokens", "codex_cost"),
        "Claude Code": ("claude", "claude_tokens", "claude_cost"),
        "Cursor": ("cursor", "cursor_tokens", "cursor_cost"),
        "One API": ("oneapi", "oneapi_tokens", "oneapi_cost"),
    }
    cards_parts: list[str] = []
    for tool in tools:
        name = str(tool.get("tool") or "")
        slug, kt, kc = series_map.get(name, ("", "", ""))
        color = colors.get(name, "#334155")
        toks_sum = sum(safe_int(r.get(kt)) for r in daily_rows if isinstance(r, dict))
        cost_sum = sum(safe_float(r.get(kc)) for r in daily_rows if isinstance(r, dict))
        breakdown_lines: list[str] = []
        for field_key, field_label in CARD_BREAKDOWNS.get(name, []):
            data_key = f"{slug}_{field_key}"
            field_sum = sum(safe_int(r.get(data_key)) for r in daily_rows if isinstance(r, dict))
            breakdown_lines.append(
                f'<div class="breakdown-row"><span>{esc(field_label)}</span>'
                f'<strong id="kpi-{esc(slug)}-{esc(field_key)}">{esc(fmt_compact(field_sum))}</strong></div>'
            )
        breakdown_html = "".join(breakdown_lines)
        cards_parts.append(
            f'<section class="card" style="--accent:{color}">'
            f"<h2>{esc(name)}</h2>"
            f'<div class="big" id="kpi-{esc(slug)}-cost">{esc(fmt_usd(cost_sum))}</div>'
            f'<div class="sub"><span id="kpi-{esc(slug)}-tokens">{esc(fmt_compact(toks_sum))}</span> tokens total</div>'
            f'<div class="token-breakdown">{breakdown_html}</div>'
            "</section>"
        )
    cards_html = "".join(cards_parts)
    total_cache_all = sum(
        safe_int(r.get("codex_cache_read"))
        + safe_int(r.get("claude_cache_create"))
        + safe_int(r.get("claude_cache_read"))
        + safe_int(r.get("cursor_cache_write"))
        + safe_int(r.get("cursor_cache_read"))
        + safe_int(r.get("oneapi_cache_write"))
        + safe_int(r.get("oneapi_cache_read"))
        for r in daily_rows
        if isinstance(r, dict)
    )
    total_toks_all = sum(
        safe_int(r.get("codex_tokens"))
        + safe_int(r.get("claude_tokens"))
        + safe_int(r.get("cursor_tokens"))
        + safe_int(r.get("oneapi_tokens"))
        for r in daily_rows
        if isinstance(r, dict)
    )
    total_cost_all = sum(
        safe_float(r.get("codex_cost"))
        + safe_float(r.get("claude_cost"))
        + safe_float(r.get("cursor_cost"))
        + safe_float(r.get("oneapi_cost"))
        for r in daily_rows
        if isinstance(r, dict)
    )
    if daily_rows:
        nwin = len(daily_rows)
        w0, w1 = daily_rows[0]["date"], daily_rows[-1]["date"]
        initial_kpi_window = esc(f"Totals for visible range: {w0} — {w1} · {nwin} day(s)")
    else:
        initial_kpi_window = esc("Totals for visible range: —")
    grand_initial_tok = esc(fmt_compact(total_toks_all))
    grand_initial_cost = esc(fmt_usd(total_cost_all))
    grand_initial_cache = esc(fmt_compact(total_cache_all))
    date_min = esc(str(daily_rows[0]["date"])) if daily_rows else ""
    date_max = esc(str(daily_rows[-1]["date"])) if daily_rows else ""
    app_js = r"""
(function () {
  const el = document.getElementById('main');
  const b64El = document.getElementById('usage-b64');
  const b64 = b64El ? b64El.textContent.trim() : '';
  let RAW = [];
  try {
    RAW = JSON.parse(atob(b64));
  } catch (e) {
    el.innerHTML = '<div id="empty-msg">Could not parse embedded data</div>';
    return;
  }
  if (!RAW.length) {
    el.innerHTML = '<div id="empty-msg">No daily rows (check Codex + Cursor API + One API)</div>';
    return;
  }
  const dates = RAW.map(r => r.date);
  const cT = RAW.map(r => r.codex_tokens || 0);
  const clT = RAW.map(r => r.claude_tokens || 0);
  const cuT = RAW.map(r => r.cursor_tokens || 0);
  const oT = RAW.map(r => r.oneapi_tokens || 0);
  const cC = RAW.map(r => Number(r.codex_cost) || 0);
  const clC = RAW.map(r => Number(r.claude_cost) || 0);
  const cuC = RAW.map(r => Number(r.cursor_cost) || 0);
  const oC = RAW.map(r => Number(r.oneapi_cost) || 0);
  const cCache = RAW.map(r => Number(r.codex_cache_read) || 0);
  const clCache = RAW.map(r => (Number(r.claude_cache_create) || 0) + (Number(r.claude_cache_read) || 0));
  const cuCache = RAW.map(r => (Number(r.cursor_cache_write) || 0) + (Number(r.cursor_cache_read) || 0));
  const oCache = RAW.map(r => (Number(r.oneapi_cache_write) || 0) + (Number(r.oneapi_cache_read) || 0));
  const breakdownFields = {
    codex: [
      ['input', 'Input', r => Number(r.codex_input) || 0],
      ['cache_read', 'Cache read', r => Number(r.codex_cache_read) || 0],
      ['output', 'Output (incl. reasoning)', r => Number(r.codex_output) || 0],
      ['reasoning', '↳ Reasoning subset', r => Number(r.codex_reasoning) || 0],
    ],
    claude: [
      ['input', 'Input', r => Number(r.claude_input) || 0],
      ['cache_create', 'Cache create', r => Number(r.claude_cache_create) || 0],
      ['cache_read', 'Cache read', r => Number(r.claude_cache_read) || 0],
      ['output', 'Output', r => Number(r.claude_output) || 0],
    ],
    cursor: [
      ['input', 'Input', r => Number(r.cursor_input) || 0],
      ['cache_write', 'Cache write', r => Number(r.cursor_cache_write) || 0],
      ['cache_read', 'Cache read', r => Number(r.cursor_cache_read) || 0],
      ['output', 'Output', r => Number(r.cursor_output) || 0],
    ],
    oneapi: [
      ['input', 'Input', r => Number(r.oneapi_input) || 0],
      ['cache_read', 'Cache read', r => Number(r.oneapi_cache_read) || 0],
      ['cache_write', 'Cache write', r => Number(r.oneapi_cache_write) || 0],
      ['output', 'Output', r => Number(r.oneapi_output) || 0],
    ],
  };

  const totalDaySpend = cC.map((v, i) => (Number(v) || 0) + (Number(clC[i]) || 0) + (Number(cuC[i]) || 0) + (Number(oC[i]) || 0));

  const COL = { codex: '#2563eb', claude: '#c2410c', cursor: '#0d9488', oneapi: '#7c3aed' };
  const chart = echarts.init(el, null, { renderer: 'canvas' });

  function mixChannel(c, t) {
    return Math.round(c + (255 - c) * t);
  }

  function stackSegStyle(hex, cap) {
    const h = hex.replace('#', '');
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    const topRgb = mixChannel(r, 0.15) + ',' + mixChannel(g, 0.15) + ',' + mixChannel(b, 0.15);
    const rad = 6;
    let br;
    if (cap === 'top') br = [rad, rad, 0, 0];
    else if (cap === 'bot') br = [0, 0, rad, rad];
    else br = [0, 0, 0, 0];
    return {
      color: {
        type: 'linear',
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: 'rgb(' + topRgb + ')' },
          { offset: 1, color: hex },
        ],
      },
      borderColor: 'rgba(15,23,42,0.06)',
      borderWidth: 1,
      borderRadius: br,
    };
  }

  const stackEmphasis = {
    focus: 'series',
    blurScope: 'coordinateSystem',
    itemStyle: { shadowBlur: 10, shadowColor: 'rgba(15,23,42,0.08)', shadowOffsetY: 1 },
  };

  const stackBar = {
    type: 'bar',
    stack: 'tokens',
    barCategoryGap: '42%',
    barMaxWidth: 34,
    emphasis: stackEmphasis,
  };

  const spendLine = {
    type: 'line',
    smooth: 0.35,
    symbol: 'circle',
    symbolSize: dates.length > 72 ? 0 : 5,
    showSymbol: dates.length <= 72,
    lineStyle: { width: 2.4, color: '#334155' },
    itemStyle: { color: '#334155', borderWidth: 0 },
    areaStyle: {
      color: {
        type: 'linear',
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: 'rgba(51,65,85,0.2)' },
          { offset: 1, color: 'rgba(51,65,85,0.02)' },
        ],
      },
    },
    emphasis: { focus: 'series', lineStyle: { width: 3 } },
  };

  function sliceRange(startPct, endPct) {
    const n = dates.length;
    if (n === 0) return { i0: 0, i1: -1 };
    // Round so (pct -> index) matches ECharts and our (index -> pct) math under JS floats.
    let i0 = Math.round((startPct / 100) * (n - 1));
    let i1 = Math.round((endPct / 100) * (n - 1));
    i0 = Math.max(0, Math.min(n - 1, i0));
    i1 = Math.max(0, Math.min(n - 1, i1));
    if (i1 < i0) { const t = i0; i0 = i1; i1 = t; }
    return { i0, i1 };
  }

  function dispatchZoom(start, end) {
    chart.dispatchAction({ type: 'dataZoom', start, end, xAxisIndex: [0, 1, 2] });
  }

  /** One category: start===end percent collapses the window and breaks bar layout; use a hair-wide span. */
  function zoomSingleCategoryIndex(k) {
    const n = dates.length;
    if (!n) return;
    if (n === 1) {
      dispatchZoom(0, 100);
      return;
    }
    const kk = Math.max(0, Math.min(n - 1, Math.round(k)));
    const den = n - 1;
    const c = (kk / den) * 100;
    const eps = 0.05;
    let start;
    let end;
    if (kk === 0) {
      start = 0;
      end = Math.min(100, eps);
    } else if (kk === n - 1) {
      end = 100;
      start = Math.max(0, 100 - eps);
    } else {
      start = c;
      end = Math.min(100, c + eps);
    }
    dispatchZoom(start, end);
  }

  function setWindowByIndex(i0, i1) {
    const n = dates.length;
    if (!n) return;
    let a = Math.max(0, Math.min(n - 1, i0));
    let b = Math.max(0, Math.min(n - 1, i1));
    if (a > b) { const t = a; a = b; b = t; }
    if (a === b) {
      zoomSingleCategoryIndex(a);
      return;
    }
    chart.dispatchAction({
      type: 'dataZoom',
      startValue: dates[a],
      endValue: dates[b],
      xAxisIndex: [0, 1, 2],
    });
  }

  function presetDays(d) {
    const n = dates.length;
    if (!n) return;
    const i1 = n - 1;
    const i0 = Math.max(0, n - Math.min(d, n));
    setWindowByIndex(i0, i1);
  }

  function applyDateInputs() {
    const s = document.getElementById('range-start');
    const e = document.getElementById('range-end');
    if (!s || !e || !s.value || !e.value) return;
    const dMin = dates[0];
    const dMax = dates[dates.length - 1];
    function clampIso(v) {
      if (v < dMin) return dMin;
      if (v > dMax) return dMax;
      return v;
    }
    let v0 = clampIso(s.value);
    let v1 = clampIso(e.value);
    if (v0 > v1) { const t = v0; v0 = v1; v1 = t; }
    if (v0 === v1) {
      const k = dates.indexOf(v0);
      if (k >= 0) zoomSingleCategoryIndex(k);
      return;
    }
    chart.dispatchAction({ type: 'dataZoom', startValue: v0, endValue: v1, xAxisIndex: [0, 1, 2] });
  }

  function pan(deltaPct) {
    const { start, end } = getDataZoomRange();
    const w = end - start;
    let ns = start + deltaPct * w;
    let ne = end + deltaPct * w;
    if (ns < 0) { ne -= ns; ns = 0; }
    if (ne > 100) { ns -= ne - 100; ne = 100; }
    dispatchZoom(ns, ne);
  }

  function sumSlice(arr, i0, i1) {
    let s = 0;
    for (let i = i0; i <= i1; i++) s += arr[i] || 0;
    return s;
  }

  function fmtTok(x) {
    if (x >= 1e9) return (x / 1e9).toFixed(2) + 'B';
    if (x >= 1e6) return (x / 1e6).toFixed(2) + 'M';
    if (x >= 1e3) return (x / 1e3).toFixed(2) + 'K';
    return String(Math.round(x));
  }

  function fmtUsd(x) {
    return '$' + x.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function getDataZoomRange() {
    const opt = chart.getOption();
    const dzList = opt.dataZoom || [];
    function pick(pred) {
      for (let i = 0; i < dzList.length; i++) {
        const z = dzList[i];
        if (z && pred(z) && z.start != null) {
          return { start: z.start, end: z.end != null ? z.end : 100 };
        }
      }
      return null;
    }
    return pick((z) => z.type === 'slider') || pick(() => true) || { start: 0, end: 100 };
  }

  /** Visible category indices from slider (startValue/endValue are indices for category axis). */
  function sliderIndexRange() {
    const dzList = chart.getOption().dataZoom || [];
    for (let i = 0; i < dzList.length; i++) {
      const z = dzList[i];
      if (!z || z.type !== 'slider' || z.start == null) continue;
      const a = z.startValue;
      const b = z.endValue;
      if (a != null && b != null && typeof a === 'number' && typeof b === 'number') {
        let i0 = Math.round(Math.min(a, b));
        let i1 = Math.round(Math.max(a, b));
        i0 = Math.max(0, Math.min(dates.length - 1, i0));
        i1 = Math.max(0, Math.min(dates.length - 1, i1));
        return { i0, i1 };
      }
      if (typeof a === 'string' && typeof b === 'string' && dates.indexOf(a) >= 0 && dates.indexOf(b) >= 0) {
        let i0 = dates.indexOf(a);
        let i1 = dates.indexOf(b);
        if (i0 > i1) { const t = i0; i0 = i1; i1 = t; }
        return { i0, i1 };
      }
    }
    const { start, end } = getDataZoomRange();
    return sliceRange(start, end);
  }

  function indicesFromZoomPart(p) {
    if (!p) return null;
    const a = p.startValue;
    const b = p.endValue;
    if (a != null && b != null) {
      if (typeof a === 'number' && typeof b === 'number') {
        let i0 = Math.round(Math.min(a, b));
        let i1 = Math.round(Math.max(a, b));
        i0 = Math.max(0, Math.min(dates.length - 1, i0));
        i1 = Math.max(0, Math.min(dates.length - 1, i1));
        return { i0, i1 };
      }
      if (typeof a === 'string' && typeof b === 'string') {
        let i0 = dates.indexOf(a);
        let i1 = dates.indexOf(b);
        if (i0 < 0 || i1 < 0) return null;
        if (i0 > i1) { const t = i0; i0 = i1; i1 = t; }
        return { i0, i1 };
      }
    }
    if (p.start != null && p.end != null) {
      return sliceRange(p.start, p.end);
    }
    return null;
  }

  /** same tick as dataZoom, getOption() may still be the *previous* window */
  function indicesFromDataZoomEvent(evt) {
    if (!evt) return null;
    if (evt.batch && evt.batch.length) {
      for (let i = evt.batch.length - 1; i >= 0; i--) {
        const r = indicesFromZoomPart(evt.batch[i]);
        if (r) return r;
      }
      return null;
    }
    return indicesFromZoomPart(evt);
  }

  function syncPickersToIndices(i0, i1) {
    const s = document.getElementById('range-start');
    const e = document.getElementById('range-end');
    if (!s || !e) return;
    if (document.activeElement === s || document.activeElement === e) return;
    const ds = dates[i0];
    const de = dates[i1];
    if (ds != null) s.value = ds;
    if (de != null) e.value = de;
  }

  function renderTotals(i0, i1) {
    const d0 = dates[i0];
    const d1 = dates[i1];
    const winEl = document.getElementById('kpi-window-label');
    if (winEl) {
      winEl.textContent =
        d0 && d1
          ? 'Totals for visible range: ' + d0 + ' — ' + d1 + ' · ' + (i1 - i0 + 1) + ' day(s)'
          : 'Totals for visible range: —';
    }
    function setKpi(slug, tokArr, costArr) {
      const costNode = document.getElementById('kpi-' + slug + '-cost');
      const tokNode = document.getElementById('kpi-' + slug + '-tokens');
      if (costNode) costNode.textContent = fmtUsd(sumSlice(costArr, i0, i1));
      if (tokNode) tokNode.textContent = fmtTok(sumSlice(tokArr, i0, i1));
    }
    function setBreakdown(slug) {
      const fields = breakdownFields[slug] || [];
      for (const [key, , getter] of fields) {
        const node = document.getElementById('kpi-' + slug + '-' + key);
        if (!node) continue;
        let total = 0;
        for (let i = i0; i <= i1; i++) total += getter(RAW[i]);
        node.textContent = fmtTok(total);
      }
    }
    setKpi('codex', cT, cC);
    setKpi('claude', clT, clC);
    setKpi('cursor', cuT, cuC);
    setKpi('oneapi', oT, oC);
    setBreakdown('codex');
    setBreakdown('claude');
    setBreakdown('cursor');
    setBreakdown('oneapi');
    const allTok = sumSlice(cT, i0, i1) + sumSlice(clT, i0, i1) + sumSlice(cuT, i0, i1) + sumSlice(oT, i0, i1);
    const allCost = sumSlice(cC, i0, i1) + sumSlice(clC, i0, i1) + sumSlice(cuC, i0, i1) + sumSlice(oC, i0, i1);
    const allCache = sumSlice(cCache, i0, i1) + sumSlice(clCache, i0, i1) + sumSlice(cuCache, i0, i1) + sumSlice(oCache, i0, i1);
    const allTokEl = document.getElementById('kpi-all-tokens');
    const allCostEl = document.getElementById('kpi-all-cost');
    const allCacheEl = document.getElementById('kpi-all-cache');
    if (allTokEl) allTokEl.textContent = fmtTok(allTok);
    if (allCostEl) allCostEl.textContent = fmtUsd(allCost);
    if (allCacheEl) allCacheEl.textContent = fmtTok(allCache);
  }

  function updateWindowTotals(evt) {
    const fromEvt = indicesFromDataZoomEvent(evt);
    if (fromEvt) {
      renderTotals(fromEvt.i0, fromEvt.i1);
      syncPickersToIndices(fromEvt.i0, fromEvt.i1);
      return;
    }
    const r = sliderIndexRange();
    renderTotals(r.i0, r.i1);
    if (evt) {
      requestAnimationFrame(() => {
        if (!chart || chart.isDisposed()) return;
        const r2 = sliderIndexRange();
        renderTotals(r2.i0, r2.i1);
        syncPickersToIndices(r2.i0, r2.i1);
      });
    } else {
      syncPickersToIndices(r.i0, r.i1);
    }
  }

  const rotate = dates.length > 36 ? 32 : 0;
  const xAxisCommon = {
    type: 'category',
    boundaryGap: true,
    data: dates,
    axisLabel: { rotate: rotate, fontSize: 11, color: '#64748b' },
    axisLine: { lineStyle: { color: '#e2e8f0' } },
    axisTick: { alignWithLabel: true, lineStyle: { color: '#e2e8f0' } },
  };

  const option = {
    animation: true,
    textStyle: { fontFamily: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif' },
    axisPointer: { link: [{ xAxisIndex: [0, 1, 2] }], snap: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(15, 23, 42, 0.05)' } },
      borderRadius: 10,
      padding: [12, 14],
      backgroundColor: 'rgba(255,255,255,0.98)',
      borderColor: '#e2e8f0',
      borderWidth: 1,
      textStyle: { color: '#0f172a', fontSize: 12 },
      extraCssText: 'box-shadow:0 12px 40px rgba(15,23,42,0.08);',
      formatter: function (params) {
        if (!params || !params.length) return '';
        const idx = params[0].dataIndex;
        const row = RAW[idx] || {};
        const date = dates[idx] || '';
        const lines = ['<strong>' + date + '</strong>'];
        function toolSection(title, slug, total, cost, fields) {
          lines.push('<div style="margin-top:8px"><span style="color:#64748b">' + title + '</span></div>');
          lines.push('Total ' + fmtTok(total) + ' · ' + fmtUsd(cost));
          for (const [, label, getter] of fields) {
            const value = getter(row);
            if (value) lines.push(label + ': ' + fmtTok(value));
          }
        }
        toolSection('Codex', 'codex', row.codex_tokens || 0, Number(row.codex_cost) || 0, breakdownFields.codex);
        toolSection('Claude Code', 'claude', row.claude_tokens || 0, Number(row.claude_cost) || 0, breakdownFields.claude);
        toolSection('Cursor', 'cursor', row.cursor_tokens || 0, Number(row.cursor_cost) || 0, breakdownFields.cursor);
        toolSection('One API', 'oneapi', row.oneapi_tokens || 0, Number(row.oneapi_cost) || 0, breakdownFields.oneapi);
        lines.push('<div style="margin-top:8px">Daily spend (all tools): <strong>' + fmtUsd(totalDaySpend[idx] || 0) + '</strong></div>');
        return lines.join('<br/>');
      },
    },
    legend: {
      data: ['Codex', 'Claude', 'Cursor', 'One API', 'Codex cache', 'Claude cache', 'Cursor cache', 'One API cache', 'Daily spend (all tools)'],
      type: 'scroll',
      top: 6,
      left: 'center',
      itemGap: 14,
      itemWidth: 10,
      itemHeight: 10,
      icon: 'circle',
      textStyle: { color: '#64748b', fontSize: 11 },
    },
    grid: [
      { left: 56, right: 48, top: 88, height: '22%' },
      { left: 56, right: 48, top: '40%', height: '22%' },
      { left: 56, right: 48, top: '68%', height: '18%' },
    ],
    xAxis: [
      { ...xAxisCommon, gridIndex: 0, axisLabel: { ...xAxisCommon.axisLabel, margin: 10 } },
      { ...xAxisCommon, gridIndex: 1, axisLabel: { show: false } },
      { ...xAxisCommon, gridIndex: 2, axisLabel: { ...xAxisCommon.axisLabel, margin: 10 } },
    ],
    yAxis: [
      {
        type: 'value',
        gridIndex: 0,
        name: 'Total tokens / day',
        nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
        axisLabel: { formatter: (v) => fmtTok(v), color: '#64748b' },
        min: 0,
        splitLine: { show: true, lineStyle: { color: 'rgba(148,163,184,0.2)', type: [4, 4] } },
      },
      {
        type: 'value',
        gridIndex: 1,
        name: 'Cache tokens / day',
        nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
        axisLabel: { formatter: (v) => fmtTok(v), color: '#64748b' },
        min: 0,
        splitLine: { show: true, lineStyle: { color: 'rgba(148,163,184,0.16)', type: [4, 4] } },
      },
      {
        type: 'value',
        gridIndex: 2,
        name: 'Total spend / day',
        nameTextStyle: { fontSize: 11, color: '#94a3b8', padding: [0, 0, 0, 8] },
        axisLabel: { formatter: (v) => '$' + v, color: '#64748b' },
        min: 0,
        splitLine: { show: true, lineStyle: { color: 'rgba(148,163,184,0.14)', type: [4, 4] } },
      },
    ],
    dataZoom: [
      {
        type: 'inside',
        xAxisIndex: [0, 1, 2],
        filterMode: 'none',
        minSpan: 0,
        minValueSpan: 1,
        maxValueSpan: dates.length,
        zoomOnMouseWheel: false,
        moveOnMouseMove: false,
        moveOnMouseWheel: false,
      },
      {
        type: 'slider',
        xAxisIndex: [0, 1, 2],
        filterMode: 'none',
        minSpan: 0,
        minValueSpan: 1,
        maxValueSpan: dates.length,
        height: 36,
        bottom: 20,
        showDetail: true,
        textStyle: { fontSize: 12, color: '#475569' },
        borderColor: '#e2e8f0',
        backgroundColor: '#f8fafc',
        fillerColor: 'rgba(13, 148, 136, 0.14)',
        handleStyle: { color: '#fff', borderColor: '#0f766e', borderWidth: 2 },
        dataBackground: {
          lineStyle: { color: '#cbd5e1', width: 0.5 },
          areaStyle: { color: 'rgba(148, 163, 184, 0.1)' },
        },
        selectedDataBackground: {
          lineStyle: { color: '#0d9488', width: 0.8 },
          areaStyle: { color: 'rgba(13, 148, 136, 0.07)' },
        },
      },
    ],
    series: [
      {
        name: 'Codex',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.codex, 'bot'),
        data: cT,
      },
      {
        name: 'Claude',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.claude, 'mid'),
        data: clT,
      },
      {
        name: 'Cursor',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.cursor, 'mid'),
        data: cuT,
      },
      {
        name: 'One API',
        ...stackBar,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: stackSegStyle(COL.oneapi, 'top'),
        data: oT,
      },
      {
        name: 'Codex cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.codex, 'bot'),
        data: cCache,
      },
      {
        name: 'Claude cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.claude, 'mid'),
        data: clCache,
      },
      {
        name: 'Cursor cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.cursor, 'mid'),
        data: cuCache,
      },
      {
        name: 'One API cache',
        ...stackBar,
        stack: 'cache',
        xAxisIndex: 1,
        yAxisIndex: 1,
        itemStyle: stackSegStyle(COL.oneapi, 'top'),
        data: oCache,
      },
      {
        name: 'Daily spend (all tools)',
        ...spendLine,
        xAxisIndex: 2,
        yAxisIndex: 2,
        data: totalDaySpend,
      },
    ],
  };

  chart.setOption(option);

  document.querySelectorAll('[data-preset]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const d = btn.getAttribute('data-preset');
      if (d === 'all') dispatchZoom(0, 100);
      else presetDays(parseInt(d, 10));
    });
  });
  document.getElementById('apply-dates')?.addEventListener('click', applyDateInputs);
  document.getElementById('pan-left')?.addEventListener('click', () => pan(-0.2));
  document.getElementById('pan-right')?.addEventListener('click', () => pan(0.2));

  chart.on('dataZoom', (evt) => updateWindowTotals(evt));
  updateWindowTotals();
  window.addEventListener('resize', () => chart.resize());
})();
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI coding usage</title>
<style>
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0;
  background: linear-gradient(165deg, #f8fafc 0%, #f1f5f9 45%, #eef2f6 100%);
  color: #0f172a;
  font-family: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  min-height: 100vh;
  -webkit-font-smoothing: antialiased;
}}
.page {{ max-width: 1200px; margin: 0 auto; padding: 40px 28px 56px; }}
.masthead {{ margin-bottom: 28px; }}
.kicker {{
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: #64748b;
  margin: 0 0 10px;
}}
h1 {{
  margin: 0;
  font-size: clamp(28px, 3.6vw, 34px);
  font-weight: 650;
  letter-spacing: -0.03em;
  line-height: 1.12;
  color: #0f172a;
}}
.meta {{
  margin-top: 20px;
  display: flex;
  flex-wrap: wrap;
  gap: 20px 28px;
  font-size: 12px;
  color: #94a3b8;
}}
.meta span {{ white-space: nowrap; }}
.meta b {{ color: #475569; font-weight: 600; }}
.masthead .cards-context {{
  margin: 16px 0 12px;
}}
.cards {{
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}}
@media (max-width: 820px) {{ .cards {{ grid-template-columns: 1fr; }} }}
.card {{
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 20px 22px;
  box-shadow: 0 4px 24px rgba(15,23,42,0.045);
  border-left: 4px solid var(--accent, #334155);
}}
.card h2 {{ font-size: 15px; font-weight: 650; margin: 0 0 6px; color: #0f172a; }}
.big {{ font-size: 26px; font-weight: 680; letter-spacing: -0.02em; margin-bottom: 6px; color: #0f172a; }}
.sub {{ font-size: 13px; color: #475569; line-height: 1.4; }}
.token-breakdown {{
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid #eef2f6;
  display: grid;
  gap: 6px;
}}
.breakdown-row {{
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  color: #64748b;
}}
.breakdown-row strong {{
  color: #334155;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}}
.methodology {{
  margin: 0 0 18px;
  padding: 14px 18px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  font-size: 13px;
  color: #475569;
  line-height: 1.55;
}}
.methodology strong {{ color: #334155; }}
.controls {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 14px 20px;
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 14px 18px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(15,23,42,0.04);
}}
.presets, .daterange, .pan-btns {{ display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }}
.ctl-label {{
  font-size: 10px;
  font-weight: 650;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #94a3b8;
  margin-right: 4px;
}}
.btn {{
  appearance: none;
  border: 1px solid #e2e8f0;
  background: #fff;
  color: #334155;
  font-size: 13px;
  font-weight: 550;
  padding: 8px 14px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, transform 0.1s;
}}
.btn:hover {{ background: #f8fafc; border-color: #cbd5e1; }}
.btn:active {{ transform: scale(0.98); }}
.btn.primary {{
  background: #0f172a;
  color: #fff;
  border-color: #0f172a;
}}
.btn.primary:hover {{ background: #1e293b; border-color: #1e293b; }}
input[type="date"] {{
  font-family: inherit;
  font-size: 13px;
  padding: 7px 10px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  color: #334155;
  background: #fff;
}}
.chart-wrap {{
  background: #fff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 8px 4px 6px;
  margin-bottom: 16px;
  box-shadow: 0 8px 30px rgba(15,23,42,0.06);
}}
#main {{ width: 100%; height: min(92vh, 980px); min-height: 620px; }}
.cards-context {{
  margin: 0 0 14px;
  font-size: 14px;
  font-weight: 600;
  color: #334155;
  letter-spacing: -0.01em;
}}
.kpi-grand {{
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 8px 18px;
  margin: 0 0 18px;
  padding: 14px 18px;
  background: linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(248,250,252,0.92) 100%);
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 2px 14px rgba(15,23,42,0.05);
}}
.kpi-grand-title {{
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: #94a3b8;
  width: 100%;
}}
.kpi-grand-metrics {{
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px 14px;
  font-size: 15px;
  color: #475569;
}}
.kpi-grand-metrics b {{
  font-weight: 680;
  font-size: 20px;
  letter-spacing: -0.02em;
  color: #0f172a;
}}
.kpi-grand-dot {{ color: #cbd5e1; font-weight: 400; }}
#empty-msg {{ padding: 48px; text-align: center; color: #94a3b8; }}
</style>
</head>
<body>
<script type="text/plain" id="usage-b64">{payload_b64}</script>
{echarts_tag}
<main class="page">
<header class="masthead">
  <p class="kicker">Usage report</p>
  <h1>AI coding spend &amp; tokens</h1>
  <div class="meta">
    <span><b>Generated</b> {esc(data["generated_at"])}</span>
    <span><b>Timezone</b> {esc(data["timezone"])}</span>
    <span><b>Span</b> {esc(span)}</span>
  </div>
  <p class="cards-context" id="kpi-window-label">{initial_kpi_window}</p>
</header>
<div class="kpi-grand" role="group" aria-label="All tools combined for visible range">
  <span class="kpi-grand-title">All tools combined</span>
  <span class="kpi-grand-metrics">
    <span><b id="kpi-all-tokens">{grand_initial_tok}</b> tokens total</span>
    <span class="kpi-grand-dot">·</span>
    <span><b id="kpi-all-cache">{grand_initial_cache}</b> cache tokens</span>
    <span class="kpi-grand-dot">·</span>
    <span><b id="kpi-all-cost">{grand_initial_cost}</b> spend total</span>
  </span>
</div>
<p class="methodology">
  <strong>Token breakdown:</strong> cards and tooltips show input, cache, and output tokens per tool.
  Codex cache = cache read; Claude cache = create + read; Cursor cache = write + read.
  <strong>Cost estimate:</strong> Codex uses the checked-in <code>{esc(pricing_version)}</code> price ledger;
  unresolved models retain an explicit legacy collector value. Claude Code and One API come from the One API gateway quota.
  Cursor costs come from the authenticated Dashboard API.
</p>
<section class="cards">{cards_html}</section>
<section class="controls" aria-label="Time range controls">
  <div class="presets">
    <span class="ctl-label">Range</span>
    <button type="button" class="btn" data-preset="7">7 days</button>
    <button type="button" class="btn" data-preset="30">30 days</button>
    <button type="button" class="btn" data-preset="90">90 days</button>
    <button type="button" class="btn" data-preset="all">All</button>
  </div>
  <div class="daterange">
    <span class="ctl-label">Dates</span>
    <input type="date" id="range-start" min="{date_min}" max="{date_max}" />
    <span style="color:#cbd5e1">→</span>
    <input type="date" id="range-end" min="{date_min}" max="{date_max}" />
    <button type="button" class="btn primary" id="apply-dates">Apply</button>
  </div>
  <div class="pan-btns">
    <span class="ctl-label">Nudge</span>
    <button type="button" class="btn" id="pan-left" title="Show earlier dates">◀</button>
    <button type="button" class="btn" id="pan-right" title="Show later dates">▶</button>
</div>
</section>
<div class="chart-wrap">
  <div id="main"></div>
</div>
</main>
<script>
{app_js}
</script>
</body>
</html>
"""

def chrome_path() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome")
    if found:
        return found
    raise RuntimeError("No Chrome/Chromium executable found for image rendering")


def render_png(html_path: Path, output_path: Path, width: int, height: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome_path(),
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--virtual-time-budget=20000",
        "--run-all-compositor-stages-before-draw",
        f"--window-size={width},{height}",
        f"--screenshot={output_path}",
        html_path.resolve().as_uri(),
    ]
    proc = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"Chrome screenshot failed: {proc.stderr.strip()[:800]}")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("Chrome did not create a PNG output")
