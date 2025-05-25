from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.conf import settings
import json
import requests
import logging
import re
import traceback

from .models import AIProvider, AIModel, Conversation, Message

logger = logging.getLogger(__name__)

# 添加管理员权限检查装饰器
def admin_required(view_func):
    """检查用户是否为管理员的装饰器"""
    def wrapper(request, *args, **kwargs):
        try:
            # 尝试获取用户资料
            profile = request.user.profile
            if not profile.is_admin:
                # 如果不是管理员，重定向到主页
                return redirect('chat-main')
        except:
            # 如果用户没有资料，也不是管理员
            return redirect('chat-main')
        
        # 如果是管理员，继续执行视图函数
        return view_func(request, *args, **kwargs)
    
    return wrapper

def ensure_valid_api_url(base_url, endpoint):
    """
    确保API URL格式正确，处理可能的反向代理情况
    
    Args:
        base_url: 提供商的基础URL
        endpoint: API端点路径，如 '/v1/chat/completions'
    
    Returns:
        完整的API URL
    """
    # 移除尾部斜杠
    base_url = base_url.rstrip('/')
    
    # 确保endpoint以斜杠开头
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint
    
    # 检查是否是反向代理URL（通常不包含http://或https://）
    if not re.match(r'^https?://', base_url):
        # 假设是反向代理，添加协议
        base_url = 'https://' + base_url
    
    return base_url + endpoint

# 页面视图
@login_required
def chat_view(request):
    """聊天主页视图"""
    # 获取可用的AI模型供选择
    models = AIModel.objects.filter(is_active=True, provider__is_active=True)
    # 获取用户的对话列表
    conversations = Conversation.objects.filter(user=request.user)
    
    # 获取当前对话，如果有指定的话
    conversation_id = request.GET.get('conversation_id')
    no_new = request.GET.get('no_new', '1') == '1'  # 默认不创建新对话
    conversation = None
    
    logger.info(f"加载聊天页面: conversation_id={conversation_id}, no_new={no_new}, 用户={request.user.username}")
    
    if conversation_id:
        try:
            conversation = Conversation.objects.get(id=conversation_id, user=request.user)
            logger.info(f"找到指定会话: {conversation.id} - {conversation.title}")
        except Conversation.DoesNotExist:
            # 如果指定的对话不存在或不属于当前用户，则忽略
            logger.warning(f"指定的会话ID {conversation_id} 不存在或不属于当前用户")
            pass
    
    # 如果没有指定对话或指定的对话无效，且no_new不为True，则创建一个新对话
    if not conversation and not no_new and models.exists():
        # 选择第一个可用的模型作为默认模型
        default_model = models.first()
        conversation = Conversation.objects.create(
            user=request.user,
            title=f"新对话 {Conversation.objects.filter(user=request.user).count() + 1}",
            selected_model=default_model
        )
        logger.info(f"创建了新会话: {conversation.id} - {conversation.title}")
    # 如果no_new为True且没有指定对话，则尝试使用最近的对话
    elif not conversation and no_new and conversations.exists():
        conversation = conversations.first()
        logger.info(f"使用最近的会话: {conversation.id} - {conversation.title}")
    
    if conversation:
        logger.info(f"最终使用的会话: {conversation.id} - {conversation.title}")
    else:
        logger.warning("无法获取或创建有效会话")
    
    context = {
        'models': models,
        'conversations': conversations,
        'conversation': conversation,
    }
    return render(request, 'chat/chat.html', context)

@login_required
def history_view(request):
    """聊天历史记录视图"""
    conversations = Conversation.objects.filter(user=request.user)
    context = {
        'conversations': conversations,
    }
    return render(request, 'chat/history.html', context)

