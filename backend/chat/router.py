"""Routes: conversations CRUD + streaming chat."""

import json
import logging
from contextlib import aclosing



from fastapi import APIRouter, Depends, HTTPException

from fastapi.responses import StreamingResponse

from sqlalchemy.orm import Session



from auth.schemas import CurrentUser

from chat import service as chat_service

from chat.schemas import ChatRequest, ConversationCreate, ConversationOut, FeedbackRequest, MessageOut

from config import GEMINI_API_KEY, JUDGE_SAMPLE_RATE, LLM_PROVIDER

from database import get_db

from dependencies import get_current_user

from rag.chain import RAG_ERROR_MESSAGE, stream_rag_answer
from rag.evidence import public_bundle
from rag.judge import judge_answer

_background_tasks: set = set()


def _schedule_eval(message_id: int, question: str, context: str, answer: str,
                   user_id: int | None = None) -> None:
    """Chấm điểm faithfulness/relevancy chạy nền, không chặn response."""
    import asyncio
    import random

    if random.random() >= JUDGE_SAMPLE_RATE:
        return

    async def _run() -> None:
        from fastapi import HTTPException

        from costs.budget import enforce_budget
        from database import SessionLocal

        # Judge chạy SAU khi request chat đã trả khoản đặt trước: nó cần khoản riêng, và khi
        # ngân sách đã cạn thì bỏ qua lần chấm (chất lượng đo bằng mẫu, thiếu một mẫu không sao).
        with SessionLocal() as budget_db:
            try:
                reservation = enforce_budget(budget_db, user_id)
            except HTTPException:
                return
        with reservation:
            result = await judge_answer(question, context, answer)
        if not result:
            return

        db = SessionLocal()
        try:
            chat_service.set_message_eval(
                db, message_id, result["faithfulness"], result["relevancy"]
            )
        finally:
            db.close()

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)



router = APIRouter(tags=["chat"])





@router.get("/conversations", response_model=list[ConversationOut])

def list_conversations(

    user: CurrentUser = Depends(get_current_user),

    db: Session = Depends(get_db),

):

    return chat_service.list_conversations(db, user.id)





@router.post("/conversations", response_model=ConversationOut)

def create_conversation(

    body: ConversationCreate,

    user: CurrentUser = Depends(get_current_user),

    db: Session = Depends(get_db),

):

    conv = chat_service.create_conversation(db, user.id, body.title)

    return ConversationOut.model_validate(conv)





@router.get("/conversations/{conv_id}/messages", response_model=list[MessageOut])

def get_messages(

    conv_id: int,

    user: CurrentUser = Depends(get_current_user),

    db: Session = Depends(get_db),

):

    conv = chat_service.get_conversation(db, user.id, conv_id)

    if not conv:

        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")

    return [MessageOut(**chat_service.message_to_out(m)) for m in chat_service.get_messages(db, conv_id)]





@router.delete("/conversations/{conv_id}")

def delete_conversation(

    conv_id: int,

    user: CurrentUser = Depends(get_current_user),

    db: Session = Depends(get_db),

):

    if not chat_service.delete_conversation(db, user.id, conv_id):

        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")

    return {"message": "Đã xóa hội thoại"}


