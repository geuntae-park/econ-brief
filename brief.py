import os
import requests
import feedparser
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FRED_API_KEY = os.environ["FRED_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y년 %m월 %d일")

# ---------- FRED 지표 ----------
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
    obs = [o for o in r.json().get("observations", []) if o["value"] != "."]
    return obs


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
                obs_y = fred_fetch(sid, limit=14)
                if len(obs_y) >= 13:
                    prev_year = float(obs_y[12]["value"])
                    yoy = (latest / prev_year - 1) * 100
                    lines.append(f"{label}  {yoy:.1f}%  ({date})")
            elif mode == "pct":
                prev = float(obs[1]["value"]) if len(obs) > 1 else latest
                diff = latest - prev
                sign = "+" if diff >= 0 else ""
                lines.append(f"{label}  {latest:.2f}%  ({sign}{diff:.2f})")
            elif mode == "diff_k":
                prev = float(obs[1]["value"]) if len(obs) > 1 else latest
                diff = (latest - prev)
                lines.append(f"{label}  {diff:+,.0f}천명  ({date})")
            else:
                prev = float(obs[1]["value"]) if len(obs) > 1 else latest
                diff = latest - prev
                sign = "+" if diff >= 0 else ""
                lines.append(f"{label}  {latest:.2f}  ({sign}{diff:.2f})")
        except Exception as e:
            lines.append(f"{label}  조회실패")
    return lines


# ---------- 시장 지수 (Yahoo Finance) ----------
YAHOO_TICKERS = {
    "^GSPC": "S&P500",
    "^IXIC": "나스닥",
    "^DJI":  "다우",
    "^VIX":  "VIX",
}


def yahoo_quote(ticker):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(url, headers=headers, params={"range": "5d", "interval": "1d"}, timeout=20)
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    meta = result["meta"]
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    return price, prev


def build_market():
    lines = []
    for tk, label in YAHOO_TICKERS.items():
        try:
            price, prev = yahoo_quote(tk)
            if price is None or not prev:
                lines.append(f"{label}  조회실패")
                continue
            chg = (price / prev - 1) * 100
            lines.append(f"{label}  {price:,.2f}  {chg:+.2f}%")
        except Exception:
            lines.append(f"{label}  조회실패")
    return lines


# ---------- 뉴스 RSS ----------
RSS_FEEDS = [
    "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
]

SKIP_STARTS = (
    "My ", "I ", "Can I", "Should I", "Is this", "Am I",
    "We ", "Dear ", "Ask ", "How much",
)

SKIP_CONTAINS = (
    "my wife", "my husband", "my mother", "my father",
    "my son", "my daughter", "my friend", "my brother", "my sister",
)


def build_news(limit=6):
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:15]:
                title = e.get("title", "").strip()
                if not title:
                    continue
                if title.startswith(SKIP_STARTS):
                    continue
                low = title.lower()
                if any(s in low for s in SKIP_CONTAINS):
                    continue
                items.append(title)
        except Exception as ex:
            print(f"RSS 실패 {url}: {ex}")
            continue

    seen, out = set(), []
    for t in items:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= limit:
            break
    return out


# ---------- Gemini 요약 (선택) ----------
def gemini_summary(raw_text):
    if not GEMINI_API_KEY:
        return ""

    import time
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return ""

    prompt = f"""아래는 오늘자 미국 경제 데이터입니다.
이 데이터만 근거로 3~4줄의 한국어 브리핑 요약을 작성하세요.

규칙:
- 데이터에 없는 내용은 절대 추측하지 말 것
- 마크다운 기호 사용 금지
- 존댓말, 간결한 문장
- 시장 분위기와 주목할 점 위주

[데이터]
{raw_text}
"""

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
            )
            text = (resp.text or "").strip()
            if text:
                return text
        except Exception as e:
            print(f"Gemini 시도 {attempt + 1} 실패: {e}")
        if attempt < 2:
            time.sleep(8)

    return ""

def gemini_translate_news(titles):
    if not GEMINI_API_KEY or not titles:
        return titles

    import time
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return titles

    joined = "\n".join(f"{i+1}. {t}" for i, t in enumerate(titles))
    prompt = f"""아래 영문 경제 뉴스 헤드라인을 한국어로 번역하세요.

규칙:
- 번호와 순서를 그대로 유지
- 의역보다 직역, 단 자연스러운 한국어로
- 기업명, 인명, 지수명은 원문 그대로 두거나 통용 표기 사용
- 번역문만 출력, 다른 설명 금지

{joined}
"""

    for attempt in range(3):
        try:
            resp = client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
            )
            text = (resp.text or "").strip()
            if not text:
                continue
            out = []
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if "." in line[:4]:
                    line = line.split(".", 1)[1].strip()
                out.append(line)
            if len(out) == len(titles):
                return out
        except Exception as e:
            print(f"번역 시도 {attempt + 1} 실패: {e}")
        if attempt < 2:
            time.sleep(8)

    return titles

# ---------- 텔레그램 ----------
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHANNEL_ID,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=30)
    r.raise_for_status()
    return r.json()


# ---------- 조립 ----------
def main():
    market = build_market()
    indicators = build_indicators()
    news = build_news()
    news_kr = gemini_translate_news(news)

    body = f"📊 미국 경제 브리핑 | {today}\n"

    body += "\n■ 시장 동향\n"
    body += "\n".join(market) if market else "조회 실패"

    body += "\n\n■ 주요 지표\n"
    body += "\n".join(indicators) if indicators else "조회 실패"

    body += "\n\n■ 주요 뉴스\n"
    body += "\n".join(f"· {t}" for t in news_kr) if news_kr else "조회 실패"

    summary = gemini_summary(body)

    if summary:
        final = f"📊 미국 경제 브리핑 | {today}\n\n■ 요약\n{summary}\n" + body.split("\n", 1)[1]
    else:
        final = body

    send_telegram(final)
    print("발송 완료")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(f"⚠️ {today} 브리핑 오류\n{type(e).__name__}: {e}")
        raise