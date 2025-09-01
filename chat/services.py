import json
import logging
import uuid
import time
import asyncio
import aiohttp
import base64
import re
import mimetypes
import os
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.shortcuts import get_object_or_404
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

from .models import AIModel, Conversation, Message
from .state_utils import get_stop_requested_sync, set_stop_requested_sync, touch_stop_request_sync, clear_stop_request_sync
from .utils import ensure_valid_api_url

logger = logging.getLogger(__name__)

AI_REQUEST_TIMEOUT = 300

async def _send_event(callback, conversation_id, event_type, data):
    """统一的事件发送函数"""
    if callback:
        await callback(event_type, data)
    else:
        await send_generation_event(conversation_id, event_type, data)


async def _handle_ai_generation_logic(conversation_id, model_id, message=None, user_message_id=None, is_regenerate=False, generation_id=None, temp_id=None, is_streaming=True, event_callback=None, file_data=None, file_name=None, file_type=None):
    """
    核心的、私有的AI响应生成逻辑。
    此函数包含与AI模型和数据库的实际交互。
    """
    conversation = None
    final_status = "unknown"
    error_detail = "发生未知错误。"
    
    try:
        uuid.UUID(generation_id)
        real_generation_id = generation_id
    except (ValueError, TypeError):
        real_generation_id = str(uuid.uuid4())

    try:
        if get_stop_requested_sync(real_generation_id):
            logger.warning(f"服务: 在任务开始时检测到 GenID {real_generation_id} 的停止请求。立即中止。")
            final_status = "cancelled"
            await _send_event(event_callback, conversation_id, 'generation_end', {'generation_id': real_generation_id, 'status': final_status})
            return

        if file_data and file_name and user_message_id:
            try:
                file_content = base64.b64decode(file_data)
                saved_path = await database_sync_to_async(default_storage.save)(f"uploads/{real_generation_id}_{file_name}", ContentFile(file_content))
                
                @database_sync_to_async
                def _update_db_message(msg_id, text_content, path):
                    msg = Message.objects.get(id=msg_id)
                    msg.content = f"{text_content}\n[file:{path}]" if text_content.strip() else f"[file:{path}]"
                    msg.save()
                    logger.info(f"已更新用户消息 {msg_id}，添加文件引用: {path}")
                
                await _update_db_message(user_message_id, message or "", saved_path)
            except Exception as e:
                logger.error(f"图片处理失败: {e}", exc_info=True)
                final_status, error_detail = "failed", f'图片处理失败: {str(e)}'
                await _send_event(event_callback, conversation_id, 'generation_end', {'generation_id': real_generation_id, 'status': final_status, 'error': error_detail})
                return

        conversation = await get_conversation_async(conversation_id)
        model = await get_model_async(model_id)
        if not conversation or not model:
            logger.error(f"无法找到会话 {conversation_id} 或模型 {model_id}")
            final_status, error_detail = "failed", "找不到会话或模型。"
            await _send_event(event_callback, conversation_id, 'generation_end', {'generation_id': real_generation_id, 'status': final_status, 'error': error_detail})
            return

        await set_db_generation_id(conversation_id, real_generation_id)
        logger.info(f"服务: 开始生成，ID 为 {real_generation_id}，会话为 {conversation_id}")
        await _send_event(event_callback, conversation_id, 'generation_start', {'generation_id': real_generation_id, 'temp_id': temp_id})

        messages_for_api = await prepare_history_messages(conversation, model, user_message_id, is_regenerate)
        request_data = {"model": model['model_name'], "messages": messages_for_api, "stream": is_streaming, **model['default_params']}
        api_url = ensure_valid_api_url(model['provider_base_url'], "/v1/chat/completions")
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {model['provider_api_key']}"}

        full_content = ""
        timeout = aiohttp.ClientTimeout(total=AI_REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if get_stop_requested_sync(real_generation_id):
                logger.warning(f"服务: 在 API 调用前检测到 GenID {real_generation_id} 的停止请求。中止。")
                final_status = "cancelled"
            else:
                async with session.post(api_url, json=request_data, headers=headers) as response:
                    if response.status == 200:
                        if is_streaming:
                            buffer = b''
                            last_heartbeat_time = time.time()
                            HEARTBEAT_INTERVAL = 15
                            INTER_CHUNK_TIMEOUT = 20

                            while True:
                                try:
                                    current_time = time.time()
                                    if current_time - last_heartbeat_time > HEARTBEAT_INTERVAL:
                                        touch_stop_request_sync(real_generation_id)
                                        last_heartbeat_time = current_time

                                    if get_stop_requested_sync(real_generation_id):
                                        logger.warning(f"服务: 在流式传输期间检测到 GenID {real_generation_id} 的停止请求。正在停止。")
                                        final_status = "cancelled"
                                        break

                                    async with asyncio.timeout(INTER_CHUNK_TIMEOUT):
                                        chunk = await response.content.read(4096)
                                    
                                    if not chunk:
                                        break

                                    buffer += chunk
                                    messages = buffer.split(b'\n\n')
                                    buffer = messages.pop()

                                    for msg in messages:
                                        if not msg: continue
                                        if get_stop_requested_sync(real_generation_id):
                                            final_status = "cancelled"
                                            break
                                        for line in msg.split(b'\n'):
                                            line_str = line.decode('utf-8').strip()
                                            if line_str.startswith('data: '):
                                                chunk_data = line_str[6:]
                                                if chunk_data == '[DONE]': continue
                                                try:
                                                    chunk_json = json.loads(chunk_data)
                                                    content_piece = extract_content_from_chunk(chunk_json)
                                                    if content_piece:
                                                        full_content += content_piece
                                                        await _send_event(event_callback, conversation_id, 'stream_update', {'generation_id': real_generation_id, 'content': content_piece, 'temp_id': temp_id})
                                                except json.JSONDecodeError:
                                                    logger.error(f"JSON 解码错误，数据块: {chunk_data}")
                                    if final_status == "cancelled": break
                                except asyncio.TimeoutError:
                                    logger.error(f"AI 响应块在 {INTER_CHUNK_TIMEOUT} 秒后超时，会话 {conversation_id}")
                                    final_status, error_detail = "failed", f"响应超时：在 {INTER_CHUNK_TIMEOUT} 秒内未收到任何数据"
                                    break
                            
                            if final_status not in ["cancelled", "failed"]:
                                final_status = "completed" if full_content else "failed"
                        else:
                            # --- 非流式响应的心跳逻辑 ---
                            heartbeat_task = None
                            response_task = None
                            try:
                                response_done = asyncio.Event()

                                async def heartbeat():
                                    """在AI响应完成前，定期检查并刷新停止信号的TTL。"""
                                    while not response_done.is_set():
                                        try:
                                            # 等待15秒或直到响应完成
                                            await asyncio.wait_for(response_done.wait(), timeout=15)
                                        except asyncio.TimeoutError:
                                            # 超时后，检查停止信号是否存在
                                            if get_stop_requested_sync(real_generation_id):
                                                # 如果存在，刷新其TTL以防过期
                                                touch_stop_request_sync(real_generation_id)
                                                logger.debug(f"非流式心跳: 刷新 GenID {real_generation_id} 的 TTL")

                                async def get_response():
                                    """获取API响应并信令任务完成。"""
                                    try:
                                        return await response.json()
                                    finally:
                                        response_done.set()

                                # 并发运行心跳和API请求
                                heartbeat_task = asyncio.create_task(heartbeat())
                                response_task = asyncio.create_task(get_response())
                                
                                response_json = await response_task
                                
                                # 在获取响应后，再次检查是否已请求停止
                                if get_stop_requested_sync(real_generation_id):
                                    final_status = "cancelled"
                                else:
                                    full_content = extract_content_from_chunk(response_json)
                                    if full_content:
                                        final_status = "completed"
                                        await _send_event(event_callback, conversation_id, 'full_message', {'generation_id': real_generation_id, 'content': full_content, 'temp_id': temp_id})
                                    else:
                                        final_status, error_detail = "failed", "非流式响应没有内容。"
                            finally:
                                # 确保任务被清理
                                if heartbeat_task and not heartbeat_task.done():
                                    heartbeat_task.cancel()
                                if response_task and not response_task.done():
                                    # 理论上此时 response_task 应该已完成
                                    response_task.cancel()
                            # --- 非流式心跳逻辑结束 ---
                    else:
                        error_text = await response.text()
                        logger.error(f"AI API 请求失败，状态码 {response.status}: {error_text}")
                        final_status, error_detail = "failed", error_text

        if final_status == "completed":
            if get_stop_requested_sync(real_generation_id):
                logger.warning(f"服务: 在保存前检测到 GenID {real_generation_id} 的停止请求。正在丢弃。")
                final_status = "cancelled"
            else:
                if is_regenerate:
                    await delete_subsequent_ai_messages(conversation_id, user_message_id)
                ai_message = await save_ai_message(conversation_id, full_content, model['id'])
                await _send_event(event_callback, conversation_id, 'id_update', {'generation_id': real_generation_id, 'temp_id': temp_id, 'message_id': ai_message['id']})

    except asyncio.CancelledError:
        logger.warning(f"服务: GenID {real_generation_id} 的生成任务被外部取消。")
        final_status = "stopped"
    except aiohttp.ClientError as e:
        logger.error(f"会话 {conversation_id} 的生成过程中出现网络错误: {e}", exc_info=True)
        final_status, error_detail = "failed", f"网络错误: {e}"
    except Exception as e:
        logger.error(f"会话 {conversation_id} 的生成过程中出现错误: {e}", exc_info=True)
        final_status, error_detail = "failed", f"内部服务器错误: {e}"
    finally:
        if conversation and real_generation_id:
            await clear_db_generation_id(conversation_id, real_generation_id)
            
            # 修复：不要在此处清除停止请求。让它通过 TTL 过期。
            # clear_stop_request_sync(real_generation_id)

            event_data = {'generation_id': real_generation_id, 'status': final_status}
            if final_status == "failed":
                event_data['error'] = error_detail
            await _send_event(event_callback, conversation_id, 'generation_end', event_data)
        logger.info(f"服务: 会话 {conversation_id} 的生成 {real_generation_id} 以状态 {final_status} 结束。")

def generate_ai_response_task(conversation_id, model_id, **kwargs):
    """
    面向 consumer 的公共函数。为 AI 生成创建一个后台任务。
    这是一个“即发即忘”的函数。
    """
    asyncio.create_task(
        _handle_ai_generation_logic(
            conversation_id=conversation_id,
            model_id=model_id,
            **kwargs
        )
    )

async def stream_ai_response(conversation_id, model_id, **kwargs):
    """
    面向 view 的公共异步生成器。
    流式传输 AI 生成事件。
    """
    event_queue = asyncio.Queue()
    
    async def callback(event_type, data):
        await event_queue.put({'type': event_type, 'data': data})

    kwargs['event_callback'] = callback
    
    asyncio.create_task(
        _handle_ai_generation_logic(
            conversation_id=conversation_id,
            model_id=model_id,
            **kwargs
        )
    )

    while True:
        event = await event_queue.get()
        yield event
        if event['type'] == 'generation_end':
            break

# --- 数据库辅助函数 ---
@database_sync_to_async
def get_conversation_async(conversation_id):
    try:
        conv = Conversation.objects.get(id=conversation_id)
        return {'id': conv.id, 'system_prompt': conv.system_prompt}
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
        history_qs = Message.objects.filter(conversation_id=conversation['id'], timestamp__lte=user_message.timestamp).order_by('timestamp')
    else:
        history_qs = Message.objects.filter(conversation_id=conversation['id']).order_by('timestamp')

    history_messages = list(history_qs)
    if len(history_messages) > model['max_history_messages']:
        history_messages = history_messages[-model['max_history_messages']:]

    messages = []
    if conversation.get('system_prompt'):
        messages.append({"role": "system", "content": conversation['system_prompt']})
    
    from .image_config import IMAGE_CONTEXT_STRATEGY, MAX_IMAGES_IN_CONTEXT
    all_image_message_ids = [msg.id for msg in history_messages if msg.is_user and re.search(r'\[file:(.*?)\]', msg.content)]
    
    latest_image_ids_to_include = set()
    if IMAGE_CONTEXT_STRATEGY == "all":
        latest_image_ids_to_include = set(all_image_message_ids)
    elif IMAGE_CONTEXT_STRATEGY == "latest_only":
        latest_image_ids_to_include = set(all_image_message_ids[-MAX_IMAGES_IN_CONTEXT:])
    
    for msg in history_messages:
        role = "user" if msg.is_user else "assistant"
        if msg.id in latest_image_ids_to_include:
            file_match = re.search(r'\[file:(.*?)\]', msg.content)
            file_path = file_match.group(1)
            text_content = msg.content.replace(file_match.group(0), '').strip()
            try:
                if default_storage.exists(file_path):
                    with default_storage.open(file_path, 'rb') as f:
                        file_data = f.read()
                    base64_content = base64.b64encode(file_data).decode('utf-8')
                    mime_type, _ = mimetypes.guess_type(file_path)
                    if not mime_type: mime_type = 'application/octet-stream'
                    multi_modal_content = [{"type": "text", "text": text_content}, {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64_content}"}}]
                    messages.append({"role": role, "content": multi_modal_content})
                else:
                    messages.append({"role": role, "content": f"{text_content}\n[图片上传失败: 文件未找到]"})
            except Exception as e:
                logger.error(f"处理消息 {msg.id} 中的文件 '{file_path}' 时出错: {e}", exc_info=True)
                messages.append({"role": role, "content": f"{text_content}\n[图片处理失败]"})
        elif msg.is_user and re.search(r'\[file:(.*?)\]', msg.content):
            file_match = re.search(r'\[file:(.*?)\]', msg.content)
            text_content = msg.content.replace(file_match.group(0), '').strip()
            image_description = f"[用户上传了图片: {os.path.basename(file_match.group(1))}]"
            messages.append({"role": role, "content": f"{text_content}\n{image_description}" if text_content else image_description})
        else:
            messages.append({"role": role, "content": msg.content})
            
    return messages

@database_sync_to_async
def save_ai_message(conversation_id, content, model_id):
    conversation = Conversation.objects.get(id=conversation_id)
    model = AIModel.objects.get(id=model_id)
    ai_message = Message.objects.create(conversation=conversation, content=content, is_user=False, model_used=model)
    conversation.save()
    return {'id': ai_message.id}

@database_sync_to_async
def delete_subsequent_ai_messages(conversation_id, user_message_id):
    try:
        user_message = Message.objects.get(id=user_message_id)
        Message.objects.filter(conversation_id=conversation_id, is_user=False, timestamp__gt=user_message.timestamp).delete()
    except Message.DoesNotExist:
        logger.error(f"找不到用户消息 {user_message_id} 来删除后续消息。")

@database_sync_to_async
def set_db_generation_id(conversation_id, generation_id):
    Conversation.objects.filter(id=conversation_id).update(current_generation_id=generation_id)

@database_sync_to_async
def clear_db_generation_id(conversation_id, generation_id_to_clear):
    conv = Conversation.objects.filter(id=conversation_id).first()
    if conv and str(conv.current_generation_id) == str(generation_id_to_clear):
        conv.current_generation_id = None
        conv.save(update_fields=['current_generation_id'])

async def send_generation_event(conversation_id, event_type, data):
    channel_layer = get_channel_layer()
    group_name = f'chat_{conversation_id}'
    await channel_layer.group_send(group_name, {'type': 'broadcast_event', 'event': {'type': event_type, 'data': data}})

def extract_content_from_chunk(chunk_json):
    if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
        choice = chunk_json['choices'][0]
        if 'delta' in choice: return choice['delta'].get('content')
        if 'message' in choice: return choice['message'].get('content')
    return None