@login_required
def settings_view(request):
    """API设置视图"""
    # 检查用户是否为管理员
    try:
        profile = request.user.profile
        is_admin = profile.is_admin
    except:
        is_admin = False
        # 如果用户没有资料，创建一个
        from users.models import UserProfile
        profile = UserProfile.objects.create(user=request.user)
    
    # 所有用户都可以访问设置页面，但内容会有所不同
    providers = AIProvider.objects.all()
    models = AIModel.objects.all()
    users = []
    
    # 如果是管理员，还获取用户列表
    if is_admin:
        from django.contrib.auth.models import User
        from users.models import UserProfile
        
        for user in User.objects.all():
            try:
                profile = user.profile
            except UserProfile.DoesNotExist:
                profile = UserProfile.objects.create(user=user)
            
            users.append({
                'id': user.id,
                'username': user.username,
                'is_admin': profile.is_admin,
                'date_joined': user.date_joined
            })
    
    context = {
        'providers': providers,
        'models': models,
        'users': users,
        'is_admin': is_admin,
        'current_user': request.user
    }
    return render(request, 'chat/settings.html', context)

def ws_test(request):
    """WebSocket测试视图"""
    return render(request, 'chat/ws_test.html')

# API接口
@login_required
@csrf_exempt
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def get_models_api(request):
    """获取和管理AI模型"""
    # 检查用户是否为管理员
    try:
        profile = request.user.profile
        is_admin = profile.is_admin
    except:
        is_admin = False
    
    if request.method == 'GET':
        # 获取单个模型详情
        model_id = request.GET.get('id')
        if model_id:
            try:
                model = get_object_or_404(AIModel, id=model_id)
                return JsonResponse({
                    'models': [{
                        'id': model.id,
                        'provider_id': model.provider.id,
                        'model_name': model.model_name,
                        'display_name': model.display_name,
                        'max_context': model.max_context,
                        'max_history_messages': model.max_history_messages,
                        'is_active': model.is_active,
                    }]
                })
            except Exception as e:
                return JsonResponse({
                    'success': False,
                    'message': f"获取模型详情失败: {str(e)}"
                }, status=400)
        
        # 获取所有模型列表
        if is_admin:
            # 管理员可以看到所有模型
            models = AIModel.objects.all()
        else:
            # 普通用户只能看到活跃的模型
            models = AIModel.objects.filter(is_active=True, provider__is_active=True)
        
        models_data = []
        for model in models:
            models_data.append({
                'id': model.id,
                'provider': model.provider.name,
                'model_name': model.model_name,
                'display_name': model.display_name,
                'max_context': model.max_context,
                'max_history_messages': model.max_history_messages,
            })
        
        return JsonResponse({'models': models_data})
    
    # 以下操作需要管理员权限
    if not is_admin:
        return JsonResponse({
            'success': False,
            'message': "权限不足，只有管理员可以管理AI模型"
        }, status=403)
    
    if request.method == 'POST':
        # 添加新模型
        try:
            data = json.loads(request.body)
            provider = get_object_or_404(AIProvider, id=data.get('provider_id'))
            
            model = AIModel.objects.create(
                provider=provider,
                model_name=data.get('model_name'),
                display_name=data.get('display_name'),
                max_context=data.get('max_context', 4096),
                max_history_messages=data.get('max_history_messages', 10),
                is_active=data.get('is_active', True)
            )
            
            return JsonResponse({
                'success': True,
                'model_id': model.id,
                'message': f"成功添加模型: {model.display_name}"
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
                return JsonResponse({'success': False, 'message': "缺少模型ID"}, status=400)
            
            model = get_object_or_404(AIModel, id=model_id)
            
            # 更新字段
            if 'provider_id' in data:
                provider = get_object_or_404(AIProvider, id=data.get('provider_id'))
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
            
            model.save()
            
            return JsonResponse({
                'success': True,
                'message': f"成功更新模型: {model.display_name}"
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
                return JsonResponse({'success': False, 'message': "缺少模型ID"}, status=400)
            
            model = get_object_or_404(AIModel, id=model_id)
            model_name = model.display_name
            model.delete()
            
            return JsonResponse({
                'success': True,
                'message': f"成功删除模型: {model_name}"
            })
        except Exception as e:
            logger.error(f"删除模型失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"删除失败: {str(e)}"
            }, status=400)
    
    return HttpResponseBadRequest("不支持的请求方法")

@login_required
@csrf_exempt
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def manage_providers_api(request):
    """管理AI服务提供商"""
    if request.method == 'GET':
        # 获取所有服务提供商
        providers = AIProvider.objects.all()
        providers_data = []
        
        for provider in providers:
            providers_data.append({
                'id': provider.id,
                'name': provider.name,
                'base_url': provider.base_url,
                'is_active': provider.is_active,
                'created_at': provider.created_at,
            })
        
        return JsonResponse({'providers': providers_data})
    
    elif request.method == 'POST':
        # 添加新的服务提供商
        try:
            data = json.loads(request.body)
            provider = AIProvider.objects.create(
                name=data['name'],
                base_url=data['base_url'],
                api_key=data['api_key'],
                is_active=data.get('is_active', True)
            )
            
            return JsonResponse({
                'success': True,
                'provider_id': provider.id,
                'message': f"成功添加服务提供商: {provider.name}"
            })
        except Exception as e:
            logger.error(f"添加服务提供商失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"添加失败: {str(e)}"
            }, status=400)
    
    elif request.method == 'PUT':
        # 更新服务提供商
        try:
            data = json.loads(request.body)
            provider_id = data.get('id')
            
            if not provider_id:
                return JsonResponse({'success': False, 'message': "缺少提供商ID"}, status=400)
            
            provider = get_object_or_404(AIProvider, id=provider_id)
            
            # 更新字段
            if 'name' in data:
                provider.name = data['name']
            if 'base_url' in data:
                provider.base_url = data['base_url']
            if 'api_key' in data:
                provider.api_key = data['api_key']
            if 'is_active' in data:
                provider.is_active = data['is_active']
            
            provider.save()
            
            return JsonResponse({
                'success': True,
                'message': f"成功更新服务提供商: {provider.name}"
            })
        except Exception as e:
            logger.error(f"更新服务提供商失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"更新失败: {str(e)}"
            }, status=400)
    
    elif request.method == 'DELETE':
        # 删除服务提供商
        try:
            data = json.loads(request.body)
            provider_id = data.get('id')
            
            if not provider_id:
                return JsonResponse({'success': False, 'message': "缺少提供商ID"}, status=400)
            
            provider = get_object_or_404(AIProvider, id=provider_id)
            provider_name = provider.name
            provider.delete()
            
            return JsonResponse({
                'success': True,
                'message': f"成功删除服务提供商: {provider_name}"
            })
        except Exception as e:
            logger.error(f"删除服务提供商失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"删除失败: {str(e)}"
            }, status=400)
    
    return HttpResponseBadRequest("不支持的请求方法")

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
        
        # 获取或创建会话
        if conversation_id:
            conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
        else:
            # 创建新会话
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
                    
                    return JsonResponse({
                        'success': True,
                        'conversation_id': conversation.id,
                        'message': full_content,
                        'message_id': ai_message.id,
                        'user_message_id': user_message.id
                    })
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
                logger.error(f"请求体: {request_data}")
                
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
        conversations = Conversation.objects.filter(user=request.user)
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
                
                conversation = Conversation.objects.create(
                    user=request.user,
                    title=data.get('title', '新对话'),
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
            
            conversation = get_object_or_404(Conversation, id=conversation_id, user=request.user)
            conversation.delete()
            
            return JsonResponse({
                'success': True,
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
@require_http_methods(["GET"])
def test_api_connection(request, provider_id):
    """测试与AI提供商API的连接"""
    try:
        provider = get_object_or_404(AIProvider, id=provider_id)
        
        # 构建测试URL
        api_url = ensure_valid_api_url(provider.base_url, "/v1/models")
        
        # 发送请求
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider.api_key}"
        }
        
        logger.info(f"测试API连接: {api_url}")
        
        try:
            response = requests.get(
                api_url,
                headers=headers,
                timeout=10
            )
            
            status_code = response.status_code
            
            if status_code == 200:
                # 成功连接
                try:
                    data = response.json()
                    models_count = len(data.get('data', []))
                    return JsonResponse({
                        'success': True,
                        'message': f"连接成功! 发现{models_count}个可用模型。",
                        'status_code': status_code,
                        'models_count': models_count
                    })
                except:
                    return JsonResponse({
                        'success': True,
                        'message': "连接成功，但无法解析模型列表。",
                        'status_code': status_code
                    })
            elif status_code == 404:
                # API端点未找到
                return JsonResponse({
                    'success': False,
                    'message': f"API端点未找到(404)。请检查基础URL是否正确: {provider.base_url}",
                    'status_code': status_code,
                    'details': "您的API可能使用了不同的端点路径。如果您使用的是反向代理，请确保正确配置了/v1/路径。"
                })
            elif status_code == 401:
                # 认证失败
                return JsonResponse({
                    'success': False,
                    'message': "认证失败(401)。请检查API密钥是否正确。",
                    'status_code': status_code
                })
            else:
                # 其他错误
                return JsonResponse({
                    'success': False,
                    'message': f"API请求失败: {status_code}",
                    'status_code': status_code,
                    'response': response.text[:200]  # 只返回前200个字符避免过大
                })
                
        except requests.exceptions.RequestException as e:
            logger.error(f"API连接测试失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"无法连接到API: {str(e)}",
                'error': str(e)
            }, status=500)
            
    except Exception as e:
        logger.error(f"测试API连接时出错: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"处理请求失败: {str(e)}"
        }, status=500)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def debug_api_response(request):
    """直接转发请求到AI服务并返回原始响应，用于调试"""
    try:
        data = json.loads(request.body)
        provider_id = data.get('provider_id')
        model_name = data.get('model_name')
        messages = data.get('messages', [])
        stream = data.get('stream', False)
        
        if not provider_id or not model_name or not messages:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数"
            }, status=400)
        
        # 获取提供商
        provider = get_object_or_404(AIProvider, id=provider_id)
        
        # 构建请求数据
        request_data = {
            "model": model_name,
            "messages": messages,
            "stream": stream
        }
        
        # 添加可选参数
        if 'temperature' in data:
            request_data['temperature'] = data['temperature']
        if 'max_tokens' in data:
            request_data['max_tokens'] = data['max_tokens']
        
        # 获取API URL和密钥
        api_url = ensure_valid_api_url(provider.base_url, "/v1/chat/completions")
        api_key = provider.api_key
        
        logger.info(f"调试API - 发送请求到: {api_url}")
        
        # 发送请求
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }
        
        if stream:
            # 流式响应处理
            response = requests.post(
                api_url,
                json=request_data,
                headers=headers,
                stream=True,
                timeout=30
            )
            
            if response.status_code == 200:
                # 收集所有流式响应片段
                chunks = []
                full_content = ""
                
                for chunk in response.iter_lines():
                    if chunk:
                        # 处理数据行
                        try:
                            # 移除 "data: " 前缀
                            if chunk.startswith(b'data: '):
                                chunk_data = chunk[6:].decode('utf-8')
                                if chunk_data.strip() == '[DONE]':
                                    continue
                                
                                chunk_json = json.loads(chunk_data)
                                chunks.append(chunk_json)
                                
                                # 尝试提取内容
                                if 'choices' in chunk_json and len(chunk_json['choices']) > 0:
                                    if 'delta' in chunk_json['choices'][0] and 'content' in chunk_json['choices'][0]['delta']:
                                        full_content += chunk_json['choices'][0]['delta']['content']
                        except Exception as e:
                            logger.error(f"处理流式响应片段时出错: {str(e)}")
                
                return JsonResponse({
                    'success': True,
                    'status_code': response.status_code,
                    'is_stream': True,
                    'chunks': chunks,
                    'full_content': full_content,
                    'headers': dict(response.headers)
                })
            else:
                return JsonResponse({
                    'success': False,
                    'status_code': response.status_code,
                    'raw_text': response.text,
                    'headers': dict(response.headers)
                })
        else:
            # 普通响应处理
            response = requests.post(
                api_url,
                json=request_data,
                headers=headers,
                timeout=30
            )
            
            # 返回完整的原始响应
            try:
                response_json = response.json()
                return JsonResponse({
                    'success': True,
                    'status_code': response.status_code,
                    'raw_response': response_json,
                    'headers': dict(response.headers)
                })
            except:
                return JsonResponse({
                    'success': False,
                    'status_code': response.status_code,
                    'raw_text': response.text,
                    'headers': dict(response.headers)
                })
            
    except Exception as e:
        logger.error(f"调试API请求失败: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"请求失败: {str(e)}"
        }, status=500)