@router.post("/conversations/{conv_id}/messages/{message_id}/feedback", response_model=MessageOut)
def submit_feedback(
    conv_id: int,
    message_id: int,
    body: FeedbackRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = chat_service.set_message_feedback(db, user.id, conv_id, message_id, body.rating)
    if not msg:
        raise HTTPException(status_code=404, detail="Không tìm thấy câu trả lời")
    return MessageOut(**chat_service.message_to_out(msg))





@router.post("/chat/{conv_id}")

async def chat_stream(

    conv_id: int,

    body: ChatRequest,

    user: CurrentUser = Depends(get_current_user),

    db: Session = Depends(get_db),

):

    """Stream RAG response, lưu user + assistant message vào DB."""

    if LLM_PROVIDER != "ollama" and not GEMINI_API_KEY:

        raise HTTPException(status_code=500, detail="Thiếu GEMINI_API_KEY")

    from saas.limits import enforce_query_quota, release_query_quota

    conv = chat_service.get_conversation(db, user.id, conv_id)

    if not conv:

        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")



    from costs.budget import enforce_budget

    budget = enforce_budget(db, user.id)
    try:
        reservation = enforce_query_quota(db, user.id, user.subscription_plan)
    except Exception:
        budget.release()
        raise
    try:
        chat_service.add_message(db, conv_id, "user", body.question)
        history = chat_service.get_history_for_rag(db, conv_id)
    except Exception:
        release_query_quota(db, reservation)
        budget.release()
        raise



    # Snapshot trước khi đóng session — generator chạy sau khi response bắt đầu

    user_id = user.id

    question = body.question

    source = body.source

    history_snapshot = list(history)



    full_answer: list[str] = []

    sources: list[str] = []
    evidence_bundle: dict = {}

    retrieved_context: list[str] = [""]



    async def event_generator():

        nonlocal sources

        from costs.context import set_request_usage

        set_request_usage(user_id=user_id, feature="chat")

        try:

            async with aclosing(stream_rag_answer(

                user_id, question, history_snapshot, source, include_evidence=True

            )) as answer_stream:
                async for kind, payload in answer_stream:

                    if kind == "evidence":
                        evidence_bundle.update(payload)
                    elif kind == "context":

                        retrieved_context[0] = payload  # type: ignore[assignment]

                    elif kind == "token":

                        full_answer.append(payload)  # type: ignore[arg-type]

                        yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"

                    elif kind == "done":

                        sources = payload  # type: ignore[assignment]

                        answer_text = "".join(full_answer)

                        from database import SessionLocal

                        db_save = SessionLocal()

                        try:

                            saved = chat_service.add_message(db_save, conv_id, "assistant", answer_text, sources, **({"evidence": evidence_bundle} if evidence_bundle else {}))

                            saved_id = saved.id

                        finally:

                            db_save.close()

                        # Câu từ chối "chưa có tài liệu" không có context: judge sẽ chấm nó
                        # faithfulness=1.0 và làm lệch trung bình chất lượng, nên không lấy mẫu.
                        if answer_text.strip() and str(retrieved_context[0] or "").strip():

                            _schedule_eval(saved_id, question, retrieved_context[0], answer_text, user_id)

                        yield f"data: {json.dumps({'done': True, 'sources': sources, 'message_id': saved_id, **public_bundle(evidence_bundle)}, ensure_ascii=False)}\n\n"

                        return

        except Exception as exc:

            logging.getLogger(__name__).warning("RAG request failed (%s)", type(exc).__name__)
            yield f"data: {json.dumps({'error': RAG_ERROR_MESSAGE, 'code': 'rag_unavailable'}, ensure_ascii=False)}\n\n"

            return

        finally:
            # Chi phí thật đã được ghi khi stream kết thúc: trả lại khoản đặt trước.
            budget.release()



    return StreamingResponse(

        event_generator(),

        media_type="text/event-stream",

        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},

    )



@router.get("/conversations/{conv_id}/messages/{message_id}/evidence/{reference_id}")
def get_message_evidence(conv_id: int, message_id: int, reference_id: str,
                         user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from chat.models import Message
    from rag.evidence import read_bundle, evidence_status
    if not chat_service.get_conversation(db, user.id, conv_id):
        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")
    message = db.query(Message).filter_by(id=message_id, conversation_id=conv_id, role="assistant").first()
    if message is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bằng chứng")
    bundle = read_bundle(message.evidence_json)
    item = next((item for item in bundle['evidence'] if item['reference_id'] == reference_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy bằng chứng")
    from fastapi.responses import JSONResponse
    return JSONResponse({"evidence": item, "document_status": evidence_status(bundle, f"user_{user.id}")}, headers={"Cache-Control": "no-store"})
