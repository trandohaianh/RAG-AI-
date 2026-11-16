"""TEST FIXTURE ONLY, never used by the production Compose services."""
import asyncio
import json
async def app(scope,receive,send):
    if scope["type"] != "http":
        return
    path=scope["path"]
    if path.startswith("/chat/"):
        await send({"type":"http.response.start","status":200,"headers":[(b"content-type",b"text/event-stream"),(b"cache-control",b"no-store")]})
        await send({"type":"http.response.body","body":b"data: first\n\n","more_body":True})
        await asyncio.sleep(1.2)
        await send({"type":"http.response.body","body":b"data: second\n\n","more_body":False})
    else:
        body=(b"a{color:red}"*1000 if path=="/style.css" else json.dumps(dict((k.decode(),v.decode()) for k,v in scope["headers"])).encode())
        content=b"text/css" if path=="/style.css" else b"application/json"
        await send({"type":"http.response.start","status":200,"headers":[(b"content-type",content)]})
        await send({"type":"http.response.body","body":body})
