import json
import logging
import traceback

from django.shortcuts import get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required

from chat.models import AIModel, Conversation, Message

logger = logging.getLogger(__name__)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def clear_conversation_api(request, conversation_id):
    """Clears all messages from a conversation."""
    try:
        conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
        # Delete all messages in this conversation
        Message.objects.filter(conversation=conversation).delete()
        logger.info(f"User {request.user.username} cleared all messages from conversation {conversation_id}")
        return JsonResponse({'success': True, 'message': '所有消息已清除。'})
    except Conversation.DoesNotExist:
        return JsonResponse({'success': False, 'message': '会话不存在或您没有权限。'}, status=404)
    except Exception as e:
        logger.error(f"清除会话 {conversation_id} 时出错: {e}")
        return JsonResponse({'success': False, 'message': '清除消息时发生错误。'}, status=500)


@login_required
@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
def conversations_api(request):
    """管理用户对话"""
    if request.method == 'GET':
        # 获取用户的所有对话
        conversations = Conversation.objects.filter(user=request.user).order_by('-updated_at') # Order by most recent
        conversations_data = []

        for conv in conversations:
            conversations_data.append({
                'id': conv.id,
                'title': conv.title,
                'created_at': conv.created_at,
                'updated_at': conv.updated_at,
                'model': conv.selected_model.display_name if conv.selected_model else None
            })

        return JsonResponse({'conversations': conversations_data})

    elif request.method == 'POST':
        # 创建新对话或更新现有对话
        try:
            data = json.loads(request.body)
            conversation_id = data.get('id')

            if conversation_id:
                # 更新现有对话
                conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
                if 'title' in data:
                    conversation.title = data['title']
                if 'selected_model_id' in data:
                    model = get_object_or_404(AIModel, id=data['selected_model_id'])
                    conversation.selected_model = model

                conversation.save()

                return JsonResponse({
                    'success': True,
                    'conversation_id': conversation.id,
                    'message': "对话已更新"
                })
            else:
                # 创建新对话
                model = None
                if 'selected_model_id' in data:
                    model = get_object_or_404(AIModel, id=data['selected_model_id'])
                else: # Default to first available model if none specified
                    model = AIModel.objects.filter(is_active=True, provider__is_active=True).first()

                if not model:
                     return JsonResponse({'success': False, 'message': "没有可用的AI模型来创建新对话"}, status=400)


                conversation = Conversation.objects.create(
                    user=request.user,
                    title=data.get('title', f"新对话 {Conversation.objects.filter(user=request.user).count() + 1}"),
                    selected_model=model
                )

                return JsonResponse({
                    'success': True,
                    'conversation_id': conversation.id,
                    'message': "新对话已创建"
                })
        except Exception as e:
            logger.error(f"创建/更新对话失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"操作失败: {str(e)}"
            }, status=400)

    elif request.method == 'DELETE':
        # 删除对话
        try:
            # In DELETE requests, parameters are often in the query string, not the body.
            # But frontend seems to send it in body, so we check both.
            if request.body:
                data = json.loads(request.body)
                conversation_id = data.get('id')
            else:
                conversation_id = request.GET.get('id')


            if not conversation_id:
                return JsonResponse({'success': False, 'message': "缺少对话ID"}, status=400)

            try:
                conversation = Conversation.objects.get(id=conversation_id, user=request.user)
                conversation.delete()
                logger.info(f"User {request.user.username} deleted conversation {conversation_id}")
                return JsonResponse({
                    'success': True,
                    'message': "对话已删除"
                })
            except Conversation.DoesNotExist:
                logger.warning(f"尝试删除不存在或不属于用户的对话: ID={conversation_id}, 用户={request.user.username}")
                return JsonResponse({'success': False, 'message': "对话不存在或不属于您"}, status=404)

        except json.JSONDecodeError:
             logger.error("删除对话请求体JSON解析失败")
             return JsonResponse({'success': False, 'message': "无效的请求格式"}, status=400)
        except Exception as e:
            logger.error(f"删除对话时发生未知错误: {str(e)}")
            logger.error(traceback.format_exc())
            return JsonResponse({
                'success': False,
                'message': f"删除失败: {str(e)}"
            }, status=500)

    return HttpResponseBadRequest("不支持的请求方法")

