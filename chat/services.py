import json
import logging
import uuid
import time
import asyncio
import aiohttp
import requests # For synchronous HTTP requests
import base64
import re
import mimetypes
import os
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.shortcuts import get_object_or_404
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.core.files.base import ContentFile

from .models import AIModel, Conversation, Message
from .state_utils import get_stop_requested_sync, set_stop_requested_sync, touch_stop_request_sync, clear_stop_request_sync
from .utils import ensure_valid_api_url

logger = logging.getLogger(__name__)

AI_REQUEST_TIMEOUT = 300  # 使用整数，而不是对象

async def _send_event(callback, conversation_id, event_type, data):
    """统一的事件发送函数"""
    if callback:
        await callback(event_type, data)
    else:
        await send_generation_event(conversation_id, event_type, data)


async def generate_ai_response(conversation_id, model_id, message=None, user_message_id=None, is_regenerate=False, generation_id=None, temp_id=None, is_streaming=True, event_callback=None, file_data=None, file_name=None, file_type=None):
    """
    核心服务函数，用于生成AI回复。
    - 支持流式和非流式两种模式。
    - 支持文本和多模态（图片）输入。
    - 处理新消息和重新生成两种情况。
    - 通过 event_callback (如果提供) 或 channel_layer (默认) 推送事件。
    """
    conversation = None
    final_status = "unknown"
    
    try:
        uuid.UUID(generation_id)
        real_generation_id = generation_id
    except (ValueError, TypeError):
        real_generation_id = str(uuid.uuid4())

    try:
        # 在任务开始时，检查是否已存在停止信号。
        if get_stop_requested_sync(real_generation_id):
            logger.warning(f"Service: Stop request for GenID {real_generation_id} detected at task start. Aborting immediately.")
            final_status = "cancelled"
            await _send_event(event_callback, conversation_id, 'generation_end', {
                'generation_id': real_generation_id,
                'status': final_status
            })
            return

        # --- 新增：处理图片上传 ---
        if file_data and file_name and user_message_id:
            try:
                file_content = base64.b64decode(file_data)
                saved_path = await database_sync_to_async(default_storage.save)(f"uploads/{real_generation_id}_{file_name}", ContentFile(file_content))
                
                @database_sync_to_async
                def _update_db_message(msg_id, text_content, path):
                    try:
                        msg = Message.objects.get(id=msg_id)
                        if text_content.strip():
                            msg.content = f"{text_content}\n[file:{path}]"
                        else:
                            msg.content = f"[file:{path}]"
                        msg.save()
                        logger.info(f"已更新用户消息 {msg_id}，添加文件引用: {path}")
                    except Message.DoesNotExist:
                        logger.error(f"无法找到用户消息 {msg_id} 来更新")
                        raise
                
                # 当 message 为 None 时，使用空字符串
                await _update_db_message(user_message_id, message or "", saved_path)
            except Exception as e:
                logger.error(f"图片处理失败: {e}", exc_info=True)
                await _send_event(event_callback, conversation_id, 'generation_end', {
                    'generation_id': real_generation_id,
                    'status': 'failed',
                    'error': f'图片处理失败: {str(e)}'
                })
                return

        # 1. 获取会话和模型
        conversation = await get_conversation_async(conversation_id)
        model = await get_model_async(model_id)
        if not conversation or not model:
            logger.error(f"无法找到会话 {conversation_id} 或模型 {model_id}")
            return

        # 2. 准备并发送 generation_start 事件
        await set_db_generation_id(conversation_id, real_generation_id)
        logger.info(f"Service: Starting generation with ID {real_generation_id} for conversation {conversation_id}")

        await _send_event(event_callback, conversation_id, 'generation_start', {
            'generation_id': real_generation_id,
            'temp_id': temp_id
        })

        # 3. 准备历史消息
        messages_for_api = await prepare_history_messages(conversation, model, user_message_id, is_regenerate)

        # 4. 构建并发送AI请求
        request_data = {
            "model": model['model_name'],
            "messages": messages_for_api,
            "stream": is_streaming,  # 使用传入的标志
            **model['default_params']
        }
        api_url = ensure_valid_api_url(model['provider_base_url'], "/v1/chat/completions")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {model['provider_api_key']}"}

        full_content = ""
        
        # 增加块间超时设置
        INTER_CHUNK_TIMEOUT = 20  # 如果20秒内没有收到任何数据（包括空包），则超时
        
        timeout = aiohttp.ClientTimeout(total=AI_REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 在请求前再次检查，以防万一
            if get_stop_requested_sync(real_generation_id):
                logger.warning(f"Service: Stop request detected for GenID {real_generation_id} just before making API call. Aborting.")
                final_status = "cancelled"
            else:
                async with session.post(api_url, json=request_data, headers=headers) as response:
                    if response.status == 200:
                        if is_streaming:
                            buffer = b''
                            last_heartbeat_time = time.time()
                            HEARTBEAT_INTERVAL = 15  # 每15秒进行一次心跳

                            while True:
                                try:
                                    # 心跳逻辑：定期延长停止信号的TTL
                                    current_time = time.time()
                                    if current_time - last_heartbeat_time > HEARTBEAT_INTERVAL:
                                        touch_stop_request_sync(real_generation_id)
                                        last_heartbeat_time = current_time

                                    # 检查停止信号
                                    if get_stop_requested_sync(real_generation_id):
                                        logger.warning(f"Service: Stop request detected for GenID {real_generation_id}. Stopping stream.")
                                        final_status = "cancelled"
                                        break

                                    # 使用块间超时来防止无限期挂起
                                    async with asyncio.timeout(INTER_CHUNK_TIMEOUT):
                                        chunk = await response.content.read(4096)
                                    
                                    if not chunk:
                                        break

                                    buffer += chunk
                                    messages = buffer.split(b'\n\n')
                                    buffer = messages.pop()

                                    for msg in messages:
                                        if not msg:
                                            continue
                                        
                                        # 在解析前再次检查，减少延迟
                                        if get_stop_requested_sync(real_generation_id):
                                            final_status = "cancelled"
                                            break

                                        for line in msg.split(b'\n'):
                                            line_str = line.decode('utf-8').strip()
                                            if line_str.startswith('data: '):
                                                chunk_data = line_str[6:]
                                                if chunk_data == '[DONE]': # 以防万一，还是处理一下
                                                    continue
                                                try:
                                                    chunk_json = json.loads(chunk_data)
                                                    content_piece = extract_content_from_chunk(chunk_json)
                                                    if content_piece:
                                                        full_content += content_piece
                                                        await _send_event(event_callback, conversation_id, 'stream_update', {
                                                            'generation_id': real_generation_id,
                                                            'content': content_piece,
                                                            'temp_id': temp_id
                                                        })
                                                except json.JSONDecodeError:
                                                    logger.error(f"JSON decode error for chunk: {chunk_data}")
                                        
                                    if final_status == "cancelled":
                                        break

                                except asyncio.TimeoutError:
                                    logger.error(f"AI response chunk timeout after {INTER_CHUNK_TIMEOUT}s for conversation {conversation_id}")
                                    final_status = "failed"
                                    error_detail = f"响应超时：在 {INTER_CHUNK_TIMEOUT} 秒内未收到任何数据"
                                    break
                            
                            if final_status not in ["cancelled", "failed"]:
                                final_status = "completed" if full_content else "failed"

                        else:
                            # 5b. 处理非流式响应 (异步)
                            response_json = await response.json()
                            full_content = extract_content_from_chunk(response_json)
                            if full_content:
                                final_status = "completed"
                                await _send_event(event_callback, conversation_id, 'full_message', {
                                    'generation_id': real_generation_id,
                                    'content': full_content,
                                    'temp_id': temp_id
                                })
                            else:
                                logger.error("Non-streaming AI response completed but no content was extracted.")
                                final_status = "failed"
                    else:
                        error_text = await response.text()
                        logger.error(f"AI API request failed with status {response.status}: {error_text}")
                        final_status = "failed"
                        error_detail = error_text

        # 6. 如果成功，保存AI消息
        if final_status == "completed":
            # 在保存前进行最后一次检查
            if get_stop_requested_sync(real_generation_id):
                logger.warning(f"Service: Stop request detected for GenID {real_generation_id} just before saving. Discarding response.")
                final_status = "cancelled"
            else:
                if is_regenerate:
                    await delete_subsequent_ai_messages(conversation_id, user_message_id)
                
                ai_message = await save_ai_message(conversation_id, full_content, model['id'])
                await _send_event(event_callback, conversation_id, 'id_update', {
                    'generation_id': real_generation_id,
                    'temp_id': temp_id,
                    'message_id': ai_message['id']
                })

    except asyncio.CancelledError:
        logger.warning(f"Service: Generation task for GenID {real_generation_id} was cancelled externally.")
        final_status = "stopped"

    except aiohttp.ClientError as e:
        logger.error(f"Network error in generate_ai_response for conversation {conversation_id}: {e}", exc_info=True)
        final_status = "failed"
        error_detail = f"网络错误: {e}"

    except Exception as e:
        logger.error(f"Error in generate_ai_response for conversation {conversation_id}: {e}", exc_info=True)
        final_status = "failed"
        error_detail = f"内部服务器错误: {e}"

    finally:
        # --- 新的、更健壮的最终状态检查 ---
        # 在发送最终事件和清理之前，做最后一次检查。
        # 这可以捕获在任务主体执行完毕后、但在 finally 块开始前收到的停止信号。
        if final_status == "completed" and get_stop_requested_sync(real_generation_id):
            logger.warning(f"Service: Stop request for GenID {real_generation_id} detected in finally block. Overriding status to 'cancelled'.")
            final_status = "cancelled"

        # 7. 清理并发送结束信号
        if conversation and real_generation_id:
            await clear_db_generation_id(conversation_id, real_generation_id)
            
            # 任务结束时，无论结果如何，都主动、确定地清理停止信号
            clear_stop_request_sync(real_generation_id)

            event_data = {
                'generation_id': real_generation_id,
                'status': final_status
            }
            if final_status == "failed" and 'error_detail' in locals():
                event_data['error'] = error_detail

            await _send_event(event_callback, conversation_id, 'generation_end', event_data)
        logger.info(f"Service: Generation {real_generation_id} for conversation {conversation_id} finished with status: {final_status}")


# --- 辅助数据库异步函数 ---
from channels.db import database_sync_to_async

@database_sync_to_async
def get_conversation_async(conversation_id):
    try:
        conv = Conversation.objects.get(id=conversation_id)
        return {
            'id': conv.id,
            'system_prompt': conv.system_prompt
        }
    except Conversation.DoesNotExist:
        return None

@database_sync_to_async
def get_model_async(model_id):
    try:
        model = AIModel.objects.select_related('provider').get(id=model_id)
        return {
            'id': model.id,
            'model_name': model.model_name,
            'max_history_messages': model.max_history_messages,
            'default_params': model.default_params,
            'provider_base_url': model.provider.base_url,
            'provider_api_key': model.provider.api_key
        }
    except AIModel.DoesNotExist:
        return None

@database_sync_to_async
def prepare_history_messages(conversation, model, user_message_id, is_regenerate):
    """准备用于API请求的消息历史记录，支持多模态内容。"""
    if is_regenerate:
        user_message = Message.objects.get(id=user_message_id)
        history_qs = Message.objects.filter(
            conversation_id=conversation['id'],
            timestamp__lte=user_message.timestamp
        ).order_by('timestamp')
    else:
        history_qs = Message.objects.filter(conversation_id=conversation['id']).order_by('timestamp')

    # 预先获取所有消息到列表中，避免在异步上下文中对QuerySet进行多次操作
    history_messages = list(history_qs)

    if len(history_messages) > model['max_history_messages']:
        history_messages = history_messages[-model['max_history_messages']:]

    messages = []
    if conversation.get('system_prompt'):
        messages.append({"role": "system", "content": conversation['system_prompt']})
    
    from .image_config import IMAGE_CONTEXT_STRATEGY, MAX_IMAGES_IN_CONTEXT
    
    # --- 健壮的 "一次成型" 方案 ---
    
    # 1. 识别所有候选图片消息
    all_image_message_ids = [
        msg.id for msg in history_messages
        if msg.is_user and re.search(r'\[file:(.*?)\]', msg.content)
    ]
    
    # 2. 根据策略确定哪些图片需要被完整包含
    latest_image_ids_to_include = set()
    if IMAGE_CONTEXT_STRATEGY == "all":
        latest_image_ids_to_include = set(all_image_message_ids)
    elif IMAGE_CONTEXT_STRATEGY == "latest_only":
        latest_image_ids_to_include = set(all_image_message_ids[-MAX_IMAGES_IN_CONTEXT:])
    
    # 3. 一次性构建最终消息列表
    for msg in history_messages:
        role = "user" if msg.is_user else "assistant"
        
        if msg.id in latest_image_ids_to_include:
            # --- 处理需要包含完整图片数据的消息 ---
            file_match = re.search(r'\[file:(.*?)\]', msg.content)
            file_path = file_match.group(1)
            text_content = msg.content.replace(file_match.group(0), '').strip()
            
            try:
                if default_storage.exists(file_path):
                    with default_storage.open(file_path, 'rb') as f:
                        file_data = f.read()
                    
                    base64_content = base64.b64encode(file_data).decode('utf-8')
                    mime_type, _ = mimetypes.guess_type(file_path)
                    if not mime_type:
                        mime_type = 'application/octet-stream'
                    
                    multi_modal_content = [
                        {"type": "text", "text": text_content},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_content}"}}
                    ]
                    messages.append({"role": role, "content": multi_modal_content})
                    logger.info(f"已将图片消息 {msg.id} 的完整内容添加到上下文中。")
                else:
                    logger.warning(f"文件 '{file_path}' 在消息 {msg.id} 中被引用但未找到。")
                    final_content = f"{text_content}\n[图片上传失败: 文件不存在]"
                    messages.append({"role": role, "content": final_content})
            except Exception as e:
                logger.error(f"处理消息 {msg.id} 中的文件 '{file_path}' 时出错: {e}", exc_info=True)
                final_content = f"{text_content}\n[图片处理失败]"
                messages.append({"role": role, "content": final_content})
        
        elif msg.is_user and re.search(r'\[file:(.*?)\]', msg.content):
            # --- 处理较旧的、不需要包含完整图片数据的图片消息 ---
            file_match = re.search(r'\[file:(.*?)\]', msg.content)
            file_path = file_match.group(1)
            text_content = msg.content.replace(file_match.group(0), '').strip()
            
            image_description = f"[用户上传了图片: {os.path.basename(file_path)}]"
            final_content = f"{text_content}\n{image_description}" if text_content else image_description
            messages.append({"role": role, "content": final_content})
            logger.info(f"已将旧图片消息 {msg.id} 的文本描述添加到上下文中。")
            
        else:
            # --- 处理普通文本消息 ---
            messages.append({"role": role, "content": msg.content})
            
    logger.info(f"准备了 {len(messages)} 条消息用于API请求，图片策略: {IMAGE_CONTEXT_STRATEGY}")
    return messages

