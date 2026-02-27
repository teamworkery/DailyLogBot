"""
Daily Journal Bot
=================
매일 오후 5시에 Telegram으로 일기 질문을 보내고,
답변을 받아 AI로 요약한 뒤 Notion에 저장합니다.
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
# 상태 관리 (오늘 질문 발송 여부, 답변 수신 여부)
# ─────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {
        "last_question_date": None,
        "today_page_id": None,
        "today_page_date": None,
        "today_messages": [],
    }


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# 오후 5시: 일기 질문 발송
# ─────────────────────────────────────────────
async def send_daily_question(context: ContextTypes.DEFAULT_TYPE):
    today = str(datetime.now(KST).date())
    state = load_state()

    if state.get("last_question_date") == today:
        logger.info(f"[{today}] 이미 오늘 질문을 보냈습니다. 건너뜁니다.")
        return

    # 이미 오늘 메시지를 보낸 적이 있으면 질문 생략
    if state.get("today_page_date") == today and state.get("today_messages"):
        logger.info(f"[{today}] 이미 오늘 메시지가 있어 질문을 건너뜁니다.")
        state["last_question_date"] = today
        save_state(state)
        return

    text = (
        f"📅 {today}\n\n"
        f"오늘은 무슨 일을 하셨나요?\n"
        f"(What did you do today?) ✍️"
    )

    await context.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)

    state["last_question_date"] = today
    save_state(state)

    logger.info(f"[{today}] 일기 질문을 발송했습니다.")


# ─────────────────────────────────────────────
# 답변 수신 → AI 요약 → Notion 저장
# ─────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # 지정한 Chat ID 에서 온 메시지만 처리
    if update.effective_chat.id != TELEGRAM_CHAT_ID:
        return

    reply_text = update.message.text or ""
    if not reply_text.strip():
        return

    state = load_state()
    today = str(datetime.now(KST).date())

    # 날짜가 바뀌었으면 상태 초기화
    if state.get("today_page_date") != today:
        state["today_page_id"] = None
        state["today_page_date"] = today
        state["today_messages"] = []

    is_first = state.get("today_page_id") is None

    # ── 처리 중 메시지 ──
    if is_first:
        await update.message.reply_text("⏳ 일기를 정리하고 있어요...")
    else:
        await update.message.reply_text("⏳ 추가 내용을 저장하고 있어요...")

    # ── 메시지 누적 ──
    state.setdefault("today_messages", [])
    state["today_messages"].append(reply_text)

    # ── 전체 메시지로 AI 요약 ──
    all_text = "\n\n".join(state["today_messages"])
    logger.info(f"[{today}] AI 요약 생성 중... (메시지 {len(state['today_messages'])}개)")
    ai_summary = summarize_with_anthropic(all_text, today)

    if is_first:
        # ── 첫 메시지: Notion 페이지 생성 ──
        logger.info(f"[{today}] Notion 페이지 생성 중...")
        page_id, notion_url = create_notion_page(today, reply_text, ai_summary)
        state["today_page_id"] = page_id
    else:
        # ── 추가 메시지: 기존 페이지에 append ──
        page_id = state["today_page_id"]
        logger.info(f"[{today}] Notion 페이지에 추가 중... (page_id={page_id})")
        notion_url = append_to_notion(page_id, reply_text, all_text, ai_summary)

    save_state(state)

    # ── 완료 메시지 ──
    result_msg = (
        f"✅ {'저장' if is_first else '추가 저장'} 완료!\n\n"
        f"🤖 *AI 요약:*\n{ai_summary}"
    )
    if notion_url:
        result_msg += f"\n\n🔗 [Notion에서 보기]({notion_url})"

    await update.message.reply_text(result_msg, parse_mode="Markdown")
    logger.info(f"[{today}] 완료! Notion URL: {notion_url}")


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
                    f"다음은 {date_str}의 하루 일기입니다.\n"
                    f"한국어로 핵심 내용과 주요 업무/하이라이트를 "
                    f"2-4개의 bullet point (• 기호 사용)로 간결하게 요약해주세요:\n\n{text}"
                )
            }]
        )
        return message.content[0].text
    except Exception as e:
        logger.error(f"Anthropic API 오류: {e}")
        return f"(요약 생성 실패: {e})"


# ─────────────────────────────────────────────
# Notion API 공통 헤더
# ─────────────────────────────────────────────
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


# ─────────────────────────────────────────────
# Notion API: 페이지 생성 (첫 메시지)
# ─────────────────────────────────────────────
def create_notion_page(date_str: str, reply_text: str, ai_summary: str) -> tuple[str | None, str]:
    """Notion 데이터베이스에 새 페이지를 생성하고 (page_id, url) 반환."""
    body = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "내용": {
                "title": [{"type": "text", "text": {"content": date_str}}]
            },
            "날짜": {
                "date": {"start": date_str}
            },
            "일기": {
                "rich_text": [{
                    "type": "text",
                    "text": {
                        "content": f"📝 원문:\n{reply_text}\n\n🤖 AI 요약:\n{ai_summary}"
                    }
                }]
            },
        },
        "children": [
            {
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": "📝 원문 답변"}}]}
            },
            {
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": reply_text}}]}
            },
            {"object": "block", "type": "divider", "divider": {}},
            {
                "object": "block", "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": "🤖 AI 요약"}}]}
            },
            {
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": ai_summary}}]}
            },
        ],
    }

    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS, json=body, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("id"), data.get("url", "")
    except Exception as e:
        logger.error(f"Notion 페이지 생성 오류: {e}")
        return None, ""


# ─────────────────────────────────────────────
# Notion API: 기존 페이지에 추가 (추가 메시지)
# ─────────────────────────────────────────────
def append_to_notion(page_id: str, new_text: str, all_text: str, ai_summary: str) -> str:
    """기존 Notion 페이지에 추가 원문 블록을 append 하고, 일기 속성(요약)을 갱신."""
    notion_url = ""

    # 1) 블록 append: 추가 원문
    append_body = {
        "children": [
            {"object": "block", "type": "divider", "divider": {}},
            {
                "object": "block", "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": f"📝 추가:\n{new_text}"}}]}
            },
        ]
    }
    try:
        resp = requests.patch(
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=NOTION_HEADERS, json=append_body, timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"Notion 블록 append 오류: {e}")

    # 2) 일기 속성 갱신: 전체 원문 + 새 요약
    summary_content = f"📝 원문:\n{all_text}\n\n🤖 AI 요약:\n{ai_summary}"
    # Notion rich_text 속성은 최대 2000자
    if len(summary_content) > 2000:
        summary_content = summary_content[:1997] + "..."

    update_body = {
        "properties": {
            "일기": {
                "rich_text": [{
                    "type": "text",
                    "text": {"content": summary_content}
                }]
            }
        }
    }
    try:
        resp = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS, json=update_body, timeout=15,
        )
        resp.raise_for_status()
        notion_url = resp.json().get("url", "")
    except Exception as e:
        logger.error(f"Notion 속성 갱신 오류: {e}")

    return notion_url


# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    logger.info("🤖 Daily Journal Bot 시작!")
    logger.info(f"📅 매일 {QUESTION_HOUR:02d}:{QUESTION_MINUTE:02d}에 질문을 발송합니다.")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # 메시지 핸들러 등록
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 매일 지정 시간에 질문 발송 (KST 기준)
    app.job_queue.run_daily(
        callback=send_daily_question,
        time=time(hour=QUESTION_HOUR, minute=QUESTION_MINUTE, second=0, tzinfo=KST),
        days=(0, 1, 2, 3, 4, 5, 6),
        name="daily_question",
    )

    # 봇 실행 (long polling)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
