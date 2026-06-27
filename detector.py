"""
美港股连续放量下跌检测器 v2.0
新增：中文名 | 指数去重标注 | 120/200日均线状态 | 板块ETF对比 | 历史趋势图
"""

import sys, os, json, time, datetime, webbrowser, threading
import importlib.util, smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ── 依赖检查 ──────────────────────────────────────────────────────────
REQUIRED = ["yfinance", "pandas", "requests"]
missing = [p for p in REQUIRED if not importlib.util.find_spec(p)]
if missing:
    print(f"缺少依赖包：{', '.join(missing)}\n请先运行：pip install {' '.join(missing)}")
    sys.exit(1)

import yfinance as yf
import pandas as pd

# ── 路径 ──────────────────────────────────────────────────────────────
_DIR         = os.path.dirname(os.path.abspath(__file__))
OUTPUT_HTML  = os.path.join(_DIR, "report.html")
HISTORY_FILE = os.path.join(_DIR, "history.json")

# ── 扫描参数 ──────────────────────────────────────────────────────────
VOL_MA_PERIOD = 20      # 成交量均线周期
CONSECUTIVE   = 3       # 连续下跌天数
BATCH_SIZE    = 20      # 每批并发股票数
BATCH_PAUSE   = 1.0     # 批次间暂停（秒）

# ── 板块 ETF 映射 ─────────────────────────────────────────────────────
SECTOR_ETF = {
    "Technology":             ("XLK", "科技"),
    "Financial Services":     ("XLF", "金融"),
    "Financials":             ("XLF", "金融"),
    "Healthcare":             ("XLV", "医疗"),
    "Energy":                 ("XLE", "能源"),
    "Consumer Cyclical":      ("XLY", "可选消费"),
    "Consumer Defensive":     ("XLP", "必选消费"),
    "Industrials":            ("XLI", "工业"),
    "Basic Materials":        ("XLB", "材料"),
    "Real Estate":            ("XLRE", "房地产"),
    "Utilities":              ("XLU", "公用事业"),
    "Communication Services": ("XLC", "通信"),
}

# ── AI 科技股名单（跌破均线时给特别提示）────────────────────────────
AI_TECH_TICKERS = {
    # 美股 AI / 半导体 / 大科技
    "NVDA","AMD","INTC","AVGO","QCOM","TXN","MU","AMAT","LRCX","KLAC","SMCI","ARM",
    "AAPL","MSFT","GOOGL","GOOG","META","AMZN","TSLA","ORCL","CRM","ADBE","INTU",
    "PANW","CRWD","SNPS","CDNS","MRVL","NFLX","IBM","CSCO","ACN","NOW","PLTR","DELL",
    # 港股 科技/互联网
    "0700.HK","9988.HK","3690.HK","9618.HK","0241.HK","0992.HK","6690.HK",
    "9999.HK","2018.HK","0020.HK","1810.HK","0285.HK","0268.HK",
}

# ── 中文名映射 ────────────────────────────────────────────────────────
CN_NAMES = {
    # 美股主要成分
    "AAPL":"苹果","MSFT":"微软","NVDA":"英伟达","AMZN":"亚马逊",
    "GOOGL":"谷歌A","GOOG":"谷歌C","META":"Meta","TSLA":"特斯拉",
    "AVGO":"博通","JPM":"摩根大通","V":"Visa","MA":"万事达",
    "WMT":"沃尔玛","UNH":"联合健康","XOM":"埃克森美孚","JNJ":"强生",
    "BAC":"美国银行","HD":"家得宝","CVX":"雪佛龙","MRK":"默克",
    "ABBV":"艾伯维","KO":"可口可乐","PEP":"百事可乐","COST":"好市多",
    "TMO":"赛默飞","CSCO":"思科","MCD":"麦当劳","ABT":"雅培",
    "LIN":"林德","ORCL":"甲骨文","AMD":"AMD","INTC":"英特尔",
    "QCOM":"高通","ADBE":"Adobe","INTU":"直觉软件","TXN":"德州仪器",
    "AMAT":"应用材料","LRCX":"拉姆研究","KLAC":"科磊","MU":"美光科技",
    "PANW":"派拓网络","CRWD":"CrowdStrike","SNPS":"新思科技",
    "CDNS":"楷登电子","MCHP":"微芯科技","NXPI":"恩智浦","ON":"安森美",
    "MRVL":"迈威尔","ADI":"亚德诺","FTNT":"飞塔","ANSS":"ANSYS",
    "GS":"高盛","MS":"摩根士丹利","C":"花旗","WFC":"富国银行",
    "BLK":"贝莱德","SCHW":"嘉信理财","AXP":"美国运通","SPGI":"标普全球",
    "MCO":"穆迪","ICE":"洲际交易所","CME":"芝商所","CB":"丘博保险",
    "PGR":"前进保险","MMC":"达信","AIG":"美国国际集团",
    "LLY":"礼来","PFE":"辉瑞","AMGN":"安进","GILD":"吉利德",
    "BIIB":"渤健","REGN":"再生元","VRTX":"维特克斯","ISRG":"直觉外科",
    "MDT":"美敦力","SYK":"史赛克","DHR":"丹纳赫","BDX":"碧迪医疗",
    "EW":"爱德华生命科学","IDXX":"爱德士","DXCM":"德康医疗",
    "GE":"通用电气","HON":"霍尼韦尔","CAT":"卡特彼勒","DE":"迪尔",
    "RTX":"雷神技术","LMT":"洛克希德马丁","NOC":"诺斯罗普格鲁曼",
    "BA":"波音","GD":"通用动力","UPS":"联合包裹","FDX":"联邦快递",
    "EMR":"艾默生","ETN":"伊顿","ITW":"伊利诺伊工具",
    "NFLX":"奈飞","DIS":"迪士尼","CMCSA":"康卡斯特",
    "T":"AT&T","VZ":"威瑞森","TMUS":"T-Mobile",
    "CVS":"CVS健康","MCK":"麦卡森","HCA":"HCA医疗",
    "F":"福特","GM":"通用汽车",
    "NKE":"耐克","SBUX":"星巴克","YUM":"百胜",
    "TGT":"塔吉特","LOW":"劳氏","TJX":"TJX",
    "COP":"康菲石油","SLB":"斯伦贝谢","EOG":"EOG资源",
    "NEE":"下一代能源","DUK":"杜克能源","SO":"南方公司",
    "AMT":"美国铁塔","PLD":"普洛斯","EQIX":"爱奎尼克斯",
    "LULU":"露露柠檬","BKNG":"缤客","ABNB":"爱彼迎",
    "UBER":"优步","DASH":"DoorDash",
    "CRM":"Salesforce","NOW":"ServiceNow","WDAY":"Workday",
    "ZS":"Zscaler","DDOG":"Datadog","SNOW":"雪花",
    "MELI":"MercadoLibre","PDD":"拼多多",
    "BRK-B":"伯克希尔B","BRK-A":"伯克希尔A",
    "PLTR":"Palantir","APP":"AppLovin",
    "BLDR":"博尔德建材","BRO":"博雷经纪","AOS":"史密斯",
    "PNR":"滨特尔","FE":"FirstEnergy","LHX":"L3Harris",
    "MGM":"米高梅","CPT":"坎登地产","MAA":"中大西洋公寓",
    "LH":"康德乐实验室","TFX":"泰利福","APH":"安费诺",
    # 港股
    "0700.HK":"腾讯控股","9988.HK":"阿里巴巴","3690.HK":"美团",
    "1211.HK":"比亚迪股份","9618.HK":"京东集团","9999.HK":"网易",
    "9888.HK":"百度","2318.HK":"中国平安","0939.HK":"建设银行",
    "1398.HK":"工商银行","3988.HK":"中国银行","0941.HK":"中国移动",
    "0005.HK":"汇丰控股","1299.HK":"友邦保险","0388.HK":"港交所",
    "0016.HK":"新鸿基地产","0001.HK":"长和","0011.HK":"恒生银行",
    "0002.HK":"中电控股","0003.HK":"香港中华煤气","0006.HK":"电能实业",
    "0012.HK":"恒基地产","0017.HK":"新世界发展","0019.HK":"太古股份A",
    "0066.HK":"港铁公司","0267.HK":"中信股份","0386.HK":"中国石化",
    "0857.HK":"中国石油","0883.HK":"中国海油","2628.HK":"中国人寿",
    "0669.HK":"创科实业","0175.HK":"吉利汽车","0762.HK":"中国联通",
    "0992.HK":"联想集团","1093.HK":"石药集团","1109.HK":"华润置地",
    "1177.HK":"中国生物制药","2382.HK":"舜宇光学","2388.HK":"中银香港",
    "0823.HK":"领展房产基金","3692.HK":"翰森制药","6098.HK":"碧桂园服务",
    "6862.HK":"海底捞","9633.HK":"农夫山泉","9961.HK":"携程集团",
    "1876.HK":"百威亚太","1928.HK":"金沙中国","2020.HK":"安踏体育",
    "0291.HK":"华润啤酒","0316.HK":"东方海外国际","0027.HK":"银河娱乐",
    "0101.HK":"恒隆地产","1038.HK":"长江基建","0288.HK":"万洲国际",
    "0322.HK":"康师傅控股","0241.HK":"阿里健康","2007.HK":"碧桂园",
    "2018.HK":"瑞声科技","2313.HK":"申洲国际","2331.HK":"李宁",
    "0881.HK":"中升控股","0868.HK":"信义玻璃","0960.HK":"龙湖集团",
    "1044.HK":"恒安国际","1209.HK":"华润万象生活","3968.HK":"招商银行",
    "2269.HK":"药明生物","6969.HK":"思摩尔国际","9626.HK":"哔哩哔哩",
    "1810.HK":"小米集团","2015.HK":"理想汽车","2333.HK":"长城汽车",
    "1919.HK":"中远海控","2899.HK":"紫金矿业","0914.HK":"海螺水泥",
    "0836.HK":"华润电力","0968.HK":"信义光能","2601.HK":"中国太保",
    "6618.HK":"京东健康","3618.HK":"重庆农商行","3888.HK":"中国软件国际",
    "0522.HK":"ASM Pacific","2238.HK":"广汽集团","1919.HK":"中远海控",
}