@database_sync_to_async
def save_ai_message(conversation_id, content, model_id, generation_id=None):
    conversation = Conversation.objects.get(id=conversation_id)
    model = AIModel.objects.get(id=model_id)
    ai_message = Message.objects.create(
        conversation=conversation,
        content=content,
        is_user=False,
        model_used=model,
        generation_id=generation_id
    )
    conversation.save()
    return {'id': ai_message.id}

@database_sync_to_async
def delete_subsequent_ai_messages(conversation_id, user_message_id):
    """删除指定用户消息之后的所有AI消息"""
    try:
        user_message = Message.objects.get(id=user_message_id)
        Message.objects.filter(
            conversation_id=conversation_id,
            is_user=False,
            timestamp__gt=user_message.timestamp
        ).delete()
    except Message.DoesNotExist:
        logger.error(f"Cannot find user message {user_message_id} to delete subsequent messages.")

@database_sync_to_async
def set_db_generation_id(conversation_id, generation_id):
    Conversation.objects.filter(id=conversation_id).update(current_generation_id=generation_id)

@database_sync_to_async
def clear_db_generation_id(conversation_id, generation_id_to_clear):
    conv = Conversation.objects.filter(id=conversation_id).first()
    if conv and str(conv.current_generation_id) == str(generation_id_to_clear):
        conv.current_generation_id = None
        conv.save(update_fields=['current_generation_id'])

