import json
import logging
import requests
import traceback
import base64
import mimetypes
import asyncio
import time

from django.shortcuts import get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

from chat.models import AIProvider, AIModel, Conversation, Message # Ensure Message is imported
from .utils import ensure_valid_api_url # Import from local utils
from chat.consumers import STOP_GENERATION_FLAGS, SYNC_STOP_GENERATION_LOCK, update_stop_flag # 导入全局终止标志和同步锁

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
        
        # 检查是否有终止标志
        with SYNC_STOP_GENERATION_LOCK:
            stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
            
        if stop_flag:
            logger.info(f"检测到会话 {conversation.id} 的终止标志，取消发送请求")
            # 重置终止标志，这样用户可以再次尝试生成
            update_stop_flag(conversation.id, False)
            logger.info(f"已重置会话 {conversation.id} 的终止标志")
            return JsonResponse({
                'success': False,
                'message': "生成已被用户终止"
            })

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

        # 检查是否有终止标志
        with SYNC_STOP_GENERATION_LOCK:
            stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
            
        if stop_flag:
            logger.info(f"检测到会话 {conversation.id} 的终止标志，取消发送请求")
            # 重置终止标志，这样用户可以再次尝试生成
            update_stop_flag(conversation.id, False)
            logger.info(f"已重置会话 {conversation.id} 的终止标志")
            return JsonResponse({
                'success': False,
                'message': "生成已被用户终止"
            })

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
                        # 在每个块处理前检查终止标志
                        with SYNC_STOP_GENERATION_LOCK:
                            stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                        
                        if stop_flag:
                            logger.info(f"检测到会话 {conversation.id} 的终止标志，停止处理流式响应")
                            # 立即退出函数，不进行任何后续处理
                            update_stop_flag(conversation.id, False)
                            logger.info(f"已重置会话 {conversation.id} 的终止标志")
                            return JsonResponse({
                                'success': False,
                                'message': "生成已被用户终止"
                            })
                            
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
                        # 再次检查终止标志
                        with SYNC_STOP_GENERATION_LOCK:
                            stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                        
                        if stop_flag:
                            logger.info(f"流式响应结束后，检测到会话 {conversation.id} 的终止标志，不进行非流式重试")
                            update_stop_flag(conversation.id, False)
                            logger.info(f"已重置会话 {conversation.id} 的终止标志")
                            return JsonResponse({
                                'success': False,
                                'message': "生成已被用户终止"
                            })
                        
                        logger.warning("流式响应未提取到内容，尝试非流式方式")
                        # 添加重试计数和延迟
                        retry_count = 0
                        max_retries = 2
                        
                        while not full_content and retry_count < max_retries:
                            retry_count += 1
                            logger.info(f"非流式重试 #{retry_count}/{max_retries}")
                            
                            # 每次重试前检查终止标志
                            with SYNC_STOP_GENERATION_LOCK:
                                stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                            
                            if stop_flag:
                                logger.info(f"非流式重试前检测到会话 {conversation.id} 的终止标志，停止重试")
                                update_stop_flag(conversation.id, False)
                                logger.info(f"已重置会话 {conversation.id} 的终止标志")
                                return JsonResponse({
                                    'success': False,
                                    'message': "生成已被用户终止"
                                })
                            
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

                # 再次检查终止标志，如果已终止则不保存结果
                with SYNC_STOP_GENERATION_LOCK:
                    stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                
                if stop_flag:
                    logger.info(f"检测到会话 {conversation.id} 的终止标志，取消保存回复")
                    # 重置终止标志，这样用户可以再次尝试生成
                    update_stop_flag(conversation.id, False)
                    logger.info(f"已重置会话 {conversation.id} 的终止标志")
                    return JsonResponse({
                        'success': False,
                        'message': "生成已被用户终止"
                    })

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

            # 检查是否有终止标志
            with SYNC_STOP_GENERATION_LOCK:
                stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                
            if stop_flag:
                logger.info(f"检测到会话 {conversation.id} 的终止标志，取消重新生成请求")
                # 重置终止标志，这样用户可以再次尝试生成
                update_stop_flag(conversation.id, False)
                logger.info(f"已重置会话 {conversation.id} 的终止标志")
                return JsonResponse({
                    'success': False,
                    'message': "生成已被用户终止"
                })

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
                        # 在每个块处理前检查终止标志
                        with SYNC_STOP_GENERATION_LOCK:
                            stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                        
                        if stop_flag:
                            logger.info(f"检测到会话 {conversation.id} 的终止标志，停止处理流式响应")
                            # 立即退出函数，不进行任何后续处理
                            update_stop_flag(conversation.id, False)
                            logger.info(f"已重置会话 {conversation.id} 的终止标志")
                            return JsonResponse({
                                'success': False,
                                'message': "生成已被用户终止"
                            })
                            
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
                                    logger.debug(f"解析的JSON (重新生成): {json.dumps(chunk_json)}")

                                    # --- More Robust Content Extraction ---
                                    content_piece = None
                                    if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
                                        choice = chunk_json['choices'][0]
                                        if 'delta' in choice and 'content' in choice['delta'] and choice['delta']['content']:
                                            content_piece = choice['delta']['content'] # OpenAI stream
                                        elif 'message' in choice and 'content' in choice['message'] and choice['message']['content']:
                                             # Handle case where a full message object arrives in a chunk
                                             content_piece = choice['message']['content']
                                             logger.warning("Received full message object in stream chunk (Regen)")
                                    # Handle Anthropic-style content array in stream? (Less likely but possible)
                                    elif 'content' in chunk_json and isinstance(chunk_json['content'], list):
                                         text_contents = []
                                         for item in chunk_json['content']:
                                             if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item:
                                                 text_contents.append(item['text'])
                                         if text_contents:
                                             content_piece = ' '.join(text_contents)
                                             logger.warning("Received Anthropic-style content array in stream chunk (Regen)")

                                    if content_piece:
                                        full_content += content_piece
                                        logger.debug(f"提取的内容片段 (重新生成): {content_piece}")
                                    # --- End Robust Extraction ---

                                except json.JSONDecodeError as je:
                                    logger.error(f"JSON解析错误 (重新生成): {je}, 数据: {chunk_data}")
                        except Exception as e:
                            logger.error(f"处理流式响应片段时出错 (重新生成): {str(e)}")

                    logger.info(f"流式响应处理完成 (重新生成)，累积内容长度: {len(full_content)}")

                    # 如果没有内容，在尝试非流式方式前先检查终止标志
                    if not full_content:
                        # 再次检查终止标志
                        with SYNC_STOP_GENERATION_LOCK:
                            stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                        
                        if stop_flag:
                            logger.info(f"流式响应结束后，检测到会话 {conversation.id} 的终止标志，不进行非流式重试 (重新生成)")
                            update_stop_flag(conversation.id, False)
                            logger.info(f"已重置会话 {conversation.id} 的终止标志")
                            return JsonResponse({
                                'success': False,
                                'message': "生成已被用户终止"
                            })
                        
                        logger.warning("流式响应未提取到内容 (重新生成)，尝试非流式方式")
                        # 添加重试计数和延迟
                        retry_count = 0
                        max_retries = 2
                        
                        while not full_content and retry_count < max_retries:
                            retry_count += 1
                            logger.info(f"重新生成非流式重试 #{retry_count}/{max_retries}")
                            
                            # 每次重试前检查终止标志
                            with SYNC_STOP_GENERATION_LOCK:
                                stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                            
                            if stop_flag:
                                logger.info(f"非流式重试前检测到会话 {conversation.id} 的终止标志，停止重试 (重新生成)")
                                update_stop_flag(conversation.id, False)
                                logger.info(f"已重置会话 {conversation.id} 的终止标志")
                                return JsonResponse({
                                    'success': False,
                                    'message': "生成已被用户终止"
                                })
                            
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
                                    logger.debug(f"非流式响应 (重新生成): {json.dumps(response_data)}")
                                    
                                    # --- Use Robust Extraction for Non-Stream Fallback ---
                                    extracted_content = ""
                                    
                                    if 'choices' in response_data and len(response_data['choices']) > 0:
                                        choice = response_data['choices'][0]
                                        if 'message' in choice and 'content' in choice['message']:
                                            extracted_content = choice['message']['content']
                                    elif 'content' in response_data: # Anthropic样式
                                        if isinstance(response_data['content'], list):
                                            text_contents = []
                                            for item in response_data['content']:
                                                if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item:
                                                    text_contents.append(item['text'])
                                            if text_contents:
                                                extracted_content = ' '.join(text_contents)
                                        else:
                                            extracted_content = str(response_data['content']) # 备用处理
                                    
                                    if extracted_content:
                                        full_content = extracted_content
                                        logger.info(f"非流式方式成功提取内容 (重新生成)，长度: {len(full_content)}")
                                        break
                                    # --- End Robust Extraction for Non-Stream ---
                                else:
                                    logger.error(f"非流式重试请求失败，状态码: {response.status_code}")
                            except Exception as retry_error:
                                logger.error(f"重新生成非流式重试 #{retry_count} 出错: {str(retry_error)}")
                                # 继续循环尝试下一次重试
                            
                            if not full_content:
                                logger.error("所有非流式重试也未能提取内容 (重新生成)")
                except Exception as e:
                    logger.error(f"处理响应时出错 (重新生成): {str(e)}")
                    logger.error(f"详细错误: {traceback.format_exc()}")

                # 再次检查终止标志，如果已终止则不保存结果
                with SYNC_STOP_GENERATION_LOCK:
                    stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                
                if stop_flag:
                    logger.info(f"检测到会话 {conversation.id} 的终止标志，取消保存重新生成的回复")
                    # 重置终止标志，这样用户可以再次尝试生成
                    update_stop_flag(conversation.id, False)
                    logger.info(f"已重置会话 {conversation.id} 的终止标志")
                    return JsonResponse({
                        'success': False,
                        'message': "生成已被用户终止"
                    })

                if full_content:
                    # 再次检查终止标志
                    with SYNC_STOP_GENERATION_LOCK:
                        stop_flag = STOP_GENERATION_FLAGS.get(str(conversation.id), False)
                    
                    if stop_flag:
                        logger.info(f"保存前检测到会话 {conversation.id} 的终止标志，不保存重新生成的回复")
                        return JsonResponse({
                            'success': False,
                            'message': "生成已被用户终止"
                        })
                        
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
        
        # 使用辅助函数设置全局终止标志
        update_stop_flag(conversation_id, True)
        
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

        return JsonResponse({
            'success': True,
            'message': "已发送终止请求"
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
