import os
import re
import html
import time
import calendar
import requests
import feedparser
from datetime import datetime
from zoneinfo import ZoneInfo

FRED_API_KEY = os.environ["FRED_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y년 %m월 %d일")


# ==================== FRED 지표 ====================
FRED_SERIES = {
    "CPIAUCSL": ("CPI(전년比)", "yoy"),
    "UNRATE":   ("실업률", "pct"),
    "FEDFUNDS": ("연방기금금리", "pct"),
    "DGS10":    ("10년물 국채", "pct"),
    "DGS2":     ("2년물 국채", "pct"),
    "DTWEXBGS": ("달러인덱스", "raw"),
    "PAYEMS":   ("비농업고용(전월比)", "diff_k"),
}


def fred_fetch(series_id, limit=14):
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return [o for o in r.json().get("observations", []) if o["value"] != "."]


def arrow(diff):
    # 유니코드에 파란 삼각형이 없어 원으로 쓴다 (빨강=상승, 파랑=하락)
    if diff > 0:
        return "🔴"
    if diff < 0:
        return "🔵"
    return "⚪"


def build_indicators():
    lines = []
    for sid, (label, mode) in FRED_SERIES.items():
        try:
            obs = fred_fetch(sid)
            if not obs:
                continue
            latest = float(obs[0]["value"])
            date = obs[0]["date"]

            if mode == "yoy":
                if len(obs) < 13:
                    continue
                yoy = (latest / float(obs[12]["value"]) - 1) * 100
                if len(obs) >= 14:
                    prev_yoy = (float(obs[1]["value"]) / float(obs[13]["value"]) - 1) * 100
                    mark = arrow(yoy - prev_yoy)
                else:
                    mark = "➖"
                lines.append(f"{mark} {label}  {yoy:.1f}%  ({date})")
            elif mode == "pct":
                prev = float(obs[1]["value"]) if len(obs) > 1 else latest
                diff = latest - prev
                lines.append(f"{arrow(diff)} {label}  {latest:.2f}%  ({diff:+.2f})")
            elif mode == "diff_k":
                prev = float(obs[1]["value"]) if len(obs) > 1 else latest
                diff = latest - prev
                lines.append(f"{arrow(diff)} {label}  {diff:+,.0f}천명  ({date})")
            else:
                prev = float(obs[1]["value"]) if len(obs) > 1 else latest
                diff = latest - prev
                lines.append(f"{arrow(diff)} {label}  {latest:.2f}  ({diff:+.2f})")
        except Exception as e:
            print(f"FRED 실패 {sid}: {e}")
            lines.append(f"⚠️ {label}  조회실패")
    return lines


# ==================== 시장 지수 / 관심 종목 ====================
YAHOO_TICKERS = {
    "^GSPC": "S&P500",
    "^IXIC": "나스닥",
    "^NDX":  "나스닥100",
    "^DJI":  "다우",
    "^SOX":  "필라델피아반도체",
    "^VIX":  "VIX",
}

# 레버리지 ETF는 기초지수가 움직여서 움직인다. 사유를 찾을 때 이 정보를 준다.
TICKER_BASIS = {
    "QLD":  "나스닥100 지수",
    "TQQQ": "나스닥100 지수",
    "USD":  "미국 반도체 지수",
    "SOXL": "미국 반도체 지수",
    "UPRO": "S&P500 지수",
    "DGRO": "미국 배당성장주",
    "SPCX": "스페이스X 주가",
}

MY_TICKERS = {
    "QLD":  "QLD",
    "TQQQ": "TQQQ",
    "USD":  "USD",
    "SOXL": "SOXL",
    "UPRO": "UPRO",
    "DGRO": "DGRO",
    "SPCX": "SPCX",
}


