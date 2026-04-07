import json
import os

def generate_visual_replay(events, symbol, output_file=None):
    if not output_file:
        os.makedirs("backtest_results", exist_ok=True)
        output_file = f"backtest_results/visual_replay_{symbol}.html"

    # Convert events to JSON efficiently
    events_json = json.dumps(events)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{symbol} Visual Replay</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        body {{
            margin: 0; padding: 0; background: #131722; color: #d1d4dc;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            display: flex; flex-direction: column; height: 100vh;
        }}
        #toolbar {{
            display: flex; align-items: center; padding: 10px 20px; background: #1e222d;
            border-bottom: 1px solid #2a2e39; gap: 15px;
        }}
        button {{
            background: #2962ff; color: white; border: none; padding: 8px 16px; 
            border-radius: 4px; cursor: pointer; font-weight: bold;
        }}
        button:hover {{ background: #1e4bd8; }}
        input[type="range"] {{ width: 150px; cursor: pointer; }}
        .status-panel {{ margin-left: auto; display: flex; gap: 20px; font-size: 13px; }}
        .status-item span {{ font-weight: bold; color: #2962ff; }}
        #chart {{ flex-grow: 1; width: 100%; }}
    </style>
</head>
<body>
    <div id="toolbar">
        <button id="btn-play">Play</button>
        <button id="btn-pause">Pause</button>
        <button id="btn-step">Step >|</button>
        <div>
            Speed: <span id="speed-label">30</span>ms 
            <input type="range" id="speed" min="10" max="500" value="30" step="10">
        </div>
        <div class="status-panel">
            <div class="status-item">Equity: $<span id="equity-label" style="color: #d1d4dc;">---</span> (<span id="return-label" style="color: #d1d4dc;">0.00%</span>)</div>
            <div class="status-item">Day: <span id="day-label" style="color: #d1d4dc;">0.0</span></div>
            <div class="status-item">State: <span id="state-label">IDLE</span></div>
            <div class="status-item">Candle: <span id="candle-label">0/0</span></div>
        </div>
    </div>
    <div id="chart"></div>

    <script>
        const events = {events_json};
        
        const container = document.getElementById('chart');
        const chart = LightweightCharts.createChart(container, {{
            layout: {{ background: {{ type: 'solid', color: '#131722' }}, textColor: '#d1d4dc' }},
            grid: {{ vertLines: {{ color: '#1e222d' }}, horzLines: {{ color: '#1e222d' }} }},
            timeScale: {{ timeVisible: true, secondsVisible: false }},
        }});

        const candleSeries = chart.addCandlestickSeries({{
            upColor: '#26a69a', downColor: '#ef5350',
            borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350'
        }});

        let pEntry = null, pSl = null, pTp = null;
        let aEntry = null, aSl = null, aTp = null;

        function clearLines() {{
            if(pEntry) candleSeries.removePriceLine(pEntry);
            if(pSl) candleSeries.removePriceLine(pSl);
            if(pTp) candleSeries.removePriceLine(pTp);
            if(aEntry) candleSeries.removePriceLine(aEntry);
            if(aSl) candleSeries.removePriceLine(aSl);
            if(aTp) candleSeries.removePriceLine(aTp);
            pEntry = pSl = pTp = aEntry = aSl = aTp = null;
        }}

        function updateStateUI(ev) {{
            const lbl = document.getElementById('state-label');
            if(ev.position.active) {{
                lbl.innerText = ev.position.type === 1 ? 'LONG OPEN' : 'SHORT OPEN';
                lbl.style.color = '#26a69a';
            }} else if(ev.pending.active) {{
                lbl.innerText = ev.pending.dir === 1 ? 'PENDING BUY' : 'PENDING SELL';
                lbl.style.color = 'orange';
            }} else {{
                lbl.innerText = 'SEEKING SETUP';
                lbl.style.color = '#787b86';
            }}
            
            const equityLabel = document.getElementById('equity-label');
            const returnLabel = document.getElementById('return-label');
            const dayLabel = document.getElementById('day-label');
            
            if (ev.equity && ev.initial_balance) {{
                const returnPct = ((ev.equity - ev.initial_balance) / ev.initial_balance * 100);
                equityLabel.innerText = ev.equity.toFixed(2);
                returnLabel.innerText = (returnPct > 0 ? '+' : '') + returnPct.toFixed(2) + '%';
                
                if(returnPct > 0) returnLabel.style.color = '#26a69a';
                else if(returnPct < 0) returnLabel.style.color = '#ef5350';
                else returnLabel.style.color = '#d1d4dc';
            }}
            
            if (events.length > 0) {{
                const firstTime = events[0].time;
                const daysPassed = (ev.time - firstTime) / 86400; // time in seconds
                dayLabel.innerText = daysPassed.toFixed(1);
            }}
        }}

        let currentIndex = 0;
        let timer = null;
        let speed = 30;

        function renderNext() {{
            if (currentIndex >= events.length) {{
                pause();
                return;
            }}
            
            const ev = events[currentIndex];
            candleSeries.update({{
                time: ev.time,
                open: ev.open,
                high: ev.high,
                low: ev.low,
                close: ev.close
            }});
            
            clearLines();
            if (ev.pending.active && !ev.position.active) {{
                pEntry = candleSeries.createPriceLine({{ price: ev.pending.entry, color: 'gray', lineStyle: 2, title: 'Limit', axisLabelVisible: true }});
                pSl = candleSeries.createPriceLine({{ price: ev.pending.sl, color: '#ef5350', lineStyle: 2, title: 'SL', axisLabelVisible: true }});
                pTp = candleSeries.createPriceLine({{ price: ev.pending.tp, color: '#26a69a', lineStyle: 2, title: 'TP', axisLabelVisible: true }});
            }}
            
            if (ev.position.active) {{
                aEntry = candleSeries.createPriceLine({{ price: ev.position.entry, color: '#2962ff', lineStyle: 0, title: 'Entry', axisLabelVisible: true }});
                aSl = candleSeries.createPriceLine({{ price: ev.position.sl, color: '#ef5350', lineStyle: 0, title: 'SL', axisLabelVisible: true }});
                aTp = candleSeries.createPriceLine({{ price: ev.position.tp, color: '#26a69a', lineStyle: 0, title: 'TP', axisLabelVisible: true }});
            }}

            updateStateUI(ev);
            document.getElementById('candle-label').innerText = `${{currentIndex + 1}} / ${{events.length}}`;
            currentIndex++;
        }}

        function play() {{
            if (timer) return;
            timer = setInterval(renderNext, speed);
        }}

        function pause() {{
            if (timer) {{
                clearInterval(timer);
                timer = null;
            }}
        }}

        document.getElementById('btn-play').addEventListener('click', play);
        document.getElementById('btn-pause').addEventListener('click', pause);
        document.getElementById('btn-step').addEventListener('click', () => {{ pause(); renderNext(); }});
        
        const speedSlider = document.getElementById('speed');
        const speedLabel = document.getElementById('speed-label');
        speedSlider.addEventListener('input', (e) => {{
            speed = parseInt(e.target.value);
            speedLabel.innerText = speed;
            if (timer) {{
                pause();
                play();
            }}
        }});

        // Preload first 50 candles instantly to give context
        const initialLoad = Math.min(50, events.length);
        for(let i=0; i<initialLoad; i++) {{ renderNext(); }}
        
        window.addEventListener('resize', () => {{
            chart.applyOptions({{ width: container.clientWidth, height: container.clientHeight }});
        }});
    </script>
</body>
</html>"""

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    print(f"Interactive Visual Replay saved to: {output_file}")
