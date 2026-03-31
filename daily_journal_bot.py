"""
Daily Journal Bot
=================
텔레그램으로 수시로 활동을 기록하면 Notion 상세DB에 즉시 저장하고,
매일 오후 5시에 하루 기록을 AI로 요약하여 메인DB에 저장합니다.
"""

import json
import logging
import os
import requests
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

# 한국 표준시 (KST = UTC+9)
KST = timezone(timedelta(hours=9))

# ─────────────────────────────────────────────
# 설정 로드
# ─────────────────────────────────────────────
load_dotenv("config.env")  # 로컬 실행용 (Railway에서는 환경변수 사용)

TELEGRAM_BOT_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID     = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY")
NOTION_TOKEN         = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID   = os.getenv("NOTION_DATABASE_ID")
NOTION_DETAIL_DB_ID  = os.getenv("NOTION_DETAIL_DATABASE_ID")
QUESTION_HOUR        = int(os.getenv("QUESTION_HOUR", "17"))   # 기본: 오후 5시
QUESTION_MINUTE      = int(os.getenv("QUESTION_MINUTE", "0"))

STATE_FILE = Path(__file__).parent / "bot_state.json"

# ─────────────────────────────────────────────
# 로깅
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent / "bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 상태 관리
# ─────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_summary_date": None,
        "today_page_id": None,
        "today_page_date": None,
    }


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# Notion API 공통 헤더
# ─────────────────────────────────────────────
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


# ─────────────────────────────────────────────
# 메시지 수신 → 상세DB에 즉시 기록
# ─────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        return

    reply_text = update.message.text or ""
    if not reply_text.strip():
        return

    now = datetime.now(KST)
    today = str(now.date())
    time_str = now.strftime("%H:%M")

    state = load_state()

    # 날짜가 바뀌었으면 상태 초기화
    if state.get("today_page_date") != today:
        state["today_page_id"] = None
        state["today_page_date"] = today

    # 메인DB 페이지가 없으면 생성
    if state.get("today_page_id") is None:
        logger.info(f"[{today}] 메인DB 페이지 생성 중...")
        page_id = create_main_page(today)
        state["today_page_id"] = page_id
        save_state(state)

    # 상세DB에 기록
    page_id = state["today_page_id"]
    success = create_detail_record(reply_text, time_str, today, page_id)

    if success:
        await update.message.reply_text(f"✅ {time_str} 기록 완료!")
    else:
        await update.message.reply_text(f"❌ 기록 실패. 로그를 확인해주세요.")

    logger.info(f"[{today} {time_str}] 기록 {'성공' if success else '실패'}: {reply_text[:50]}")


# ─────────────────────────────────────────────
# Notion API: 메인DB 페이지 생성 (하루 단위)
# ─────────────────────────────────────────────
def create_main_page(date_str: str) -> str | None:
    """메인 Daily Log DB에 날짜 페이지를 생성하고 page_id를 반환."""
    body = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "내용": {
                "title": [{"type": "text", "text": {"content": date_str}}]
            },
            "날짜": {
                "date": {"start": date_str}
            },
        },
    }

    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS, json=body, timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("id")
    except Exception as e:
        logger.error(f"메인DB 페이지 생성 오류: {e}")
        return None


# ─────────────────────────────────────────────
# Notion API: 상세DB에 기록 추가
# ─────────────────────────────────────────────
def create_detail_record(text: str, time_str: str, date_str: str, main_page_id: str | None) -> bool:
    """상세DB에 개별 기록을 추가."""
    title = f"{time_str} | {text}"
    if len(title) > 200:
        title = title[:197] + "..."

    properties = {
        "이름": {
            "title": [{"type": "text", "text": {"content": title}}]
        },
        "시작 시간": {
            "rich_text": [{"type": "text", "text": {"content": time_str}}]
        },
        "메모": {
            "rich_text": [{"type": "text", "text": {"content": text}}]
        },
        "날짜(Daily Log 연결)": {
            "date": {"start": date_str}
        },
    }

    if main_page_id:
        properties["Daily Log DB"] = {
            "relation": [{"id": main_page_id}]
        }

    body = {
        "parent": {"database_id": NOTION_DETAIL_DB_ID},
        "properties": properties,
    }

    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS, json=body, timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"상세DB 기록 오류: {e}")
        return False