# ── 股票列表获取 ───────────────────────────────────────────────────────
def get_sp500() -> list:
    FALLBACK = [
        "MMM","AOS","ABT","ABBV","ACN","ADBE","AMD","AES","AFL","A","APD","ABNB","AKAM",
        "ALB","ARE","ALGN","ALLE","LNT","ALL","GOOGL","GOOG","MO","AMZN","AMCR","AEE",
        "AAL","AEP","AXP","AIG","AMT","AWK","AMP","AME","AMGN","APH","ADI","ANSS","AON",
        "APA","AAPL","AMAT","APTV","ACGL","ADM","ANET","AJG","AIZ","T","ATO","ADSK","ADP",
        "AZO","AVB","AVY","AXON","BKR","BALL","BAC","BK","BBWI","BAX","BDX","BRK-B","BBY",
        "BIIB","BLK","BX","BA","BSX","BMY","AVGO","BR","BRO","BF-B","BLDR","BG",
        "CDNS","CZR","CPT","CPB","COF","CAH","KMX","CCL","CARR","CAT","CBOE","CBRE",
        "CDW","CE","COR","CNC","CNX","CDAY","CF","CRL","SCHW","CHTR","CVX","CMG","CB","CHD",
        "CI","CINF","CTAS","CSCO","C","CFG","CLX","CME","CMS","KO","CTSH","CL","CMCSA",
        "CAG","COP","ED","STZ","CEG","COO","CPRT","GLW","CPAY","CTVA","CSGP","COST",
        "CTRA","CRWD","CCI","CSX","CMI","CVS","DHR","DRI","DVA","DAY","DECK","DE","DELL",
        "DAL","DVN","DXCM","FANG","DLR","DFS","DG","DLTR","D","DPZ","DOV","DOW","DHI",
        "DTE","DUK","DD","EMN","ETN","EBAY","ECL","EIX","EW","EA","ELV","LLY","EMR","ENPH",
        "ETR","EOG","EPAM","EQT","EFX","EQIX","EQR","ERIE","ESS","EL","EG","EVRG","ES",
        "EXC","EXPE","EXPD","EXR","XOM","FFIV","FDS","FICO","FAST","FRT","FDX","FIS","FITB",
        "FSLR","FE","FI","FMC","F","FTNT","FTV","FOXA","FOX","BEN","FCX","GRMN","IT","GE",
        "GEHC","GEV","GEN","GNRC","GD","GIS","GM","GPC","GILD","GS","HAL","HIG","HAS","HCA",
        "DOC","HSIC","HSY","HES","HPE","HLT","HOLX","HD","HON","HRL","HST","HWM","HPQ",
        "HUBB","HUM","HBAN","HII","IBM","IEX","IDXX","ITW","INCY","IR","PODD","INTC","ICE",
        "IFF","IP","IPG","INTU","ISRG","IVZ","INVH","IQV","IRM","JBHT","JBL","JKHY","J",
        "JNJ","JCI","JPM","JNPR","K","KVUE","KDP","KEY","KEYS","KMB","KIM","KMI","KKR",
        "KLAC","KHC","KR","LHX","LH","LRCX","LW","LVS","LDOS","LEN","LII","LIN","LYV",
        "LKQ","LMT","L","LOW","LULU","LYB","MTB","MRO","MPC","MKTX","MAR","MMC","MLM",
        "MAS","MA","MTCH","MKC","MCD","MCK","MDT","MRK","META","MET","MTD","MGM","MCHP",
        "MU","MSFT","MAA","MRNA","MHK","MOH","TAP","MDLZ","MPWR","MNST","MCO","MS",
        "MOS","MSI","MSCI","NDAQ","NTAP","NFLX","NEM","NWSA","NWS","NEE","NKE","NI","NDSN",
        "NSC","NTRS","NOC","NCLH","NRG","NUE","NVDA","NVR","NXPI","ORLY","OXY","ODFL","OMC",
        "ON","OKE","ORCL","OTIS","PCAR","PKG","PLTR","PANW","PARA","PH","PAYX","PAYC","PYPL",
        "PNR","PEP","PFE","PCG","PM","PSX","PNW","PNC","POOL","PPG","PPL","PFG","PG","PGR",
        "PLD","PRU","PEG","PTC","PSA","PHM","QRVO","PWR","QCOM","DGX","RL","RJF","RTX",
        "O","REG","REGN","RF","RSG","RMD","RVTY","ROK","ROL","ROP","ROST","RCL","SPGI",
        "CRM","SBAC","SLB","STX","SRE","NOW","SHW","SPG","SWKS","SJM","SNA","SOLV","SO",
        "LUV","SWK","SBUX","STT","STLD","STE","SYK","SMCI","SYF","SNPS","SYY","TMUS","TROW",
        "TTWO","TPR","TRGP","TGT","TEL","TDY","TFX","TER","TSLA","TXN","TPG","TXT","TMO",
        "TJX","TSCO","TT","TDG","TRV","TRMB","TFC","TYL","TSN","USB","UBER","UDR","UHS",
        "UNP","UAL","UPS","URI","UNH","VLO","VTR","VLTO","VRSN","VRSK","VZ","VRTX",
        "VTRS","VICI","V","VST","VMC","WRB","GWW","WAB","WBA","WMT","DIS","WBD","WM","WAT",
        "WEC","WFC","WELL","WST","WDC","WHR","WY","WMB","WTW","WDAY","WYNN","XEL",
        "XYL","YUM","ZBRA","ZBH","ZTS",
    ]
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        tickers = [t.replace(".", "-") for t in tables[0]["Symbol"].tolist()]
        if len(tickers) > 100:
            return tickers
    except Exception:
        pass
    return FALLBACK


