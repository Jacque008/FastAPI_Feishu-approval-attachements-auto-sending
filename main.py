import json
import hmac
import hashlib
from typing import Any, Dict, Set

from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from config import get_settings
from handlers import ApprovalHandler

APP = FastAPI()

# Event deduplication - in production, use Redis or database
_processed_events: Set[str] = set()
_MAX_PROCESSED_EVENTS = 10000

settings = get_settings()
approval_handler = ApprovalHandler(settings)


def verify_token(body: Dict[str, Any]) -> None:
    token = body.get("token")
    if token and token != settings.feishu_verification_token:
        raise HTTPException(status_code=403, detail="invalid token")


def verify_signature(request: Request, raw_body: bytes) -> None:
    """
    Verify Feishu event signature if signing secret is configured.
    """
    if not settings.feishu_signing_secret:
        return

    timestamp = request.headers.get("X-Lark-Request-Timestamp") or ""
    nonce = request.headers.get("X-Lark-Request-Nonce") or ""
    signature = request.headers.get("X-Lark-Signature") or ""

    if not (timestamp and nonce and signature):
        raise HTTPException(status_code=400, detail="missing signature headers")

    base = f"{timestamp}\n{nonce}\n{raw_body.decode('utf-8')}\n".encode("utf-8")
    digest = hmac.new(
        settings.feishu_signing_secret.encode("utf-8"),
        base,
        hashlib.sha256,
    ).hexdigest()

    if not signature.endswith(digest):
        raise HTTPException(status_code=403, detail="invalid signature")


def get_event_id(body: Dict[str, Any]) -> str:
    """Extract event ID for deduplication."""
    # v2.0 event format
    header = body.get("header", {})
    if header.get("event_id"):
        return header["event_id"]

    # v1.0 event format
    if body.get("uuid"):
        return body["uuid"]

    # Fallback to event data hash
    event = body.get("event", {})
    instance_code = (
        event.get("instance_code")
        or event.get("approval_code")
        or event.get("object", {}).get("instance_code")
        or ""
    )
    status = event.get("status") or event.get("instance_status") or ""
    return f"{instance_code}:{status}"


def is_duplicate_event(event_id: str) -> bool:
    """Check if event was already processed."""
    global _processed_events

    if event_id in _processed_events:
        return True

    # Simple cleanup when set gets too large
    if len(_processed_events) >= _MAX_PROCESSED_EVENTS:
        _processed_events = set(list(_processed_events)[-5000:])

    _processed_events.add(event_id)
    return False


async def process_approval_event(body: Dict[str, Any]) -> None:
    """Background task to process approval event."""
    try:
        await approval_handler.handle_event(body)
    except Exception as e:
        print(f"Error processing approval event: {e}")


@APP.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@APP.post("/feishu/webhook/approval")
async def feishu_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    try:
        body = json.loads(raw_body.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid json")

    # 1) Verify signature if enabled
    verify_signature(request, raw_body)

    # 2) Token verification
    verify_token(body)

    # 3) URL verification (challenge)
    if body.get("type") == "url_verification" and "challenge" in body:
        return JSONResponse({"challenge": body["challenge"]})

    # Debug: print full event body
    print(f"=== Received webhook ===")
    print(f"Body: {json.dumps(body, ensure_ascii=False, indent=2)}")

    # 4) Deduplication check
    event_id = get_event_id(body)
    if is_duplicate_event(event_id):
        print(f"Duplicate event {event_id}, skipping")
        return JSONResponse({"ok": True})

    # 5) Process event in background
    print(f"Processing event: {event_id}")
    background_tasks.add_task(process_approval_event, body)

    return JSONResponse({"ok": True})
