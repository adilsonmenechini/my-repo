import json
import uuid
import time
import re
import logging
from typing import Dict, Any, List, Union, AsyncGenerator

logger = logging.getLogger("anthropic_proxy.converter")

TARGET_MODELS = [
    "claude-sonnet-5",
    "claude-haiku-5-5",
    "claude-opus-5"
]


def format_system_prompt(system_val: Union[str, List[Any], None]) -> str:
    if not system_val:
        return ""
    if isinstance(system_val, str):
        return system_val
    if isinstance(system_val, list):
        parts = []
        for block in system_val:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n\n".join(parts)
    return str(system_val)

def anthropic_to_openai_messages(anthropic_messages: List[Dict[str, Any]], system_prompt: str = "") -> List[Dict[str, Any]]:
    openai_messages = []
    
    if system_prompt:
        openai_messages.append({
            "role": "system",
            "content": system_prompt
        })
        
    for msg in anthropic_messages:
        role = msg.get("role", "user")
        content = msg.get("content")
        if content is None:
            content = ""
            
        if isinstance(content, str):
            openai_messages.append({"role": role, "content": content})
            continue
            
        if isinstance(content, list):
            text_parts = []
            tool_calls = []
            tool_results = []
            image_parts = []
            
            for item in content:
                if isinstance(item, str):
                    text_parts.append(item)
                    continue
                if not isinstance(item, dict):
                    continue
                    
                item_type = item.get("type")
                if item_type == "text":
                    text_parts.append(item.get("text", ""))
                elif item_type == "image":
                    source = item.get("source", {})
                    if source.get("type") == "base64":
                        media_type = source.get("media_type", "image/png")
                        data = source.get("data", "")
                        image_parts.append({
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{data}"
                            }
                        })
                elif item_type == "tool_use":
                    tool_input = item.get("input", {})
                    args_str = json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input)
                    tool_calls.append({
                        "id": item.get("id", f"toolu_{uuid.uuid4().hex[:16]}"),
                        "type": "function",
                        "function": {
                            "name": item.get("name"),
                            "arguments": args_str
                        }
                    })
                elif item_type == "tool_result":
                    tool_content = item.get("content", "")
                    if isinstance(tool_content, list):
                        tool_str = "".join([b.get("text", "") for b in tool_content if isinstance(b, dict) and b.get("type") == "text"])
                    else:
                        tool_str = str(tool_content)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": item.get("tool_use_id"),
                        "content": tool_str
                    })
            
            if tool_results:
                for tr in tool_results:
                    openai_messages.append(tr)
            else:
                formatted_content = []
                if text_parts:
                    combined_text = "\n".join(text_parts)
                    if image_parts:
                        formatted_content.append({"type": "text", "text": combined_text})
                    else:
                        formatted_content = combined_text
                if image_parts:
                    if isinstance(formatted_content, str):
                        formatted_content = [{"type": "text", "text": formatted_content}]
                    formatted_content.extend(image_parts)
                
                msg_obj = {"role": role, "content": formatted_content if formatted_content is not None else ""}
                if tool_calls and role == "assistant":
                    msg_obj["tool_calls"] = tool_calls
                openai_messages.append(msg_obj)
                
    return openai_messages

def anthropic_to_openai_request(anthropic_req: Dict[str, Any]) -> Dict[str, Any]:
    model = anthropic_req.get("model") or "claude-sonnet-5"

    system_prompt = format_system_prompt(anthropic_req.get("system"))
    raw_messages = anthropic_req.get("messages", [])

    logger.info(f"Converting Anthropic request (model pass-through): '{model}'")
    openai_messages = anthropic_to_openai_messages(raw_messages, system_prompt)
    
    payload = {
        "model": model,
        "messages": openai_messages,
        "stream": anthropic_req.get("stream", False)
    }
    
    if "max_tokens" in anthropic_req:
        payload["max_tokens"] = anthropic_req["max_tokens"]
    if "temperature" in anthropic_req:
        payload["temperature"] = anthropic_req["temperature"]
    if "top_p" in anthropic_req:
        payload["top_p"] = anthropic_req["top_p"]
        
    if "tools" in anthropic_req:
        tools = []
        for t in anthropic_req["tools"]:
            tools.append({
                "type": "function",
                "function": {
                    "name": t.get("name"),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {})
                }
            })
        payload["tools"] = tools
        logger.info(f"Converted {len(tools)} tool definitions.")
        
    return payload