def get_nasdaq100() -> list:
    FALLBACK = [
        "ADBE","AMD","ABNB","GOOGL","GOOG","AMZN","AEP","AMGN","ADI","ANSS","AAPL","AMAT",
        "APP","ASML","TEAM","ADSK","ADP","AXON","BIIB","BKNG","AVGO","CDNS","CDW","CHTR",
        "CTAS","CSCO","CCEP","CTSH","CMCSA","CEG","CPRT","CSGP","COST","CRWD","CSX","DDOG",
        "DXCM","FANG","DASH","EA","EXC","FAST","FTNT","FOX","FOXA","GILD","GFS","HON","IDXX",
        "ILMN","INTC","INTU","ISRG","KDP","KLAC","KHC","LRCX","LIN","LULU","MAR","MRVL",
        "MTCH","MELI","META","MCHP","MU","MSFT","MRNA","MDLZ","MDB","MNST","NDAQ","NXPI",
        "NFLX","NVDA","NWSA","NWS","ODFL","ON","ORLY","PCAR","PANW","PAYX","PYPL","PDD",
        "QCOM","REGN","ROP","ROST","CRM","SBUX","SMCI","SNPS","TTWO","TMUS","TSLA","TXN",
        "VRSK","VRTX","WBD","WDAY","XEL","ZS","ZM",
    ]
    try:
        tables = pd.read_html("https://en.wikipedia.org/wiki/Nasdaq-100")
        for t in tables:
            cols = [str(c).lower() for c in t.columns]
            if "ticker" in cols:
                col = t.columns[[str(c).lower() == "ticker" for c in t.columns][0] if True else 0]
                # find correct col index
                col = t.columns[[str(c).lower() == "ticker" for c in t.columns].index(True)]
                tickers = t[col].dropna().tolist()
                if len(tickers) > 50:
                    return tickers
    except Exception:
        pass
    return FALLBACK


def get_hsi() -> list:
    return [
        "0001.HK","0002.HK","0003.HK","0005.HK","0006.HK","0011.HK","0012.HK",
        "0016.HK","0017.HK","0019.HK","0027.HK","0066.HK","0101.HK","0175.HK",
        "0241.HK","0267.HK","0288.HK","0291.HK","0316.HK","0322.HK","0386.HK",
        "0388.HK","0669.HK","0700.HK","0762.HK","0823.HK","0857.HK","0868.HK",
        "0881.HK","0883.HK","0939.HK","0941.HK","0960.HK","0968.HK","0992.HK",
        "1038.HK","1044.HK","1093.HK","1109.HK","1113.HK","1177.HK","1209.HK",
        "1211.HK","1299.HK","1398.HK","1876.HK","1928.HK","1997.HK","2007.HK",
        "2018.HK","2020.HK","2269.HK","2313.HK","2318.HK","2319.HK","2331.HK",
        "2382.HK","2388.HK","2628.HK","3690.HK","3692.HK","3968.HK","3988.HK",
        "6098.HK","6862.HK","9618.HK","9633.HK","9888.HK","9961.HK","9988.HK",
        "9999.HK","0020.HK","0135.HK","0151.HK","0358.HK","0522.HK","0836.HK",
    ]


def get_hk_all() -> list:
    extra = [
        "0041.HK","0055.HK","0168.HK","0177.HK","0191.HK","0220.HK","0285.HK",
        "0293.HK","0330.HK","0341.HK","0371.HK","0392.HK","0460.HK","0489.HK",
        "0548.HK","0551.HK","0575.HK","0659.HK","0670.HK","0683.HK","0728.HK",
        "0732.HK","0741.HK","0753.HK","0763.HK","0806.HK","0817.HK","0853.HK",
        "0866.HK","0869.HK","0880.HK","0884.HK","0916.HK","0966.HK","0981.HK",
        "1024.HK","1059.HK","1066.HK","1072.HK","1099.HK","1117.HK","1121.HK",
        "1128.HK","1137.HK","1141.HK","1157.HK","1163.HK","1179.HK","1186.HK",
        "1193.HK","1199.HK","1212.HK","1234.HK","1288.HK","1302.HK","1313.HK",
        "1336.HK","1347.HK","1359.HK","1378.HK","1381.HK","1382.HK","1456.HK",
        "1458.HK","1478.HK","1508.HK","1513.HK","1521.HK","1530.HK","1548.HK",
        "1556.HK","1559.HK","1585.HK","1590.HK","1600.HK","1618.HK","1633.HK",
        "1638.HK","1658.HK","1666.HK","1668.HK","1671.HK","1700.HK","1717.HK",
        "1728.HK","1772.HK","1776.HK","1801.HK","1810.HK","1821.HK","1833.HK",
        "1880.HK","1883.HK","1888.HK","1908.HK","1918.HK","1919.HK","1929.HK",
        "1958.HK","1972.HK","1988.HK","2008.HK","2009.HK","2015.HK","2038.HK",
        "2068.HK","2128.HK","2169.HK","2196.HK","2202.HK","2207.HK","2238.HK",
        "2282.HK","2333.HK","2338.HK","2355.HK","2356.HK","2380.HK","2383.HK",
        "2386.HK","2399.HK","2488.HK","2518.HK","2600.HK","2601.HK","2611.HK",
        "2638.HK","2669.HK","2689.HK","2799.HK","2823.HK","2828.HK","2899.HK",
        "3618.HK","3633.HK","3718.HK","3799.HK","3888.HK","3900.HK","3908.HK",
        "3918.HK","3998.HK","6030.HK","6060.HK","6099.HK","6110.HK","6186.HK",
        "6601.HK","6618.HK","6690.HK","6969.HK","9626.HK","9901.HK","9866.HK",
        "0914.HK","1088.HK","2601.HK","0966.HK","1339.HK",
    ]
    combined = list(dict.fromkeys(get_hsi() + extra))
    return combined


# ── 构建统一的 ticker→markets 映射（去重）────────────────────────────
def build_ticker_markets() -> dict:
    """返回 {ticker: [market1, market2, ...]} 的有序字典"""
    mapping: dict = {}
    sources = [
        ("美股·S&P500",     get_sp500),
        ("美股·纳斯达克100", get_nasdaq100),
        ("港股·恒生指数",    get_hsi),
        ("港股·全市场",      get_hk_all),
    ]
    for market, getter in sources:
        tickers = getter()
        for t in tickers:
            mapping.setdefault(t, [])
            if market not in mapping[t]:
                mapping[t].append(market)
    return mapping


