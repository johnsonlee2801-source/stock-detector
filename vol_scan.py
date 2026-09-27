#!/usr/bin/env python3
"""
加密货币波动率压缩观察站 v2

拉日线 K 线，算每个币当前波动率在过去一年里的百分位，输出终端表格 / JSON / HTML 报告。
只用标准库。数据源默认走 Binance 的公共数据镜像 data-api.binance.vision（GitHub Actions
的美国机器也能访问），拉不到的币自动用 OKX 兜底。

用法示例：
  python3 vol_scan.py ETH SOL NEAR                          # 指定币种
  python3 vol_scan.py --top-volume 100 --file watchlist.txt   # 成交额前 100 + 自选
  python3 vol_scan.py --top-volume 100 --file watchlist.txt \
      --html report.html --json vol_data.json --history vol_history.json

指标含义：
  vol20     20 日对数收益率标准差，年化百分比（波动的绝对大小）
  vol_pct   vol20 在回看期内的百分位，0 = 一年最安静，100 = 一年最疯狂
  bbw_pct   布林带宽度(20,2) 的百分位，对应 TradingView 的 BBWP
  squeeze   布林带缩进肯特纳通道(20,1.5ATR) 的连续天数，对应 SQZMOM 的黑点
  mom       SQZMOM 动量值占价格的百分比，正为多头，负为空头；trend 是比前一天升还是降
  hi20/lo20 最近 20 日最高价 / 最低价，突破参考
"""

import argparse
import csv
import gzip
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# 先走公共数据镜像（美国机器也能访问），不行再走主站
BINANCE_HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
OKX = "https://www.okx.com"
UA = {"User-Agent": "Mozilla/5.0 vol-scan/2.0", "Accept-Encoding": "gzip", "Connection": "close"}
STABLE_OR_LEVERAGED = ("USD", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EUR", "USD1",
                       "UP", "DOWN", "BULL", "BEAR")
BEIJING = timezone(timedelta(hours=8))


def get_json(url, params=None, retries=3):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return json.loads(raw.decode())
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"请求失败 {url}: {last_err}")


def get_json_binance(path, params=None):
    """依次尝试各个 Binance 主机，哪个通用哪个。"""
    errs = []
    for host in BINANCE_HOSTS:
        try:
            return get_json(host + path, params, retries=2)
        except Exception as e:  # noqa: BLE001
            errs.append(str(e)[:100])
    raise RuntimeError(" | ".join(errs))


# ---------- 拉 K 线，统一返回按时间升序的 [(close, high, low), ...]，只含已收盘的 K 线 ----------

def base_symbol(s):
    s = s.strip().upper().replace("-", "").replace("/", "")
    for q in ("USDT", "USDC"):
        if s.endswith(q) and len(s) > len(q):
            s = s[: -len(q)]
    return s


def klines_binance(base, limit=400):
    data = get_json_binance("/api/v3/klines",
                            {"symbol": base + "USDT", "interval": "1d", "limit": limit})
    now_ms = time.time() * 1000
    return [(float(k[4]), float(k[2]), float(k[3])) for k in data if k[6] < now_ms]


def klines_okx(base, limit=300):
    data = get_json(OKX + "/api/v5/market/candles",
                    {"instId": f"{base}-USDT", "bar": "1D", "limit": limit})
    if data.get("code") != "0":
        raise RuntimeError(f"OKX: {data.get('msg')}")
    rows = [(float(k[4]), float(k[2]), float(k[3])) for k in data["data"] if k[8] == "1"]
    rows.reverse()
    return rows


def fetch_rows(base, exchange):
    """按 exchange 取数：binance / okx / auto（先 binance，失败换 okx）。返回 (rows, source)。"""
    order = {"binance": ["binance"], "okx": ["okx"], "auto": ["binance", "okx"]}[exchange]
    errs = []
    for ex in order:
        try:
            rows = klines_binance(base) if ex == "binance" else klines_okx(base)
            if rows:
                return rows, ex
            errs.append(f"{ex}: 无数据")
        except Exception as e:  # noqa: BLE001
            errs.append(str(e)[:90])
    raise RuntimeError(" | ".join(errs))


def top_volume_binance(n):
    tickers = get_json_binance("/api/v3/ticker/24hr", {"type": "MINI"})
    out = []
    for t in tickers:
        s = t["symbol"]
        if not s.endswith("USDT"):
            continue
        b = s[:-4]
        if b.endswith(STABLE_OR_LEVERAGED) or b in STABLE_OR_LEVERAGED:
            continue
        out.append((float(t["quoteVolume"]), b))
    out.sort(reverse=True)
    return [b for _, b in out[:n]]