@login_required
@require_http_methods(["GET"])
def messages_api(request, conversation_id):
    """获取特定对话的消息"""
    try:
        conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
        messages = Message.objects.filter(conversation=conversation).order_by('timestamp')

        messages_data = []
        for msg in messages:
            messages_data.append({
                'id': msg.id,
                'content': msg.content,
                'is_user': msg.is_user,
                'timestamp': msg.timestamp,
                'model': msg.model_used.display_name if msg.model_used else None
            })

        return JsonResponse({
            'success': True,
            'conversation_id': conversation_id,
            'conversation_title': conversation.title,
            'messages': messages_data
        })
    except Exception as e:
        logger.error(f"获取消息失败: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"获取失败: {str(e)}"
        }, status=400)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def edit_message_api(request):
    """编辑消息内容"""
    try:
        data = json.loads(request.body)
        message_id = data.get('message_id')
        content = data.get('content')

        if not message_id or content is None: # Check for None explicitly for empty content
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数"
            }, status=400)

        if isinstance(message_id, str) and message_id.startswith('temp-'):
            return JsonResponse({
                'success': False,
                'message': "无法编辑临时消息，请等待消息保存后再试"
            }, status=400)

        try:
            message = Message.objects.get(id=message_id)
            if message.conversation.user != request.user:
                return JsonResponse({
                    'success': False,
                    'message': "无权编辑此消息"
                }, status=403)

            if not message.is_user:
                return JsonResponse({
                    'success': False,
                    'message': "只能编辑用户消息"
                }, status=400)

            message.content = content
            message.save()

            return JsonResponse({
                'success': True,
                'message_id': message.id,
                'content': message.content
            })
        except Message.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': "消息不存在"
            }, status=404)

    except Exception as e:
        logger.error(f"编辑消息出错: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"处理请求失败: {str(e)}"
        }, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def delete_message_api(request):
    """删除消息"""
    try:
        data = json.loads(request.body)
        message_id = data.get('message_id')

        if not message_id:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数"
            }, status=400)

        try:
            message = Message.objects.get(id=message_id)
            if message.conversation.user != request.user:
                return JsonResponse({
                    'success': False,
                    'message': "无权删除此消息"
                }, status=403)

            is_user_message = message.is_user
            conversation = message.conversation
            timestamp = message.timestamp
            deleted_message_id = message.id

            message.delete()

            if is_user_message:
                messages_to_delete = Message.objects.filter(
                    conversation=conversation,
                    timestamp__gt=timestamp
                )
                deleted_count = messages_to_delete.count()
                messages_to_delete.delete()
                logger.info(f"已删除用户消息 {deleted_message_id} 及其后的 {deleted_count} 条消息")
            else:
                 logger.info(f"已删除AI回复消息 {deleted_message_id}")

            return JsonResponse({
                'success': True,
                'message': "消息已删除"
            })
        except Message.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': "消息不存在"
            }, status=404)

    except Exception as e:
        logger.error(f"删除消息出错: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"处理请求失败: {str(e)}"
        }, status=500)


@login_required
@csrf_exempt
@require_http_methods(["POST"])
def sync_conversation_api(request):
    """
    同步会话数据，用于跨设备恢复聊天状态
    """
    try:
        data = json.loads(request.body)
        conversation_id = data.get('conversation_id')

        logger.info(f"收到会话同步请求: conversation_id={conversation_id}, 用户={request.user.username}")

        conversation = None
        if conversation_id:
             try:
                 conversation = Conversation.objects.get(id=conversation_id, user=request.user)
                 logger.info(f"找到指定会话: {conversation.id} - {conversation.title}")
             except Conversation.DoesNotExist:
                 logger.warning(f"指定会话ID {conversation_id} 不存在或不属于用户，尝试获取其他会话")
                 conversation = None

        if not conversation:
            user_conversations = Conversation.objects.filter(user=request.user).order_by('-updated_at')
            if user_conversations.exists():
                conversation = user_conversations.first()
                logger.info(f"使用用户最近的会话: {conversation.id} - {conversation.title}")
            else:
                logger.info("用户没有任何会话，尝试创建新会话")
                default_model = AIModel.objects.filter(is_active=True, provider__is_active=True).first()
                if not default_model:
                    logger.error("没有可用的AI模型，无法创建新会话")
                    return JsonResponse({
                        'success': False,
                        'message': "没有可用的AI模型"
                    }, status=400)

                conversation = Conversation.objects.create(
                    user=request.user,
                    title="新对话",
                    selected_model=default_model
                )
                logger.info(f"已创建新会话: {conversation.id} - {conversation.title}")

        messages = Message.objects.filter(conversation=conversation).order_by('timestamp')
        messages_data = []

        for msg in messages:
            messages_data.append({
                'id': msg.id,
                'content': msg.content,
                'is_user': msg.is_user,
                'timestamp': msg.timestamp.isoformat(),
                'model': msg.model_used.display_name if msg.model_used else None
            })

        response_data = {
            'success': True,
            'conversation': {
                'id': conversation.id,
                'title': conversation.title,
                'created_at': conversation.created_at.isoformat(),
                'updated_at': conversation.updated_at.isoformat(),
                'model_id': conversation.selected_model.id if conversation.selected_model else None,
                'model_name': conversation.selected_model.display_name if conversation.selected_model else None
            },
            'messages': messages_data
        }

        logger.info(f"同步成功，返回会话 {conversation.id} 的数据，包含 {len(messages_data)} 条消息")
        return JsonResponse(response_data)
    except Exception as e:
        logger.error(f"同步会话失败: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f"同步失败: {str(e)}"
        }, status=500)


