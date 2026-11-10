#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三线合一图表 · 数据抓取 + 校验 + 生成单文件离线 HTML
=====================================================
只依赖 Python 3 标准库（不需要 pip install 任何东西）。

用法：
    python build.py              # 联网抓最新数据 → 校验 → 重新生成 HTML
    python build.py --offline    # 不联网，用 raw/ 里缓存的数据重新生成 HTML
    python build.py --out 某处.html               # 输出到指定文件（也可在 site.json 里写 "out_file"）
    python build.py --offline --snapshot --out x.html
                                 # 快照版：不联网、不导出，图表库从 cdnjs 加载（给 claude.ai 预览等环境）
    python build.py --lang zh    # 只生成中文版（--lang en 只生成英文版；默认中英文两个都生成）

中英双语：template.html + guide.html 生成中文页，template_en.html + guide_en.html 生成英文页，
    两页右上角互相切换（English / 中文）。中文页输出到 out_file（默认 ../三线合一图表.html），
    英文页输出到 out_file_en；没写时自动取名：index.html → en.html，其它 xxx.html → xxx-EN.html。

site.json（可选，与本文件放在一起）：作者名、作者链接、网址、源码地址、版本号——
    填了网址（site_url）才会生成 X/Twitter 大图预览所需的 og:image。

数据源（全部免费、无需 API Key）：
    1) CoinMetrics Community API  → 每日 MVRV (CapMVRVCur) + 每日收盘参考价 (PriceUSD, UTC 00:00)
    2) Bitstamp 公共 API          → BTC/USD 日线 OHLC（用于盘中最高/最低）
    3) mempool.space              → 当前区块高度（用于估算下次减半日期）

输出：
    ../三线合一图表.html   （单文件，内嵌 ECharts + 全部数据，可离线打开）
    raw/                   （原始数据缓存）
    report.txt             （本次数据校验报告）