def top_volume_okx(n):
    data = get_json(OKX + "/api/v5/market/tickers", {"instType": "SPOT"})
    out = []
    for t in data["data"]:
        inst = t["instId"]
        if not inst.endswith("-USDT"):
            continue
        b = inst[:-5]
        if b.endswith(STABLE_OR_LEVERAGED) or b in STABLE_OR_LEVERAGED:
            continue
        out.append((float(t["volCcy24h"]), b))
    out.sort(reverse=True)
    return [b for _, b in out[:n]]


# ---------- 指标计算 ----------

def stdev(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def sma(xs, n):
    return [None] * (n - 1) + [sum(xs[i - n + 1: i + 1]) / n for i in range(n - 1, len(xs))]


def ema(xs, n):
    k = 2 / (n + 1)
    out = [xs[0]]
    for x in xs[1:]:
        out.append(x * k + out[-1] * (1 - k))
    return out


def linreg_end(vals):
    """对 vals 做线性回归，返回回归线在最后一点的值（LazyBear SQZMOM 的动量算法）。"""
    L = len(vals)
    xs = list(range(L))
    xm, ym = (L - 1) / 2, sum(vals) / L
    den = sum((x - xm) ** 2 for x in xs)
    b = sum((x - xm) * (v - ym) for x, v in zip(xs, vals)) / den if den else 0.0
    a = ym - b * xm
    return a + b * (L - 1)


def percentile_rank(history, current):
    """current 在 history 里处于第几百分位，0 表示历史最低，100 表示历史最高。"""
    hist = [h for h in history if h is not None]
    if not hist or current is None:
        return None
    below = sum(1 for h in hist if h < current)
    return 100.0 * below / len(hist)


def analyze(rows, window=20, lookback=252, bb_mult=2.0, kc_mult=1.5, spark_days=60):
    closes = [r[0] for r in rows]
    highs = [r[1] for r in rows]
    lows = [r[2] for r in rows]
    n = len(closes)
    if n < window + 30:
        raise RuntimeError(f"K 线太少（{n} 根），至少要 {window + 30} 根")

    # 20 日对数收益率标准差，年化
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, n)]
    vol = [None] * window
    for i in range(window, n):
        vol.append(stdev(rets[i - window: i]) * math.sqrt(365) * 100)

    # 布林带宽度 = (上轨-下轨)/中轨
    mid = sma(closes, window)
    sd_list = [None] * n
    bbw = [None] * n
    for i in range(window - 1, n):
        sd_list[i] = stdev(closes[i - window + 1: i + 1])
        bbw[i] = 2 * bb_mult * sd_list[i] / mid[i] if mid[i] else None

    # 肯特纳通道 EMA20 ± 1.5*ATR20；挤压 = 布林带整体在通道之内
    tr = [highs[0] - lows[0]]
    for i in range(1, n):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    atr = sma(tr, window)
    e = ema(closes, window)
    squeeze = [False] * n
    for i in range(window - 1, n):
        bb_up, bb_dn = mid[i] + bb_mult * sd_list[i], mid[i] - bb_mult * sd_list[i]
        kc_up, kc_dn = e[i] + kc_mult * atr[i], e[i] - kc_mult * atr[i]
        squeeze[i] = bb_up < kc_up and bb_dn > kc_dn
    sq_days = 0
    for s in reversed(squeeze):
        if not s:
            break
        sq_days += 1

    # SQZMOM 动量：close 减去 (区间中点 + SMA)/2，再取 20 日线性回归末端值
    hi20 = [None] * n
    lo20 = [None] * n
    mom_src = [None] * n
    for i in range(window - 1, n):
        hi20[i] = max(highs[i - window + 1: i + 1])
        lo20[i] = min(lows[i - window + 1: i + 1])
        mom_src[i] = closes[i] - ((hi20[i] + lo20[i]) / 2 + mid[i]) / 2
    mom = [None] * n
    for i in range(2 * window - 2, n):
        mom[i] = linreg_end(mom_src[i - window + 1: i + 1])
    mom_now = mom[-1] / closes[-1] * 100 if mom[-1] is not None else None
    mom_prev = mom[-2] / closes[-2] * 100 if len(mom) > 1 and mom[-2] is not None else None
    if mom_now is None or mom_prev is None:
        mom_trend = "flat"
    else:
        mom_trend = "up" if mom_now > mom_prev else "down" if mom_now < mom_prev else "flat"

    lb = min(lookback, n)
    cur_vol, cur_bbw = vol[-1], bbw[-1]

    # 最近 spark_days 天的 bbw_pct 走势，用于页面上的迷你曲线
    spark = []
    for i in range(max(window, n - spark_days), n):
        hist = bbw[max(window - 1, i - lb): i]
        p = percentile_rank(hist, bbw[i])
        spark.append(round(p, 1) if p is not None else None)

    chg30 = (closes[-1] / closes[-31] - 1) * 100 if n > 31 else None
    chg7 = (closes[-1] / closes[-8] - 1) * 100 if n > 8 else None
    return {
        "price": closes[-1],
        "chg7": chg7,
        "chg30": chg30,
        "vol20": cur_vol,
        "vol_pct": percentile_rank(vol[-lb:-1], cur_vol),
        "bbw": cur_bbw * 100,
        "bbw_pct": percentile_rank(bbw[-lb:-1], cur_bbw),
        "squeeze_days": sq_days,
        "mom": mom_now,
        "mom_trend": mom_trend,
        "hi20": hi20[-1],
        "lo20": lo20[-1],
        "to_hi20": (hi20[-1] / closes[-1] - 1) * 100,
        "to_lo20": (lo20[-1] / closes[-1] - 1) * 100,
        "spark": spark,
        "bars": n,
        "lookback_used": lb,
    }