# --- 辅助 Channel Layer 函数 ---
async def send_generation_event(conversation_id, event_type, data):
    """向客户端发送生成事件"""
    channel_layer = get_channel_layer()
    group_name = f'chat_{conversation_id}'
    
    message_to_send = {
        'type': 'broadcast_event',
        'event': {
            'type': event_type,
            'data': data
        }
    }
    
    await channel_layer.group_send(group_name, message_to_send)

# --- 辅助内容提取函数 ---
def extract_content_from_chunk(chunk_json):
    """从流式或非流式数据块中提取内容"""
    if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
        choice = chunk_json['choices'][0]
        if 'delta' in choice: # 流式
            return choice['delta'].get('content')
        if 'message' in choice: # 非流式
            return choice['message'].get('content')
    return None


# =====================================================================================
# == 同步HTTP服务函数 (Synchronous HTTP Service Functions)
# =====================================================================================

def _get_model_sync(model_id):
    """同步获取模型信息"""
    try:
        model = AIModel.objects.select_related('provider').get(id=model_id)
        return {
            'id': model.id,
            'model_name': model.model_name,
            'max_history_messages': model.max_history_messages,
            'default_params': model.default_params,
            'provider_base_url': model.provider.base_url,
            'provider_api_key': model.provider.api_key
        }
    except AIModel.DoesNotExist:
        return None

