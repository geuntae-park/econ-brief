import os
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from anthropic import Anthropic

# 환경변수에서 키 읽기
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

client = Anthropic(api_key=ANTHROPIC_API_KEY)

today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y년 %m월 %d일")

PROMPT = f"""오늘은 {today}입니다. 웹 검색을 사용해 최근 24시간 미국 경제 관련 정보를 조사하고,
아래 형식으로 한국어 브리핑을 작성하세요.

📊 미국 경제 브리핑 | {today}

■ 주요 지표 발표
- (어제 발표된 경제지표와 수치, 예상치 대비 결과)

■ 시장 동향
- (S&P500, 나스닥, 다우 등락률 / 10년물 금리 / 달러인덱스)

■ 주요 이슈
- (연준 발언, 정책 이슈, 주목할 기업 뉴스 등 2~3개)

■ 오늘 주목할 일정
- (오늘 예정된 지표 발표나 이벤트)

규칙:
- 각 항목은 한 줄로 간결하게
- 수치는 반드시 검색으로 확인한 실제 값만 사용
- 확인 안 되는 항목은 "발표 없음" 또는 생략
- 전체 1500자 이내
- 마크다운 기호(**, ##) 사용 금지, 위 형식 그대로
"""

def get_briefing():
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        tools=[{
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 8
        }],
        messages=[{"role": "user", "content": PROMPT}]
    )
    return "".join(
        block.text for block in resp.content
        if block.type == "text"
    ).strip()

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHANNEL_ID,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=30)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    try:
        brief = get_briefing()
        if not brief:
            brief = f"⚠️ {today} 브리핑 생성 실패 (내용 없음)"
        send_telegram(brief)
        print("발송 완료")
    except Exception as e:
        send_telegram(f"⚠️ {today} 브리핑 오류\n{type(e).__name__}: {e}")
        raise