def classify(r, flag=10.0):
    p = r["bbw_pct"]
    if p is None:
        return "数据不足"
    if p <= flag:
        return "★ 极度压缩"
    if r["squeeze_days"] > 0:
        return "挤压中"
    if p <= 25:
        return "偏压缩"
    if p >= 90:
        return "膨胀"
    return "普通"


# ---------- 输出 ----------

def fmt(v, spec="{:.1f}"):
    return "-" if v is None else spec.format(v)


def print_table(results, flag, lookback):
    head = (f"{'':2}{'symbol':<9}{'src':<4}{'price':>11}{'chg30%':>8}{'vol20%':>8}{'vol_pct':>8}"
            f"{'bbw_pct':>8}{'squeeze':>8}{'mom%':>7}{'trend':>6}{'to_hi20%':>9}{'bars':>6}")
    print(head)
    print("-" * len(head))
    for r in results:
        star = "*" if r["bbw_pct"] is not None and r["bbw_pct"] <= flag else " "
        sq = f"on {r['squeeze_days']}d" if r["squeeze_days"] else "off"
        print(f"{star:2}{r['symbol']:<9}{r['source'][:3]:<4}{fmt(r['price'], '{:.4g}'):>11}"
              f"{fmt(r['chg30'], '{:+.1f}'):>8}{fmt(r['vol20']):>8}{fmt(r['vol_pct']):>8}"
              f"{fmt(r['bbw_pct']):>8}{sq:>8}{fmt(r['mom'], '{:+.1f}'):>7}{r['mom_trend']:>6}"
              f"{fmt(r['to_hi20'], '{:+.1f}'):>9}{r['bars']:>6}")
    if results:
        lb = min(r["lookback_used"] for r in results)
        print(f"\n排序：bbw_pct 从低到高。* 表示 bbw_pct <= {flag:g}。"
              f"回看期 {lookback} 日（数据最少的币实际 {lb} 日）。")


def update_history(path, summary, keep=400):
    try:
        with open(path, encoding="utf-8") as f:
            hist = json.load(f)
    except (OSError, ValueError):
        hist = []
    hist = [h for h in hist if h.get("date") != summary["date"]]
    hist.append(summary)
    hist = hist[-keep:]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False)
    return hist


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>加密货币 · 波动率压缩观察站</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0f1117;--card:#1a1d2e;--card2:#20243a;--accent:#e63946;--green:#2ec4b6;
  --yellow:#ffd166;--dim:#8b8fa8;--border:#2a2d3e;--text:#e2e8f0;--blue:#4a90d9;--purple:#b892ff;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'SF Pro Display',-apple-system,sans-serif;min-height:100vh;}