# ── 数据处理工具 ───────────────────────────────────────────────────────
def _normalize_df(raw: pd.DataFrame, ticker: str):
    """统一处理 yfinance 1.x 的 MultiIndex 列结构差异"""
    if raw is None or raw.empty:
        return None
    if not isinstance(raw.columns, pd.MultiIndex):
        df = raw
    else:
        lvl0 = raw.columns.get_level_values(0).unique().tolist()
        lvl1 = raw.columns.get_level_values(1).unique().tolist()
        if ticker in lvl0:
            df = raw[ticker]
        elif ticker in lvl1:
            df = raw.xs(ticker, axis=1, level=1)
        else:
            df = raw.droplevel(1, axis=1)
    needed = {"Open", "High", "Low", "Close", "Volume"}
    if not needed.issubset(set(df.columns)):
        return None
    df = df[list(needed)].dropna(subset=["Close"])
    return df if not df.empty else None


def fetch_batch(tickers: list, period: str) -> dict:
    """批量下载数据"""
    result = {}
    try:
        raw = yf.download(
            tickers, period=period, interval="1d",
            group_by="ticker", auto_adjust=True,
            progress=False, threads=True,
        )
        for t in tickers:
            result[t] = _normalize_df(raw, t)
    except Exception as e:
        print(f"  [错误] 批量下载失败：{e}")
        for t in tickers:
            result[t] = None
    return result


# ── 信号检测 ──────────────────────────────────────────────────────────
def check_signal(ticker: str, df: pd.DataFrame, markets: list):
    """
    检测连续3天阴线 + 放量条件。
    返回 result dict 或 None。
    此阶段不含均线（均线在 enrich_ma 里补充）。
    """
    if df is None or len(df) < VOL_MA_PERIOD + CONSECUTIVE:
        return None

    df = df.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    recent  = df.iloc[-CONSECUTIVE:]
    history = df.iloc[-(VOL_MA_PERIOD + CONSECUTIVE):-CONSECUTIVE]
    vol_ma  = history["Volume"].mean()
    if vol_ma == 0:
        return None

    is_bearish   = (recent["Close"] < recent["Open"]).all()
    is_declining = all(
        recent["Close"].iloc[i] < recent["Close"].iloc[i-1]
        for i in range(1, CONSECUTIVE)
    )
    if not (is_bearish and is_declining):
        return None

    above_ma      = (recent["Volume"] > vol_ma).all()
    vol_increasing = all(
        recent["Volume"].iloc[i] > recent["Volume"].iloc[i-1]
        for i in range(1, CONSECUTIVE)
    )

    if above_ma and vol_increasing:
        category = "★ 连续放量下跌"
    elif above_ma or vol_increasing:
        category = "▲ 部分放量下跌"
    else:
        category = "仅阴线下跌"

    last       = recent.iloc[-1]
    start_open = recent["Open"].iloc[0]
    total_chg  = (last["Close"] - start_open) / start_open * 100
    day_chg    = (last["Close"] - last["Open"])  / last["Open"]  * 100

    # 判断主市场（美股/港股）
    main_market = "港股" if any("港股" in m for m in markets) and not any("美股" in m for m in markets) else (
        "美股" if any("美股" in m for m in markets) else "其他"
    )

    return {
        "ticker":           ticker,
        "cn_name":          CN_NAMES.get(ticker, ""),
        "markets":          markets,
        "main_market":      main_market,
        "category":         category,
        "close":            round(float(last["Close"]), 3),
        "open":             round(float(last["Open"]),  3),
        "day_chg_pct":      round(day_chg,   2),
        "total_chg_3d_pct": round(total_chg, 2),
        "vol_last":         int(last["Volume"]),
        "vol_ma20":         int(vol_ma),
        "vol_ratio":        round(float(last["Volume"]) / vol_ma, 2),
        "vol_d1":           int(recent["Volume"].iloc[0]),
        "vol_d2":           int(recent["Volume"].iloc[1]),
        "vol_d3":           int(recent["Volume"].iloc[2]),
        "date":             str(recent.index[-1].date()),
        "above_ma_vol":     bool(above_ma),
        "vol_increasing":   bool(vol_increasing),
        # 均线和板块在第二阶段填入
        "below_ma120":      None,
        "below_ma200":      None,
        "ai_ma_alert":      False,   # AI科技股 + 跌破均线
        "sector":           "",
        "sector_cn":        "",
        "sector_etf":       "",
        "sector_3d_pct":    None,
    }


# ── 第二阶段：均线 + 板块信息补充 ────────────────────────────────────
def _get_ticker_info_safe(ticker: str, timeout_s: int = 8) -> dict:
    """在独立线程中获取 ticker info，超时返回空字典"""
    result = {}
    def _fetch():
        try:
            result.update(yf.Ticker(ticker).info)
        except Exception:
            pass
    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    return result


def enrich_ma(hits: list, ticker_data: dict) -> None:
    """
    为命中股票补充 120/200 日均线状态（原地修改）。
    ticker_data: {ticker: 1y DataFrame}
    """
    for r in hits:
        df = ticker_data.get(r["ticker"])
        if df is None or df.empty:
            continue
        closes = df["Close"]
        n = len(closes)
        last_close = float(closes.iloc[-1])
        if n >= 120:
            ma120 = float(closes.iloc[-120:].mean())
            r["below_ma120"] = last_close < ma120
        if n >= 200:
            ma200 = float(closes.iloc[-200:].mean())
            r["below_ma200"] = last_close < ma200
        # AI科技股 + 跌破任一均线 → 特别提示
        broke_ma = r["below_ma120"] is True or r["below_ma200"] is True
        r["ai_ma_alert"] = broke_ma and (r["ticker"] in AI_TECH_TICKERS)


def enrich_sector(hits: list) -> None:
    """
    为命中股票补充板块信息 + 对应 ETF 3日涨跌幅（原地修改）。
    仅处理美股（港股板块数据不稳定）。
    """
    # 收集需要的 ETF 代码
    needed_etfs: set = set()
    ticker_sector: dict = {}

    print(f"\n  [板块] 获取 {len(hits)} 只命中股票的板块信息…")
    for r in hits:
        if "美股" not in r["main_market"]:
            continue
        info = _get_ticker_info_safe(r["ticker"])
        sector_en = info.get("sector", "")
        if sector_en and sector_en in SECTOR_ETF:
            etf_code, sector_cn = SECTOR_ETF[sector_en]
            r["sector"]    = sector_en
            r["sector_cn"] = sector_cn
            r["sector_etf"] = etf_code
            needed_etfs.add(etf_code)
            ticker_sector[r["ticker"]] = etf_code

    # 批量获取 ETF 3日数据
    etf_returns: dict = {}
    if needed_etfs:
        etf_list = list(needed_etfs)
        try:
            raw = yf.download(
                etf_list, period="10d", interval="1d",
                group_by="ticker", auto_adjust=True,
                progress=False, threads=True,
            )
            for etf in etf_list:
                df = _normalize_df(raw, etf)
                if df is not None and len(df) >= 4:
                    r3 = (float(df["Close"].iloc[-1]) - float(df["Close"].iloc[-4])) \
                         / float(df["Close"].iloc[-4]) * 100
                    etf_returns[etf] = round(r3, 2)
        except Exception as e:
            print(f"  [警告] ETF 数据获取失败：{e}")

    # 回填
    for r in hits:
        etf = r.get("sector_etf", "")
        if etf and etf in etf_returns:
            r["sector_3d_pct"] = etf_returns[etf]