def yahoo_quote(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers,
                     params={"range": "5d", "interval": "1d"}, timeout=20)
    r.raise_for_status()
    meta = r.json()["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    return price, prev


def fetch_quotes(ticker_map):
    """[(라벨, 현재가, 등락률)] 형태로 시세를 모은다. 실패하면 가격이 None."""
    rows = []
    for tk, label in ticker_map.items():
        try:
            price, prev = yahoo_quote(tk)
            if price is None or not prev:
                rows.append((label, None, None))
                continue
            rows.append((label, price, (price / prev - 1) * 100))
        except Exception as e:
            print(f"Yahoo 실패 {tk}: {e}")
            rows.append((label, None, None))
    return rows


def format_quotes(rows, reasons=None, align=False):
    """align=True면 고정폭 코드블록용으로 자릿수를 맞춘다."""
    reasons = reasons or {}
    lines = []
    for label, price, chg in rows:
        if price is None:
            lines.append(f"⚠️ {label}  조회실패")
            continue
        why = reasons.get(label, "")
        tail = f"  {why}" if why else ""
        if align:
            lines.append(f"{arrow(chg)} {label:<5}{price:>8,.2f} {chg:>+6.1f}%{tail}")
        else:
            lines.append(f"{arrow(chg)} {label}  {price:,.2f}  {chg:+.2f}%{tail}")
    return lines


# ==================== 뉴스 RSS ====================
RSS_FEEDS = [
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",   # CNBC Economy
    "https://www.cnbc.com/id/15839135/device/rss/rss.html",   # CNBC Markets
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",   # CNBC Finance
    "https://www.cnbc.com/id/19854910/device/rss/rss.html",   # CNBC Tech
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",          # WSJ Markets
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",  # MarketWatch
    "https://news.google.com/rss/search?q=when:1d+site:reuters.com+economy+OR+fed+OR+markets&hl=en-US&gl=US&ceid=US:en",
]

FED_FEEDS = [
    "https://www.federalreserve.gov/feeds/press_monetary.xml",
]

KR_RSS_FEEDS = [
    "https://news.google.com/rss/search?q=SK%ED%95%98%EC%9D%B4%EB%8B%89%EC%8A%A4+OR+%EC%82%BC%EC%84%B1%EC%A0%84%EC%9E%90+%EB%B0%98%EB%8F%84%EC%B2%B4&hl=ko&gl=KR&ceid=KR:ko",
    "https://news.google.com/rss/search?q=when:1d+%EC%BD%94%EC%8A%A4%ED%94%BC+OR+%ED%99%98%EC%9C%A8+OR+%EC%99%B8%EA%B5%AD%EC%9D%B8+%EC%88%9C%EB%A7%A4%EC%88%98&hl=ko&gl=KR&ceid=KR:ko",
]

SKIP_STARTS = (
    "My ", "I ", "Can I", "Should I", "Is this", "Am I",
    "We ", "Dear ", "Ask ", "How much",
)

SKIP_CONTAINS = (
    "my wife", "my husband", "my mother", "my father",
    "my son", "my daughter", "my friend", "my brother", "my sister",
)


def clean_title(entry):
    """제목에서 구글뉴스가 덧붙인 ' - 언론사' 꼬리표를 제거한다."""
    title = (entry.get("title") or "").strip()
    if not title:
        return ""

    src = (entry.get("source") or {}).get("title", "")
    if src:
        suffix = f" - {src}"
        while title.endswith(suffix):
            title = title[:-len(suffix)].strip()

    # 원문 제목에 이미 언론사명이 붙어 있는 경우가 있다.
    # (예: "... 벗어나 - 조선비즈 - Chosunbiz" → 영문명만 위에서 떨어진다)
    # 마지막 조각이 짧은 한 낱말이면 언론사명으로 보고 한 번 더 뗀다.
    head, sep, tail = title.rpartition(" - ")
    if sep and head and " " not in tail and len(tail) <= 8:
        title = head.strip()

    return title


def is_personal(title):
    if title.startswith(SKIP_STARTS):
        return True
    low = title.lower()
    return any(s in low for s in SKIP_CONTAINS)


def bigrams(title):
    t = re.sub(r"[^0-9A-Za-z가-힣]+", "", title).lower()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def is_near_dup(grams, seen_grams, threshold=0.6):
    """같은 기사를 매체만 바꿔 싣는 경우를 걸러낸다."""
    if not grams:
        return False
    for prev in seen_grams:
        if not prev:
            continue
        overlap = len(grams & prev) / min(len(grams), len(prev))
        if overlap >= threshold:
            return True
    return False


def collect_titles(urls, limit, per_feed=10, skip_personal=False):
    """피드를 번갈아 훑어 한 매체가 목록을 독식하지 않게 한다."""
    buckets = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
        except Exception as ex:
            print(f"RSS 실패 {url}: {ex}")
            continue
        bucket = []
        for e in feed.entries[:per_feed]:
            title = clean_title(e)
            if not title:
                continue
            if skip_personal and is_personal(title):
                continue
            bucket.append(title)
        buckets.append(bucket)

    seen, seen_grams, out = set(), [], []
    for i in range(per_feed):
        for bucket in buckets:
            if i >= len(bucket):
                continue
            title = bucket[i]
            if title in seen:
                continue
            grams = bigrams(title)
            if is_near_dup(grams, seen_grams):
                continue
            seen.add(title)
            seen_grams.append(grams)
            out.append(title)
            if len(out) >= limit:
                return out
    return out


def build_news(limit=8):
    return collect_titles(RSS_FEEDS, limit, skip_personal=True)


def build_kr_news(limit=5):
    return collect_titles(KR_RSS_FEEDS, limit)


def build_fed(limit=3, max_age_days=3):
    """최근 발표만 싣는다. 새 발표가 없으면 빈 목록 → 섹션 자체를 생략."""
    now = time.time()
    seen, out = set(), []
    for url in FED_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as ex:
            print(f"Fed RSS 실패 {url}: {ex}")
            continue
        for e in feed.entries[:10]:
            pp = e.get("published_parsed")
            if not pp or now - calendar.timegm(pp) > max_age_days * 86400:
                continue
            title = clean_title(e)
            if not title or title in seen:
                continue
            seen.add(title)
            out.append(title)
            if len(out) >= limit:
                return out
    return out


# ==================== Gemini ====================
def _gemini_client():
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai
        return genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Gemini 클라이언트 생성 실패: {e}")
        return None

GEMINI_MODELS = ["gemini-3.6-flash", "gemini-flash-latest"]


def _gemini_call(prompt, tries=3):
    client = _gemini_client()
    if client is None:
        return ""
    for model in GEMINI_MODELS:
        for attempt in range(tries):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                text = (resp.text or "").strip()
                if text:
                    return text
            except Exception as e:
                print(f"[{model}] 시도 {attempt + 1} 실패: {e}")
            if attempt < tries - 1:
                time.sleep(15)
    return ""

def gemini_translate_news(titles):
    if not titles:
        return titles

    joined = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = f"""아래 영문 경제 뉴스 헤드라인을 한국어로 번역하세요.

규칙:
- 번호와 순서를 그대로 유지
- 자연스러운 한국어로, 과도한 의역 금지
- 기업명, 인명, 지수명은 통용되는 표기 사용
- 번역문만 출력, 다른 설명 금지

{joined}
"""
    text = _gemini_call(prompt)
    if not text:
        return titles

    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "." in line[:4]:
            head, _, tail = line.partition(".")
            if head.strip().isdigit():
                line = tail.strip()
        out.append(line)

    return out if len(out) == len(titles) else titles


REASON_LIMIT = 8


def shorten(text, limit=REASON_LIMIT):
    """코드블록 가로 폭을 지키려고 사유를 짧게 자른다. 가능하면 띄어쓰기에서."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut if len(cut) >= 4 else text[:limit]


def gemini_reasons(rows, headlines):
    """관심 종목별 한 줄 사유를 {라벨: 사유} 로 만든다. 실패하면 빈 dict.

    QLD와 TQQQ처럼 같은 지수를 추종하는 종목은 사유가 다를 수 없다.
    그래서 종목이 아니라 기초자산 단위로 묻고, 답을 그 묶음 전체에 나눠준다.
    """
    movers = [(label, chg) for label, price, chg in rows if price is not None]
    if not movers or not headlines:
        return {}

    groups = {}   # 기초자산 -> [(티커, 등락률)]
    for label, chg in movers:
        groups.setdefault(TICKER_BASIS.get(label, label), []).append((label, chg))

    lines = []
    for basis, members in groups.items():
        names = ", ".join(t for t, _ in members)
        avg = sum(c for _, c in members) / len(members)
        lines.append(f"{names} ({basis}) {avg:+.1f}%")
    quotes = "\n".join(lines)
    news = "\n".join(f"- {h}" for h in headlines)

    prompt = f"""아래는 오늘 내가 보유한 미국 자산의 등락률과 오늘자 뉴스 헤드라인입니다.
각 줄이 왜 그렇게 움직였는지 한국어로 아주 짧게 설명하세요.

괄호 안은 그 종목이 추종하는 기초자산입니다.
기초자산이 움직여서 종목이 움직이므로, 그 기초자산이나 해당 섹터에 관한
뉴스가 있으면 그것이 곧 사유입니다. 반드시 연결해서 쓰세요.

예시:
  뉴스에 "나스닥 기술주 급락"이 있으면 -> QLD, TQQQ | 나스닥 급락
  뉴스에 "반도체주 반등"이 있으면 -> USD, SOXL | 반도체 반등

규칙:
- 출력은 "왼쪽 이름들 | 사유" 형식으로 한 줄씩, 입력 순서와 개수를 그대로 유지
- 왼쪽 이름은 입력에 적힌 그대로 옮겨 쓸 것
- 사유는 공백 포함 {REASON_LIMIT}자 이내, 명사형으로 끝낼 것
- 사유는 그 줄의 기초자산과 직접 관련된 것이어야 함
  (나스닥100을 추종하는 줄에 반도체 이야기를 쓰지 말 것)
- 사유는 등락 방향과 일치해야 함. 오른 줄에 하락 사유를 붙이지 말 것
- 등락폭에 맞는 표현을 쓸 것
  1% 미만은 보합/소폭, 급등·급락·폭락은 3% 이상일 때만
- 뉴스에 전혀 없는 구체적 사건(실적 발표, 계약 체결, 목표가 변경)은 지어내지 말 것
- 연결할 뉴스가 하나도 없는 줄만 비워둘 것
- 대부분의 줄은 채워져야 정상입니다. 전부 비우는 것은 잘못된 답입니다.
- 마크다운 기호 사용 금지, 다른 설명 금지

[보유 자산]
{quotes}

[오늘 뉴스]
{news}
"""
    text = _gemini_call(prompt)
    if not text:
        return {}
    print(f"[사유 응답]\n{text}")

    # 모델이 형식을 흔들어도 최대한 건져낸다.
    # 한 줄에 여러 티커가 있으면 그 줄의 사유를 모두에게 준다.
    labels = sorted({label for label, _ in movers}, key=len, reverse=True)
    sep = "|" if "|" in text else ":"

    out = {}
    for line in text.split("\n"):
        head, found, why = line.partition(sep)
        if not found:
            continue
        why = why.strip().strip("*").strip("-–—").strip()
        if not why:
            continue
        for label in labels:
            if label in head and label not in out:
                out[label] = shorten(why)

    missed = [v for v in labels if v not in out]
    if missed:
        print(f"[사유 없음] {', '.join(missed)}")
    return out


def gemini_summary(raw_text):
    prompt = f"""아래는 오늘자 미국 경제 데이터입니다.
이 데이터만 근거로 4~5줄의 한국어 브리핑 요약을 작성하세요.

규칙:
- 데이터에 없는 내용은 절대 추측하지 말 것
- 마크다운 기호 사용 금지
- 존댓말, 간결한 문장
- 지수 흐름, 반도체 관련 동향, 연준·정책 발표, 주목할 뉴스 위주

[데이터]
{raw_text}
"""
    return _gemini_call(prompt)


# ==================== 텔레그램 ====================
def send_telegram(text, parse_mode=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    r = requests.post(url, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


# ==================== 조립 ====================
def compose(as_html, summary, market, holdings, indicators, fed, news, kr_news):
    """같은 내용을 평문(Gemini 입력용)과 HTML(발송용) 두 벌로 만든다."""
    # 따옴표까지 바꾸면 &#x27; 이 그대로 노출될 수 있어 태그 문자만 이스케이프한다
    esc = (lambda t: html.escape(t, quote=False)) if as_html else (lambda t: t)
    parts = [f"📊 미국 경제 브리핑 | {esc(today)}"]

    if summary:
        parts.append("📝 요약\n" + esc(summary))

    parts.append("📈 시장 동향\n" +
                 ("\n".join(esc(l) for l in market) if market else "조회 실패"))

    if holdings:
        block = "\n".join(esc(l) for l in holdings)
        # 고정폭 블록이라야 숫자 자릿수가 세로로 맞는다
        parts.append("💼 관심 종목\n" + (f"<pre>{block}</pre>" if as_html else block))
    else:
        parts.append("💼 관심 종목\n조회 실패")

    parts.append("📉 주요 지표\n" +
                 ("\n".join(esc(l) for l in indicators) if indicators else "조회 실패"))

    if fed:
        parts.append("🏛️ 연준·정책 발표\n" +
                     "\n".join(f"• {esc(t)}" for t in fed))

    parts.append("📰 주요 뉴스\n" +
                 ("\n".join(f"• {esc(t)}" for t in news) if news else "조회 실패"))

    parts.append("🇰🇷 국내 시장·반도체\n" +
                 ("\n".join(f"• {esc(t)}" for t in kr_news) if kr_news else "조회 실패"))

    return "\n\n".join(parts)


def main():
    market_rows = fetch_quotes(YAHOO_TICKERS)
    holding_rows = fetch_quotes(MY_TICKERS)
    indicators = build_indicators()
    news = build_news()
    fed = build_fed()

    # 번역은 한 번에 묶어 호출 수와 rate limit 위험을 줄인다
    merged = gemini_translate_news(news + fed)
    news_kr, fed_kr = merged[:len(news)], merged[len(news):]

    kr_news = build_kr_news()

    # 사유는 관심 종목에만 붙인다. 번역된 한국어 헤드라인이 근거.
    reasons = gemini_reasons(holding_rows, news_kr + fed_kr + kr_news)
    market = format_quotes(market_rows)
    holdings = format_quotes(holding_rows, reasons, align=True)

    sections = (market, holdings, indicators, fed_kr, news_kr, kr_news)
    summary = gemini_summary(compose(False, None, *sections))
    send_telegram(compose(True, summary, *sections), parse_mode="HTML")
    print("발송 완료")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(f"⚠️ {today} 브리핑 오류\n{type(e).__name__}: {e}")
        raise