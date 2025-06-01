import json
import logging
import json
import logging
import requests
import traceback
import base64
import mimetypes
import asyncio
import time
import uuid # <-- Import uuid

from django.shortcuts import get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from chat.models import AIProvider, AIModel, Conversation, Message # Ensure Message is imported
from .utils import ensure_valid_api_url # Import from local utils
# --- Import new state utils ---
from chat.state_utils import get_stop_requested_sync, set_stop_requested_sync # Import new functions (clear_stop_request_state_sync is not used directly here)

logger = logging.getLogger(__name__)

# API接口 - 核心聊天功能

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def upload_file_api(request):
    """处理文件上传请求并转发到AI服务"""
    try:
        conversation_id = request.POST.get('conversation_id')
        model_id = request.POST.get('model_id')
        message_content = request.POST.get('message', '请描述这张图片')
        
        if not model_id or 'file' not in request.FILES:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数或文件"
            }, status=400)
        
        uploaded_file = request.FILES['file']
        
        # 获取选择的模型
        model = get_object_or_404(AIModel, id=model_id)
        is_new_conversation = False
        
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
        
        # 保存用户上传文件消息
        file_message_content = f"{message_content}\n[上传文件: {uploaded_file.name}]"
        user_message = Message.objects.create(
            conversation=conversation,
            content=file_message_content,
            is_user=True,
            model_used=model
        )
        
        # 获取历史消息
        history_messages = Message.objects.filter(conversation=conversation).order_by('timestamp')
        
        # 如果消息超过模型的限制，则只取最近的消息
        if history_messages.count() > model.max_history_messages:
            history_messages = history_messages[history_messages.count() - model.max_history_messages:]
        
        # 构建API请求
        api_url = ensure_valid_api_url(model.provider.base_url, "/v1/chat/completions")
        api_key = model.provider.api_key
        
        # 获取文件内容并进行base64编码
        file_content = uploaded_file.read()
        base64_content = base64.b64encode(file_content).decode('utf-8')
        
        # 确定文件的MIME类型
        mime_type = uploaded_file.content_type
        if not mime_type:
            mime_type = mimetypes.guess_type(uploaded_file.name)[0] or 'application/octet-stream'
        
        # 构建消息列表
        messages = []
        
        # 添加历史消息
        for msg in history_messages:
            if msg.id != user_message.id:  # 排除当前上传消息
                role = "user" if msg.is_user else "assistant"
                messages.append({
                    "role": role,
                    "content": msg.content
                })
        
        # 构建带图片的用户消息 - 尝试多种可能的格式
        
        # 1. 尝试OpenAI的格式
        user_message_with_image_openai = {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": message_content
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_content}"
                    }
                }
            ]
        }
        
        # 2. 尝试Claude的格式 (Anthropic)
        user_message_with_image_claude = {
            "role": "user",
            "content": f"""{message_content}
            
<image>
data:{mime_type};base64,{base64_content}
</image>"""
        }
        
        # 先尝试OpenAI格式
        messages.append(user_message_with_image_openai)
        
        # 构建请求数据
        request_data = {
            "model": model.model_name,
            "messages": messages,
            **model.default_params
        }
        
        # --- MODIFIED: Check stop state using NEW state utils ---
        stop_state = get_stop_requested_sync(conversation.id) # 使用新的函数获取停止状态
        if stop_state['requested']: # 检查 'requested' 标志
            logger.warning(f"API upload_file_api: 检测到会话 {conversation.id} 的停止请求，取消发送。StopState: {stop_state}")
            # 注意：文件上传API通常没有特定的 generation_id，所以我们只检查 general request
            # 不需要重置状态，因为没有特定的生成任务被取消
            return JsonResponse({
                'success': False, # 返回 False，因为请求未被发送
                'message': "操作已被用户终止" # 更通用的消息
            })
        # --- END MODIFIED ---

        # 记录请求信息（不包含敏感数据）
        logger.info(f"发送请求到 {api_url}")
        logger.info(f"请求模型: {model.model_name}")
        
        # 替换base64数据为提示，避免日志过大
        log_data = json.dumps(request_data)
        log_data = log_data.replace(base64_content, "[BASE64_DATA]")
        logger.info(f"请求结构: {log_data[:500]}...")
        
        # 设置请求头
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        try:
            # 发送请求 - 纯JSON方式
            response = requests.post(
                api_url,
                headers=headers,
                json=request_data,
                timeout=60
            )
            
            logger.info(f"文件上传API响应状态码: {response.status_code}")
            
            # 如果OpenAI格式失败，尝试Claude格式
            if response.status_code != 200:
                logger.info("OpenAI格式失败，尝试Claude格式")
                
                # 替换为Claude格式的消息
                messages.pop()  # 移除最后一条消息
                messages.append(user_message_with_image_claude)  # 添加Claude格式消息
                
                # 更新请求数据
                request_data["messages"] = messages
                
                # 发送第二次请求
                response = requests.post(
                    api_url,
                    headers=headers,
                    json=request_data,
                    timeout=60
                )
                
                logger.info(f"Claude格式API响应状态码: {response.status_code}")
            
            # 如果成功获取响应
            if response.status_code == 200:
                try:
                    response_data = response.json()
                    logger.info(f"API响应头: {response.headers}")
                    
                    # 尝试记录响应数据的前1000个字符（避免过大）
                    response_str = json.dumps(response_data)
                    logger.info(f"API响应数据片段: {response_str[:1000]}")
                    
                    # 提取AI回复内容
                    ai_content = ""
                    
                    # 尝试提取不同格式的响应
                    if 'choices' in response_data and len(response_data['choices']) > 0:
                        # 标准OpenAI格式
                        if 'message' in response_data['choices'][0] and 'content' in response_data['choices'][0]['message']:
                            ai_content = response_data['choices'][0]['message']['content']
                            logger.info(f"成功从OpenAI格式提取AI回复内容，长度: {len(ai_content)}")
                        # Claude可能的格式
                        elif 'delta' in response_data['choices'][0] and 'content' in response_data['choices'][0]['delta']:
                            ai_content = response_data['choices'][0]['delta']['content']
                            logger.info(f"成功从delta格式提取AI回复内容，长度: {len(ai_content)}")
                        # 直接包含content的格式
                        elif 'content' in response_data['choices'][0]:
                            ai_content = response_data['choices'][0]['content']
                            logger.info(f"成功从直接content格式提取AI回复内容，长度: {len(ai_content)}")
                        # 直接是字符串格式
                        elif isinstance(response_data['choices'][0], str):
                            ai_content = response_data['choices'][0]
                            logger.info(f"成功从字符串格式提取AI回复内容，长度: {len(ai_content)}")
                        else:
                            logger.warning("API响应中没有找到content字段")
                            logger.warning(f"响应结构: {json.dumps(response_data['choices'][0])[:500]}")
                    # 尝试Claude特殊格式 - role/content结构
                    elif 'role' in response_data and 'content' in response_data:
                        # 检查content是否为数组
                        if isinstance(response_data['content'], list):
                            # 提取所有text类型的内容
                            text_contents = []
                            for item in response_data['content']:
                                if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item:
                                    text_contents.append(item['text'])
                            
                            if text_contents:
                                ai_content = ' '.join(text_contents)
                                logger.info(f"成功从content数组中提取文本内容，长度: {len(ai_content)}")
                            else:
                                logger.warning("无法从content数组中提取文本内容")
                                ai_content = json.dumps(response_data['content'])
                        else:
                            ai_content = str(response_data['content'])
                            logger.info(f"成功从role/content结构提取AI回复内容，长度: {len(ai_content)}")
                    # 尝试顶层content字段
                    elif 'content' in response_data:
                        if isinstance(response_data['content'], list):
                            # 处理content数组
                            text_contents = []
                            for item in response_data['content']:
                                if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item:
                                    text_contents.append(item['text'])
                                elif isinstance(item, str):
                                    text_contents.append(item)
                            
                            if text_contents:
                                ai_content = ' '.join(text_contents)
                                logger.info(f"成功从content数组中提取文本内容，长度: {len(ai_content)}")
                            else:
                                ai_content = json.dumps(response_data['content'])
                        else:
                            ai_content = str(response_data['content'])
                            logger.info(f"成功从顶层content字段提取AI回复内容，长度: {len(ai_content)}")
                    # 如果是字符串数组，尝试拼接
                    elif isinstance(response_data, list) and all(isinstance(item, str) for item in response_data):
                        ai_content = ''.join(response_data)
                        logger.info(f"成功从字符串数组提取AI回复内容，长度: {len(ai_content)}")
                    else:
                        logger.warning("无法从API响应中提取内容")
                        logger.warning(f"完整响应: {json.dumps(response_data)[:1000]}")
                        
                        # 尝试将整个响应作为字符串处理
                        try:
                            ai_content = "无法正确解析API响应。原始响应: " + json.dumps(response_data)[:500]
                        except:
                            ai_content = "无法正确解析API响应，且无法转换为字符串。"
                    
                    # 保存AI回复
                    ai_message = Message.objects.create(
                        conversation=conversation,
                        content=ai_content,
                        is_user=False,
                        model_used=model
                    )
                    
                    # 更新会话时间
                    conversation.save()
                    
                    # 构建响应数据
                    response_data = {
                        'success': True,
                        'message': "文件上传成功",
                        'user_message_id': user_message.id,
                        'ai_message_id': ai_message.id,
                        'ai_response': ai_content,
                        'new_conversation_id': conversation.id if is_new_conversation else None
                    }
                    
                    logger.info(f"返回给前端的响应: {json.dumps(response_data)[:500]}")
                    return JsonResponse(response_data)
                    
                except Exception as e:
                    logger.error(f"处理AI回复时出错: {str(e)}")
                    logger.error(traceback.format_exc())
                    
                    # 尝试获取原始响应内容
                    try:
                        raw_response = response.text[:1000]
                        logger.error(f"原始响应内容: {raw_response}")
                    except:
                        logger.error("无法获取原始响应内容")
                    
                    # 发生错误，但文件仍然上传成功，保存没有AI回复的消息
                    return JsonResponse({
                        'success': True,
                        'message': "文件已上传，但处理AI回复时出错",
                        'user_message_id': user_message.id,
                        'new_conversation_id': conversation.id if is_new_conversation else None
                    })
            
            else:
                # API请求失败
                error_message = "API请求失败"
                try:
                    error_data = response.json()
                    if 'error' in error_data:
                        error_message = error_data['error'].get('message', "API请求失败")
                except:
                    error_message = response.text[:200] if response.text else "API请求失败"
                
                logger.error(f"API请求失败: {error_message}")
                logger.error(f"完整响应: {response.text}")
                
                # 删除已创建的消息和对话（如果是新对话）
                user_message.delete()
                if is_new_conversation:
                    conversation.delete()
                
                return JsonResponse({
                    'success': False,
                    'message': f"API请求失败: {error_message}"
                })
                
        except Exception as e:
            logger.error(f"文件上传请求出错: {str(e)}")
            logger.error(traceback.format_exc())
            
            # 删除已创建的消息和对话（如果是新对话）
            user_message.delete()
            if is_new_conversation:
                conversation.delete()
            
            return JsonResponse({
                'success': False,
                'message': f"文件上传失败: {str(e)}"
            })
            
    except Exception as e:
        logger.error(f"处理文件上传请求时出错: {str(e)}")
        logger.error(traceback.format_exc())
        
        return JsonResponse({
            'success': False,
            'message': f"处理请求时出错: {str(e)}"
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def chat_api(request):
    """处理聊天请求并转发到AI服务"""
    conversation = None # Initialize for finally block
    generation_id = None # Initialize for finally block
    generation_id_str = None # Initialize for finally block
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
        # --- Generate Generation ID, store it, and clear stop state ---
        generation_id = uuid.uuid4()
        generation_id_str = str(generation_id) # Use string representation for consistency
        conversation.current_generation_id = generation_id # Store UUID object
        conversation.save(update_fields=['current_generation_id', 'updated_at']) # Also update 'updated_at'
        logger.info(f"API chat_api: Starting generation with ID {generation_id_str} for conversation {conversation.id}, saved to model.")
        # Clear any previous stop request before starting
        set_stop_requested_sync(conversation.id, False) # Clear flag and generation_id_to_stop
        logger.info(f"API chat_api: Cleared stop request flag for conversation {conversation.id} before sending request.")
        # --- END ---

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

        # --- MODIFIED: Check stop state using NEW state utils and generation_id before sending ---
        stop_state = get_stop_requested_sync(conversation.id) # Use new function
        # Compare generation_id as string
        if stop_state['requested'] and stop_state.get('generation_id_to_stop') == generation_id_str: # Use .get() for safety and string comparison
            logger.warning(f"API chat_api: 检测到会话 {conversation.id} 针对生成 {generation_id_str} 的停止请求 (发送前)，取消生成。StopState: {stop_state}")
            # Resetting state is handled in finally block
            # Clear generation ID handled in finally block
            # conversation.current_generation_id = None # Handled in finally
            # conversation.save(update_fields=['current_generation_id']) # Handled in finally
            return JsonResponse({
                'success': True, # Return success=True but status=cancelled
                'status': 'cancelled',
                'message': "生成已被用户终止 (发送前检测到)",
                'user_message_id': user_message.id # Include user message ID
            })
        # --- END MODIFIED ---

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
                        # --- Check stop state using precise generation_id in stream loop ---
                        stop_state = get_stop_requested_sync(conversation.id)
                        if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str: # Use string comparison
                            logger.warning(f"API chat_api: 检测到会话 {conversation.id} 停止请求 (流处理中，匹配 GenID: {generation_id_str})，停止处理。StopState: {stop_state}")
                            # Resetting state is handled in finally block
                            # Clear generation ID handled in finally block
                            return JsonResponse({ # Correctly indented
                                'success': True, # Return success=True but status=cancelled
                                'status': 'cancelled',
                                'message': "生成已被用户终止 (流处理中检测到)",
                                'user_message_id': user_message.id # Include user message ID
                            })
                        # --- END MODIFIED ---

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

                    # 如果没有内容，在尝试非流式方式前先检查终止标志
                    if not full_content:
                        # --- Check stop state using precise generation_id before fallback ---
                        stop_state = get_stop_requested_sync(conversation.id)
                        if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str: # Use string comparison
                            logger.warning(f"API chat_api: 检测到会话 {conversation.id} 停止请求 (回退前，匹配 GenID: {generation_id_str})，不进行非流式重试。StopState: {stop_state}")
                            # Resetting state is handled in finally block
                            # Clear generation ID handled in finally block
                            return JsonResponse({ # Correctly indented
                                'success': True, # Return success=True but status=cancelled
                                'status': 'cancelled',
                                'message': "生成已被用户终止 (回退前检测到)",
                                'user_message_id': user_message.id # Include user message ID
                            })
                        # --- END MODIFIED ---

                        logger.warning("流式响应未提取到内容，尝试非流式方式")
                        # 添加重试计数和延迟
                        retry_count = 0
                        max_retries = 2
                        
                        while not full_content and retry_count < max_retries:
                            retry_count += 1
                            logger.info(f"非流式重试 #{retry_count}/{max_retries}")

                            # --- Check stop state using precise generation_id in fallback loop ---
                            stop_state = get_stop_requested_sync(conversation.id)
                            if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str: # Use string comparison
                                logger.warning(f"API chat_api: 检测到会话 {conversation.id} 停止请求 (回退循环中，匹配 GenID: {generation_id_str})，停止重试。StopState: {stop_state}")
                                # Resetting state is handled in finally block
                                # Clear generation ID handled in finally block
                                return JsonResponse({ # Correctly indented
                                    'success': True, # Return success=True but status=cancelled
                                    'status': 'cancelled',
                                    'message': "生成已被用户终止 (回退循环中检测到)",
                                    'user_message_id': user_message.id # Include user message ID
                                })
                            # --- END MODIFIED ---

                            # 短暂延迟后重试
                            time.sleep(1)
                            
                            # 修改请求为非流式
                            request_data['stream'] = False
                            
                            try:
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
                                            break
                            except Exception as retry_error:
                                logger.error(f"非流式重试 #{retry_count} 出错: {str(retry_error)}")
                                # 继续循环尝试下一次重试
                except Exception as e:
                    logger.error(f"处理响应时出错: {str(e)}")
                    logger.error(f"详细错误: {traceback.format_exc()}")

                # --- Final stop check using precise generation_id before saving ---
                stop_state = get_stop_requested_sync(conversation.id)
                if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str: # Use string comparison
                    logger.warning(f"API chat_api: 检测到会话 {conversation.id} 停止请求 (保存前，匹配 GenID: {generation_id_str})，不保存回复。StopState: {stop_state}")
                    # Resetting state is handled in finally block
                    # Clear generation ID handled in finally block
                    return JsonResponse({ # Correctly indented
                        'success': True, # Return success=True but status=cancelled
                        'status': 'cancelled',
                        'message': "生成已被用户终止 (最终检查)",
                        'user_message_id': user_message.id # Include user message ID
                    })
                # --- END MODIFIED ---

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
                    
                    # 成功保存，不需要重置标志 (只有取消时才重置)
                    # update_stop_flag(conversation.id, False) # REMOVED
                    logger.info(f"API View (chat_api - success): AI message saved. Flag reset is handled only on cancellation.")
                    return JsonResponse(response_data)
                else:
                    logger.error("未能从响应中提取内容")
                    # 返回原始响应以便调试
                    try:
                        response_text = str(response.text)[:500]  # 截取前500个字符避免过大
                    except:
                        response_text = "无法获取响应文本"
                    
                    # 提取内容失败，不需要重置标志 (只有取消时才重置)
                    # update_stop_flag(conversation.id, False) # REMOVED
                    logger.info(f"API View (chat_api - content extraction failed): Flag reset is handled only on cancellation.")
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
                
                # API 404 错误，不需要重置标志 (只有取消时才重置)
                # update_stop_flag(conversation.id, False) # REMOVED
                logger.info(f"API View (chat_api - 404 error): Flag reset is handled only on cancellation.")
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
                
                # 其他 API 错误，不需要重置标志 (只有取消时才重置)
                # update_stop_flag(conversation.id, False) # REMOVED
                logger.info(f"API View (chat_api - other API error): Flag reset is handled only on cancellation.")
                return JsonResponse({
                    'success': False,
                    'message': f"API请求失败: {error_message}",
                    'user_message_id': user_message.id
                }, status=500)
        except requests.exceptions.RequestException as e:
            logger.error(f"请求异常: {str(e)}")
            # 请求异常，不需要重置标志 (只有取消时才重置)
            # if 'conversation' in locals():
            #     update_stop_flag(conversation.id, False)
            #     logger.info(f"API View (chat_api - request exception): 重置会话 {conversation.id} 的终止标志为 False")
            # else:
            #      logger.warning("API View (chat_api - request exception): Conversation not defined, cannot reset flag.")
            logger.info(f"API View (chat_api - request exception): Flag reset is handled only on cancellation.")
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
        
        # 通用异常，不需要重置标志 (只有取消时才重置)
        # if 'conversation' in locals():
        #     update_stop_flag(conversation.id, False)
        #     logger.info(f"API View (chat_api - general exception): 重置会话 {conversation.id} 的终止标志为 False")
        # else:
        #     logger.warning("API View (chat_api - general exception): Conversation not defined, cannot reset flag.")
        # 
        # logger.info(f"API View (chat_api - general exception): Flag reset is handled only on cancellation.")

        # Clear generation ID on error
        if 'conversation' in locals() and conversation and 'generation_id' in locals() and conversation.current_generation_id == generation_id:
             conversation.current_generation_id = None
             conversation.save(update_fields=['current_generation_id'])
             logger.info(f"API chat_api: Cleared generation ID {generation_id} for conversation {conversation.id} in exception handler.")
        
        # Ensure stop flag is cleared on exception
        if 'conversation' in locals() and conversation:
            set_stop_requested_sync(conversation.id, False)
            logger.info(f"API chat_api: Cleared stop request flag for conversation {conversation.id} in general exception handler.")

        return JsonResponse({
            'success': False,
            'message': f"处理请求时出错: {str(e)}",
            'user_message_id': user_message_id
        }, status=500)
    finally:
        # Ensure generation ID is cleared from the model if this process was the one that set it
        if 'conversation' in locals() and conversation and 'generation_id' in locals(): # Check if generation_id was defined
            try:
                # Compare UUIDs directly
                if conversation.current_generation_id == generation_id:
                    conversation.current_generation_id = None
                    conversation.save(update_fields=['current_generation_id'])
                    logger.info(f"API chat_api: Cleared generation ID {generation_id_str} for conversation {conversation.id} in finally block.")
                else:
                    logger.info(f"API chat_api: Generation ID {generation_id_str} no longer current for conversation {conversation.id} in finally block (current is {conversation.current_generation_id}), not clearing.")
            except Exception as refresh_err:
                 logger.error(f"API chat_api: Error checking/clearing generation ID for conversation {conversation.id} in finally block: {refresh_err}")

        # Conditionally reset global stop state
        if 'conversation' in locals() and conversation and 'generation_id_str' in locals(): # Check for generation_id_str
            try:
                stop_state = get_stop_requested_sync(conversation.id)
                # Compare using generation_id_str
                if stop_state['requested'] and stop_state.get('generation_id_to_stop') == generation_id_str:
                    set_stop_requested_sync(conversation.id, False) # Clear flag and generation_id_to_stop
                    logger.info(f"API chat_api: Reset global stop state for conversation {conversation.id} because it targeted this generation ({generation_id_str}).")
                elif stop_state['requested']:
                     logger.info(f"API chat_api: Global stop state for conversation {conversation.id} is requested, but targeted ID ({stop_state.get('generation_id_to_stop')}) does not match this generation ({generation_id_str}). Not resetting.")
            except Exception as stop_reset_err:
                logger.error(f"API chat_api: Error checking/resetting global stop state for conversation {conversation.id} in finally block: {stop_reset_err}")

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
    conversation = None # Initialize conversation to None for finally block
    generation_id = None # Initialize generation_id to None for finally block
    generation_id_str = None # Initialize for finally block
    # --- Start of the main try block for the entire function ---
    try:
        data = json.loads(request.body)
        message_id = data.get('message_id') # ID of the *user* message
        model_id = data.get('model_id')

        if not message_id or not model_id:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数 (message_id of user message, model_id)"
            }, status=400)

        if isinstance(message_id, str) and message_id.startswith('temp-'):
            return JsonResponse({
                'success': False,
                'message': "无法基于临时消息重新生成回复"
            }, status=400)

        # --- Inner try block to fetch the message ---
        try:
            user_message = Message.objects.get(id=message_id)
            if user_message.conversation.user != request.user:
                return JsonResponse({'success': False, 'message': "无权访问此消息"}, status=403)
            if not user_message.is_user:
                return JsonResponse({'success': False, 'message': "只能基于用户消息重新生成"}, status=400)

            model = get_object_or_404(AIModel, id=model_id)
            conversation = user_message.conversation

            # --- Generate Generation ID, store it, clear stop state, and send start signal ---
            generation_id = uuid.uuid4()
            generation_id_str = str(generation_id) # Use string representation for consistency
            conversation.current_generation_id = generation_id # Store UUID object
            conversation.save(update_fields=['current_generation_id', 'updated_at'])
            logger.info(f"API regenerate_message_api: Starting generation with ID {generation_id_str} for conversation {conversation.id}, saved to model.")

            # Clear any previous stop request before starting
            set_stop_requested_sync(conversation.id, False) # Clear flag and generation_id_to_stop
            logger.info(f"API regenerate_message_api: Cleared stop request flag for conversation {conversation.id} before sending request.")

            # --- MODIFIED: Send generation_started signal ---
            channel_layer = get_channel_layer()
            conversation_group_name = f'chat_{conversation.id}'
            async_to_sync(channel_layer.group_send)(
                conversation_group_name,
                {
                    'type': 'generation_start', # Use 'generation_start' consistently
                    'generation_id': generation_id_str, # Send the generated ID
                    # Include the user message ID that triggered the regeneration
                    # The frontend might use this to associate the loading indicator
                    'temp_id': str(user_message.id) # Use user message ID as temp_id for regeneration
                }
            )
            logger.info(f"API regenerate_message_api: Sent generation_started signal for GenID {generation_id_str} to group {conversation_group_name}")
            # --- END MODIFIED ---

            # --- Prepare request data ---
            history_messages = Message.objects.filter(
                conversation=conversation,
                timestamp__lte=user_message.timestamp
            ).order_by('timestamp')

            if history_messages.count() > model.max_history_messages:
                history_messages = history_messages[history_messages.count() - model.max_history_messages:]

            messages = []
            for msg in history_messages:
                role = "user" if msg.is_user else "assistant"
                messages.append({"role": role, "content": msg.content})

            request_data = {
                "model": model.model_name,
                "messages": messages,
                "stream": True,
                **model.default_params
            }
            log_data = request_data.copy()
            logger.info(f"重新生成回复请求数据: {json.dumps(log_data)}")

            api_url = ensure_valid_api_url(model.provider.base_url, "/v1/chat/completions")
            api_key = model.provider.api_key

            # --- REMOVED: Pre-send stop check (check happens before saving now) ---
            # stop_state = get_stop_requested_sync(conversation.id)
            # if stop_state: ...

            # --- Send request and process response ---
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
            logger.info(f"重新生成回复: 用户消息ID={message_id}, 模型={model.model_name}, API URL={api_url}")

            response = requests.post(api_url, json=request_data, headers=headers, stream=True, timeout=60)

            if response.status_code == 200:
                full_content = ""
                # --- Inner try for stream processing ---
                try:
                    logger.info("开始处理流式响应 (重新生成)")
                    for chunk in response.iter_lines():
                        # --- Check stop state using precise generation_id in stream loop ---
                        stop_state = get_stop_requested_sync(conversation.id)
                        # Use generation_id_str (string comparison)
                        if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str:
                            logger.warning(f"API regenerate_message_api: 检测到会话 {conversation.id} 停止请求 (流处理中，匹配 GenID: {generation_id_str})，停止处理。StopState: {stop_state}")
                            # Resetting state is handled in finally block
                            # Clear generation ID handled in finally block
                            return JsonResponse({
                                'success': True, # Return success=True but status=cancelled
                                'status': 'cancelled', # Indicate cancellation
                                'message': "生成已被用户终止 (流处理中检测到)",
                                'user_message_id': user_message.id # Include user message ID
                            }) # Correctly indented
                        # --- END MODIFIED ---

                        if not chunk: continue

                        if chunk.startswith(b'data: '):
                            chunk_data = chunk[6:].decode('utf-8')
                            if chunk_data.strip() == '[DONE]': continue
                            try:
                                chunk_json = json.loads(chunk_data)
                                content_piece = None
                                if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
                                    choice = chunk_json['choices'][0]
                                    if 'delta' in choice and 'content' in choice['delta'] and choice['delta']['content']:
                                        content_piece = choice['delta']['content']
                                    elif 'message' in choice and 'content' in choice['message'] and choice['message']['content']:
                                        content_piece = choice['message']['content']
                                elif 'content' in chunk_json and isinstance(chunk_json['content'], list):
                                    text_contents = [item['text'] for item in chunk_json['content'] if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item]
                                    if text_contents: content_piece = ' '.join(text_contents)

                                if content_piece: full_content += content_piece
                            except json.JSONDecodeError as je: logger.error(f"JSON解析错误 (重新生成): {je}, 数据: {chunk_data}")
                            except Exception as e_inner: logger.error(f"处理流式响应片段时出错 (重新生成): {str(e_inner)}")
                    logger.info(f"流式响应处理完成 (重新生成)，累积内容长度: {len(full_content)}")

                    # --- Fallback to non-stream if no content ---
                    if not full_content:
                        # --- Check stop state using precise generation_id before fallback ---
                        stop_state = get_stop_requested_sync(conversation.id)
                        # Use generation_id_str (string comparison)
                        if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str:
                            logger.warning(f"API regenerate_message_api: 检测到会话 {conversation.id} 停止请求 (回退前，匹配 GenID: {generation_id_str})，不进行非流式重试。StopState: {stop_state}")
                            # Resetting state is handled in finally block
                            # Clear generation ID handled in finally block
                            return JsonResponse({
                                'success': True, # Return success=True but status=cancelled
                                'status': 'cancelled', # Indicate cancellation
                                'message': "生成已被用户终止 (回退前检测到)",
                                'user_message_id': user_message.id # Include user message ID
                            }) # Correctly indented
                        # --- END MODIFIED ---

                        logger.warning("流式响应未提取到内容 (重新生成)，尝试非流式方式")
                        retry_count = 0
                        max_retries = 2
                        while not full_content and retry_count < max_retries:
                            retry_count += 1
                            logger.info(f"重新生成非流式重试 #{retry_count}/{max_retries}")
                            # --- Check stop state using precise generation_id in fallback loop ---
                            stop_state = get_stop_requested_sync(conversation.id)
                            # Use generation_id_str (string comparison)
                            if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str:
                                logger.warning(f"API regenerate_message_api: 检测到会话 {conversation.id} 停止请求 (回退循环中，匹配 GenID: {generation_id_str})，停止重试。StopState: {stop_state}")
                                # Resetting state is handled in finally block
                                # Clear generation ID handled in finally block
                                return JsonResponse({
                                    'success': True, # Return success=True but status=cancelled
                                    'status': 'cancelled', # Indicate cancellation
                                    'message': "生成已被用户终止 (回退循环中检测到)",
                                    'user_message_id': user_message.id # Include user message ID
                                }) # Correctly indented
                            # --- END MODIFIED ---
                            # Correctly dedented lines below, still inside the while loop
                            time.sleep(1)
                            request_data['stream'] = False
                            try:
                                response = requests.post(api_url, json=request_data, headers=headers, timeout=60)
                                if response.status_code == 200:
                                    response_data = response.json()
                                    extracted_content = ""
                                    if 'choices' in response_data and len(response_data['choices']) > 0:
                                        choice = response_data['choices'][0]
                                        if 'message' in choice and 'content' in choice['message']:
                                            extracted_content = choice['message']['content']
                                    elif 'content' in response_data:
                                        if isinstance(response_data['content'], list):
                                            text_contents = [item['text'] for item in response_data['content'] if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item]
                                            if text_contents: extracted_content = ' '.join(text_contents)
                                        else: extracted_content = str(response_data['content'])
                                    if extracted_content:
                                        full_content = extracted_content
                                        logger.info(f"非流式方式成功提取内容 (重新生成)，长度: {len(full_content)}")
                                        break
                                else: logger.error(f"非流式重试请求失败，状态码: {response.status_code}")
                            except Exception as retry_error: logger.error(f"重新生成非流式重试 #{retry_count} 出错: {str(retry_error)}")
                        if not full_content: logger.error("所有非流式重试也未能提取内容 (重新生成)")
                # --- End inner try for stream processing ---
                except Exception as e_stream:
                    logger.error(f"处理响应时出错 (重新生成): {str(e_stream)}")
                    logger.error(f"详细错误: {traceback.format_exc()}")
                    full_content = "" # Ensure content is empty on error

                # --- Final stop check using precise generation_id before saving ---
                stop_state = get_stop_requested_sync(conversation.id)
                # Use generation_id_str (string comparison)
                if stop_state['requested'] and stop_state['generation_id_to_stop'] == generation_id_str:
                    logger.warning(f"API regenerate_message_api: 检测到会话 {conversation.id} 停止请求 (保存前，匹配 GenID: {generation_id_str})，不保存回复。StopState: {stop_state}")
                    # Resetting state is handled in finally block
                    # Clear generation ID handled in finally block
                    return JsonResponse({
                        'success': True, # Return success=True but status=cancelled
                        'status': 'cancelled', # Indicate cancellation
                        'message': "生成已被用户终止 (保存前检测到)",
                        'user_message_id': user_message.id # Include user message ID
                    }) # Correctly indented
                # --- END MODIFIED ---

                # --- Save and return if content exists ---
                if full_content:
                    # Delete previous AI messages after the user message being regenerated from
                    Message.objects.filter(conversation=conversation, timestamp__gt=user_message.timestamp).delete()
                    # Save the new AI message
                    ai_message = Message.objects.create(
                        conversation=conversation, content=full_content, is_user=False, model_used=model
                    )
                    conversation.save() # Update conversation timestamp
                    logger.info(f"API View (regenerate - success): AI message {ai_message.id} saved for conversation {conversation.id}.")

                    # --- Send WebSocket Events ---
                    channel_layer = get_channel_layer()
                    conversation_group_name = f'chat_{conversation.id}'

                    # 1. Send the new AI message content
                    async_to_sync(channel_layer.group_send)(
                        conversation_group_name,
                        {
                            'type': 'chat_message', # Consumer handler: chat_message
                            'message_id': ai_message.id,
                            'conversation_id': conversation.id,
                            'message': ai_message.content, # Use 'message' key for content
                            'is_user': False,
                            'timestamp': ai_message.timestamp.isoformat(),
                            'model_name': ai_message.model_used.display_name if ai_message.model_used else None,
                            'user_message_id': user_message.id, # ID of the message being regenerated from
                            'generation_id': generation_id_str # Use string representation
                        }
                    )
                    logger.info(f"API View (regenerate - success): Sent WebSocket 'chat_message' for new AI message {ai_message.id} (GenID: {generation_id_str}) to group {conversation_group_name}")

                    # 2. Send the generation_end signal
                    async_to_sync(channel_layer.group_send)(
                        conversation_group_name,
                        {
                            'type': 'generation_end', # Consumer handler: generation_end
                            'generation_id': generation_id_str, # Use string representation
                            'status': 'completed'
                        }
                    )
                    logger.info(f"API View (regenerate - success): Sent 'generation_end' signal (completed) for GenID {generation_id_str} to group {conversation_group_name}")
                    # --- End Send WebSocket Events ---

                    # Return success response to the HTTP request
                    return JsonResponse({
                        'success': True,
                        'message_id': ai_message.id,
                        'content': full_content,
                        'timestamp': ai_message.timestamp.isoformat(),
                        'user_message_id': user_message.id, # Include user message ID
                        'generation_id': generation_id_str # Also include generation_id in HTTP response
                    })
                else:
                    logger.error("未能从响应中提取内容 (重新生成)")
                    try: response_text = str(response.text)[:500]
                    except: response_text = "无法获取最终响应文本"
                    logger.info(f"API View (regenerate - content extraction failed): Flag reset is handled only on cancellation.")
                    # --- Send generation_end signal with 'failed' status ---
                    if 'conversation' in locals() and conversation and 'generation_id_str' in locals():
                        channel_layer = get_channel_layer()
                        conversation_group_name = f'chat_{conversation.id}'
                        async_to_sync(channel_layer.group_send)(
                            conversation_group_name,
                            {
                                'type': 'generation_end',
                                'generation_id': generation_id_str,
                                'status': 'failed' # Send failed status
                            }
                        )
                        logger.info(f"API View (regenerate - content extraction failed): Sent 'generation_end' signal (failed) for GenID {generation_id_str} to group {conversation_group_name}")
                    # --- End Send ---
                    return JsonResponse({
                        'success': False,
                        'message': f"无法从API响应中提取内容 (重新生成)。最终响应片段：{response_text}",
                        'user_message_id': user_message.id # Include user message ID
                    }, status=500)
            # --- Handle non-200 status code ---
            else:
                logger.error(f"重新生成回复API请求失败: {response.status_code}")
                try:
                    error_detail = response.json()
                    error_message = error_detail.get('error', {}).get('message', '未知错误')
                except: error_message = response.text[:200] if response.text else '未知错误'
                logger.info(f"API View (regenerate - API error): Flag reset is handled only on cancellation.")
                # --- Send generation_end signal with 'failed' status ---
                if 'conversation' in locals() and conversation and 'generation_id_str' in locals():
                    channel_layer = get_channel_layer()
                    conversation_group_name = f'chat_{conversation.id}'
                    async_to_sync(channel_layer.group_send)(
                        conversation_group_name,
                        {
                            'type': 'generation_end',
                            'generation_id': generation_id_str,
                            'status': 'failed' # Send failed status
                        }
                    )
                    logger.info(f"API View (regenerate - API error): Sent 'generation_end' signal (failed) for GenID {generation_id_str} to group {conversation_group_name}")
                # --- End Send ---
                return JsonResponse({
                    'success': False,
                    'message': f"无法从API响应中提取内容 (重新生成): {response.status_code} - {error_message}",
                    'user_message_id': user_message.id # Include user message ID
                }, status=response.status_code)
        # --- End inner try block for fetching message ---
        except Message.DoesNotExist:
            return JsonResponse({'success': False, 'message': "消息不存在"}, status=404)
    # --- End of the main try block ---
    except Exception as e:
        # Log the error
        logger.error(f"重新生成回复时发生意外错误: {str(e)}")
        logger.error(f"详细错误追踪: {traceback.format_exc()}")
        # Log that flag reset is handled only on cancellation
        logger.info(f"API View (regenerate - general exception handler): Flag reset is handled only on cancellation.")
        # Clear generation ID handled in finally block
        # --- Send generation_end signal with 'failed' status ---
        if 'conversation' in locals() and conversation and 'generation_id_str' in locals():
            channel_layer = get_channel_layer()
            conversation_group_name = f'chat_{conversation.id}'
            async_to_sync(channel_layer.group_send)(
                conversation_group_name,
                {
                    'type': 'generation_end',
                    'generation_id': generation_id_str,
                    'status': 'failed' # Send failed status
                }
            )
            logger.info(f"API View (regenerate - general exception): Sent 'generation_end' signal (failed) for GenID {generation_id_str} to group {conversation_group_name}")
        # --- End Send ---

        # Return JSON response for the exception
        return JsonResponse({
            'success': False,
            'message': f"处理重新生成请求时发生内部错误: {str(e)}"
        }, status=500)
    finally:
        # Ensure generation ID is cleared from the model if this process was the one that set it
        # Check if 'conversation' and 'generation_id' were defined in the try block
        if 'conversation' in locals() and conversation and 'generation_id' in locals():
            # Refresh conversation object to get the latest state before checking/clearing
            try:
                # Use refresh_from_db only if the object might be stale (e.g., long process)
                # For simplicity here, let's assume the 'conversation' object is up-to-date enough
                # conversation.refresh_from_db(fields=['current_generation_id']) # Optional refresh
                if conversation.current_generation_id == generation_id: # Compare UUIDs directly
                    conversation.current_generation_id = None
                    conversation.save(update_fields=['current_generation_id'])
                    logger.info(f"API regenerate_message_api: Cleared generation ID {generation_id_str} for conversation {conversation.id} in finally block.")
                else:
                    # Log if the ID was already cleared or changed by another process
                    logger.info(f"API regenerate_message_api: Generation ID {generation_id_str} no longer current for conversation {conversation.id} in finally block (current is {conversation.current_generation_id}), not clearing.")
            except Exception as refresh_err:
                 logger.error(f"API regenerate_message_api: Error checking/clearing generation ID for conversation {conversation.id} in finally block: {refresh_err}")

        # --- Conditionally reset global stop state ---
        # Only reset the global stop state if this specific generation was the one targeted.
        if 'conversation' in locals() and conversation and 'generation_id_str' in locals(): # Check for generation_id_str
            try:
                stop_state = get_stop_requested_sync(conversation.id)
                # Compare using generation_id_str
                if stop_state['requested'] and stop_state.get('generation_id_to_stop') == generation_id_str:
                    set_stop_requested_sync(conversation.id, False) # Clear flag and generation_id_to_stop
                    logger.info(f"API regenerate_message_api: Reset global stop state for conversation {conversation.id} because it targeted this generation ({generation_id_str}).")
                # Optional: Log if stop was requested but for a different ID, or not requested at all
                elif stop_state['requested']:
                     logger.info(f"API regenerate_message_api: Global stop state for conversation {conversation.id} is requested, but targeted ID ({stop_state.get('generation_id_to_stop')}) does not match this generation ({generation_id_str}). Not resetting.")
                # else: logger.info(f"API regenerate_message_api: Global stop state for conversation {conversation.id} was not requested. Not resetting.")
            except Exception as stop_reset_err:
                logger.error(f"API regenerate_message_api: Error checking/resetting global stop state for conversation {conversation.id} in finally block: {stop_reset_err}")
        # --- End conditional reset ---

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

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def stop_generation_api(request):
    """处理终止AI生成回复的请求"""
    try:
        data = json.loads(request.body)
        conversation_id = data.get('conversation_id')
        
        if not conversation_id:
            return JsonResponse({
                'success': False,
                'message': "缺少会话ID"
            }, status=400)
        
        # 验证会话归属
        try:
            conversation = Conversation.objects.get(id=conversation_id, user=request.user)
        except Conversation.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': "会话不存在或无权访问"
            }, status=404)

        # --- Get current generation ID and set stop state ---
        current_gen_id = conversation.current_generation_id
        if current_gen_id:
            logger.info(f"API Stop: Requesting stop for conversation {conversation_id}, targeting generation ID {current_gen_id}")
            set_stop_requested_sync(conversation.id, True, generation_id_to_stop=str(current_gen_id))
            stop_message = f"已发送终止请求 (目标 Generation ID: {current_gen_id})"
        else:
            # --- MODIFIED: Do not set a general flag, just log and inform user ---
            logger.warning(f"API Stop: Conversation {conversation_id} has no active generation ID (current_generation_id is None). No specific stop request sent.")
            # set_stop_requested_sync(conversation.id, True) # REMOVED general flag setting
            stop_message = "当前没有正在进行的生成任务，未发送特定终止请求"
            # --- END MODIFIED ---
        # --- END Set stop state ---

        # 获取通道层并发送终止消息到WebSocket组
        channel_layer = get_channel_layer()
        conversation_group_name = f'chat_{conversation_id}'
        
        # 发送终止生成的消息到WebSocket组
        async_to_sync(channel_layer.group_send)(
            conversation_group_name,
            {
                'type': 'generation_stopped',
                'message': '用户终止了生成'
            }
        )
        
        logger.info(f"用户 {request.user.username} 终止了会话 {conversation_id} 的AI回复生成 (通过API)")
        
        # --- Add logic to delete subsequent AI messages ---
        try:
            # Find the last user message in this conversation
            last_user_message = Message.objects.filter(
                conversation=conversation,
                is_user=True
            ).order_by('-timestamp').first()

            if last_user_message:
                # Delete AI messages created after the last user message
                messages_to_delete = Message.objects.filter(
                    conversation=conversation,
                    is_user=False,
                    timestamp__gt=last_user_message.timestamp
                )
                deleted_count = messages_to_delete.count()
                if deleted_count > 0:
                    messages_to_delete.delete()
                    logger.info(f"API Stop: 已删除会话 {conversation_id} 中最后一条用户消息后的 {deleted_count} 条AI回复")
                else:
                    logger.info(f"API Stop: 会话 {conversation_id} 中最后一条用户消息后没有需要删除的AI回复")
            else:
                # Handle case where there might be no user messages yet (e.g., stopping very early)
                # Delete ALL AI messages in this case? Or do nothing? Let's do nothing for now.
                logger.warning(f"API Stop: 会话 {conversation_id} 中未找到用户消息，无法删除后续AI回复")

        except Exception as delete_err:
            logger.error(f"API Stop: 删除后续AI消息时出错: {str(delete_err)}")
            # Log the error but still return success for the stop request itself
        # --- End deletion logic ---

        # Return specific message based on whether an ID was targeted
        return JsonResponse({
            'success': True,
            'message': stop_message # Return the potentially modified message
        })
    except Exception as e:
        logger.error(f"处理终止生成请求时出错: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f"处理请求时出错: {str(e)}"
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def get_models_api(request):
    """获取和管理AI模型 (管理员可管理，普通用户可查看活跃模型)"""
    # 检查用户是否为管理员
    is_admin = is_user_admin(request.user)

    if request.method == 'GET':
        # 获取单个模型详情 (对所有登录用户开放)
        model_id = request.GET.get('id')
        if model_id:
            try:
                model = get_object_or_404(AIModel, id=model_id)
                # Optionally restrict if model or provider is inactive for non-admins
                if not is_admin and (not model.is_active or not model.provider.is_active):
                     return JsonResponse({'success': False, 'message': "模型不可用"}, status=404)

                return JsonResponse({
                    'models': [{ # Keep structure consistent with list view
                        'id': model.id,
                        'provider_id': model.provider.id,
                        'provider_name': model.provider.name, # Add provider name
                        'model_name': model.model_name,
                        'display_name': model.display_name,
                        'max_context': model.max_context,
                        'max_history_messages': model.max_history_messages,
                        'is_active': model.is_active,
                        'default_params': model.default_params, # Include default params
                    }]
                })
            except Exception as e:
                logger.error(f"获取模型详情失败: {e}")
                return JsonResponse({
                    'success': False,
                    'message': f"获取模型详情失败: {str(e)}"
                }, status=400)

        # 获取所有模型列表
        if is_admin:
            # 管理员可以看到所有模型
            models = AIModel.objects.all().order_by('provider__name', 'display_name')
        else:
            # 普通用户只能看到活跃的模型
            models = AIModel.objects.filter(is_active=True, provider__is_active=True).order_by('provider__name', 'display_name')

        models_data = []
        for model in models:
            model_data = {
                'id': model.id,
                'model_name': model.model_name,
                'display_name': model.display_name,
                'max_context': model.max_context,
                'max_history_messages': model.max_history_messages,
                'is_active': model.is_active, # Include active status
                'default_params': model.default_params, # Include default params
            }
            
            # 只有管理员可以看到服务提供商的详细信息
            if is_admin:
                model_data['provider'] = model.provider.name
                model_data['provider_id'] = model.provider.id
            else:
                # 普通用户只能看到服务提供商的名称，不能看到ID
                model_data['provider'] = model.provider.name
            
            models_data.append(model_data)

        return JsonResponse({'models': models_data})

    # --- 以下操作需要管理员权限 ---
    if not is_admin:
        return JsonResponse({
            'success': False,
            'message': "权限不足，只有管理员可以管理AI模型"
        }, status=403)

    # 处理POST, PUT, DELETE请求
    if request.method == 'POST':
        # 添加新模型
        try:
            data = json.loads(request.body)
            provider_id = data.get('provider_id')
            model_name = data.get('model_name')
            display_name = data.get('display_name')
            max_context = data.get('max_context', 4096)
            max_history_messages = data.get('max_history_messages', 20)
            is_active = data.get('is_active', True)
            default_params = data.get('default_params', {})
            
            if not provider_id or not model_name or not display_name:
                return JsonResponse({
                    'success': False,
                    'message': "缺少必要参数 (provider_id, model_name, display_name)"
                }, status=400)
            
            provider = get_object_or_404(AIProvider, id=provider_id)
            
            model = AIModel.objects.create(
                provider=provider,
                model_name=model_name,
                display_name=display_name,
                max_context=max_context,
                max_history_messages=max_history_messages,
                is_active=is_active,
                default_params=default_params
            )
            
            return JsonResponse({
                'success': True,
                'model_id': model.id,
                'message': "模型已创建"
            })
        except Exception as e:
            logger.error(f"添加模型失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"添加失败: {str(e)}"
            }, status=400)
    
    elif request.method == 'PUT':
        # 更新模型
        try:
            data = json.loads(request.body)
            model_id = data.get('id')
            
            if not model_id:
                return JsonResponse({
                    'success': False,
                    'message': "缺少模型ID"
                }, status=400)
            
            model = get_object_or_404(AIModel, id=model_id)
            
            if 'provider_id' in data:
                provider = get_object_or_404(AIProvider, id=data['provider_id'])
                model.provider = provider
            if 'model_name' in data:
                model.model_name = data['model_name']
            if 'display_name' in data:
                model.display_name = data['display_name']
            if 'max_context' in data:
                model.max_context = data['max_context']
            if 'max_history_messages' in data:
                model.max_history_messages = data['max_history_messages']
            if 'is_active' in data:
                model.is_active = data['is_active']
            if 'default_params' in data:
                model.default_params = data['default_params']
            
            model.save()
            
            return JsonResponse({
                'success': True,
                'message': "模型已更新"
            })
        except Exception as e:
            logger.error(f"更新模型失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"更新失败: {str(e)}"
            }, status=400)
    
    elif request.method == 'DELETE':
        # 删除模型
        try:
            data = json.loads(request.body)
            model_id = data.get('id')
            
            if not model_id:
                return JsonResponse({
                    'success': False,
                    'message': "缺少模型ID"
                }, status=400)
            
            model = get_object_or_404(AIModel, id=model_id)
            model.delete()
            
            return JsonResponse({
                'success': True,
                'message': "模型已删除"
            })
        except Exception as e:
            logger.error(f"删除模型失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"删除失败: {str(e)}"
            }, status=400)
    
    return HttpResponseBadRequest("不支持的请求方法")

# 添加一个辅助函数来检查用户是否是管理员
def is_user_admin(user):
    """检查用户是否为管理员"""
    try:
        profile = user.profile
        return profile.is_admin
    except:
        return False