def _prepare_history_messages_sync(conversation_id, system_prompt, model, user_message_id, is_regenerate, generation_id=None):
    """同步准备API请求的消息历史，支持多模态"""
    if is_regenerate:
        # 优先使用 generation_id 查找用户消息，提供更可靠的重新生成机制
        user_message = Message.objects.filter(generation_id=generation_id, is_user=True).first()
        if not user_message:
            # 如果找不到，回退到使用 message_id，以兼容旧数据或不同流程
            user_message = get_object_or_404(Message, id=user_message_id)

        history_qs = Message.objects.filter(
            conversation_id=conversation_id,
            timestamp__lte=user_message.timestamp
        ).order_by('timestamp')
    else:
        history_qs = Message.objects.filter(conversation_id=conversation_id).order_by('timestamp')

    history_messages = list(history_qs.all())

    if len(history_messages) > model['max_history_messages']:
        history_messages = history_messages[-model['max_history_messages']:]

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    from .image_config import IMAGE_CONTEXT_STRATEGY, MAX_IMAGES_IN_CONTEXT
    
    all_image_message_ids = [msg.id for msg in history_messages if msg.is_user and re.search(r'\[file:(.*?)\]', msg.content)]
    
    latest_image_ids_to_include = set()
    if IMAGE_CONTEXT_STRATEGY == "all":
        latest_image_ids_to_include = set(all_image_message_ids)
    elif IMAGE_CONTEXT_STRATEGY == "latest_only":
        latest_image_ids_to_include = set(all_image_message_ids[-MAX_IMAGES_IN_CONTEXT:])

    for msg in history_messages:
        role = "user" if msg.is_user else "assistant"
        
        file_match = re.search(r'\[file:(.*?)\]', msg.content)

        if msg.id in latest_image_ids_to_include and file_match:
            file_path = file_match.group(1)
            text_content = msg.content.replace(file_match.group(0), '').strip()
            
            try:
                if default_storage.exists(file_path):
                    with default_storage.open(file_path, 'rb') as f:
                        file_data = f.read()
                    
                    base64_content = base64.b64encode(file_data).decode('utf-8')
                    mime_type, _ = mimetypes.guess_type(file_path)
                    if not mime_type: mime_type = 'application/octet-stream'
                    
                    multi_modal_content = [
                        {"type": "text", "text": text_content},
                        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_content}"}}
                    ]
                    messages.append({"role": role, "content": multi_modal_content})
                else:
                    final_content = f"{text_content}\n[图片上传失败: 文件不存在]"
                    messages.append({"role": role, "content": final_content})
            except Exception as e:
                logger.error(f"处理消息 {msg.id} 中的文件 '{file_path}' 时出错: {e}", exc_info=True)
                final_content = f"{text_content}\n[图片处理失败]"
                messages.append({"role": role, "content": final_content})
        
        elif msg.is_user and file_match:
            file_path = file_match.group(1)
            text_content = msg.content.replace(file_match.group(0), '').strip()
            image_description = f"[用户上传了图片: {os.path.basename(file_path)}]"
            final_content = f"{text_content}\n{image_description}" if text_content else image_description
            messages.append({"role": role, "content": final_content})
        else:
            messages.append({"role": role, "content": msg.content})
            
    return messages