"""
import json, sys, os, time, datetime as dt, urllib.request, urllib.error, io

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "raw")
TEMPLATE = os.path.join(HERE, "template.html")
GUIDE = os.path.join(HERE, "guide.html")
ECHARTS = os.path.join(HERE, "echarts.min.js")
TEMPLATE_EN = os.path.join(HERE, "template_en.html")
GUIDE_EN = os.path.join(HERE, "guide_en.html")
OUT_HTML = os.path.join(os.path.dirname(HERE), "三线合一图表.html")   # 默认输出；site.json 的 out_file 或命令行 --out 可改
REPORT = os.path.join(HERE, "report.txt")

CM_URL = ("https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
          "?assets=btc&metrics=CapMVRVCur,PriceUSD&frequency=1d&start_time=2010-07-18&page_size=10000")
BS_URL = "https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=1000&start={start}"
TIP_URL = "https://mempool.space/api/blocks/tip/height"

# 四次减半（区块时间已用 mempool.space 核对，UTC）
HALVINGS = [
    {"id": "2012", "date": "2012-11-28", "block": 210000, "utc": "2012-11-28 15:24:38"},
    {"id": "2016", "date": "2016-07-09", "block": 420000, "utc": "2016-07-09 16:46:13"},
    {"id": "2020", "date": "2020-05-11", "block": 630000, "utc": "2020-05-11 19:23:43"},
    {"id": "2024", "date": "2024-04-20", "block": 840000, "utc": "2024-04-20 00:09:27"},
]
NEXT_HALVING_BLOCK = 1050000
# Bitstamp 早期（2011~2012）成交极稀疏，盘中高低点不可靠；只从第一次减半日起采用
BITSTAMP_FROM = "2012-11-28"

_log = io.StringIO()
try:  # Windows 控制台 / 重定向时避免因个别字符无法编码而报错
    sys.stdout.reconfigure(errors="replace")
except Exception:  # noqa
    pass


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    _log.write(s + "\n")


def http_json(url, tries=4):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sanxian-heyi-chart/1.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("下载失败: %s -> %s" % (url, last))


def d2s(d):
    return d.strftime("%Y-%m-%d")


def s2d(s):
    return dt.date.fromisoformat(s)


def fetch_all():
    os.makedirs(RAW, exist_ok=True)
    log("[1/3] 下载 CoinMetrics（MVRV + 收盘价）…")
    rows = []
    url = CM_URL
    while url:
        j = http_json(url)
        rows += j["data"]
        url = j.get("next_page_url")
    json.dump(rows, open(os.path.join(RAW, "coinmetrics.json"), "w"))
    log("      共 %d 行，%s → %s" % (len(rows), rows[0]["time"][:10], rows[-1]["time"][:10]))

    log("[2/3] 下载 Bitstamp 日线 OHLC…")
    out = {}
    start = int(dt.datetime(2011, 8, 1, tzinfo=dt.timezone.utc).timestamp())
    end_all = int(time.time())
    prev_last = None
    while start <= end_all:
        j = http_json(BS_URL.format(start=start))
        o = j["data"]["ohlc"]
        if not o:
            break
        for c in o:
            ts = int(c["timestamp"])
            d = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
            out[d] = [float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]), float(c["volume"])]
        last_ts = int(o[-1]["timestamp"])
        if prev_last is not None and last_ts <= prev_last:
            break  # 没有再往前推进，防止死循环
        prev_last = last_ts
        start = last_ts + 86400
        time.sleep(0.3)
    json.dump(out, open(os.path.join(RAW, "bitstamp.json"), "w"))
    ks = sorted(out)
    log("      共 %d 天，%s → %s" % (len(ks), ks[0], ks[-1]))

    log("[3/3] 读取当前区块高度…")
    try:
        tip = int(http_json(TIP_URL))
    except Exception as e:  # noqa
        tip = None
        log("      （读取失败，忽略：%s）" % e)
    json.dump({"height": tip, "utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")},
              open(os.path.join(RAW, "tip.json"), "w"))
    log("      高度 = %s" % tip)


def load_and_validate():
    cm_rows = json.load(open(os.path.join(RAW, "coinmetrics.json")))
    bs = json.load(open(os.path.join(RAW, "bitstamp.json")))
    tip = json.load(open(os.path.join(RAW, "tip.json")))

    cm = [(r["time"][:10], float(r["CapMVRVCur"]), float(r["PriceUSD"]))
          for r in cm_rows if r.get("CapMVRVCur") and r.get("PriceUSD")]
    assert cm, "CoinMetrics 数据为空"
    start, last = cm[0][0], cm[-1][0]

    log("\n===== 数据校验 =====")
    # 1) 连续性
    n_expected = (s2d(last) - s2d(start)).days + 1
    dates = [r[0] for r in cm]
    gaps = [(a, b) for a, b in zip(dates, dates[1:]) if (s2d(b) - s2d(a)).days != 1]
    log("CoinMetrics: %s → %s，应有 %d 天，实有 %d 天，日期断档 %d 处" % (start, last, n_expected, len(cm), len(gaps)))
    assert not gaps and len(cm) == n_expected, "CoinMetrics 日期不连续！%s" % gaps[:5]
    assert all(m > 0 and p > 0 for _, m, p in cm), "存在非正数"
    # 2) 单日异常跳变（>40%）提示
    jumps = [(cm[i][0], cm[i][2] / cm[i - 1][2] - 1) for i in range(1, len(cm)) if cm[i][0] >= "2012-11-28" and abs(cm[i][2] / cm[i - 1][2] - 1) > 0.40]
    log("2012-11-28 之后单日涨跌幅 >40%% 的天数: %d %s" % (len(jumps), jumps[:5]))

    # 3) Bitstamp 对比
    hi, lo = [], []
    devs = []
    for d, m, p in cm:
        b = bs.get(d)
        if b and d >= BITSTAMP_FROM and b[4] > 0 and b[2] > 0:
            # 盘中高低应包住收盘参考价（容差 3%），否则视为该所当日数据异常，不采用
            if b[2] * 0.97 <= p <= b[1] * 1.03:
                hi.append(b[1]); lo.append(b[2])
                devs.append(abs(b[3] / p - 1))
                continue
        hi.append(None); lo.append(None)
    devs.sort()
    n_hl = sum(1 for x in hi if x is not None)
    log("Bitstamp 盘中高/低：采用 %d 天（自 %s 起）；与 CoinMetrics 收盘价偏差 中位数 %.3f%% / 95分位 %.3f%% / 最大 %.2f%%"
        % (n_hl, BITSTAMP_FROM, devs[len(devs) // 2] * 100, devs[int(len(devs) * .95)] * 100, devs[-1] * 100))
    miss = [d for (d, _, _), h in zip(cm, hi) if d >= BITSTAMP_FROM and h is None]
    log("自 %s 起未采用 Bitstamp 高低点的日期（停机/异常/当日未收盘）: %s" % (BITSTAMP_FROM, miss))

    # 下一次减半日期估算：用第 4 次减半以来的平均出块时间外推
    avg_min, eta = None, None
    try:
        h4 = dt.datetime.strptime(HALVINGS[-1]["utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        tu = dt.datetime.strptime(tip["utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
        if tip.get("height") and tip["height"] > HALVINGS[-1]["block"]:
            avg_min = (tu - h4).total_seconds() / 60.0 / (tip["height"] - HALVINGS[-1]["block"])
            eta = (tu + dt.timedelta(minutes=avg_min * (NEXT_HALVING_BLOCK - tip["height"]))).strftime("%Y-%m-%d")
    except Exception:  # noqa
        pass
    log("下一次减半（区块 %d）估算：%s（平均 %.3f 分钟/块）" % (NEXT_HALVING_BLOCK, eta, avg_min or 0))
    data = {
        "meta": {
            "built_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "last_date": last,
            "tip_height": tip.get("height"),
            "tip_utc": tip.get("utc"),
            "next_halving_block": NEXT_HALVING_BLOCK,
            "avg_block_min": round(avg_min, 4) if avg_min else None,
            "next_halving_eta": eta,
            "sources": {
                "mvrv_close": "CoinMetrics Community API: CapMVRVCur, PriceUSD (daily, UTC)",
                "high_low": "Bitstamp public API: BTC/USD daily OHLC (from %s)" % BITSTAMP_FROM,
            },
        },
        "halvings": HALVINGS,
        "start": start,
        "mvrv": [round(m, 4) for _, m, _ in cm],
        "close": [round(p, 2) if p >= 1 else round(p, 5) for _, _, p in cm],
        "hi": hi,
        "lo": lo,
    }
    return data


def key_events(data):
    """独立于页面 JS 的第二套实现，用来交叉核对页面里表格的数字。"""
    start = s2d(data["start"])
    n = len(data["mvrv"])
    D = [d2s(start + dt.timedelta(days=i)) for i in range(n)]
    idx = {d: i for i, d in enumerate(D)}
    mv, cl, hi, lo = data["mvrv"], data["close"], data["hi"], data["lo"]
    H = [idx[h["date"]] for h in data["halvings"]]
    log("\n===== 各周期关键日期（Python 独立计算，用于核对页面表格）=====")
    res = []
    for k, h in enumerate(H):
        end = H[k + 1] - 1 if k + 1 < len(H) else n - 1
        topwin = range(h, min(end, h + 700) + 1)
        t_hi = max((i for i in topwin if hi[i] is not None), key=lambda i: hi[i])
        t_cl = max(topwin, key=lambda i: cl[i])
        m_max = max(topwin, key=lambda i: mv[i])
        after = range(t_hi, end + 1)
        b_lo = min((i for i in after if lo[i] is not None), key=lambda i: lo[i])
        b_cl = min(after, key=lambda i: cl[i])
        m_min = min(after, key=lambda i: mv[i])
        below = [i for i in after if mv[i] < 1]
        zones = []  # MVRV<1 的日子按“间隔 ≤120 天”聚成区；第一个区 = 熊市底部区
        for i in below:
            if zones and i - zones[-1][1] <= 120:
                zones[-1][1] = i; zones[-1][2] += 1
            else:
                zones.append([i, i, 1])
        r = {
            "cycle": data["halvings"][k]["id"], "halving": D[h],
            "top_intraday": (D[t_hi], hi[t_hi], t_hi - h), "top_close": (D[t_cl], cl[t_cl], t_cl - h),
            "mvrv_max": (D[m_max], mv[m_max], m_max - h),
            "bottom_intraday": (D[b_lo], lo[b_lo], b_lo - h), "bottom_close": (D[b_cl], cl[b_cl], b_cl - h),
            "mvrv_min": (D[m_min], mv[m_min], m_min - h),
            "below1": (D[below[0]], D[below[-1]], len(below)) if below else None,
            "zones": [(D[z[0]], D[z[1]], z[2]) for z in zones],
            "top_to_bottom_days": b_lo - t_hi,
            "drawdown_intraday_pct": round((lo[b_lo] / hi[t_hi] - 1) * 100, 2),
        }
        res.append(r)
        log(json.dumps(r, ensure_ascii=False))
    return res


SITE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "site.json")


def site_conf():
    """site.json（可选）：{"author": "", "author_url": "", "site_url": "", "repo_url": "", "version": "1.0", "out_file": ""}"""
    try:
        return json.load(open(SITE, encoding="utf-8"))
    except Exception:
        return {}


def meta_tags(conf, lang="zh"):
    import html as _h
    from urllib.parse import quote
    if lang == "en":
        title = "BTC Three-Line Convergence · Halving-Aligned MVRV Overlay (Interactive)"
        desc = ("Bitcoin’s MVRV after each halving, stacked so that halving day = day 0 (Murphy’s “three-line convergence”, 三线合一). "
                "Updates itself when opened; zoom and pan, hover to see each line’s real dates; full guide, bear-market timing analogs "
                "and backtests. Single file, works offline.")
    else:
        title = "BTC 三线合一 · 减半后 MVRV 重叠曲线（交互版）"
        desc = ("把比特币每一轮减半后的 MVRV 按“减半日 = 第 0 天”对齐叠放（Murphy 三线合一）。打开即自动更新数据；"
                "可缩放拖动，悬停看每条线的真实日期；附完整讲解、历史熊底时间类比与回测。单文件，离线可用。")
    url = (conf.get("site_url") or "").strip()
    if url and not url.endswith("/"):
        url += "/"
    svg = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='#141413'/>"
           "<polyline points='8,44 19,36 29,40 40,22 56,28' fill='none' stroke='#3987e5' stroke-width='5' stroke-linecap='round' stroke-linejoin='round'/>"
           "<polyline points='8,50 20,30 31,35 44,29 56,38' fill='none' stroke='#0ca30c' stroke-width='5' stroke-linecap='round' stroke-linejoin='round'/>"
           "<polyline points='8,38 21,33 33,29 45,35 56,33' fill='none' stroke='#e66767' stroke-width='5.5' stroke-linecap='round' stroke-linejoin='round'/></svg>")
    e = lambda x: _h.escape(x, quote=True)
    tags = [
        '<meta name="description" content="%s">' % e(desc),
        '<meta name="theme-color" content="#f9f9f7" media="(prefers-color-scheme: light)">',
        '<meta name="theme-color" content="#0d0d0d" media="(prefers-color-scheme: dark)">',
        '<link rel="icon" href="data:image/svg+xml,%s">' % quote(svg, safe=":/=' "),
        '<meta property="og:type" content="website">',
        '<meta property="og:title" content="%s">' % e(title),
        '<meta property="og:description" content="%s">' % e(desc),
        '<meta property="og:locale" content="%s">' % ("en_US" if lang == "en" else "zh_CN"),
        '<meta property="og:locale:alternate" content="%s">' % ("zh_CN" if lang == "en" else "en_US"),
        '<meta name="twitter:card" content="%s">' % ("summary_large_image" if url else "summary"),
        '<meta name="twitter:title" content="%s">' % e(title),
        '<meta name="twitter:description" content="%s">' % e(desc),
    ]
    if url:
        en_name = os.path.basename(conf.get("out_file_en") or "en.html")
        page = url + (en_name if lang == "en" else "")
        img = url + ("og-en.png" if lang == "en" else "og.png")
        tags += ['<meta property="og:url" content="%s">' % e(page),
                 '<meta property="og:image" content="%s">' % e(img),
                 '<meta property="og:image:width" content="1200">', '<meta property="og:image:height" content="630">',
                 '<meta name="twitter:image" content="%s">' % e(img),
                 '<link rel="canonical" href="%s">' % e(page),
                 '<link rel="alternate" hreflang="zh-CN" href="%s">' % e(url),
                 '<link rel="alternate" hreflang="en" href="%s">' % e(url + en_name),
                 '<link rel="alternate" hreflang="x-default" href="%s">' % e(url)]
    return "\n".join(tags)


def _arg(name):
    for k, a in enumerate(sys.argv):
        if a == name and k + 1 < len(sys.argv):
            return sys.argv[k + 1]
    return None


def out_html_path(conf):
    if _arg("--out"):
        return os.path.abspath(_arg("--out"))
    if conf.get("out_file"):
        return os.path.join(os.path.dirname(HERE), conf["out_file"])
    return OUT_HTML


def out_html_path_en(conf, zh_path):
    if _arg("--out-en"):
        return os.path.abspath(_arg("--out-en"))
    if conf.get("out_file_en"):
        return os.path.join(os.path.dirname(HERE), conf["out_file_en"])
    d, b = os.path.split(zh_path)
    return os.path.join(d, "en.html" if b.lower() == "index.html" else os.path.splitext(b)[0] + "-EN.html")


def build_all(data):
    """中文页 + 英文页（--lang zh / en 只生成一个；快照版默认只生成中文）。"""
    conf = site_conf()
    zh = out_html_path(conf)
    en = out_html_path_en(conf, zh)
    want = _arg("--lang") or ("zh" if "--snapshot" in sys.argv else "both")
    if want == "en" and _arg("--out") and not _arg("--out-en"):
        en = zh
    alt_for_en = "./" if os.path.basename(zh).lower() == "index.html" else os.path.basename(zh)
    if want in ("zh", "both"):
        build_html(data, "zh", zh, os.path.basename(en) if want == "both" or os.path.exists(en) else "")
    if want in ("en", "both") and os.path.exists(TEMPLATE_EN):
        build_html(data, "en", en, alt_for_en if want == "both" or os.path.exists(zh) else "")


def build_html(data, lang="zh", out=None, alt=""):
    conf = site_conf()
    out = out or out_html_path(conf)
    data = dict(data)
    snapshot = "--snapshot" in sys.argv   # 快照版：给 claude.ai 预览等不能联网的环境（去掉页面外壳，数据固定）
    data["meta"] = dict(data["meta"], site={"author": conf.get("author", ""), "author_url": conf.get("author_url", ""),
                                            "site_url": conf.get("site_url", ""), "repo_url": conf.get("repo_url", ""),
                                            "version": conf.get("version", "1.0"),
                                            "edition": "snapshot" if snapshot else "live",
                                            "live_url": conf.get("site_url", "")})
    tpl = open(TEMPLATE_EN if lang == "en" else TEMPLATE, encoding="utf-8").read()
    if "--snapshot" in sys.argv:
        alt = ""
    tpl = tpl.replace("__LANG_ALT__", alt.replace("&", "&amp;").replace('"', "&quot;"))
    ech = open(ECHARTS, encoding="utf-8").read()
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    assert "</script" not in payload.lower()
    assert "/*__ECHARTS__*/" in tpl and "/*__DATA__*/" in tpl
    gpath = GUIDE_EN if lang == "en" else GUIDE
    guide = open(gpath, encoding="utf-8").read() if os.path.exists(gpath) else ""
    assert "</script" not in guide.lower()
    if snapshot:   # 快照版放在 claude.ai 上：图表库从 cdnjs 加载（与内嵌的 5.6.0 逐字节相同）
        tpl = tpl.replace("<script>/*__ECHARTS__*/</script>", '<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"></script>')
        assert "/*__ECHARTS__*/" not in tpl
        tpl = tpl.replace('<script id="sx-data"', '<script>/*__ECHARTS__*/</script>\n<script id="sx-data"', 1)  # 占位符留给下面的替换（替换成空注释）
        ech = "/* echarts loaded from cdnjs */"
        # 快照版不提供文件下载 / 打印（按钮本来就隐藏；这里把代码也拿掉，claude.ai 页面里本来也下载不了）
        a, b = tpl.index("/* ---------- export ---------- */"), tpl.index("/* ---------- wiring ---------- */")
        tpl = tpl[:a] + "/* ---------- export ---------- */\nfunction exportCsv(){}\nfunction exportHtml(){}\n\n" + tpl[b:]
        assert tpl.count("window.print();") == 1
        tpl = tpl.replace("window.print();", "/* 快照版不打印 */")
    html = (tpl.replace("<!--__META__-->", meta_tags(conf, lang))
               .replace("<!--__GUIDE__-->", guide)
               .replace("/*__ECHARTS__*/", ech.replace("</script", "<\\/script"))
               .replace("/*__DATA__*/", payload))
    if snapshot:
        import re as _re
        html = _re.sub(r'<html lang="[^"]+">\n', "", html.replace("<!DOCTYPE html>\n", ""), count=1).replace("<head>\n", "")
        html = html.replace('<meta charset="utf-8">\n', "").replace('<meta name="viewport" content="width=device-width, initial-scale=1">\n', "")
        html = html.replace(meta_tags(conf, lang) + "\n", "")
        html = html.replace("<title>BTC 三线合一 · 减半后 MVRV 重叠曲线（交互版）</title>", "<title>BTC 三线合一</title>")
        html = html.replace("<title>BTC Three-Line Convergence · Halving-Aligned MVRV Overlay (Interactive)</title>", "<title>BTC Three-Line Convergence</title>")
        html = html.replace("</head>\n<body>\n", "").replace("</body>\n</html>", "")
        assert not _re.search(r"<(?:!DOCTYPE|html|head|body)\b", html[:4000], _re.I)
    open(out, "w", encoding="utf-8", newline="\n").write(html)
    log("\n已生成%s: %s  (%.2f MB)，数据截至 %s (UTC)" % ("英文版" if lang == "en" else "中文版", out, os.path.getsize(out) / 1e6, data["meta"]["last_date"]))


def main():
    offline = "--offline" in sys.argv
    if not offline:
        fetch_all()
    data = load_and_validate()
    ev = key_events(data)
    json.dump(data, open(os.path.join(RAW, "data.json"), "w"), separators=(",", ":"))
    json.dump(ev, open(os.path.join(RAW, "events_python.json"), "w"), ensure_ascii=False, indent=1)
    if os.path.exists(TEMPLATE) and os.path.exists(ECHARTS):
        build_all(data)
    else:
        log("（未找到 template.html / echarts.min.js，跳过 HTML 生成）")
    open(REPORT, "w", encoding="utf-8").write(_log.getvalue())


if __name__ == "__main__":
    main()