header{background:var(--card);border-bottom:1px solid var(--border);padding:16px 28px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
header h1{font-size:1.15rem;font-weight:700;}
.meta{color:var(--dim);font-size:.8rem;margin-left:auto;}
.stats{padding:18px 28px 0;display:flex;gap:12px;flex-wrap:wrap;}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 16px;min-width:150px;flex:1;}
.stat-card .val{font-size:1.5rem;font-weight:700;}
.stat-card .lbl{font-size:.72rem;color:var(--dim);margin-top:2px;}
.regime{padding:14px 28px 0;}
.regime-box{background:var(--card);border:1px solid var(--border);border-left:4px solid var(--blue);border-radius:10px;padding:12px 16px;font-size:.88rem;line-height:1.55;}
.chart-wrap{margin:14px 28px 0;background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 18px;}
.chart-title{font-size:.85rem;font-weight:600;margin-bottom:8px;}
canvas#histChart{max-height:150px;}
.controls{padding:16px 28px 8px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
.controls label{color:var(--dim);font-size:.82rem;}
select,input[type=text]{background:var(--card);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:6px;font-size:.82rem;outline:none;}
#search{width:180px;}
.chk{display:flex;align-items:center;gap:6px;font-size:.82rem;color:var(--dim);cursor:pointer;}
.table-wrap{padding:0 28px 24px;overflow-x:auto;}
table{width:100%;border-collapse:collapse;font-size:.82rem;}
thead tr{background:var(--card);}
th{padding:9px 10px;text-align:left;color:var(--dim);font-weight:500;border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;}
th:hover{color:var(--text);}
th.nosort{cursor:default;}
td{padding:8px 10px;border-bottom:1px solid var(--border);white-space:nowrap;}
tr:hover td{background:var(--card);}
.red{color:var(--accent);} .green{color:var(--green);} .yellow{color:var(--yellow);} .dim{color:var(--dim);} .blue{color:var(--blue);} .purple{color:var(--purple);}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.74rem;font-weight:600;white-space:nowrap;}
.b-ext{background:rgba(74,144,217,.22);color:var(--blue);}
.b-sq{background:rgba(184,146,255,.18);color:var(--purple);}
.b-low{background:rgba(46,196,182,.15);color:var(--green);}
.b-hot{background:rgba(230,57,70,.18);color:var(--accent);}
.b-mid{background:rgba(139,143,168,.12);color:var(--dim);}
.bar-bg{display:inline-block;width:60px;height:7px;background:var(--card2);border-radius:4px;vertical-align:middle;margin-right:6px;overflow:hidden;}
.bar-fg{display:block;height:100%;border-radius:4px;}
.src{font-size:.68rem;color:var(--dim);margin-left:4px;}
.watch{color:var(--yellow);margin-right:3px;}
.no-data{text-align:center;color:var(--dim);padding:50px;}
.help{padding:0 28px 48px;max-width:980px;}
.help h2{font-size:1rem;margin:22px 0 8px;}
.help h3{font-size:.9rem;margin:14px 0 6px;color:var(--yellow);}
.help p,.help li{font-size:.85rem;line-height:1.65;color:#c9cfdf;}
.help ul{padding-left:20px;}
.help code{background:var(--card2);padding:1px 5px;border-radius:4px;font-size:.8rem;}
.failed{padding:0 28px 24px;font-size:.78rem;color:var(--dim);}
</style>
</head>
<body>
<header>
  <span style="font-size:1.7rem">🫧</span>
  <h1>加密货币 · 波动率压缩观察站</h1>
  <div class="meta">生成时间：__GENERATED__（北京时间） &nbsp;|&nbsp; 数据：Binance 日线，缺失用 OKX 兜底 &nbsp;|&nbsp; 回看期 __LOOKBACK__ 日</div>
</header>

<div class="stats">
  <div class="stat-card"><div class="val" id="s-total">0</div><div class="lbl">扫描币种</div></div>
  <div class="stat-card"><div class="val blue" id="s-ext">0</div><div class="lbl">★ 极度压缩（bbw_pct ≤ __FLAG__）</div></div>
  <div class="stat-card"><div class="val purple" id="s-sq">0</div><div class="lbl">挤压中（布林带缩进肯特纳通道）</div></div>
  <div class="stat-card"><div class="val green" id="s-low">0</div><div class="lbl">偏压缩（bbw_pct ≤ 25）</div></div>
  <div class="stat-card"><div class="val red" id="s-hot">0</div><div class="lbl">膨胀（bbw_pct ≥ 90）</div></div>
  <div class="stat-card"><div class="val yellow" id="s-avg">-</div><div class="lbl">全市场 bbw_pct 中位数</div></div>
</div>

<div class="regime"><div class="regime-box" id="regime"></div></div>

<div class="chart-wrap">
  <div class="chart-title">历史走势：全市场 bbw_pct 中位数（线）与极度压缩币数（柱）</div>
  <canvas id="histChart"></canvas>
  <div id="histNote" class="dim" style="font-size:.78rem;margin-top:6px;"></div>
</div>

<div class="controls">
  <label>状态</label>
  <select id="stateFilter" onchange="applyFilters()">
    <option value="">全部</option>
    <option value="★ 极度压缩">★ 极度压缩</option>
    <option value="挤压中">挤压中</option>
    <option value="偏压缩">偏压缩</option>
    <option value="普通">普通</option>
    <option value="膨胀">膨胀</option>
  </select>
  <label class="chk"><input type="checkbox" id="watchOnly" onchange="applyFilters()"> 只看自选</label>
  <input type="text" id="search" placeholder="搜索币种…" oninput="applyFilters()">
  <span class="dim" style="font-size:.78rem">点表头排序。默认按 bbw_pct 从低到高，最压缩的在最上面。</span>
</div>

<div class="table-wrap">
<table>
<thead><tr>
  <th onclick="sortBy('symbol')">币种 ↕</th>
  <th onclick="sortBy('state_rank')">状态 ↕</th>
  <th onclick="sortBy('price')">价格 ↕</th>
  <th onclick="sortBy('chg7')">7日 ↕</th>
  <th onclick="sortBy('chg30')">30日 ↕</th>
  <th onclick="sortBy('vol20')">年化波动 ↕</th>
  <th onclick="sortBy('vol_pct')">vol_pct ↕</th>
  <th onclick="sortBy('bbw_pct')">bbw_pct ↕</th>
  <th class="nosort">bbw_pct 近60日</th>
  <th onclick="sortBy('squeeze_days')">挤压天数 ↕</th>
  <th onclick="sortBy('mom')">动量 ↕</th>
  <th class="nosort">20日区间 低 – 高</th>
  <th onclick="sortBy('to_hi20')">距上沿 ↕</th>
</tr></thead>
<tbody id="tableBody"></tbody>
</table>
<div class="no-data" id="noData" style="display:none">没有符合条件的币</div>
</div>

<div class="failed" id="failed"></div>

<div class="help">
  <h2>这张表怎么看</h2>
  <p>这张表只回答一个问题：<b>哪个币现在处在"憋大招"的状态</b>。它不预测方向，也不给买入价。用法是每天扫一眼，等状态列出现 ★ 或"挤压中"，再打开 K 线细看。</p>

  <h3>各列含义</h3>
  <ul>
    <li><b>状态</b>：★ 极度压缩 = bbw_pct 低于 __FLAG__，对应 TradingView BBWP 的蓝柱；挤压中 = 布林带缩进肯特纳通道，对应 SQZMOM 的黑点；膨胀 = bbw_pct 高于 90，对应 BBWP 的红柱，意思是这波动作快耗尽了。</li>
    <li><b>年化波动</b>：这个币本身有多野。ETH 通常 40 到 60，小币常见 100 以上。这是绝对值，不用来比"压不压缩"。</li>
    <li><b>vol_pct / bbw_pct</b>：核心列。当前波动跟它自己过去一年比处在哪个位置，0 是一年最安静，100 是一年最疯狂。两列算法不同但意思一样，bbw_pct 更贴近 TradingView 的 BBWP。</li>
    <li><b>bbw_pct 近60日</b>：迷你曲线。曲线从高处一路滑到底部，说明压缩正在形成；在底部趴了很久，说明憋得久。</li>
    <li><b>挤压天数</b>：布林带缩在肯特纳通道里的连续天数。天数越多，释放时的幅度通常越大。</li>
    <li><b>动量</b>：SQZMOM 的动量柱，换算成占价格的百分比。正数偏多，负数偏空；箭头是跟前一天比在升还是降。挤压释放时看它的颜色定方向。</li>
    <li><b>20日区间 / 距上沿</b>：最近 20 天的最低价和最高价，以及现价离上沿还有多远。挤压释放时，向上突破区间上沿是多头入场参考，跌破下沿是空头参考。</li>
  </ul>

  <h3>怎么用</h3>
  <ul>
    <li>先看顶部的市场状态。全市场中位数高于 60 时是膨胀期，大家都在动，这时候不找挤压，找的是"膨胀"列里跑过头的币。</li>
    <li>中位数低于 30 时是压缩期，逐个看 ★ 和"挤压中"的币，优先挤压天数多、动量刚从负转正或从正转负的。</li>
    <li>挤压释放的确认：bbw_pct 从底部开始抬头，同时价格突破 20 日区间的上沿或下沿，动量箭头与突破方向一致。三个条件凑齐再考虑进场，仓位按年化波动大小反向调整。</li>
    <li>压缩出现在 30 日大涨之后多半是中继，出现在 30 日大跌之后多半是底部整理。看 30 日列区分。</li>
  </ul>

  <h3>数据说明</h3>
  <p>币种范围是 Binance 24 小时成交额前 __TOPN__ 的 USDT 交易对加上 <code>watchlist.txt</code> 里的自选（自选前面有 ★ 标记）。上线不足 50 天的新币跳过。Binance 没有的币自动用 OKX 数据，币种后面标 okx。每天北京时间早上 8 点 20 分左右自动更新，日线以 UTC 零点收盘为准。</p>
</div>

<script>
const RAW = __DATA__;
const HIST = __HISTORY__;
const FAILED = __FAILED__;
const FLAG = __FLAG__;
const STATE_RANK = {"★ 极度压缩":0,"挤压中":1,"偏压缩":2,"普通":3,"膨胀":4,"数据不足":5};
RAW.forEach(r => { r.state_rank = STATE_RANK[r.state] ?? 9; });
let sortKey = 'bbw_pct', sortAsc = true;

function fmt(v, d=1, sign=false) {
  if (v === null || v === undefined || Number.isNaN(v)) return '-';
  const s = v.toFixed(d);
  return sign && v > 0 ? '+' + s : s;
}
function fmtPrice(v) {
  if (v === null || v === undefined) return '-';
  if (v >= 1000) return v.toLocaleString('en-US', {maximumFractionDigits: 0});
  if (v >= 1) return v.toFixed(v >= 100 ? 1 : 2);
  return v.toPrecision(3);
}
function badge(state) {
  const cls = {"★ 极度压缩":"b-ext","挤压中":"b-sq","偏压缩":"b-low","膨胀":"b-hot"}[state] || "b-mid";
  return `<span class="badge ${cls}">${state}</span>`;
}
function pctColor(p) {
  if (p === null || p === undefined) return 'var(--dim)';
  if (p <= FLAG) return 'var(--blue)';
  if (p <= 25) return 'var(--green)';
  if (p >= 90) return 'var(--accent)';
  return 'var(--yellow)';
}
function pctBar(p) {
  if (p === null || p === undefined) return '-';
  return `<span class="bar-bg"><span class="bar-fg" style="width:${Math.max(2,Math.round(p*0.6))}px;background:${pctColor(p)}"></span></span>${p.toFixed(1)}`;
}
function spark(arr) {
  const pts = (arr || []).filter(v => v !== null && v !== undefined);
  if (pts.length < 2) return '';
  const w = 90, h = 22, n = arr.length;
  const path = arr.map((v, i) => v === null ? null : `${(i/(n-1)*w).toFixed(1)},${(h - v/100*(h-2) - 1).toFixed(1)}`).filter(Boolean).join(' ');
  const last = pts[pts.length-1];
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><line x1="0" y1="${h-FLAG/100*(h-2)-1}" x2="${w}" y2="${h-FLAG/100*(h-2)-1}" stroke="#2a2d3e" stroke-width="1"/><polyline fill="none" stroke="${pctColor(last)}" stroke-width="1.5" points="${path}"/></svg>`;
}
function chgCell(v) {
  return `<td class="${v < 0 ? 'red' : v > 0 ? 'green' : ''}">${fmt(v,1,true)}%</td>`;
}
function momCell(r) {
  if (r.mom === null || r.mom === undefined) return '<td>-</td>';
  const arrow = r.mom_trend === 'up' ? '↑' : r.mom_trend === 'down' ? '↓' : '→';
  const cls = r.mom > 0 ? 'green' : r.mom < 0 ? 'red' : '';
  return `<td class="${cls}">${fmt(r.mom,1,true)}% ${arrow}</td>`;
}

function applyFilters() {
  const st = document.getElementById('stateFilter').value;
  const wo = document.getElementById('watchOnly').checked;
  const q = document.getElementById('search').value.trim().toLowerCase();
  let data = RAW.filter(r => {
    if (st && r.state !== st) return false;
    if (wo && !r.watch) return false;
    if (q && !r.symbol.toLowerCase().includes(q)) return false;
    return true;
  });
  data.sort((a,b) => {
    let va = a[sortKey], vb = b[sortKey];
    if (va === null || va === undefined) return 1;
    if (vb === null || vb === undefined) return -1;
    if (typeof va === 'string') return sortAsc ? va.localeCompare(vb) : vb.localeCompare(va);
    return sortAsc ? va - vb : vb - va;
  });
  render(data);
}
function sortBy(key) {
  if (sortKey === key) sortAsc = !sortAsc; else { sortKey = key; sortAsc = key === 'symbol' || key === 'bbw_pct' || key === 'vol_pct' || key === 'state_rank'; }
  applyFilters();
}
function render(data) {
  const tbody = document.getElementById('tableBody');
  const noData = document.getElementById('noData');
  if (!data.length) { tbody.innerHTML=''; noData.style.display='block'; return; }
  noData.style.display='none';
  tbody.innerHTML = data.map(r => `<tr>
    <td>${r.watch ? '<span class="watch">★</span>' : ''}<b>${r.symbol}</b>${r.source === 'okx' ? '<span class="src">okx</span>' : ''}</td>
    <td>${badge(r.state)}</td>
    <td>${fmtPrice(r.price)}</td>
    ${chgCell(r.chg7)}
    ${chgCell(r.chg30)}
    <td>${fmt(r.vol20,0)}%</td>
    <td>${pctBar(r.vol_pct)}</td>
    <td>${pctBar(r.bbw_pct)}</td>
    <td>${spark(r.spark)}</td>
    <td>${r.squeeze_days ? '<span class="purple">on ' + r.squeeze_days + 'd</span>' : '<span class="dim">off</span>'}</td>
    ${momCell(r)}
    <td class="dim">${fmtPrice(r.lo20)} – ${fmtPrice(r.hi20)}</td>
    <td>${fmt(r.to_hi20,1,true)}%</td>
  </tr>`).join('');
}

function median(xs) { const a = xs.filter(v => v !== null && v !== undefined).sort((x,y)=>x-y); if (!a.length) return null; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m-1]+a[m])/2; }
function initStats() {
  const ext = RAW.filter(r => r.state === '★ 极度压缩').length;
  const sq  = RAW.filter(r => r.squeeze_days > 0).length;
  const low = RAW.filter(r => r.bbw_pct !== null && r.bbw_pct <= 25).length;
  const hot = RAW.filter(r => r.state === '膨胀').length;
  const med = median(RAW.map(r => r.bbw_pct));
  document.getElementById('s-total').textContent = RAW.length;
  document.getElementById('s-ext').textContent = ext;
  document.getElementById('s-sq').textContent = sq;
  document.getElementById('s-low').textContent = low;
  document.getElementById('s-hot').textContent = hot;
  document.getElementById('s-avg').textContent = med === null ? '-' : med.toFixed(0);
  let regime, color;
  if (med === null) { regime = '数据不足。'; color = 'var(--dim)'; }
  else if (med >= 60) { regime = `<b>膨胀期</b>：全市场 bbw_pct 中位数 ${med.toFixed(0)}，多数币都在大幅波动。这不是找挤压的时候，重点看"膨胀"列里跑过头的币，等它们的波动率回落，别追高。`; color = 'var(--accent)'; }
  else if (med <= 30) { regime = `<b>压缩期</b>：全市场 bbw_pct 中位数 ${med.toFixed(0)}，市场整体在憋。逐个看 ★ 和"挤压中"的币，优先挤压天数多、动量刚翻转的，等突破 20 日区间再动手。`; color = 'var(--blue)'; }
  else { regime = `<b>过渡期</b>：全市场 bbw_pct 中位数 ${med.toFixed(0)}，冷热不均。只看个别 ★ 的币，其余观望。`; color = 'var(--yellow)'; }
  const box = document.getElementById('regime');
  box.innerHTML = regime; box.style.borderLeftColor = color;
  if (FAILED.length) document.getElementById('failed').textContent = '未能拉取（两个交易所都没有或上线太短）：' + FAILED.map(f => f[0]).join('、');
}
function initChart() {
  const note = document.getElementById('histNote');
  if (!HIST || HIST.length < 2) { note.textContent = '历史图需要至少两天的数据，明天开始积累。'; return; }
  const ctx = document.getElementById('histChart').getContext('2d');
  new Chart(ctx, {
    data: {
      labels: HIST.map(h => h.date),
      datasets: [
        { type: 'line', label: 'bbw_pct 中位数', data: HIST.map(h => h.median_bbw_pct), borderColor: '#ffd166', backgroundColor: 'rgba(255,209,102,.15)', tension: .3, pointRadius: 2, yAxisID: 'y' },
        { type: 'bar', label: '极度压缩币数', data: HIST.map(h => h.extreme), backgroundColor: 'rgba(74,144,217,.55)', yAxisID: 'y2' },
      ]
    },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { labels: { color: '#8b8fa8', boxWidth: 10 } } },
      scales: { x: { ticks: { color: '#8b8fa8', maxTicksLimit: 12 }, grid: { color: '#2a2d3e' } },
        y: { min: 0, max: 100, ticks: { color: '#8b8fa8' }, grid: { color: '#2a2d3e' } },
        y2: { position: 'right', min: 0, ticks: { color: '#8b8fa8', precision: 0 }, grid: { display: false } } } }
  });
}
initStats(); initChart(); applyFilters();
</script>
</body>
</html>
"""


def build_html(results, history, failed, args, generated):
    for r in results:
        r["state"] = classify(r, args.flag)
    slim = [{k: v for k, v in r.items() if k not in ("lookback_used",)} for r in results]
    html = HTML_TEMPLATE
    html = html.replace("__DATA__", json.dumps(slim, ensure_ascii=False))
    html = html.replace("__HISTORY__", json.dumps(history, ensure_ascii=False))
    html = html.replace("__FAILED__", json.dumps(failed, ensure_ascii=False))
    html = html.replace("__GENERATED__", generated)
    html = html.replace("__LOOKBACK__", str(args.lookback))
    html = html.replace("__FLAG__", f"{args.flag:g}")
    html = html.replace("__TOPN__", str(args.top_volume or 0))
    return html


# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser(description="扫描一批币的波动率压缩程度")
    ap.add_argument("symbols", nargs="*", help="币种，如 ETH SOL 或 ETHUSDT")
    ap.add_argument("--file", help="每行一个币种的自选文件，页面上会标 ★")
    ap.add_argument("--exchange", choices=["auto", "binance", "okx"], default="auto",
                    help="auto = 先 Binance，拉不到换 OKX")
    ap.add_argument("--top-volume", type=int, metavar="N", help="自动取 24h 成交额前 N 的 USDT 交易对")
    ap.add_argument("--window", type=int, default=20, help="波动率窗口，默认 20 日")
    ap.add_argument("--lookback", type=int, default=252, help="百分位回看期，默认 252 日")
    ap.add_argument("--flag", type=float, default=10.0, help="bbw_pct 低于该值算极度压缩，默认 10")
    ap.add_argument("--csv", help="结果另存为 CSV")
    ap.add_argument("--json", help="结果另存为 JSON（含迷你曲线数据）")
    ap.add_argument("--history", help="历史汇总 JSON，每天追加一条")
    ap.add_argument("--html", help="生成 HTML 报告")
    ap.add_argument("--quiet", action="store_true", help="不打印终端表格")
    args = ap.parse_args()

    watch = []
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            watch = [base_symbol(line.split("#")[0]) for line in f
                     if line.split("#")[0].strip()]
    symbols = [base_symbol(s) for s in args.symbols] + watch
    if args.top_volume:
        fetch_top = top_volume_binance if args.exchange != "okx" else top_volume_okx
        symbols += fetch_top(args.top_volume)
    symbols = list(dict.fromkeys(symbols))
    if not symbols:
        ap.error("没有币种。给几个币种名，或用 --file / --top-volume")
    watch_set = set(watch) | {base_symbol(s) for s in args.symbols}

    results, failed = [], []
    for i, sym in enumerate(symbols, 1):
        print(f"\r[{i}/{len(symbols)}] {sym:<10}", end="", file=sys.stderr, flush=True)
        try:
            rows, source = fetch_rows(sym, args.exchange)
            r = analyze(rows, args.window, args.lookback)
            r["symbol"] = sym
            r["source"] = source
            r["watch"] = sym in watch_set
            results.append(r)
        except Exception as e:  # noqa: BLE001
            failed.append((sym, str(e)[:120]))
        time.sleep(0.25)
    print("\r" + " " * 30 + "\r", end="", file=sys.stderr)

    results.sort(key=lambda r: (r["bbw_pct"] if r["bbw_pct"] is not None else 999))
    for r in results:
        r["state"] = classify(r, args.flag)

    if not args.quiet:
        print_table(results, args.flag, args.lookback)
        if failed:
            print(f"\n失败 {len(failed)} 个：")
            for sym, err in failed:
                print(f"  {sym}: {err}")

    now = datetime.now(BEIJING)
    generated = now.strftime("%Y-%m-%d %H:%M")
    history = []
    if args.history:
        pcts = sorted(r["bbw_pct"] for r in results if r["bbw_pct"] is not None)
        med = None
        if pcts:
            m = len(pcts) // 2
            med = pcts[m] if len(pcts) % 2 else (pcts[m - 1] + pcts[m]) / 2
        summary = {
            "date": now.strftime("%Y-%m-%d"),
            "scanned": len(results),
            "extreme": sum(1 for r in results if r["state"] == "★ 极度压缩"),
            "squeeze_on": sum(1 for r in results if r["squeeze_days"] > 0),
            "expanded": sum(1 for r in results if r["state"] == "膨胀"),
            "median_bbw_pct": round(med, 1) if med is not None else None,
            "extreme_symbols": [r["symbol"] for r in results if r["state"] == "★ 极度压缩"],
        }
        history = update_history(args.history, summary)
        print(f"历史已更新 {args.history}（{len(history)} 天）")

    if args.csv and results:
        cols = ["symbol", "source", "state", "price", "chg7", "chg30", "vol20", "vol_pct", "bbw",
                "bbw_pct", "squeeze_days", "mom", "mom_trend", "hi20", "lo20", "to_hi20", "bars"]
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
        print(f"已保存 {args.csv}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"generated": generated, "lookback": args.lookback, "flag": args.flag,
                       "results": results, "failed": failed}, f, ensure_ascii=False)
        print(f"已保存 {args.json}")

    if args.html:
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(build_html(results, history, failed, args, generated))
        print(f"已生成 {args.html}")


if __name__ == "__main__":
    main()