from django.http import StreamingHttpResponse
from chat.services import generate_ai_response
import asyncio
import uuid
from channels.db import database_sync_to_async

# --- 为视图准备的异步数据库操作 ---

@database_sync_to_async
def create_user_message(conversation_id, user, content, model_id):
    """异步创建用户消息"""
    conversation = get_object_or_404(Conversation, id=conversation_id, user=user)
    user_message = Message.objects.create(
        conversation=conversation,
        content=content,
        is_user=True,
        model_used_id=model_id
    )
    return user_message.id

async def collect_events(generator):
    """异步地从生成器中收集所有项目。"""
    return [item async for item in generator]

@csrf_exempt
@require_http_methods(["POST"])
async def http_chat_view(request):
    """
    处理HTTP回退的聊天请求，统一支持流式和非流式响应 (异步视图)。
    现在支持 application/json 和 multipart/form-data。
    """
    # 检查用户认证状态
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'error': '用户未登录'}, status=401)
    
    try:
        content_type = request.content_type
        is_image_upload = 'multipart/form-data' in content_type

        # --- 1. 解析请求数据 ---
        if is_image_upload:
            data = request.POST
            file = request.FILES.get('file')
            if not file:
                return HttpResponseBadRequest("Missing file in multipart/form-data request")
        else:
            data = json.loads(request.body)
            file = None

        conversation_id = data.get('conversation_id')
        model_id = data.get('model_id')
        message_content = data.get('message', '')
        is_regenerate = data.get('is_regenerate', 'false').lower() == 'true'
        user_message_id = data.get('message_id')
        is_streaming = data.get('is_streaming', 'true').lower() == 'true'
        generation_id = data.get('generation_id', str(uuid.uuid4()))

        if not model_id or (not message_content and not is_regenerate and not file):
            return HttpResponseBadRequest("Missing required parameters")

        # --- 2. 创建用户消息 ---
        if not is_regenerate:
            # 对于图片上传，即使没有文本，也创建一个消息占位符
            display_content = message_content if message_content.strip() else ('[图片上传]' if file else '')
            user_message_id = await create_user_message(
                conversation_id, request.user, display_content, model_id
            )

        # --- 3. 定义事件生成器 ---
        async def event_generator():
            event_queue = asyncio.Queue()
            async def callback(event_type, data):
                await event_queue.put({'type': event_type, 'data': data})

            task_kwargs = {
                'conversation_id': conversation_id,
                'model_id': model_id,
                'user_message_id': user_message_id,
                'is_regenerate': is_regenerate,
                'generation_id': generation_id,
                'temp_id': generation_id,
                'is_streaming': is_streaming,
                'event_callback': callback
            }

            if is_image_upload and file:
                import base64
                from chat.services import generate_ai_response_with_image
                
                file_data_b64 = base64.b64encode(file.read()).decode('utf-8')
                task_kwargs.update({
                    'message': message_content,
                    'file_data': file_data_b64,
                    'file_name': file.name,
                    'file_type': file.content_type,
                })
                asyncio.create_task(generate_ai_response_with_image(**task_kwargs))
            else:
                task_kwargs['message'] = message_content # For regenerate
                asyncio.create_task(generate_ai_response(**task_kwargs))

            while True:
                try:
                    async with asyncio.timeout(320):
                        event = await event_queue.get()
                    yield event
                    if event['type'] == 'generation_end':
                        break
                except asyncio.TimeoutError:
                    logger.error(f"HTTP event_generator timeout for GenID {generation_id}")
                    yield {'type': 'generation_end', 'data': {'status': 'failed', 'error': '服务器处理超时'}}
                    break
        
        # --- 4. 根据流式或非流式返回响应 ---
        if is_streaming:
            async def sse_stream():
                async for event in event_generator():
                    sse_event = f"event: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                    yield sse_event
            
            response = StreamingHttpResponse(sse_stream(), content_type='text/event-stream')
            response['Cache-Control'] = 'no-cache'
            return response
        else:
            events = await collect_events(event_generator())
            
            final_content = ""
            message_id = None
            final_status = "failed"
            error_message = "Unknown error"

            for event in events:
                if event['type'] == 'full_message':
                    final_content = event['data'].get('content', '')
                if event['type'] == 'id_update':
                    message_id = event['data'].get('message_id')
                if event['type'] == 'generation_end':
                    final_status = event['data'].get('status')
                    if final_status == 'failed':
                        error_message = event['data'].get('error', error_message)

            if final_status == 'completed':
                return JsonResponse({
                    'success': True,
                    'content': final_content,
                    'message_id': message_id,
                    'generation_id': generation_id,
                })
            else:
                return JsonResponse({
                    'success': False,
                    'error': error_message,
                    'generation_id': generation_id,
                }, status=500)

    except json.JSONDecodeError:
        return HttpResponseBadRequest("Invalid JSON format")
    except Exception as e:
        logger.error(f"HTTP chat view error: {e}", exc_info=True)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