# ── 主扫描流程 ────────────────────────────────────────────────────────
def scan_all() -> tuple:
    """
    两阶段扫描：
    Phase 1: 35天数据，全量扫描，检测信号
    Phase 2: 1年数据，仅命中股票，补充均线
    返回 (hits, total_scanned)
    """
    ticker_markets = build_ticker_markets()
    all_tickers    = list(ticker_markets.keys())
    total          = len(all_tickers)
    print(f"\n共 {total} 只唯一股票（已去重）")

    # ── Phase 1：35天快速扫描 ──────────────────────────────
    print("\n── Phase 1：信号扫描（35天数据）──")
    hits = []
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, total, BATCH_SIZE):
        batch = all_tickers[i: i + BATCH_SIZE]
        batch_no = i // BATCH_SIZE + 1
        print(f"  批次 {batch_no}/{total_batches}  ({batch[0]} … {batch[-1]})", end="\r")
        data = fetch_batch(batch, period="35d")
        for t, df in data.items():
            r = check_signal(t, df, ticker_markets[t])
            if r:
                hits.append(r)
        time.sleep(BATCH_PAUSE)

    print(f"\n  Phase 1 完成，命中 {len(hits)} 只")

    # ── Phase 2：1年数据，补充均线 ────────────────────────
    if hits:
        print(f"\n── Phase 2：均线计算（1年数据，{len(hits)} 只）──")
        hit_tickers = [r["ticker"] for r in hits]
        ma_data: dict = {}
        for i in range(0, len(hit_tickers), BATCH_SIZE):
            batch = hit_tickers[i: i + BATCH_SIZE]
            data  = fetch_batch(batch, period="1y")
            ma_data.update(data)
            time.sleep(BATCH_PAUSE)
        enrich_ma(hits, ma_data)

        # ── Phase 3：板块信息 ──────────────────────────────
        enrich_sector(hits)

    # 排序：★ > ▲ > 仅，同类按3日跌幅排
    cat_order = {"★ 连续放量下跌": 0, "▲ 部分放量下跌": 1, "仅阴线下跌": 2}
    hits.sort(key=lambda r: (cat_order.get(r["category"], 9), r["total_chg_3d_pct"]))

    return hits, total


# ── 历史数据管理 ──────────────────────────────────────────────────────
def update_history(hits: list) -> list:
    """追加今日命中数到 history.json，返回完整历史"""
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
    except Exception:
        history = []

    today = datetime.date.today().isoformat()
    history = [h for h in history if h.get("date") != today]  # 去掉今日旧记录
    history.append({
        "date":  today,
        "total": len(hits),
        "hot":   sum(1 for r in hits if r["category"].startswith("★")),
        "mid":   sum(1 for r in hits if r["category"].startswith("▲")),
        "low":   sum(1 for r in hits if r["category"] == "仅阴线下跌"),
    })
    history = sorted(history, key=lambda x: x["date"])[-90:]  # 保留最近90天

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    return history


# ── HTML 模板 ─────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>美港股连续放量下跌检测器</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{--bg:#0f1117;--card:#1a1d2e;--card2:#20243a;--accent:#e63946;--green:#2ec4b6;
  --yellow:#ffd166;--dim:#8b8fa8;--border:#2a2d3e;--text:#e2e8f0;--blue:#4a90d9;}
*{box-sizing:border-box;margin:0;padding:0;}
body{background:var(--bg);color:var(--text);font-family:'SF Pro Display',-apple-system,sans-serif;min-height:100vh;}
/* HEADER */
header{background:var(--card);border-bottom:1px solid var(--border);padding:16px 28px;
  display:flex;align-items:center;gap:12px;}
header h1{font-size:1.15rem;font-weight:700;}
.meta{color:var(--dim);font-size:.8rem;margin-left:auto;}
/* OVERVIEW */
.overview{display:grid;grid-template-columns:1fr 1.8fr;gap:16px;padding:20px 28px 0;}
.stat-group{display:flex;flex-direction:column;gap:10px;}
.stat-row{display:flex;gap:10px;}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:12px 16px;flex:1;}
.stat-card .val{font-size:1.5rem;font-weight:700;}
.stat-card .lbl{font-size:.7rem;color:var(--dim);margin-top:2px;}
/* HIGHLIGHTS */
.highlights{display:flex;gap:16px;padding:16px 28px 0;}
.hl-card{background:var(--card);border:1px solid var(--border);border-radius:10px;
  padding:14px 18px;flex:1;cursor:pointer;transition:border-color .2s;}
.hl-card:hover{border-color:var(--accent);}
.hl-label{font-size:.7rem;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;}
.hl-ticker{font-size:1.1rem;font-weight:700;margin:4px 0 2px;}
.hl-name{font-size:.8rem;color:var(--dim);}
.hl-val{font-size:1.3rem;font-weight:700;margin-top:6px;}
/* CHART */
.chart-wrap{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:14px 18px;}
.chart-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.chart-title{font-size:.85rem;font-weight:600;}
.range-btns{display:flex;gap:6px;}
.range-btn{background:var(--card2);border:1px solid var(--border);color:var(--dim);
  padding:3px 10px;border-radius:5px;font-size:.75rem;cursor:pointer;transition:.15s;}
.range-btn.active,.range-btn:hover{border-color:var(--blue);color:var(--blue);}
canvas#histChart{max-height:120px;}
/* CONTROLS */
.controls{padding:16px 28px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}
.controls label{color:var(--dim);font-size:.82rem;}
select,input[type=text]{background:var(--card);border:1px solid var(--border);color:var(--text);
  padding:5px 10px;border-radius:6px;font-size:.82rem;outline:none;}
#search{width:200px;}
/* TABLE */
.table-wrap{padding:0 28px 48px;overflow-x:auto;}
table{width:100%;border-collapse:collapse;font-size:.82rem;}
thead tr{background:var(--card);}
th{padding:9px 12px;text-align:left;color:var(--dim);font-weight:500;
  border-bottom:1px solid var(--border);white-space:nowrap;cursor:pointer;user-select:none;}
