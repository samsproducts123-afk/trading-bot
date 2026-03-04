#!/usr/bin/env python3
"""
GATE.IO LISTING SCANNER — Render.com Deployment
Runs 24/7. Scans every 30 seconds for new listings.
Sends alerts via Telegram. FREE TIER COMPATIBLE.
"""
import os, json, time, threading, hashlib, hmac, requests
from datetime import datetime, timezone
from flask import Flask, jsonify

app = Flask(__name__)
GATE_API_KEY = os.environ.get('GATE_API_KEY', '')
GATE_API_SECRET = os.environ.get('GATE_API_SECRET', '')
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
SCAN_INTERVAL = int(os.environ.get('SCAN_INTERVAL', '30'))

state = {
    'gate_pairs': set(),
    'upbit_markets': set(),
    'binance_announcements': set(),
    'volumes': {},
    'scan_count': 0,
    'alerts': [],
    'last_scan': None,
    'started_at': datetime.now(timezone.utc).isoformat(),
    'initialized': False
}

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT_ID, 'text': message, 'parse_mode': 'HTML'},
            timeout=10
        )
    except:
        pass

def scan_gate_pairs():
    try:
        r = requests.get('https://api.gateio.ws/api/v4/spot/currency_pairs', timeout=10)
        if r.status_code != 200:
            return []
        current = set(p['id'] for p in r.json() if p.get('trade_status') == 'tradable')
        if not state['initialized']:
            state['gate_pairs'] = current
            return []
        new_pairs = current - state['gate_pairs']
        state['gate_pairs'] = current
        return [{'source': 'Gate.io', 'pair': p, 'type': 'NEW_PAIR'} for p in new_pairs]
    except:
        return []

def scan_upbit_markets():
    try:
        r = requests.get('https://api.upbit.com/v1/market/all?isDetails=true', timeout=10)
        if r.status_code != 200:
            return []
        current = set(m['market'] for m in r.json())
        if not state['initialized']:
            state['upbit_markets'] = current
            return []
        new_m = current - state['upbit_markets']
        state['upbit_markets'] = current
        return [{'source': 'Upbit', 'pair': m, 'type': 'NEW_LISTING'} for m in new_m]
    except:
        return []

def scan_binance():
    try:
        r = requests.get(
            'https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query?catalogId=48&pageNo=1&pageSize=10',
            timeout=10,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        if r.status_code != 200:
            return []
        articles = r.json().get('data', {}).get('articles', [])
        current = set(a.get('code', '') for a in articles)
        titles = {a.get('code', ''): a.get('title', '') for a in articles}
        if not state['initialized']:
            state['binance_announcements'] = current
            return []
        new_codes = current - state['binance_announcements']
        state['binance_announcements'] = current
        results = []
        for c in new_codes:
            t = titles.get(c, '')
            if 'list' in t.lower():
                results.append({'source': 'Binance', 'title': t, 'type': 'LISTING_ANNOUNCEMENT'})
        return results
    except:
        return []

def scan_volume_spikes():
    try:
        r = requests.get('https://api.gateio.ws/api/v4/spot/tickers', timeout=10)
        if r.status_code != 200:
            return []
        alerts = []
        new_vols = {}
        for t in r.json():
            pair = t.get('currency_pair', '')
            if '_USDT' not in pair:
                continue
            if '3L_' in pair or '5L_' in pair or '3S_' in pair or '5S_' in pair:
                continue
            try:
                vol = float(t.get('quote_volume', 0) or 0)
                change = float(t.get('change_percentage', 0) or 0)
                price = float(t.get('last', 0) or 0)
            except:
                continue
            new_vols[pair] = vol
            if state['initialized']:
                old = state['volumes'].get(pair, 0)
                if old > 100 and vol > 500000:
                    ratio = vol / old
                    if ratio > 5 and change > 15:
                        alerts.append({
                            'source': 'Volume',
                            'pair': pair,
                            'volume': vol,
                            'ratio': ratio,
                            'change': change,
                            'price': price,
                            'type': 'VOLUME_SPIKE'
                        })
        state['volumes'] = new_vols
        return alerts
    except:
        return []

def scanner_loop():
    while True:
        try:
            now = datetime.now(timezone.utc).strftime('%H:%M:%S UTC')
            all_alerts = []
            all_alerts.extend(scan_gate_pairs())
            all_alerts.extend(scan_upbit_markets())
            all_alerts.extend(scan_binance())
            all_alerts.extend(scan_volume_spikes())
            if not state['initialized']:
                state['initialized'] = True
                msg = '🟢 <b>Listing Scanner ONLINE</b>\n'
                msg += f'Gate.io: {len(state["gate_pairs"])} pairs\n'
                msg += f'Upbit: {len(state["upbit_markets"])} markets\n'
                msg += f'Scanning every {SCAN_INTERVAL}s'
                send_telegram(msg)
                print(f'Baselines set: Gate {len(state["gate_pairs"])} | Upbit {len(state["upbit_markets"])}')
            for a in all_alerts:
                msg = f'🚨 <b>{a["type"]}</b>\n'
                msg += f'{a.get("source","")}: {a.get("pair", a.get("title","?"))}'
                if 'volume' in a:
                    msg += f'\nVol: ${a["volume"]:,.0f} ({a.get("ratio",0):.1f}x)'
                if 'change' in a:
                    msg += f'\nChange: +{a["change"]:.1f}%'
                send_telegram(msg)
                state['alerts'].append({'time': now, **a})
            state['scan_count'] += 1
            state['last_scan'] = now
            if state['scan_count'] % 10 == 0:
                print(f'[{now}] Scan #{state["scan_count"]}')
        except Exception as e:
            print(f'ERROR: {e}')
        time.sleep(SCAN_INTERVAL)

@app.route('/')
def home():
    return jsonify({
        'status': 'running',
        'scans': state['scan_count'],
        'last_scan': state['last_scan'],
        'alerts': state['alerts'][-10:]
    })

@app.route('/health')
def health():
    return 'OK', 200

if __name__ == '__main__':
    threading.Thread(target=scanner_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
