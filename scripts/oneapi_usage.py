#!/usr/bin/env python3
"""Collect LLM usage data from One API gateway.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ONEAPI_BASE = "https://oneapi-comate.baidu-int.com"
PAGE_SIZE = 20
DEFAULT_TZ = "Asia/Shanghai"


def resolve_tz(name: str) -> dt.tzinfo:
    try:
        return ZoneInfo(name)
    except Exception:
        return dt.timezone.utc


def safe_int(v: Any) -> int:
    if isinstance(v, bool) or v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def chrome_use_path() -> str:
    e = os.environ.get("CHROME_USE_BIN", "").strip()
    if e:
        return e
    f = shutil.which("chrome-use")
    return f if f else "chrome-use"


FETCH_JS = """(async()=>{const B='%(base)s';const S=%(start_ts)d;const E=%(end_ts)d;const R=[];const V=new Set();try{const ru=await fetch(B+'/api/user/self',{credentials:'include'});const rd=await ru.json();if(!rd.success)return JSON.stringify({_e:'not_auth'})}catch(e){return JSON.stringify({_e:'auth_err:'+e.message})}for(let p=0;p<2000;p++){try{const u=B+'/api/log/self/?p='+p+'&type=0&model_name=&start_timestamp='+S+'&end_timestamp='+E;const r=await fetch(u,{credentials:'include'});if(!r.ok){if(p===0)return JSON.stringify({_e:'http_'+r.status});break}const d=await r.json();const a=d.data||[];if(a.length===0)break;for(const c of a){const i=String(c.request_id||'');if(i&&V.has(i))continue;if(i)V.add(i);R.push(c)}if(a.length<%(ps)d)break}catch(e){if(p===0)return JSON.stringify({_e:'fetch_err:'+e.message});break}}return JSON.stringify(R)})()
"""


def collect_oneapi(
    timezone: str = DEFAULT_TZ,
    state_path: str = "/tmp/oneapi-chrome-state.json",
    since: str = "",
    until: str = "",
) -> dict[str, Any]:
    tz = resolve_tz(timezone)
    now = dt.datetime.now(tz=tz)
    end_ts = int(dt.datetime.combine(dt.date.fromisoformat(until) if until else now.date(), dt.time.max if until else now.time(), tzinfo=tz).timestamp())
    start_ts = int(dt.datetime.combine(dt.date.fromisoformat(since) if since else (now - dt.timedelta(days=5)).date(), dt.time.min, tzinfo=tz).timestamp())

    if not Path(state_path).exists():
        raise FileNotFoundError(f"Chrome state not found: {state_path}")

    cu = chrome_use_path()
    subprocess.run([cu, "open", ONEAPI_BASE + "/log"], text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60, check=False)

    # Write JS to a real file (not NamedTemporaryFile which chrome-use may not see)
    js = FETCH_JS % {"base": ONEAPI_BASE, "ps": PAGE_SIZE, "start_ts": start_ts, "end_ts": end_ts}
    js_file = f"/tmp/oneapi_fetch_{os.getpid()}.js"
    try:
        with open(js_file, "w") as f:
            f.write(js)
        proc = subprocess.run(
            [cu, "eval", "--file", js_file, "--timeout", "180000"],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240, check=False,
        )
    finally:
        Path(js_file).unlink(missing_ok=True)

    if proc.returncode != 0:
        raise RuntimeError(f"chrome-use eval: {proc.stderr.strip()[:500]}")

    raw = proc.stdout.strip()
    if not raw:
        raise RuntimeError("no output from chrome-use")
    try:
        records = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"invalid JSON: {raw[:300]}")
    if isinstance(records, str):
        records = json.loads(records)
    if isinstance(records, dict):
        err = records.get("_e", "")
        if err == "not_auth":
            raise RuntimeError("One API session expired. Re-login in Chrome.")
        elif err:
            raise RuntimeError(f"One API fetch error: {err}")
    if not isinstance(records, list):
        raise RuntimeError(f"unexpected response type: {type(records).__name__}")

    by_date_model: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for rec in records:
        ct = safe_int(rec.get("created_at"))
        if not ct:
            continue
        date = dt.datetime.fromtimestamp(ct, tz=tz).strftime("%Y-%m-%d")
        model = str(rec.get("model_name") or "unknown")
        pt = safe_int(rec.get("prompt_tokens"))
        ct = safe_int(rec.get("completion_tokens"))
        cr = safe_int(rec.get("cache_read_tokens"))
        cw = safe_int(rec.get("cache_write_tokens"))
        q = safe_int(rec.get("quota"))
        a = by_date_model[date][model]
        a["input_tokens"] += pt
        a["output_tokens"] += ct
        a["cache_read_tokens"] += cr
        a["cache_write_tokens"] += cw
        a["total_tokens"] += pt + ct + cr + cw
        a["count"] += 1
        a["quota_total"] += q

    daily, ti, to, tcr, tcw, tt, tq, tr = [], 0, 0, 0, 0, 0, 0, 0
    for date in sorted(by_date_model):
        models = by_date_model[date]
        di = sum(m["input_tokens"] for m in models.values())
        do = sum(m["output_tokens"] for m in models.values())
        dcr = sum(m["cache_read_tokens"] for m in models.values())
        dcw = sum(m["cache_write_tokens"] for m in models.values())
        dtok = sum(m["total_tokens"] for m in models.values())
        dq = sum(m["quota_total"] for m in models.values())
        dr = sum(m["count"] for m in models.values())
        daily.append({
            "date": date, "tokens": dtok, "input": di, "output": do,
            "cache_read": dcr, "cache_write": dcw, "requests": dr, "quota": dq,
            "model_breakdowns": [{"model": mn, **mv} for mn, mv in sorted(models.items(), key=lambda x: -x[1]["total_tokens"])],
        })
        ti += di; to += do; tcr += dcr; tcw += dcw; tt += dtok; tq += dq; tr += dr

    first = daily[0]["date"] if daily else ""
    last = daily[-1]["date"] if daily else ""
    return {
        "available": True, "timezone": timezone, "request_count": len(records),
        "history": {"first": first, "last": last},
        "totals": {"input_tokens": ti, "output_tokens": to, "cache_read_tokens": tcr,
                    "cache_write_tokens": tcw, "total_tokens": tt, "quota": tq, "requests": tr},
        "daily_timeline": daily,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--timezone", default=DEFAULT_TZ)
    p.add_argument("--state-path", default="/tmp/oneapi-chrome-state.json")
    p.add_argument("--since", default="")
    p.add_argument("--until", default="")
    a = p.parse_args()
    try:
        r = collect_oneapi(a.timezone, a.state_path, a.since, a.until)
        print(json.dumps(r, ensure_ascii=False, indent=2))
    except (FileNotFoundError, RuntimeError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
