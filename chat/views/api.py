import json
import logging
import requests
import traceback

from django.shortcuts import get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required

from chat.models import AIProvider, AIModel, Conversation, Message
from .utils import ensure_valid_api_url # Import from local utils

logger = logging.getLogger(__name__)

# API接口 - 核心聊天功能

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def chat_api(request):
    """处理聊天请求并转发到AI服务"""
    try:
        data = json.loads(request.body)
        conversation_id = data.get('conversation_id')
        message_content = data.get('message')
        model_id = data.get('model_id')
        temp_id = data.get('temp_id')  # 获取临时ID

        if not message_content or not model_id:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数"
            }, status=400)

        # 获取选择的模型
        model = get_object_or_404(AIModel, id=model_id)
        is_new_conversation = False # Flag to track if a new conversation was created

        # 获取或创建会话
        if conversation_id:
            conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
        else:
            # 创建新会话
            is_new_conversation = True
            conversation = Conversation.objects.create(
                user=request.user,
                selected_model=model,
                title=message_content[:30] + "..." if len(message_content) > 30 else message_content
            )

        # 保存用户消息
        user_message = Message.objects.create(
            conversation=conversation,
            content=message_content,
            is_user=True,
            model_used=model
        )

        # 记录临时ID和真实ID的映射关系（可以用于调试）
        if temp_id:
            logger.info(f"临时ID映射: {temp_id} -> {user_message.id}")

        # 获取历史消息，根据模型的上下文限制
        history_messages = Message.objects.filter(conversation=conversation).order_by('timestamp')

        # 如果消息超过模型的限制，则只取最近的消息
        if history_messages.count() > model.max_history_messages:
            history_messages = history_messages[history_messages.count() - model.max_history_messages:]

        # 构建OpenAI格式的消息列表
        messages = []
        for msg in history_messages:
            role = "user" if msg.is_user else "assistant"
            messages.append({
                "role": role,
                "content": msg.content
            })

        # 构建请求数据
        request_data = {
            "model": model.model_name,
            "messages": messages,
            "stream": True,  # 使用流式响应模式
            **model.default_params  # 添加默认参数
        }

        # 获取API URL和密钥
        api_url = ensure_valid_api_url(model.provider.base_url, "/v1/chat/completions")
        api_key = model.provider.api_key

        # 记录请求信息（不包含敏感数据）
        logger.info(f"发送请求到 {api_url}")
        logger.info(f"请求模型: {model.model_name}")

        # 发送请求到AI服务提供商
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        try:
            logger.info("开始发送API请求...")

            # 使用流式响应
            response = requests.post(
                api_url,
                json=request_data,
                headers=headers,
                stream=True,
                timeout=60
            )

            logger.info(f"API响应状态码: {response.status_code}")

            # 解析响应
            if response.status_code == 200:
                # 收集所有流式响应片段
                full_content = ""

                try:
                    logger.info("开始处理流式响应")
                    for chunk in response.iter_lines():
                        if not chunk:
                            continue

                        # 记录原始响应片段
                        logger.debug(f"原始响应片段: {chunk}")

                        # 处理数据行
                        try:
                            # 移除 "data: " 前缀
                            if chunk.startswith(b'data: '):
                                chunk_data = chunk[6:].decode('utf-8')
                                logger.debug(f"解码后的数据: {chunk_data}")

                                if chunk_data.strip() == '[DONE]':
                                    logger.debug("收到结束标记")
                                    continue

                                try:
                                    chunk_json = json.loads(chunk_data)
                                    logger.debug(f"解析的JSON: {json.dumps(chunk_json)}")

                                    # 尝试提取内容
                                    if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
                                        if 'delta' in chunk_json['choices'][0]:
                                            delta = chunk_json['choices'][0]['delta']
                                            if 'content' in delta and delta['content']:
                                                content_piece = delta['content']
                                                full_content += content_piece
                                                logger.debug(f"提取的内容片段: {content_piece}")
                                except json.JSONDecodeError as je:
                                    logger.error(f"JSON解析错误: {je}, 数据: {chunk_data}")
                        except Exception as e:
                            logger.error(f"处理流式响应片段时出错: {str(e)}")

                    logger.info(f"流式响应处理完成，累积内容长度: {len(full_content)}")

                    # 如果没有内容，尝试非流式方式重试
                    if not full_content:
                        logger.warning("流式响应未提取到内容，尝试非流式方式")
                        # 修改请求为非流式
                        request_data['stream'] = False

                        # 重新发送请求
                        response = requests.post(
                            api_url,
                            json=request_data,
                            headers=headers,
                            timeout=60
                        )

                        if response.status_code == 200:
                            response_data = response.json()
                            logger.debug(f"非流式响应: {json.dumps(response_data)}")

                            if 'choices' in response_data and len(response_data['choices']) > 0:
                                if 'message' in response_data['choices'][0] and 'content' in response_data['choices'][0]['message']:
                                    full_content = response_data['choices'][0]['message']['content']
                                    logger.info(f"非流式方式成功提取内容，长度: {len(full_content)}")
                except Exception as e:
                    logger.error(f"处理响应时出错: {str(e)}")
                    logger.error(f"详细错误: {traceback.format_exc()}")

                if full_content:
                    # 保存AI回复
                    ai_message = Message.objects.create(
                        conversation=conversation,
                        content=full_content,
                        is_user=False,
                        model_used=model
                    )

                    # 更新会话时间
                    conversation.save()  # 自动更新updated_at字段

                    # 构建基础响应数据
                    response_data = {
                        'success': True,
                        'conversation_id': conversation.id, # Always return the current conversation ID
                        'message': full_content,
                        'message_id': ai_message.id,
                        'timestamp': ai_message.timestamp.isoformat(), # Add ISO timestamp
                        'user_message_id': user_message.id # ID of the user message saved
                    }
                    # 如果是新创建的对话，添加 new_conversation_id 字段
                    if is_new_conversation:
                        response_data['new_conversation_id'] = conversation.id

                    return JsonResponse(response_data)
                else:
                    logger.error("未能从响应中提取内容")
                    # 返回原始响应以便调试
                    try:
                        response_text = str(response.text)[:500]  # 截取前500个字符避免过大
                    except:
                        response_text = "无法获取响应文本"

                    return JsonResponse({
                        'success': False,
                        'message': f"无法从API响应中提取内容，请检查API设置。响应：{response_text}",
                        'user_message_id': user_message.id
                    }, status=500)
            elif response.status_code == 404:
                logger.error(f"API端点未找到(404): {api_url}")
                # 记录完整请求信息以便调试
                logger.error(f"请求头: {headers}")
                logger.error(f"请求体: {json.dumps(request_data)}") # Log request data

                # 尝试记录响应内容
                try:
                    error_content = response.json()
                    logger.error(f"API错误响应: {error_content}")
                except:
                    logger.error(f"API错误响应(无法解析): {response.text}")

                return JsonResponse({
                    'success': False,
                    'message': f"AI服务API端点未找到(404)，请检查服务提供商的基础URL配置是否正确"
                }, status=response.status_code)
            else:
                logger.error(f"API请求失败，状态码: {response.status_code}")
                try:
                    error_detail = response.json()
                    error_message = error_detail.get('error', {}).get('message', '未知错误')
                except:
                    error_message = response.text[:200] if response.text else '未知错误'

                return JsonResponse({
                    'success': False,
                    'message': f"API请求失败: {error_message}",
                    'user_message_id': user_message.id
                }, status=500)
        except requests.exceptions.RequestException as e:
            logger.error(f"请求异常: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"无法连接到API服务: {str(e)}",
                'user_message_id': user_message.id
            }, status=500)

    except Exception as e:
        logger.error(f"处理聊天请求时出错: {str(e)}")
        logger.error(traceback.format_exc())

        # 如果已创建用户消息，则返回其ID
        user_message_id = getattr(locals().get('user_message'), 'id', None)

        return JsonResponse({
            'success': False,
            'message': f"处理请求时出错: {str(e)}",
            'user_message_id': user_message_id
        }, status=500)

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
            data = json.loads(request.body)
            conversation_id = data.get('id')

            if not conversation_id:
                return JsonResponse({'success': False, 'message': "缺少对话ID"}, status=400)

            # 使用 try-except 而不是 get_object_or_404 来区分 404 和其他错误
            try:
                conversation = Conversation.objects.get(id=conversation_id, user=request.user)
                conversation.delete()
                return JsonResponse({
                    'success': True,
                    'message': "对话已删除"
                })
            except Conversation.DoesNotExist:
                logger.warning(f"尝试删除不存在或不属于用户的对话: ID={conversation_id}, 用户={request.user.username}")
                return JsonResponse({'success': False, 'message': "对话不存在或不属于您"}, status=404) # 返回 404

        except json.JSONDecodeError:
             logger.error("删除对话请求体JSON解析失败")
             return JsonResponse({'success': False, 'message': "无效的请求格式"}, status=400)
        except Exception as e:
            logger.error(f"删除对话时发生未知错误: {str(e)}")
            logger.error(traceback.format_exc())
            return JsonResponse({
                'success': False,
                'message': "对话已删除"
            })
        except Exception as e:
            logger.error(f"删除对话失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"删除失败: {str(e)}"
            }, status=400)

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

        # 检查是否是临时ID
        if isinstance(message_id, str) and message_id.startswith('temp-'):
            return JsonResponse({
                'success': False,
                'message': "无法编辑临时消息，请等待消息保存后再试"
            }, status=400)

        # 获取消息并验证所有权
        try:
            message = Message.objects.get(id=message_id)
            if message.conversation.user != request.user:
                return JsonResponse({
                    'success': False,
                    'message': "无权编辑此消息"
                }, status=403)

            # 只允许编辑用户消息
            if not message.is_user:
                return JsonResponse({
                    'success': False,
                    'message': "只能编辑用户消息"
                }, status=400)

            # 更新消息内容
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
def regenerate_message_api(request):
    """重新生成AI回复"""
    try:
        data = json.loads(request.body)
        message_id = data.get('message_id') # This should be the ID of the *user* message to regenerate from
        model_id = data.get('model_id') # The model to use for regeneration

        if not message_id or not model_id:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数 (message_id of user message, model_id)"
            }, status=400)

        # 检查是否是临时ID
        if isinstance(message_id, str) and message_id.startswith('temp-'):
            return JsonResponse({
                'success': False,
                'message': "无法基于临时消息重新生成回复，请等待消息保存后再试"
            }, status=400)

        # 获取用户消息
        try:
            user_message = Message.objects.get(id=message_id)
            if user_message.conversation.user != request.user:
                return JsonResponse({
                    'success': False,
                    'message': "无权访问此消息"
                }, status=403)

            # 确保是用户消息
            if not user_message.is_user:
                return JsonResponse({
                    'success': False,
                    'message': "只能基于用户消息重新生成回复"
                }, status=400)

            # 获取选择的模型
            model = get_object_or_404(AIModel, id=model_id)
            conversation = user_message.conversation

            # 获取历史消息，直到当前用户消息 (inclusive)
            history_messages = Message.objects.filter(
                conversation=conversation,
                timestamp__lte=user_message.timestamp
            ).order_by('timestamp')

            # 如果消息超过模型的限制，则只取最近的消息
            if history_messages.count() > model.max_history_messages:
                history_messages = history_messages[history_messages.count() - model.max_history_messages:]

            # 构建OpenAI格式的消息列表
            messages = []
            for msg in history_messages:
                role = "user" if msg.is_user else "assistant"
                messages.append({
                    "role": role,
                    "content": msg.content
                })

            # 构建请求数据
            request_data = {
                "model": model.model_name,
                "messages": messages,
                "stream": True, # Always use stream for consistency with chat_api
                **model.default_params  # 添加默认参数
            }

            # 记录完整请求数据（排除敏感信息）
            log_data = request_data.copy()
            logger.info(f"重新生成回复请求数据: {json.dumps(log_data)}")

            # 获取API URL和密钥
            api_url = ensure_valid_api_url(model.provider.base_url, "/v1/chat/completions")
            api_key = model.provider.api_key

            # 发送请求到AI服务提供商
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}"
            }

            logger.info(f"重新生成回复: 用户消息ID={message_id}, 模型={model.model_name}, API URL={api_url}")

            # 使用流式响应
            response = requests.post(
                api_url,
                json=request_data,
                headers=headers,
                stream=True,
                timeout=60
            )

            if response.status_code == 200:
                # 收集所有流式响应片段
                full_content = ""

                try:
                    logger.info("开始处理流式响应 (重新生成)")
                    for chunk in response.iter_lines():
                        if not chunk:
                            continue

                        # 处理数据行
                        try:
                            # 移除 "data: " 前缀
                            if chunk.startswith(b'data: '):
                                chunk_data = chunk[6:].decode('utf-8')

                                if chunk_data.strip() == '[DONE]':
                                    continue

                                try:
                                    chunk_json = json.loads(chunk_data)
                                    # 尝试提取内容
                                    if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
                                        if 'delta' in chunk_json['choices'][0]:
                                            delta = chunk_json['choices'][0]['delta']
                                            if 'content' in delta and delta['content']:
                                                content_piece = delta['content']
                                                full_content += content_piece
                                except json.JSONDecodeError as je:
                                    logger.error(f"JSON解析错误 (重新生成): {je}, 数据: {chunk_data}")
                        except Exception as e:
                            logger.error(f"处理流式响应片段时出错 (重新生成): {str(e)}")

                    logger.info(f"流式响应处理完成 (重新生成)，累积内容长度: {len(full_content)}")

                    # 如果没有内容，尝试非流式方式重试 (less likely needed now, but keep for robustness)
                    if not full_content:
                        logger.warning("流式响应未提取到内容 (重新生成)，尝试非流式方式")
                        request_data['stream'] = False
                        response = requests.post(api_url, json=request_data, headers=headers, timeout=60)
                        if response.status_code == 200:
                            response_data = response.json()
                            if 'choices' in response_data and len(response_data['choices']) > 0:
                                if 'message' in response_data['choices'][0] and 'content' in response_data['choices'][0]['message']:
                                    full_content = response_data['choices'][0]['message']['content']
                                    logger.info(f"非流式方式成功提取内容 (重新生成)，长度: {len(full_content)}")
                except Exception as e:
                    logger.error(f"处理响应时出错 (重新生成): {str(e)}")
                    logger.error(f"详细错误: {traceback.format_exc()}")

                if full_content:
                    # 删除此用户消息之后的所有消息 (包括旧的AI回复)
                    Message.objects.filter(
                        conversation=conversation,
                        timestamp__gt=user_message.timestamp
                    ).delete()

                    # 保存新的AI回复
                    ai_message = Message.objects.create(
                        conversation=conversation,
                        content=full_content,
                        is_user=False,
                        model_used=model
                    )

                    # 更新会话时间
                    conversation.save()

                    return JsonResponse({
                        'success': True,
                        'message_id': ai_message.id, # ID of the new AI message
                        'content': full_content,
                        'timestamp': ai_message.timestamp.isoformat(), # Add ISO timestamp
                        'user_message_id': user_message.id # ID of the user message it follows
                    })
                else:
                    logger.error("未能从响应中提取内容 (重新生成)")
                    try:
                        response_text = str(response.text)[:500]
                    except:
                        response_text = "无法获取响应文本"
                    return JsonResponse({
                        'success': False,
                        'message': f"无法从API响应中提取内容 (重新生成)。响应：{response_text}",
                    }, status=500)
            else:
                logger.error(f"重新生成回复API请求失败: {response.status_code}")
                try:
                    error_detail = response.json()
                    error_message = error_detail.get('error', {}).get('message', '未知错误')
                except:
                     error_message = response.text[:200] if response.text else '未知错误'
                return JsonResponse({
                    'success': False,
                    'message': f"无法从API响应中提取内容 (重新生成): {response.status_code} - {error_message}"
                }, status=response.status_code)

        except Message.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': "消息不存在"
            }, status=404)

    except Exception as e:
        logger.error(f"重新生成回复出错: {str(e)}")
        logger.error(f"详细错误信息: {traceback.format_exc()}")
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
    接收客户端保存的会话ID，返回完整的会话数据
    """
    try:
        data = json.loads(request.body)
        conversation_id = data.get('conversation_id')

        logger.info(f"收到会话同步请求: conversation_id={conversation_id}, 用户={request.user.username}")

        conversation = None
        if conversation_id:
             try:
                 # 尝试获取指定的会话
                 conversation = Conversation.objects.get(id=conversation_id, user=request.user)
                 logger.info(f"找到指定会话: {conversation.id} - {conversation.title}")
             except Conversation.DoesNotExist:
                 logger.warning(f"指定会话ID {conversation_id} 不存在或不属于用户，尝试获取其他会话")
                 conversation = None # Explicitly set to None

        if not conversation:
            # 如果找不到指定会话或未提供ID，检查用户是否有其他会话
            user_conversations = Conversation.objects.filter(user=request.user).order_by('-updated_at')
            if user_conversations.exists():
                # 返回最近的会话
                conversation = user_conversations.first()
                logger.info(f"使用用户最近的会话: {conversation.id} - {conversation.title}")
            else:
                # 用户没有任何会话，创建一个新的
                logger.info("用户没有任何会话，尝试创建新会话")
                # 获取一个默认模型
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

        # 获取会话的消息
        messages = Message.objects.filter(conversation=conversation).order_by('timestamp')
        messages_data = []

        logger.info(f"会话 {conversation.id} 的消息数量: {messages.count()}")

        for msg in messages:
            messages_data.append({
                'id': msg.id,
                'content': msg.content,
                'is_user': msg.is_user,
                'timestamp': msg.timestamp.isoformat(),
                'model': msg.model_used.display_name if msg.model_used else None
            })

        # 返回会话和消息数据
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

        # 获取消息并验证所有权
        try:
            message = Message.objects.get(id=message_id)
            if message.conversation.user != request.user:
                return JsonResponse({
                    'success': False,
                    'message': "无权删除此消息"
                }, status=403)

            # 记录要删除的消息信息
            is_user_message = message.is_user
            conversation = message.conversation
            timestamp = message.timestamp
            deleted_message_id = message.id # Store ID before deleting

            # 删除消息
            message.delete()
            deleted_ai_message_id = None # Initialize

            # 如果删除的是用户消息，同时删除其之后的所有消息（包括对应的AI回复及后续交互）
            # This is a common pattern: deleting a user message invalidates subsequent context.
            if is_user_message:
                messages_to_delete = Message.objects.filter(
                    conversation=conversation,
                    timestamp__gt=timestamp
                )
                deleted_count = messages_to_delete.count()
                messages_to_delete.delete()
                logger.info(f"已删除用户消息 {deleted_message_id} 及其后的 {deleted_count} 条消息")

            else:
                 # If deleting an AI message, maybe just delete that one?
                 # Or delete it and the user message before it?
                 # Current logic only deletes the specified message if it's AI.
                 # Let's stick to deleting only the specified AI message for now.
                 logger.info(f"已删除AI回复消息 {deleted_message_id}")


            return JsonResponse({
                'success': True,
                'message': "消息已删除"
                # Optionally return IDs of deleted messages if needed by frontend
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
def debug_response_api(request):
    """处理API调试请求并返回原始响应"""
    try:
        data = json.loads(request.body)
        provider_id = data.get('provider_id')
        model_name = data.get('model_name')
        messages = data.get('messages', [])
        temperature = data.get('temperature', 0.7)
        max_tokens = data.get('max_tokens', 1000)
        stream_mode = data.get('stream', False)

        if not provider_id or not model_name or not messages:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数 (provider_id, model_name, messages)"
            }, status=400)

        # 获取服务提供商
        provider = get_object_or_404(AIProvider, id=provider_id)

        # 构建API URL
        api_url = ensure_valid_api_url(provider.base_url, "/v1/chat/completions")
        
        # 构建请求数据
        request_data = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream_mode
        }

        # 发送请求到AI服务提供商
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider.api_key}"
        }

        logger.info(f"API调试 - 发送请求到: {api_url}")

        try:
            if stream_mode:
                # 处理流式响应
                response = requests.post(
                    api_url,
                    json=request_data,
                    headers=headers,
                    stream=True,
                    timeout=60
                )
                
                # 收集流式响应数据
                chunks = []
                full_content = ""
                
                if response.status_code == 200:
                    for chunk in response.iter_lines():
                        if chunk:
                            # 处理数据行
                            try:
                                # 移除 "data: " 前缀
                                if chunk.startswith(b'data: '):
                                    chunk_data = chunk[6:].decode('utf-8')
                                    
                                    if chunk_data.strip() == '[DONE]':
                                        chunks.append({'done': True})
                                        continue
                                    
                                    chunk_json = json.loads(chunk_data)
                                    chunks.append(chunk_json)
                                    
                                    # 尝试提取内容
                                    if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
                                        if 'delta' in chunk_json['choices'][0]:
                                            delta = chunk_json['choices'][0]['delta']
                                            if 'content' in delta and delta['content']:
                                                full_content += delta['content']
                            except Exception as e:
                                logger.error(f"处理流式响应片段时出错: {str(e)}")
                    
                    return JsonResponse({
                        'success': True,
                        'status_code': response.status_code,
                        'is_stream': True,
                        'chunks': chunks[:10],  # 只返回前10个数据块
                        'chunks_count': len(chunks),
                        'full_content': full_content
                    })
                else:
                    # 处理流式请求的错误响应
                    try:
                        error_text = next(response.iter_lines()).decode('utf-8', errors='ignore')
                    except:
                        error_text = "无法读取错误响应"
                    
                    return JsonResponse({
                        'success': False,
                        'status_code': response.status_code,
                        'raw_text': error_text
                    })
            else:
                # 处理普通响应
                response = requests.post(
                    api_url,
                    json=request_data,
                    headers=headers,
                    timeout=60
                )
                
                try:
                    response_json = response.json()
                    return JsonResponse({
                        'success': response.status_code == 200,
                        'status_code': response.status_code,
                        'is_stream': False,
                        'raw_response': response_json
                    })
                except json.JSONDecodeError:
                    return JsonResponse({
                        'success': False,
                        'status_code': response.status_code,
                        'raw_text': response.text[:1000]  # 限制返回文本大小
                    })
                
        except requests.exceptions.RequestException as req_err:
            logger.error(f"API调试 - 请求失败: {req_err}")
            return JsonResponse({
                'success': False,
                'message': f"请求失败: {req_err}",
                'error_type': type(req_err).__name__
            }, status=500)
            
    except Exception as e:
        logger.error(f"API调试请求处理失败: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f"处理请求失败: {str(e)}"
        }, status=500)
