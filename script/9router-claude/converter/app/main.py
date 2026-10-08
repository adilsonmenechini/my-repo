import os
import sys
import httpx
import json
import logging
import traceback
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from app.converter import (
    anthropic_to_openai_request,
    openai_to_anthropic_response,
    openai_to_anthropic_sse_stream,
    TARGET_MODELS
)

# Configure logging to stdout with clear formatting
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("anthropic_proxy")

app = FastAPI(title="Anthropic Compatible API Proxy", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPSTREAM_BASE_URL = os.getenv("UPSTREAM_BASE_URL", "http://127.0.0.1:20338/v1").rstrip("/")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "claude-sonnet-5")

@app.get("/health")
async def health_check():
    logger.info(f"Health check endpoint called. Upstream: {UPSTREAM_BASE_URL}")
    return {"status": "ok", "upstream": UPSTREAM_BASE_URL}

CLAUDE_ALIASES = [
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
    "claude-opus-5"
]


@app.get("/v1/models")
@app.get("/models")
async def list_models():
    """Proxy /v1/models returning Anthropic standard IDs first to satisfy client validation."""
    logger.info(f"Fetching models from upstream {UPSTREAM_BASE_URL}/models")
    raw_models = []
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{UPSTREAM_BASE_URL}/models")
            if resp.status_code == 200:
                data = resp.json()
                raw_models = data.get("data", []) or data.get("models", [])
                logger.info(f"Upstream returned {len(raw_models)} models.")
    except Exception as e:
        logger.error(f"Error fetching upstream models: {e}")
        
    if not raw_models:
        raw_models = [{"id": m} for m in TARGET_MODELS]

    all_model_ids = set()
    formatted_models = []
    
    # 1. Standard Anthropic Claude IDs first
    for alias in CLAUDE_ALIASES:
        if alias not in all_model_ids:
            all_model_ids.add(alias)
            formatted_models.append({
                "id": alias,
                "name": alias,
                "model": alias,
                "model_name": alias,
                "display_name": alias,
                "object": "model",
                "type": "model",
                "owned_by": "anthropic",
                "provider": "anthropic",
                "created": 1700000000,
                "capabilities": {
                    "completion": True,
                    "chat": True,
                    "stream": True,
                    "vision": True,
                    "tools": True
                }
            })

    # 2. Actual upstream models
    for item in raw_models:
        if isinstance(item, str):
            m_id = item
            item = {}
        else:
            m_id = item.get("id") or item.get("name") or item.get("model")
            
        if not m_id or m_id in all_model_ids:
            continue
        all_model_ids.add(m_id)
        
        formatted_models.append({
            "id": m_id,
            "name": m_id,
            "model": m_id,
            "model_name": m_id,
            "display_name": m_id,
            "object": "model",
            "type": "model",
            "owned_by": "anthropic",
            "provider": "anthropic",
            "created": item.get("created", 1700000000),
            "capabilities": item.get("capabilities", {
                "completion": True,
                "chat": True,
                "stream": True,
                "vision": True,
                "tools": True
            })
        })
        
    return {
        "object": "list",
        "data": formatted_models,
        "models": formatted_models
    }

@app.post("/v1/messages")
async def create_message(request: Request):
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse incoming JSON body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    requested_model = body.get("model", DEFAULT_MODEL)
    is_stream = body.get("stream", False)
    
    logger.info(f"========== INCOMING ANTHROPIC REQUEST ==========")
    logger.info(f"Model: {requested_model} | Stream: {is_stream}")
    logger.info(f"Payload Summary: messages={len(body.get('messages', []))}, system={bool(body.get('system'))}, max_tokens={body.get('max_tokens')}")
    logger.debug(f"Full Anthropic Request: {json.dumps(body)}")
    
    try:
        openai_payload = anthropic_to_openai_request(body)
        logger.info(f"Converted OpenAI payload model: {openai_payload.get('model')}, messages_count: {len(openai_payload.get('messages', []))}")
    except Exception as e:
        logger.error(f"Failed during request conversion: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=422, detail=f"Request conversion error: {str(e)}")

    headers = {
        "Content-Type": "application/json"
    }
    
    auth_header = request.headers.get("Authorization")
    if auth_header:
        headers["Authorization"] = auth_header
        
    api_key_header = request.headers.get("x-api-key")
    if api_key_header and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {api_key_header}"

    client = httpx.AsyncClient(timeout=120.0)
    upstream_url = f"{UPSTREAM_BASE_URL}/chat/completions"
    
    logger.info(f"Forwarding request to upstream: POST {upstream_url}")

    if is_stream:
        try:
            req = client.build_request("POST", upstream_url, json=openai_payload, headers=headers)
            r = await client.send(req, stream=True)
            
            logger.info(f"Upstream stream response status: {r.status_code}")
            
            if r.status_code != 200:
                error_content = await r.aread()
                await client.aclose()
                logger.error(f"Upstream error {r.status_code}: {error_content.decode(errors='ignore')}")
                return JSONResponse(
                    status_code=r.status_code,
                    content={"type": "error", "error": {"type": "api_error", "message": error_content.decode(errors="ignore")}}
                )
                
            async def line_generator():
                chunk_count = 0
                try:
                    async for line in r.aiter_lines():
                        chunk_count += 1
                        if chunk_count <= 5 or chunk_count % 20 == 0:
                            logger.debug(f"Stream chunk #{chunk_count}: {line[:100]}")
                        yield line
                except Exception as stream_err:
                    logger.error(f"Error during streaming from upstream: {stream_err}")
                finally:
                    logger.info(f"Stream completed. Total chunks: {chunk_count}")
                    await r.aclose()
                    await client.aclose()
                    
            return StreamingResponse(
                openai_to_anthropic_sse_stream(line_generator(), requested_model),
                media_type="text/event-stream"
            )
        except Exception as e:
            await client.aclose()
            logger.error(f"Exception initiating stream request: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Proxy stream error: {str(e)}")
    else:
        try:
            resp = await client.post(upstream_url, json=openai_payload, headers=headers)
            await client.aclose()
            
            logger.info(f"Upstream response status: {resp.status_code}")
            
            if resp.status_code != 200:
                logger.error(f"Upstream returned status {resp.status_code}: {resp.text}")
                return JSONResponse(
                    status_code=resp.status_code,
                    content={"type": "error", "error": {"type": "api_error", "message": resp.text}}
                )
                
            openai_json = resp.json()
            logger.info(f"Received OpenAI response from upstream successfully.")
            
            anthropic_resp = openai_to_anthropic_response(openai_json, requested_model)
            logger.info(f"Converted to Anthropic message ID: {anthropic_resp.get('id')}")
            return JSONResponse(content=anthropic_resp)
            
        except Exception as e:
            await client.aclose()
            logger.error(f"Exception during non-stream request: {e}\n{traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Proxy request error: {str(e)}")