th:hover{color:var(--text);}
td{padding:8px 12px;border-bottom:1px solid var(--border);white-space:nowrap;vertical-align:middle;}
tr:hover td{background:rgba(255,255,255,.02);}
/* BADGES */
.badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:.7rem;font-weight:600;margin:1px;}
.b-hot{background:rgba(230,57,70,.18);color:var(--accent);}
.b-mid{background:rgba(255,209,102,.15);color:var(--yellow);}
.b-low{background:rgba(139,143,168,.12);color:var(--dim);}
.b-idx{background:rgba(74,144,217,.12);color:var(--blue);font-size:.65rem;}
.b-ma{padding:2px 6px;border-radius:4px;font-size:.68rem;font-weight:600;}
.b-ma-break200{background:rgba(230,57,70,.2);color:var(--accent);}
.b-ma-break120{background:rgba(255,150,50,.15);color:#ff9632;}
.b-ma-ok{background:rgba(46,196,182,.12);color:var(--green);}
.b-ma-na{background:rgba(139,143,168,.1);color:var(--dim);}
.b-ai-alert{background:rgba(155,89,182,.25);color:#c77dff;border-left:3px solid #c77dff;}
.hl-ai{border-color:rgba(155,89,182,.5)!important;}
tr.ai-alert-row td{background:rgba(155,89,182,.07);}
/* MISC */
.red{color:var(--accent);} .green{color:var(--green);} .yellow{color:var(--yellow);}
.bar{display:inline-block;height:7px;background:var(--accent);border-radius:3px;vertical-align:middle;}
.no-data{text-align:center;color:var(--dim);padding:60px;}
.sort-hint{margin-left:auto;color:var(--dim);font-size:.75rem;}
</style>
</head>
<body>
<header>
  <span style="font-size:1.6rem">📉</span>
  <h1>美港股 · 连续放量下跌检测器</h1>
  <div class="meta">更新时间：__SCAN_TIME__ &nbsp;|&nbsp; 数据来源：Yahoo Finance</div>
</header>

<div class="overview">
  <div class="stat-group">
    <div class="stat-row">
      <div class="stat-card"><div class="val red" id="s-total">__TOTAL__</div><div class="lbl">命中总数</div></div>
      <div class="stat-card"><div class="val red" id="s-hot">__HOT__</div><div class="lbl">★ 连续放量下跌</div></div>
    </div>
    <div class="stat-row">
      <div class="stat-card"><div class="val yellow" id="s-mid">__MID__</div><div class="lbl">▲ 部分放量下跌</div></div>
      <div class="stat-card"><div class="val" id="s-low">__LOW__</div><div class="lbl">仅阴线下跌</div></div>
    </div>
    <div class="stat-row">
      <div class="stat-card" style="flex:1"><div class="val green">__SCANNED__</div><div class="lbl">扫描总数（去重）</div></div>
    </div>
  </div>
  <div class="chart-wrap">
    <div class="chart-header">
      <span class="chart-title">历史每日命中数趋势</span>
      <div class="range-btns">
        <button class="range-btn" onclick="setRange('2w')">2周</button>
        <button class="range-btn active" onclick="setRange('1m')">1月</button>
        <button class="range-btn" onclick="setRange('3m')">3月</button>
      </div>
    </div>
    <canvas id="histChart"></canvas>
  </div>
</div>

<div class="highlights">
  <div class="hl-card" id="hl-drop" onclick="highlightRow(hlDrop)">
    <div class="hl-label">📉 今日最大跌幅</div>
    <div class="hl-ticker" id="hl-drop-ticker">—</div>
    <div class="hl-name" id="hl-drop-name"></div>
    <div class="hl-val red" id="hl-drop-val">—</div>
  </div>
  <div class="hl-card" id="hl-vol" onclick="highlightRow(hlVol)">
    <div class="hl-label">🔥 今日最高量比</div>
    <div class="hl-ticker" id="hl-vol-ticker">—</div>
    <div class="hl-name" id="hl-vol-name"></div>
    <div class="hl-val yellow" id="hl-vol-val">—</div>
  </div>
  <div class="hl-card hl-ai" id="hl-ai" onclick="highlightAI()">
    <div class="hl-label">🤖 AI科技股破均线预警</div>
    <div class="hl-ticker" id="hl-ai-count">—</div>
    <div class="hl-name" id="hl-ai-names" style="font-size:0.78rem;line-height:1.5;"></div>
    <div class="hl-val red" id="hl-ai-val">只需关注</div>
  </div>
</div>

<div class="controls">
  <label>市场</label>
  <select id="mkFilter" onchange="applyFilters()">
    <option value="">全部</option>
    <option value="美股">美股</option>
    <option value="港股">港股</option>
  </select>
  <label>信号</label>
  <select id="catFilter" onchange="applyFilters()">
    <option value="">全部</option>
    <option value="★">★ 连续放量下跌</option>
    <option value="▲">▲ 部分放量下跌</option>
    <option value="仅">仅阴线下跌</option>
  </select>
  <label>均线</label>
  <select id="maFilter" onchange="applyFilters()">
    <option value="">全部</option>
    <option value="200">跌破200日线</option>
    <option value="120">跌破120日线</option>
  </select>
  <input type="text" id="search" placeholder="🔍 搜索代码/名称…" oninput="applyFilters()">
  <span class="sort-hint">点击列头排序</span>
</div>

<div class="table-wrap">
<table id="mainTable">
<thead><tr>
  <th onclick="sortBy('ticker')">代码 ↕</th>
  <th>中文名</th>
  <th>所属指数</th>
  <th onclick="sortBy('category')">信号 ↕</th>
  <th onclick="sortBy('close')">收盘价 ↕</th>
  <th onclick="sortBy('day_chg_pct')">当日涨跌 ↕</th>
  <th onclick="sortBy('total_chg_3d_pct')">3日涨跌 ↕</th>
  <th>均线状态</th>
  <th onclick="sortBy('sector_cn')">板块 ↕</th>
  <th onclick="sortBy('sector_3d_pct')">板块3日 ↕</th>
  <th onclick="sortBy('vol_ratio')">量比 ↕</th>
  <th>D-2</th><th>D-1</th><th>昨日成交量</th>
  <th onclick="sortBy('date')">日期 ↕</th>
</tr></thead>
<tbody id="tbody"></tbody>
</table>
<div id="noData" class="no-data" style="display:none">暂无符合条件的股票</div>
</div>

<script>
const RAW  = __JSON_DATA__;
const HIST = __JSON_HIST__;
let sortKey='category', sortAsc=false;
let hlDrop=null, hlVol=null;

// ── 工具函数 ──────────────────────────────────────────
function fmt(n,d=2){return n==null?'—':Number(n).toFixed(d);}
function fmtVol(v){
  if(!v)return'—';
  if(v>=1e8)return(v/1e8).toFixed(2)+'亿';
  if(v>=1e4)return(v/1e4).toFixed(1)+'万';
  return v.toLocaleString();
}
function chgCls(v){return v<0?'red':v>0?'green':'';}
function badge(cat){
  if(cat.startsWith('★'))return`<span class="badge b-hot">${cat}</span>`;
  if(cat.startsWith('▲'))return`<span class="badge b-mid">${cat}</span>`;
  return`<span class="badge b-low">${cat}</span>`;
}
function maBadge(r){
  if(r.below_ma200===true) return'<span class="b-ma b-ma-break200">破200日</span>';
  if(r.below_ma120===true) return'<span class="b-ma b-ma-break120">破120日</span>';
  if(r.below_ma200===false&&r.below_ma120===false) return'<span class="b-ma b-ma-ok">均线上方</span>';
  return'<span class="b-ma b-ma-na">—</span>';
}
function idxBadges(markets){
  const short={'美股·S&P500':'S&P','美股·纳斯达克100':'NDX','港股·恒生指数':'HSI','港股·全市场':'HKALL'};
  return markets.map(m=>`<span class="badge b-idx">${short[m]||m}</span>`).join('');
}

// ── 高亮卡片 ──────────────────────────────────────────
let hlAIList=[];
function initHighlights(){
  if(!RAW.length)return;
  hlDrop=RAW.reduce((a,b)=>a.total_chg_3d_pct<b.total_chg_3d_pct?a:b);
  hlVol =RAW.reduce((a,b)=>a.vol_ratio>b.vol_ratio?a:b);
  hlAIList=RAW.filter(r=>r.ai_ma_alert);
  const d=hlDrop,v=hlVol;
  document.getElementById('hl-drop-ticker').textContent=d.ticker;
  document.getElementById('hl-drop-name').textContent=d.cn_name||'';
  document.getElementById('hl-drop-val').textContent=fmt(d.total_chg_3d_pct)+'%（3日）';
  document.getElementById('hl-vol-ticker').textContent=v.ticker;
  document.getElementById('hl-vol-name').textContent=v.cn_name||'';
  document.getElementById('hl-vol-val').textContent=fmt(v.vol_ratio)+'x（量比）';
  // AI 科技股预警卡
  const aiCard=document.getElementById('hl-ai');
  if(hlAIList.length===0){
    document.getElementById('hl-ai-count').textContent='无';
    document.getElementById('hl-ai-names').textContent='今日无AI科技股破均线';
    document.getElementById('hl-ai-val').textContent='—';
  } else {
    document.getElementById('hl-ai-count').textContent=hlAIList.length+'只';
    document.getElementById('hl-ai-names').innerHTML=hlAIList.map(r=>{
      const ma=r.below_ma200?'破200日':'破120日';
      return`<b>${r.ticker}</b> ${r.cn_name||''} <span style="color:#ff9632;font-size:.7rem;">${ma}</span>`;
    }).join('<br>');
    document.getElementById('hl-ai-val').textContent='点击查看';
    aiCard.style.borderColor='rgba(199,125,255,.7)';
  }
}
function highlightAI(){
  if(!hlAIList.length)return;
  const tickers=new Set(hlAIList.map(r=>r.ticker));
  document.querySelectorAll('#tbody tr').forEach(tr=>{
    if(tickers.has(tr.dataset.ticker)){
      tr.scrollIntoView({behavior:'smooth',block:'center'});
      tr.style.outline='2px solid #c77dff';
      setTimeout(()=>tr.style.outline='',2500);
    }
  });
}
function highlightRow(r){
  if(!r)return;
  const trs=document.querySelectorAll('#tbody tr');
  trs.forEach(tr=>{
    if(tr.dataset.ticker===r.ticker){
      tr.scrollIntoView({behavior:'smooth',block:'center'});
      tr.style.outline='2px solid var(--accent)';
      setTimeout(()=>tr.style.outline='',2000);
    }
  });
}

// ── 历史图表 ──────────────────────────────────────────
let chart=null, currentRange='1m';
function getChartData(range){
  const now=new Date();
  const days=range==='2w'?14:range==='1m'?30:90;
  const cutoff=new Date(now-days*86400000);
  return HIST.filter(d=>new Date(d.date)>=cutoff);
}
function setRange(r){
  currentRange=r;
  document.querySelectorAll('.range-btn').forEach(b=>{
    b.classList.toggle('active',b.textContent===({'2w':'2周','1m':'1月','3m':'3月'}[r]));
  });
  renderChart(r);
}
function renderChart(range){
  const data=getChartData(range);
  const labels=data.map(d=>d.date.slice(5));
  const vals=data.map(d=>d.total);
  if(chart)chart.destroy();
  chart=new Chart(document.getElementById('histChart'),{
    type:'line',
    data:{
      labels,
      datasets:[{
        label:'命中数',data:vals,
        borderColor:'#e63946',backgroundColor:'rgba(230,57,70,.08)',
        borderWidth:2,pointRadius:3,pointBackgroundColor:'#e63946',fill:true,tension:.3,
      }]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},tooltip:{callbacks:{
        title:i=>'📅 '+data[i[0].dataIndex].date,
        label:i=>'命中：'+i.raw+'只',
      }}},
      scales:{
        x:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#8b8fa8',maxTicksLimit:10}},
        y:{grid:{color:'rgba(255,255,255,.05)'},ticks:{color:'#8b8fa8',precision:0},min:0},
      }
    }
  });
}

