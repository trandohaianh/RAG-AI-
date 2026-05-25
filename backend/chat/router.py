"""Routes: conversations CRUD + streaming chat."""

import json



from fastapi import APIRouter, Depends, HTTPException

from fastapi.responses import StreamingResponse

from sqlalchemy.orm import Session



from auth.schemas import CurrentUser

from chat import service as chat_service

from chat.schemas import ChatRequest, ConversationCreate, ConversationOut, MessageOut

from config import GEMINI_API_KEY

from database import get_db

from dependencies import get_current_user

from rag.chain import stream_rag_answer



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





@router.post("/chat/{conv_id}")

async def chat_stream(

    conv_id: int,

    body: ChatRequest,

    user: CurrentUser = Depends(get_current_user),

    db: Session = Depends(get_db),

):

    """Stream RAG response, lưu user + assistant message vào DB."""

    if not GEMINI_API_KEY:

        raise HTTPException(status_code=500, detail="Thiếu GEMINI_API_KEY")

    from saas.limits import enforce_query_quota

    enforce_query_quota(db, user.id, user.subscription_plan)

    conv = chat_service.get_conversation(db, user.id, conv_id)

    if not conv:

        raise HTTPException(status_code=404, detail="Không tìm thấy hội thoại")



    chat_service.add_message(db, conv_id, "user", body.question)

    history = chat_service.get_history_for_rag(db, conv_id)



    # Snapshot trước khi đóng session — generator chạy sau khi response bắt đầu

    user_id = user.id

    question = body.question

    history_snapshot = list(history)



    full_answer: list[str] = []

    sources: list[str] = []



    async def event_generator():

        nonlocal sources

        try:

            async for kind, payload in stream_rag_answer(

                user_id, question, history_snapshot

            ):

                if kind == "token":

                    full_answer.append(payload)  # type: ignore[arg-type]

                    yield f"data: {json.dumps({'token': payload}, ensure_ascii=False)}\n\n"

                elif kind == "done":

                    sources = payload  # type: ignore[assignment]

                    yield f"data: {json.dumps({'done': True, 'sources': sources}, ensure_ascii=False)}\n\n"

        except Exception as exc:

            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"

            return



        answer_text = "".join(full_answer)

        from database import SessionLocal



        db_save = SessionLocal()

        try:

            chat_service.add_message(db_save, conv_id, "assistant", answer_text, sources)

        finally:

            db_save.close()



    return StreamingResponse(

        event_generator(),

        media_type="text/event-stream",

        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},

    )