def api_debug_view(request):
    """API调试页面视图"""
    providers = AIProvider.objects.filter(is_active=True)
    models = AIModel.objects.filter(is_active=True)
    
    context = {
        'providers': providers,
        'models': models,
    }
    return render(request, 'chat/api_debug.html', context)

@login_required
@csrf_exempt
@require_http_methods(["POST"])
def edit_message_api(request):
    """编辑消息内容"""
    try:
        data = json.loads(request.body)
        message_id = data.get('message_id')
        content = data.get('content')
        
        if not message_id or not content:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数"
            }, status=400)
        
        # 检查是否是临时ID
        if message_id.startswith('temp-'):
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
        message_id = data.get('message_id')
        model_id = data.get('model_id')
        
        if not message_id or not model_id:
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数"
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
            
            # 获取历史消息，直到当前用户消息
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
                **model.default_params  # 添加默认参数
            }
            
            # 根据模型设置决定是否使用流式响应
            # 强制使用流式响应，确保与前端一致
            use_stream = True
            request_data['stream'] = use_stream
            
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

            logger.info(f"重新生成回复: 用户消息ID={message_id}, 模型={model.model_name}, 流式={use_stream}, API URL={api_url}")
            
            if use_stream:
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
                        conversation.save()
                        
                        return JsonResponse({
                            'success': True,
                            'message_id': ai_message.id,
                            'content': full_content
                        })
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
                else:
                    logger.error(f"重新生成回复API请求失败: {response.status_code}")
                    return JsonResponse({
                        'success': False,
                        'message': f"无法从API响应中提取内容: {response.status_code} - {response.text}"
                    }, status=500)
            else:
                # 使用普通响应
                response = requests.post(
                    api_url,
                    json=request_data,
                    headers=headers,
                    timeout=60
                )
                
                if response.status_code == 200:
                    response_data = response.json()
                    
                    # 提取AI回复内容
                    if 'choices' in response_data and len(response_data['choices']) > 0:
                        content = response_data['choices'][0]['message']['content']
                        
                        # 保存新的AI回复
                        ai_message = Message.objects.create(
                            conversation=conversation,
                            content=content,
                            is_user=False,
                            model_used=model
                        )
                        
                        # 更新会话时间
                        conversation.save()
                        
                        return JsonResponse({
                            'success': True,
                            'message_id': ai_message.id,
                            'content': content
                        })
                    else:
                        return JsonResponse({
                            'success': False,
                            'message': "无法从API响应中提取内容"
                        }, status=500)
                else:
                    return JsonResponse({
                        'success': False,
                        'message': f"AI服务请求失败: {response.status_code} - {response.text}"
                    }, status=response.status_code)
                
        except Message.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': "消息不存在"
            }, status=404)
            
    except Exception as e:
        logger.error(f"重新生成回复出错: {str(e)}")
        # 记录详细的堆栈跟踪
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
        
        if not conversation_id:
            logger.warning("同步请求缺少会话ID参数")
            return JsonResponse({
                'success': False,
                'message': "缺少会话ID参数"
            }, status=400)
        
        try:
            # 尝试获取指定的会话
            conversation = Conversation.objects.get(id=conversation_id, user=request.user)
            logger.info(f"找到指定会话: {conversation.id} - {conversation.title}")
        except Conversation.DoesNotExist:
            logger.warning(f"指定会话ID {conversation_id} 不存在，尝试获取其他会话")
            # 如果找不到，检查用户是否有其他会话
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
            
            # 删除消息
            message.delete()
            
            # 如果删除的是用户消息，同时删除其对应的AI回复
            if is_user_message:
                # 查找该用户消息之后的第一个AI回复
                next_ai_message = Message.objects.filter(
                    conversation=conversation,
                    is_user=False,
                    timestamp__gt=timestamp
                ).order_by('timestamp').first()
                
                if next_ai_message:
                    next_ai_message.delete()
                    logger.info(f"已删除用户消息 {message_id} 及其对应的AI回复 {next_ai_message.id}")
                else:
                    logger.info(f"已删除用户消息 {message_id}，未找到对应的AI回复")
            else:
                logger.info(f"已删除AI回复消息 {message_id}")
            
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
