"""CRUD conversations & messages."""

import json



from sqlalchemy.orm import Session



from chat.models import Conversation, Message





def list_conversations(db: Session, user_id: int) -> list[Conversation]:

    return (

        db.query(Conversation)

        .filter(Conversation.user_id == user_id)

        .order_by(Conversation.created_at.desc())

        .all()

    )





def create_conversation(db: Session, user_id: int, title: str) -> Conversation:

    conv = Conversation(user_id=user_id, title=title)

    db.add(conv)

    db.commit()

    db.refresh(conv)

    return conv


def get_or_create_conversation(db: Session, user_id: int, title: str) -> Conversation:

    conv = (

        db.query(Conversation)

        .filter(Conversation.user_id == user_id, Conversation.title == title)

        .first()

    )

    if conv:

        return conv

    return create_conversation(db, user_id, title)





def get_conversation(db: Session, user_id: int, conv_id: int) -> Conversation | None:

    return (

        db.query(Conversation)

        .filter(Conversation.id == conv_id, Conversation.user_id == user_id)

        .first()

    )





def delete_conversation(db: Session, user_id: int, conv_id: int) -> bool:

    conv = get_conversation(db, user_id, conv_id)

    if not conv:

        return False

    db.delete(conv)

    db.commit()

    return True





def get_messages(db: Session, conv_id: int) -> list[Message]:

    return (

        db.query(Message)

        .filter(Message.conversation_id == conv_id)

        .order_by(Message.created_at.asc())

        .all()

    )





def message_to_out(msg: Message) -> dict:

    sources: list[str] = []

    if msg.sources_json:

        try:

            sources = json.loads(msg.sources_json)

        except json.JSONDecodeError:

            pass

    return {

        "id": msg.id,

        "role": msg.role,

        "content": msg.content,

        "sources": sources,

        "rating": msg.rating,

        "eval_faithfulness": msg.eval_faithfulness,

        "eval_relevancy": msg.eval_relevancy,

        "created_at": msg.created_at,

    }





def add_message(

    db: Session,

    conv_id: int,

    role: str,

    content: str,

    sources: list[str] | None = None,

) -> Message:

    msg = Message(

        conversation_id=conv_id,

        role=role,

        content=content,

        sources_json=json.dumps(sources or [], ensure_ascii=False),

    )

    db.add(msg)

    db.commit()

    db.refresh(msg)

    return msg





def set_message_feedback(
    db: Session, user_id: int, conv_id: int, message_id: int, rating: int
) -> Message | None:
    """Lưu đánh giá 👍/👎 cho một câu trả lời của assistant."""
    conv = get_conversation(db, user_id, conv_id)
    if not conv:
        return None
    msg = (
        db.query(Message)
        .filter(
            Message.id == message_id,
            Message.conversation_id == conv_id,
            Message.role == "assistant",
        )
        .first()
    )
    if not msg:
        return None
    msg.rating = rating if rating != 0 else None
    db.commit()
    db.refresh(msg)
    return msg


def set_message_eval(db: Session, message_id: int, faithfulness: float, relevancy: float) -> None:
    """Lưu điểm tự động chấm (LLM-as-judge) cho một câu trả lời assistant."""
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        return
    msg.eval_faithfulness = faithfulness
    msg.eval_relevancy = relevancy
    db.commit()


def get_history_for_rag(db: Session, conv_id: int, exclude_last: bool = True) -> list[dict]:

    """Lịch sử hội thoại cho prompt RAG."""

    messages = get_messages(db, conv_id)

    if exclude_last and messages:

        messages = messages[:-1]

    return [{"role": m.role, "content": m.content} for m in messages]