# ─────────────────────────────────────────────
# Notion API: 오늘 상세 기록 조회
# ─────────────────────────────────────────────
def get_today_records(date_str: str) -> list[dict]:
    """상세DB에서 해당 날짜의 기록을 시간순으로 조회."""
    body = {
        "filter": {
            "property": "날짜(Daily Log 연결)",
            "date": {"equals": date_str}
        },
        "sorts": [{"property": "시작 시간", "direction": "ascending"}],
    }

    try:
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DETAIL_DB_ID}/query",
            headers=NOTION_HEADERS, json=body, timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("results", [])
    except Exception as e:
        logger.error(f"상세DB 조회 오류: {e}")
        return []


def format_records_for_summary(records: list[dict]) -> str:
    """조회된 기록을 AI 요약용 텍스트로 변환."""
    lines = []
    for r in records:
        props = r.get("properties", {})
        time_val = ""
        time_rt = props.get("시작 시간", {}).get("rich_text", [])
        if time_rt:
            time_val = time_rt[0].get("plain_text", "")

        memo_val = ""
        memo_rt = props.get("메모", {}).get("rich_text", [])
        if memo_rt:
            memo_val = memo_rt[0].get("plain_text", "")

        if time_val and memo_val:
            lines.append(f"{time_val} | {memo_val}")
        elif memo_val:
            lines.append(memo_val)

    return "\n".join(lines)


# ─────────────────────────────────────────────
# Notion API: 메인DB 일기 필드 업데이트
# ─────────────────────────────────────────────
def update_main_page_summary(page_id: str, ai_summary: str):
    """메인DB 페이지의 '일기' 속성을 AI 요약으로 갱신."""
    content = ai_summary
    if len(content) > 2000:
        content = content[:1997] + "..."

    body = {
        "properties": {
            "일기": {
                "rich_text": [{"type": "text", "text": {"content": content}}]
            }
        }
    }

    try:
        resp = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS, json=body, timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"메인DB 요약 갱신 오류: {e}")


# ─────────────────────────────────────────────
# Anthropic API: AI 요약
# ─────────────────────────────────────────────
def summarize_with_anthropic(text: str, date_str: str) -> str:
    try:
        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": (
                    f"다음은 {date_str}에 시간순으로 기록된 하루 활동 로그입니다.\n\n"
                    f"{text}\n\n"
                    f"위 기록에서 **업무·작업 관련 내용만** 추려서, "
                    f"시간 흐름 순서대로 2-5개의 bullet point (• 기호 사용)로 요약해주세요.\n"
                    f"기상, 식사, 샤워, 산책 등 일상 루틴은 제외하고 "
                    f"실제로 수행한 업무와 성과 위주로 간결하게 작성해주세요."
                )
            }]
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"Anthropic API 오류: {e}")
        return f"(요약 생성 실패: {e})"


# ─────────────────────────────────────────────
# 오후 5시: 하루 업무 요약 자동 생성
# ─────────────────────────────────────────────
async def send_daily_summary(context: ContextTypes.DEFAULT_TYPE):
    today = str(datetime.now(KST).date())
    state = load_state()

    if state.get("last_summary_date") == today:
        logger.info(f"[{today}] 이미 오늘 요약을 생성했습니다. 건너뜁니다.")
        return

    # 오늘 기록 조회
    records = get_today_records(today)
    if not records:
        logger.info(f"[{today}] 오늘 기록이 없어 요약을 건너뜁니다.")
        return

    # AI 업무 요약 생성
    records_text = format_records_for_summary(records)
    logger.info(f"[{today}] AI 업무 요약 생성 중... ({len(records)}개 기록)")
    ai_summary = summarize_with_anthropic(records_text, today)

    # 메인DB에 요약 저장
    page_id = state.get("today_page_id")
    if page_id:
        update_main_page_summary(page_id, ai_summary)
        logger.info(f"[{today}] 메인DB에 요약 저장 완료.")

    # 텔레그램에 요약 전송
    summary_msg = f"📋 {today} 업무 요약\n\n{ai_summary}"
    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=summary_msg)

    state["last_summary_date"] = today
    save_state(state)


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    logger.info("🤖 Daily Journal Bot 시작!")
    logger.info(f"📅 매일 {QUESTION_HOUR:02d}:{QUESTION_MINUTE:02d}에 업무 요약을 생성합니다.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # 메시지 핸들러 등록
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 매일 지정 시간에 질문 발송 (KST 기준)
    app.job_queue.run_daily(
        callback=send_daily_summary,
        time=time(hour=QUESTION_HOUR, minute=QUESTION_MINUTE, second=0, tzinfo=KST),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="daily_summary",
    )

    # 봇 실행 (long polling)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