// ── 筛选 & 排序 ───────────────────────────────────────
function applyFilters(){
  const mk  =document.getElementById('mkFilter').value;
  const cat =document.getElementById('catFilter').value;
  const ma  =document.getElementById('maFilter').value;
  const q   =document.getElementById('search').value.toLowerCase();
  let data=RAW.filter(r=>{
    if(mk  &&!r.main_market.includes(mk))return false;
    if(cat &&!r.category.startsWith(cat))return false;
    if(ma==='200'&&r.below_ma200!==true)return false;
    if(ma==='120'&&r.below_ma120!==true&&r.below_ma200!==true)return false;
    if(q&&!r.ticker.toLowerCase().includes(q)&&!r.cn_name.includes(q))return false;
    return true;
  });
  data=data.sort((a,b)=>{
    let va=a[sortKey],vb=b[sortKey];
    if(va==null)va=sortAsc?Infinity:-Infinity;
    if(vb==null)vb=sortAsc?Infinity:-Infinity;
    if(typeof va==='string')return sortAsc?va.localeCompare(vb):vb.localeCompare(va);
    return sortAsc?va-vb:vb-va;
  });
  render(data);
  // 更新统计
  document.getElementById('s-total').textContent=data.length;
  document.getElementById('s-hot').textContent=data.filter(r=>r.category.startsWith('★')).length;
  document.getElementById('s-mid').textContent=data.filter(r=>r.category.startsWith('▲')).length;
  document.getElementById('s-low').textContent=data.filter(r=>r.category==='仅阴线下跌').length;
}
function sortBy(k){
  sortKey===k?sortAsc=!sortAsc:(sortKey=k,sortAsc=false);
  applyFilters();
}
function render(data){
  const tbody=document.getElementById('tbody');
  const noData=document.getElementById('noData');
  if(!data.length){tbody.innerHTML='';noData.style.display='block';return;}
  noData.style.display='none';
  const maxR=Math.max(...data.map(r=>r.vol_ratio||0),1);
  tbody.innerHTML=data.map(r=>{
    const bw=Math.min(55,Math.round((r.vol_ratio/maxR)*55));
    const s3=r.sector_3d_pct!=null
      ?`<span class="${chgCls(r.sector_3d_pct)}">${fmt(r.sector_3d_pct)}%</span>`
      :'<span style="color:var(--dim)">—</span>';
    return`<tr data-ticker="${r.ticker}" class="${r.ai_ma_alert?'ai-alert-row':''}">
      <td><b>${r.ticker}</b>${r.ai_ma_alert?'<span class="badge b-ai-alert" style="margin-left:4px">🤖AI</span>':''}</td>
      <td style="color:var(--dim)">${r.cn_name||'—'}</td>
      <td>${idxBadges(r.markets)}</td>
      <td>${badge(r.category)}</td>
      <td>${fmt(r.close,3)}</td>
      <td class="${chgCls(r.day_chg_pct)}">${fmt(r.day_chg_pct)}%</td>
      <td class="${chgCls(r.total_chg_3d_pct)}">${fmt(r.total_chg_3d_pct)}%</td>
      <td>${maBadge(r)}</td>
      <td style="color:var(--dim)">${r.sector_cn||'—'}</td>
      <td>${s3}</td>
      <td><span class="bar" style="width:${bw}px;margin-right:4px"></span>${fmt(r.vol_ratio,2)}x</td>
      <td style="color:var(--dim)">${fmtVol(r.vol_d1)}</td>
      <td style="color:var(--dim)">${fmtVol(r.vol_d2)}</td>
      <td>${fmtVol(r.vol_d3)}</td>
      <td style="color:var(--dim)">${r.date}</td>
    </tr>`;
  }).join('');
}