def openai_to_anthropic_response(openai_resp: Dict[str, Any], requested_model: str) -> Dict[str, Any]:
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    choices = openai_resp.get("choices", [])
    usage = openai_resp.get("usage", {})
    
    content_blocks = []
    stop_reason = "end_turn"
    
    if choices:
        choice = choices[0]
        msg = choice.get("message", {}) or {}
        finish_reason = choice.get("finish_reason")
        
        if finish_reason in ["length"]:
            stop_reason = "max_tokens"
        elif finish_reason in ["tool_calls", "function_call"]:
            stop_reason = "tool_use"
            
        text_content = msg.get("content")
        if text_content is not None and str(text_content).strip():
            content_blocks.append({
                "type": "text",
                "text": str(text_content)
            })
            
        tool_calls = msg.get("tool_calls") or []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function", {}) or {}
            args_raw = fn.get("arguments", "{}")
            if isinstance(args_raw, str):
                try:
                    args = json.loads(args_raw)
                except Exception:
                    args = {}
            elif isinstance(args_raw, dict):
                args = args_raw
            else:
                args = {}
                
            tool_id = tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}"
            tool_name = fn.get("name")
            if tool_name:
                stop_reason = "tool_use"
                content_blocks.append({
                    "type": "tool_use",
                    "id": tool_id,
                    "name": tool_name,
                    "input": args
                })
            
    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})
        
    logger.info(f"Generated Anthropic response object with {len(content_blocks)} content block(s), stop_reason: {stop_reason}")
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0) if usage else 0,
            "output_tokens": usage.get("completion_tokens", 0) if usage else 0
        }
    }

async def openai_to_anthropic_sse_stream(openai_line_generator: AsyncGenerator[str, None], requested_model: str) -> AsyncGenerator[str, None]:
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    logger.info(f"Starting SSE stream translation for message {msg_id}")
    
    # 1. message_start
    message_start = {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": requested_model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0}
        }
    }
    yield f"event: message_start\ndata: {json.dumps(message_start)}\n\n"
    
    block_index = 0
    text_block_started = False
    active_tool_calls: Dict[int, Dict[str, Any]] = {}
    output_tokens = 0
    stop_reason = "end_turn"
    
    async for line in openai_line_generator:
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data: "):
            data_str = line[6:]
            if data_str == "[DONE]":
                break
            try:
                data = json.loads(data_str)
                choices = data.get("choices", [])
                if not choices:
                    continue
                    
                choice = choices[0]
                delta = choice.get("delta", {}) or {}
                finish_reason = choice.get("finish_reason")
                
                if finish_reason:
                    if finish_reason == "length":
                        stop_reason = "max_tokens"
                    elif finish_reason in ["tool_calls", "function_call"]:
                        stop_reason = "tool_use"
                
                # Handle text delta
                text_chunk = delta.get("content")
                if text_chunk:
                    output_tokens += 1
                    if not text_block_started:
                        text_block_started = True
                        block_start = {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {"type": "text", "text": ""}
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(block_start)}\n\n"
                        
                    delta_evt = {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {
                            "type": "text_delta",
                            "text": text_chunk
                        }
                    }
                    yield f"event: content_block_delta\ndata: {json.dumps(delta_evt)}\n\n"
                
                # Handle tool_calls delta
                tool_calls_delta = delta.get("tool_calls") or []
                for tc in tool_calls_delta:
                    tc_idx = tc.get("index", 0)
                    fn = tc.get("function", {}) or {}
                    
                    if tc_idx not in active_tool_calls:
                        # Close text block if active
                        if text_block_started:
                            block_stop = {"type": "content_block_stop", "index": block_index}
                            yield f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n"
                            text_block_started = False
                            block_index += 1
                            
                        tool_id = tc.get("id") or f"toolu_{uuid.uuid4().hex[:16]}"
                        fn_name = fn.get("name", "")
                        
                        active_tool_calls[tc_idx] = {
                            "anthropic_index": block_index,
                            "id": tool_id,
                            "name": fn_name,
                            "started": True
                        }
                        
                        tool_start_evt = {
                            "type": "content_block_start",
                            "index": block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": fn_name,
                                "input": {}
                            }
                        }
                        yield f"event: content_block_start\ndata: {json.dumps(tool_start_evt)}\n\n"
                        stop_reason = "tool_use"
                        block_index += 1
                        
                    # Handle arguments delta chunk
                    args_chunk = fn.get("arguments")
                    if args_chunk:
                        output_tokens += 1
                        a_idx = active_tool_calls[tc_idx]["anthropic_index"]
                        json_delta_evt = {
                            "type": "content_block_delta",
                            "index": a_idx,
                            "delta": {
                                "type": "input_json_delta",
                                "partial_json": args_chunk
                            }
                        }
                        yield f"event: content_block_delta\ndata: {json.dumps(json_delta_evt)}\n\n"

            except Exception as parse_err:
                logger.warning(f"Error translating stream chunk: {parse_err}")
                continue

    # Close active text block if open
    if text_block_started:
        block_stop = {"type": "content_block_stop", "index": block_index}
        yield f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n"
        
    # Close active tool_use blocks
    for tc_idx, tc_info in active_tool_calls.items():
        block_stop = {"type": "content_block_stop", "index": tc_info["anthropic_index"]}
        yield f"event: content_block_stop\ndata: {json.dumps(block_stop)}\n\n"

    # Message delta
    msg_delta = {
        "type": "message_delta",
        "delta": {
            "stop_reason": stop_reason,
            "stop_sequence": None
        },
        "usage": {"output_tokens": output_tokens}
    }
    yield f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n"
    
    # Message stop
    msg_stop = {"type": "message_stop"}
    yield f"event: message_stop\ndata: {json.dumps(msg_stop)}\n\n"
    logger.info(f"Stream completed for message {msg_id} with stop_reason: {stop_reason}")
