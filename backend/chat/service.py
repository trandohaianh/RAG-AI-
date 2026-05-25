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





def get_history_for_rag(db: Session, conv_id: int, exclude_last: bool = True) -> list[dict]:

    """Lịch sử hội thoại cho prompt RAG."""

    messages = get_messages(db, conv_id)

    if exclude_last and messages:

        messages = messages[:-1]

    return [{"role": m.role, "content": m.content} for m in messages]