// ── 声音 + 浏览器通知 ────────────────────────────────
function playAlertTone(){
  try{
    const ctx=new(window.AudioContext||window.webkitAudioContext)();
    const freqs=[523,659,784]; // C5 E5 G5 三音提示
    freqs.forEach((f,i)=>{
      const o=ctx.createOscillator(),g=ctx.createGain();
      o.connect(g);g.connect(ctx.destination);
      o.type='sine';o.frequency.value=f;
      g.gain.setValueAtTime(0,ctx.currentTime+i*0.18);
      g.gain.linearRampToValueAtTime(0.25,ctx.currentTime+i*0.18+0.04);
      g.gain.linearRampToValueAtTime(0,ctx.currentTime+i*0.18+0.18);
      o.start(ctx.currentTime+i*0.18);
      o.stop(ctx.currentTime+i*0.18+0.22);
    });
  }catch(e){}
}
function sendBrowserNotif(count, names){
  if(!('Notification' in window))return;
  const doSend=()=>{
    new Notification('🤖 AI科技股破均线预警',{
      body:`${count}只：${names}\n点击查看详情`,
      icon:'https://johnsonlee28.github.io/stock-detector/favicon.ico',
      tag:'ai-alert',
    });
  };
  if(Notification.permission==='granted') doSend();
  else if(Notification.permission!=='denied')
    Notification.requestPermission().then(p=>{ if(p==='granted') doSend(); });
}

// ── 初始化 ────────────────────────────────────────────
initHighlights();
renderChart('1m');
applyFilters();

// AI 预警：声音 + 通知（页面加载后触发）
if(hlAIList.length>0){
  const names=hlAIList.slice(0,3).map(r=>r.ticker).join('、')+(hlAIList.length>3?'等':'');
  setTimeout(()=>{
    playAlertTone();
    sendBrowserNotif(hlAIList.length, names);
  }, 800);
}
</script>
</body>
</html>
"""


def send_ai_alert_email(ai_hits: list) -> None:
    """
    当有 AI 科技股破均线时，发邮件通知。
    需要环境变量：
      GMAIL_USER     发件人 Gmail 地址
      GMAIL_APP_PASS Gmail 应用专用密码（16位，非登录密码）
    """
    gmail_user = os.environ.get("GMAIL_USER", "")
    gmail_pass = os.environ.get("GMAIL_APP_PASS", "")
    to_email   = os.environ.get("ALERT_TO", gmail_user)  # 收件人，默认同发件人

    if not gmail_user or not gmail_pass:
        print("  ⚠️  未配置 GMAIL_USER / GMAIL_APP_PASS，跳过邮件通知")
        return

    scan_date = datetime.date.today().isoformat()

    # 构建邮件正文
    rows_html = ""
    rows_text = ""
    for r in ai_hits:
        ma_str = "破200日线" if r.get("below_ma200") else "破120日线"
        chg3   = f"{r.get('total_chg_3d_pct', 0):.2f}%"
        rows_html += (
            f"<tr>"
            f"<td style='padding:6px 12px;font-weight:bold'>{r['ticker']}</td>"
            f"<td style='padding:6px 12px'>{r.get('cn_name','')}</td>"
            f"<td style='padding:6px 12px;color:#e63946'>{ma_str}</td>"
            f"<td style='padding:6px 12px;color:#e63946'>{chg3}（3日）</td>"
            f"<td style='padding:6px 12px'>{r.get('category','')}</td>"
            f"</tr>"
        )
        rows_text += f"  • {r['ticker']} {r.get('cn_name','')}  {ma_str}  {chg3}（3日）  {r.get('category','')}\n"

    body_html = f"""
<div style="font-family:sans-serif;background:#0f1117;color:#e2e8f0;padding:24px;border-radius:12px;max-width:600px">
  <h2 style="color:#c77dff;margin-bottom:4px">🤖 AI科技股破均线预警</h2>
  <p style="color:#8b8fa8;font-size:0.85rem;margin-bottom:16px">{scan_date} · 美港股连续放量下跌检测器</p>
  <p style="margin-bottom:16px">以下 <b style="color:#c77dff">{len(ai_hits)}</b> 只 AI 科技股出现连续下跌并跌破均线：</p>
  <table style="width:100%;border-collapse:collapse;background:#1a1d2e;border-radius:8px;overflow:hidden">
    <thead>
      <tr style="background:#20243a;color:#8b8fa8;font-size:0.8rem">
        <th style="padding:8px 12px;text-align:left">代码</th>
        <th style="padding:8px 12px;text-align:left">名称</th>
        <th style="padding:8px 12px;text-align:left">均线状态</th>
        <th style="padding:8px 12px;text-align:left">3日涨跌</th>
        <th style="padding:8px 12px;text-align:left">信号</th>
      </tr>
    </thead>
    <tbody>{rows_html}</tbody>
  </table>
  <p style="margin-top:20px">
    <a href="https://johnsonlee28.github.io/stock-detector/report.html"
       style="background:#c77dff;color:#fff;padding:10px 20px;border-radius:6px;text-decoration:none;font-weight:bold">
      查看完整报告 →
    </a>
  </p>
</div>
"""
    body_text = f"AI科技股破均线预警 {scan_date}\n\n{rows_text}\n查看报告：https://johnsonlee28.github.io/stock-detector/report.html"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🤖 AI科技股预警 {scan_date}：{len(ai_hits)}只破均线"
    msg["From"]    = gmail_user
    msg["To"]      = to_email
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
            server.login(gmail_user, gmail_pass)
            server.sendmail(gmail_user, to_email, msg.as_string())
        print(f"  ✅ 预警邮件已发送至 {to_email}")
    except Exception as e:
        print(f"  ❌ 邮件发送失败：{e}")


def build_html(hits: list, history: list, scanned: int) -> str:
    hot = sum(1 for r in hits if r["category"].startswith("★"))
    mid = sum(1 for r in hits if r["category"].startswith("▲"))
    low = len(hits) - hot - mid
    scan_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = HTML
    html = html.replace("__SCAN_TIME__", scan_time)
    html = html.replace("__TOTAL__",   str(len(hits)))
    html = html.replace("__HOT__",     str(hot))
    html = html.replace("__MID__",     str(mid))
    html = html.replace("__LOW__",     str(low))
    html = html.replace("__SCANNED__", str(scanned))
    html = html.replace("__JSON_DATA__", json.dumps(hits,    ensure_ascii=False))
    html = html.replace("__JSON_HIST__", json.dumps(history, ensure_ascii=False))
    return html


# ── 主流程 ────────────────────────────────────────────────────────────
def main():
    print("=" * 58)
    print("  美港股 · 连续放量下跌检测器 v2.0")
    print("=" * 58)
    print(f"  策略：连续{CONSECUTIVE}天阴线 + 量>{VOL_MA_PERIOD}日均量 + 量逐日递增")
    print()

    hits, scanned = scan_all()
    history = update_history(hits)

    html = build_html(hits, history, scanned)
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    # ── AI 科技股预警邮件 ──────────────────────────────────
    ai_hits = [r for r in hits if r.get("ai_ma_alert")]
    if ai_hits:
        print(f"\n🤖 AI科技股破均线：{len(ai_hits)} 只，发送预警邮件...")
        send_ai_alert_email(ai_hits)
    else:
        print("\n🤖 AI科技股：今日无破均线信号")

    print(f"\n{'='*58}")
    print(f"  完成！扫描 {scanned} 只，命中 {len(hits)} 只")
    print(f"  报告：{OUTPUT_HTML}")
    print(f"{'='*58}")

    if "--no-browser" not in sys.argv:
        webbrowser.open(f"file://{OUTPUT_HTML}")


if __name__ == "__main__":
    main()