def generate_ai_response_for_http(conversation_id, model_id, message_content, user_message_id, is_regenerate, generation_id, is_streaming, file_data=None, file_name=None):
    """
    为同步的HTTP视图生成AI回复。
    - 使用 `requests` 库进行同步API调用。
    - 支持文本和图片上传。
    - 支持流式和非流式响应。
    """
    logger.info(f"HTTP Service: Starting generation with ID {generation_id} for conversation {conversation_id}")
    final_status = "unknown"
    error_detail = "An unknown error occurred."
    full_content = ""
    ai_message_id = None

    try:
        conversation = get_object_or_404(Conversation, id=conversation_id)
        model = _get_model_sync(model_id)
        if not model:
            raise ValueError("AI model not found.")

        # --- 处理文件上传 ---
        if file_data and file_name and user_message_id:
            try:
                saved_path = default_storage.save(f"uploads/{generation_id}_{file_name}", ContentFile(file_data))
                msg_to_update = Message.objects.get(id=user_message_id)
                if message_content.strip():
                    msg_to_update.content = f"{message_content}\n[file:{saved_path}]"
                else:
                    msg_to_update.content = f"[file:{saved_path}]"
                msg_to_update.save()
                logger.info(f"HTTP Service: Updated user message {user_message_id} with file reference: {saved_path}")
            except Exception as e:
                raise ValueError(f"File upload processing failed: {e}")

        # 1. 准备历史消息
        messages_for_api = _prepare_history_messages_sync(
            conversation.id, conversation.system_prompt, model, user_message_id, is_regenerate, generation_id=generation_id
        )
        logger.info(f"HTTP Service: Prepared {len(messages_for_api)} messages for API request. GenID: {generation_id}")

        # 2. 构建并发送AI请求
        request_data = {
            "model": model['model_name'],
            "messages": messages_for_api,
            "stream": is_streaming,
            **model['default_params']
        }
        api_url = ensure_valid_api_url(model['provider_base_url'], "/v1/chat/completions")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {model['provider_api_key']}"}

        response = requests.post(
            api_url,
            json=request_data,
            headers=headers,
            stream=is_streaming,
            timeout=AI_REQUEST_TIMEOUT
        )
        response.raise_for_status()

        if is_streaming:
            def stream_generator():
                nonlocal full_content, final_status, error_detail
                try:
                    last_heartbeat_time = time.time()
                    HEARTBEAT_INTERVAL = 15  # 每15秒进行一次心跳

                    for line in response.iter_lines():
                        # 心跳逻辑：定期延长停止信号的TTL
                        current_time = time.time()
                        if current_time - last_heartbeat_time > HEARTBEAT_INTERVAL:
                            touch_stop_request_sync(generation_id)
                            last_heartbeat_time = current_time

                        # 检查停止信号
                        if get_stop_requested_sync(generation_id):
                            logger.warning(f"HTTP Service: Stop request detected for GenID {generation_id}. Stopping stream.")
                            final_status = "cancelled"
                            break

                        if line:
                            line_str = line.decode('utf-8').strip()
                            if line_str.startswith('data: '):
                                chunk_data = line_str[6:]
                                if chunk_data == '[DONE]':
                                    continue
                                try:
                                    chunk_json = json.loads(chunk_data)
                                    content_piece = extract_content_from_chunk(chunk_json)
                                    if content_piece:
                                        full_content += content_piece
                                        yield {'type': 'stream_update', 'data': {'content': content_piece, 'generation_id': generation_id}}
                                except json.JSONDecodeError:
                                    logger.warning(f"Could not decode stream chunk: {chunk_data}")
                    
                    if final_status not in ["cancelled", "failed"]:
                        final_status = "completed"

                except Exception as e:
                    logger.error(f"Error during streaming response for GenID {generation_id}: {e}", exc_info=True)
                    final_status = "failed"
                    error_detail = f"流式响应处理失败: {e}"

                # The generator returns its final state. The view will handle it.
                return {
                    'status': final_status,
                    'content': full_content,
                    'error': error_detail if final_status == 'failed' else None
                }

            return stream_generator()
        else:
            response_json = response.json()
            full_content = extract_content_from_chunk(response_json)
            if full_content:
                final_status = "completed"
            else:
                final_status = "failed"
                error_detail = "Non-streaming response contained no content."
                logger.error(f"Non-streaming AI response for GenID {generation_id} completed but no content was extracted.")

    except requests.exceptions.RequestException as e:
        logger.error(f"HTTP request to AI API failed for GenID {generation_id}: {e}", exc_info=True)
        final_status = "failed"
        error_detail = f"AI服务请求失败: {e}"
    except Exception as e:
        logger.error(f"Error in generate_ai_response_for_http for GenID {generation_id}: {e}", exc_info=True)
        final_status = "failed"
        error_detail = f"服务器内部错误: {e}"
    finally:
        # For streaming responses, the status is finalized in the view after the generator is consumed.
        if not is_streaming:
            logger.info(f"HTTP Service: Generation {generation_id} for conversation {conversation_id} finished with status: {final_status}")

    # For streaming, the function has already returned the generator.
    # The following logic is ONLY for the non-streaming case.
    if not is_streaming:
        if final_status == "completed":
            if is_regenerate:
                user_message = get_object_or_404(Message, id=user_message_id)
                Message.objects.filter(
                    conversation_id=conversation_id,
                    is_user=False,
                    timestamp__gt=user_message.timestamp
                ).delete()
                logger.info(f"HTTP Service: Deleted subsequent AI messages for regeneration. GenID: {generation_id}")

            ai_message = Message.objects.create(
                conversation=conversation,
                content=full_content,
                is_user=False,
                model_used_id=model['id'],
                generation_id=generation_id
            )
            conversation.save()
            ai_message_id = ai_message.id
            logger.info(f"HTTP Service: Saved AI message {ai_message_id} to conversation {conversation_id}. GenID: {generation_id}")

        return {
            'status': final_status,
            'content': full_content,
            'message_id': ai_message_id,
            'error': error_detail if final_status == 'failed' else None
        }
