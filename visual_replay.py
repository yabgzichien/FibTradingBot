import json
import os


def generate_visual_replay(events, symbol, htf_df=None, output_file=None):
    """
    Generates an interactive dual-pane visual replay HTML.

    Top pane   : H4 candlestick chart — auto-syncs to the current M15 bar time.
    Bottom pane: M15 candlestick chart — driven by the step/play controls.

    Controls:
      Play / Pause  — auto-advance M15 bars.
      Step ›|       — advance one M15 bar.
      ‹| Back       — go back one M15 bar.
      Speed slider  — controls playback interval (ms).
    """
    if not output_file:
        os.makedirs("backtest_results", exist_ok=True)
        output_file = f"backtest_results/visual_replay_{symbol}.html"

    events_json = json.dumps(events)

    # ── Serialise H4 candles ──────────────────────────────────────────────────
    htf_candles = []
    if htf_df is not None and not htf_df.empty:
        for ts, row in htf_df.iterrows():
            try:
                t = int(ts.timestamp())
            except Exception:
                continue
            htf_candles.append({
                "time":  t,
                "open":  float(row["open"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
                "close": float(row["close"]),
            })
    htf_json = json.dumps(htf_candles)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{symbol} — Dual-TF Visual Replay</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #0d1117;
            color: #d1d4dc;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex;
            flex-direction: column;
            height: 100vh;
            overflow: hidden;
        }}

        /* ── Toolbar ─────────────────────────────────────────────────────── */
        #toolbar {{
            display: flex;
            align-items: center;
            padding: 8px 16px;
            background: #161b22;
            border-bottom: 1px solid #21262d;
            gap: 10px;
            flex-shrink: 0;
        }}
        .btn {{
            background: #1f6feb;
            color: #fff;
            border: none;
            padding: 6px 14px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: background 0.15s;
        }}
        .btn:hover  {{ background: #388bfd; }}
        .btn:active {{ background: #1158c7; }}
        .btn-back   {{ background: #21262d; }}
        .btn-back:hover {{ background: #30363d; }}
        #speed-wrap {{ display: flex; align-items: center; gap: 6px; font-size: 12px; color: #8b949e; }}
        input[type="range"] {{ width: 120px; cursor: pointer; accent-color: #1f6feb; }}
        .sep {{ width: 1px; height: 24px; background: #21262d; }}

        /* ── Status panel ────────────────────────────────────────────────── */
        .status-panel {{
            margin-left: auto;
            display: flex;
            gap: 18px;
            font-size: 12px;
            color: #8b949e;
        }}
        .status-item {{ display: flex; flex-direction: column; align-items: flex-end; }}
        .status-label {{ font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .status-value {{ font-weight: 700; font-size: 13px; color: #e6edf3; }}
        .green  {{ color: #3fb950 !important; }}
        .red    {{ color: #f85149 !important; }}
        .orange {{ color: #d29922 !important; }}
        .blue   {{ color: #58a6ff !important; }}

        /* ── Chart layout: two panes stacked ─────────────────────────────── */
        #charts-wrapper {{
            display: flex;
            flex-direction: column;
            flex-grow: 1;
            overflow: hidden;
        }}
        .pane-header {{
            background: #161b22;
            border-bottom: 1px solid #21262d;
            padding: 4px 12px;
            font-size: 11px;
            font-weight: 600;
            color: #8b949e;
            letter-spacing: 0.5px;
            flex-shrink: 0;
        }}
        #htf-chart-container {{
            flex: 1;
            min-height: 0;
        }}
        .pane-divider {{
            height: 4px;
            background: #21262d;
            cursor: row-resize;
            flex-shrink: 0;
        }}
        #etf-chart-container {{
            flex: 1.6;
            min-height: 0;
        }}
    </style>
</head>
<body>

<!-- ── Toolbar ────────────────────────────────────────────────────────────── -->
<div id="toolbar">
    <button class="btn btn-back" id="btn-back" title="Go back one M15 candle">‹| Back</button>
    <button class="btn" id="btn-play">▶ Play</button>
    <button class="btn" id="btn-pause">⏸ Pause</button>
    <button class="btn" id="btn-step">Step ›|</button>

    <div class="sep"></div>

    <div id="speed-wrap">
        Speed:
        <input type="range" id="speed" min="10" max="800" value="80" step="10">
        <span id="speed-label">80</span>ms
    </div>

    <div class="sep"></div>

    <div class="status-panel">
        <div class="status-item">
            <span class="status-label">Equity</span>
            <span class="status-value" id="equity-label">---</span>
        </div>
        <div class="status-item">
            <span class="status-label">Return</span>
            <span class="status-value" id="return-label">0.00%</span>
        </div>
        <div class="status-item">
            <span class="status-label">Day</span>
            <span class="status-value" id="day-label">0</span>
        </div>
        <div class="status-item">
            <span class="status-label">State</span>
            <span class="status-value" id="state-label">IDLE</span>
        </div>
        <div class="status-item">
            <span class="status-label">M15 Bar</span>
            <span class="status-value" id="candle-label">0 / 0</span>
        </div>
    </div>
</div>

<!-- ── Dual pane chart area ───────────────────────────────────────────────── -->
<div id="charts-wrapper">
    <div class="pane-header">H4 — Anchor Timeframe</div>
    <div id="htf-chart-container"></div>
    <div class="pane-divider" id="divider"></div>
    <div class="pane-header">M15 — Execution Timeframe</div>
    <div id="etf-chart-container"></div>
</div>

<script>
// ── Data ────────────────────────────────────────────────────────────────────
const events   = {events_json};
const htfBars  = {htf_json};

// ── H4 chart (top) ──────────────────────────────────────────────────────────
const htfContainer = document.getElementById('htf-chart-container');
const htfChart = LightweightCharts.createChart(htfContainer, {{
    layout: {{ background: {{ type: 'solid', color: '#0d1117' }}, textColor: '#8b949e' }},
    grid:   {{ vertLines: {{ color: '#161b22' }}, horzLines: {{ color: '#161b22' }} }},
    crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    rightPriceScale: {{ borderColor: '#21262d' }},
    timeScale: {{ borderColor: '#21262d', timeVisible: true, secondsVisible: false }},
}});
const htfSeries = htfChart.addCandlestickSeries({{
    upColor: '#3fb950', downColor: '#f85149',
    borderUpColor: '#3fb950', borderDownColor: '#f85149',
    wickUpColor: '#3fb950', wickDownColor: '#f85149',
}});

// ── H4 chart starts empty — bars revealed as M15 advances ──────────────────
let htfRevealedCount = 0;

// Binary search: how many H4 bars have time <= m15Time?
function countVisibleHtfBars(m15Time) {{
    let lo = 0, hi = htfBars.length;
    while (lo < hi) {{
        const mid = (lo + hi) >> 1;
        if (htfBars[mid].time <= m15Time) lo = mid + 1;
        else hi = mid;
    }}
    return lo;
}}

// ── M15 chart (bottom) ──────────────────────────────────────────────────────
const etfContainer = document.getElementById('etf-chart-container');
const etfChart = LightweightCharts.createChart(etfContainer, {{
    layout: {{ background: {{ type: 'solid', color: '#0d1117' }}, textColor: '#8b949e' }},
    grid:   {{ vertLines: {{ color: '#161b22' }}, horzLines: {{ color: '#161b22' }} }},
    crosshair: {{ mode: LightweightCharts.CrosshairMode.Normal }},
    rightPriceScale: {{ borderColor: '#21262d' }},
    timeScale: {{ borderColor: '#21262d', timeVisible: true, secondsVisible: false }},
}});
const etfSeries = etfChart.addCandlestickSeries({{
    upColor: '#3fb950', downColor: '#f85149',
    borderUpColor: '#3fb950', borderDownColor: '#f85149',
    wickUpColor: '#3fb950', wickDownColor: '#f85149',
}});

// ── Price lines (pending / position) ─────────────────────────────────────────
let pEntry = null, pSl = null, pTp = null;
let aEntry = null, aSl = null, aTp = null;

function clearLines() {{
    [pEntry, pSl, pTp, aEntry, aSl, aTp].forEach(l => {{
        if (l) try {{ etfSeries.removePriceLine(l); }} catch(e) {{}}
    }});
    pEntry = pSl = pTp = aEntry = aSl = aTp = null;
}}

// State management ─────────────────────────────────────────────────────────
// snapshot[i] = {{ etfCandles: [...], htfCount: N }}
// etfCandles = full M15 candle array rendered up to and including bar i.
// htfCount   = how many H4 bars had been revealed at that same moment.
const snapshots = [];
let currentIndex = 0;
let timer = null;
let speed = 80;



// Scroll H4 chart so the current H4 bar is always visible.
let lastHtfTime = -1;
function syncHtfChart(m15Time) {{
    if (htfBars.length === 0 || htfRevealedCount === 0) return;
    const bar = htfBars[Math.min(htfRevealedCount - 1, htfBars.length - 1)];
    if (!bar || bar.time === lastHtfTime) return;
    lastHtfTime = bar.time;
    try {{
        htfChart.timeScale().setVisibleRange({{
            from: bar.time - 80 * 14400,
            to:   bar.time + 20 * 14400,
        }});
    }} catch(e) {{}}
}}

// Render bar at index `idx` onto the M15 chart (cumulative — adds the candle).
function renderBarAt(idx) {{
    const ev = events[idx];
    etfSeries.update({{
        time:  ev.time,
        open:  ev.open,
        high:  ev.high,
        low:   ev.low,
        close: ev.close,
    }});
    return ev;
}}

// ── Forward step ─────────────────────────────────────────────────────────────
function stepForward() {{
    if (currentIndex >= events.length) {{ pause(); return; }}

    const ev        = events[currentIndex];
    const newCandle = {{ time: ev.time, open: ev.open, high: ev.high, low: ev.low, close: ev.close }};

    // ── Reveal any newly unlocked H4 bars ────────────────────────────────────
    // An H4 bar is visible when its open-time <= current M15 bar's time.
    const targetHtf = countVisibleHtfBars(ev.time);
    while (htfRevealedCount < targetHtf) {{
        htfSeries.update(htfBars[htfRevealedCount]);
        htfRevealedCount++;
    }}

    // ── Save snapshot (M15 candles + H4 count) ────────────────────────────────
    if (!snapshots[currentIndex]) {{
        const prevCandles = currentIndex > 0 ? snapshots[currentIndex - 1].etfCandles : [];
        snapshots[currentIndex] = {{
            etfCandles: [...prevCandles, newCandle],
            htfCount:   htfRevealedCount,
        }};
    }}

    etfSeries.update(newCandle);
    syncHtfChart(ev.time);
    renderUI(ev, currentIndex);
    currentIndex++;
}}

// ── Backward step ─────────────────────────────────────────────────────────────
function stepBackward() {{
    pause();
    if (currentIndex <= 1) return;

    currentIndex--;                     // undo the last rendered bar
    const prevIdx  = currentIndex - 1;
    const prevSnap = prevIdx >= 0 ? snapshots[prevIdx] : null;

    // Restore M15 chart
    etfSeries.setData(prevSnap ? prevSnap.etfCandles : []);

    // Restore H4 chart — if crossing an H4 boundary backward, slice it back
    const prevHtfCount = prevSnap ? prevSnap.htfCount : 0;
    if (prevHtfCount !== htfRevealedCount) {{
        htfRevealedCount = prevHtfCount;
        htfSeries.setData(htfBars.slice(0, htfRevealedCount));
    }}

    const ev = events[prevIdx >= 0 ? prevIdx : 0];
    renderUI(ev, prevIdx);
    syncHtfChart(ev.time);
}}

// ── UI update ────────────────────────────────────────────────────────────────
function renderUI(ev, idx) {{
    clearLines();

    if (ev.pending && ev.pending.active && !(ev.position && ev.position.active)) {{
        pEntry = etfSeries.createPriceLine({{ price: ev.pending.entry, color: '#8b949e', lineStyle: 2, lineWidth: 1, title: 'Limit', axisLabelVisible: true }});
        pSl    = etfSeries.createPriceLine({{ price: ev.pending.sl,    color: '#f85149', lineStyle: 2, lineWidth: 1, title: 'SL',    axisLabelVisible: true }});
        pTp    = etfSeries.createPriceLine({{ price: ev.pending.tp,    color: '#3fb950', lineStyle: 2, lineWidth: 1, title: 'TP',    axisLabelVisible: true }});
    }}

    if (ev.position && ev.position.active) {{
        aEntry = etfSeries.createPriceLine({{ price: ev.position.entry, color: '#58a6ff', lineStyle: 0, lineWidth: 1, title: 'Entry', axisLabelVisible: true }});
        aSl    = etfSeries.createPriceLine({{ price: ev.position.sl,    color: '#f85149', lineStyle: 0, lineWidth: 1, title: 'SL',   axisLabelVisible: true }});
        aTp    = etfSeries.createPriceLine({{ price: ev.position.tp,    color: '#3fb950', lineStyle: 0, lineWidth: 1, title: 'TP',   axisLabelVisible: true }});
    }}

    // State label
    const stateEl = document.getElementById('state-label');
    if (ev.position && ev.position.active) {{
        stateEl.innerText = ev.position.type === 1 ? 'LONG OPEN' : 'SHORT OPEN';
        stateEl.className = 'status-value ' + (ev.position.type === 1 ? 'green' : 'red');
    }} else if (ev.pending && ev.pending.active) {{
        stateEl.innerText = ev.pending.dir === 1 ? 'PENDING BUY' : 'PENDING SELL';
        stateEl.className = 'status-value orange';
    }} else {{
        stateEl.innerText = 'SEEKING';
        stateEl.className = 'status-value';
    }}

    // Equity / return
    if (ev.equity && ev.initial_balance) {{
        const ret = (ev.equity - ev.initial_balance) / ev.initial_balance * 100;
        document.getElementById('equity-label').innerText = '$' + ev.equity.toFixed(2);
        const retEl = document.getElementById('return-label');
        retEl.innerText = (ret >= 0 ? '+' : '') + ret.toFixed(2) + '%';
        retEl.className = 'status-value ' + (ret > 0 ? 'green' : ret < 0 ? 'red' : '');
    }}

    // Day counter
    if (events.length > 0) {{
        const days = (ev.time - events[0].time) / 86400;
        document.getElementById('day-label').innerText = days.toFixed(1) + 'd';
    }}

    document.getElementById('candle-label').innerText = `${{idx + 1}} / ${{events.length}}`;
}}

// ── Playback controls ────────────────────────────────────────────────────────
function play() {{
    if (timer) return;
    timer = setInterval(stepForward, speed);
}}

function pause() {{
    if (timer) {{ clearInterval(timer); timer = null; }}
}}

document.getElementById('btn-play').addEventListener('click', play);
document.getElementById('btn-pause').addEventListener('click', pause);
document.getElementById('btn-step').addEventListener('click', () => {{ pause(); stepForward(); }});
document.getElementById('btn-back').addEventListener('click', stepBackward);

const speedSlider = document.getElementById('speed');
const speedLabelEl = document.getElementById('speed-label');
speedSlider.addEventListener('input', (e) => {{
    speed = parseInt(e.target.value);
    speedLabelEl.innerText = speed;
    if (timer) {{ pause(); play(); }}
}});

// ── Draggable divider ─────────────────────────────────────────────────────────
const divider = document.getElementById('divider');
const htfCont = document.getElementById('htf-chart-container');
const etfCont = document.getElementById('etf-chart-container');
let dragging = false, startY = 0, startHtfH = 0, startEtfH = 0;

divider.addEventListener('mousedown', (e) => {{
    dragging = true;
    startY    = e.clientY;
    startHtfH = htfCont.getBoundingClientRect().height;
    startEtfH = etfCont.getBoundingClientRect().height;
    document.body.style.cursor = 'row-resize';
    e.preventDefault();
}});
document.addEventListener('mousemove', (e) => {{
    if (!dragging) return;
    const dy = e.clientY - startY;
    const newHtfH = Math.max(80, startHtfH + dy);
    const newEtfH = Math.max(80, startEtfH - dy);
    htfCont.style.flex = 'none';
    htfCont.style.height = newHtfH + 'px';
    etfCont.style.flex = 'none';
    etfCont.style.height = newEtfH + 'px';
    htfChart.applyOptions({{ width: htfCont.clientWidth, height: newHtfH }});
    etfChart.applyOptions({{ width: etfCont.clientWidth, height: newEtfH }});
}});
document.addEventListener('mouseup', () => {{
    dragging = false;
    document.body.style.cursor = '';
}});

// ── Resize handler ────────────────────────────────────────────────────────────
function onResize() {{
    htfChart.applyOptions({{ width: htfContainer.clientWidth, height: htfContainer.clientHeight }});
    etfChart.applyOptions({{ width: etfContainer.clientWidth, height: etfContainer.clientHeight }});
}}
window.addEventListener('resize', onResize);

// ── Initial load: pre-render first 50 M15 bars ────────────────────────────────
const initialLoad = Math.min(50, events.length);
for (let i = 0; i < initialLoad; i++) {{ stepForward(); }}
etfChart.timeScale().fitContent();

</script>
</body>
</html>"""

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"Dual-TF Visual Replay saved to: {output_file}")
