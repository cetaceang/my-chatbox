import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.exceptions import ObjectDoesNotExist
import requests
import aiohttp
import asyncio
import logging
import threading

from .models import Conversation, Message, AIModel

logger = logging.getLogger(__name__)

# 配置常量
AI_REQUEST_TIMEOUT = 300  # AI请求超时时间（秒）
AI_REQUEST_MAX_RETRIES = 2  # AI请求最大重试次数

# 全局字典，用于跟踪每个会话的终止状态
STOP_GENERATION_FLAGS = {}
# 全局锁，用于同步对STOP_GENERATION_FLAGS的访问
STOP_GENERATION_LOCK = asyncio.Lock()  # 用于异步代码
# 同步锁，用于同步视图函数
SYNC_STOP_GENERATION_LOCK = threading.Lock()  # 用于同步代码

# 辅助函数，用于同步更新标志
def update_stop_flag(conversation_id, value):
    """同步更新终止标志 - 供同步代码使用"""
    with SYNC_STOP_GENERATION_LOCK:
        STOP_GENERATION_FLAGS[str(conversation_id)] = value
        logger.info(f"同步更新会话 {conversation_id} 的终止标志为 {value}")
    return value

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # 验证用户权限
        user = self.scope["user"]
        if user.is_anonymous:
            await self.close()
            return

        self.conversation_id = self.scope['url_route']['kwargs']['conversation_id']
        
        # 验证对话归属
        if not await self.validate_conversation_ownership(user):
            await self.close()
            return

        self.conversation_group_name = f'chat_{self.conversation_id}'
        
        # 加入对话组
        await self.channel_layer.group_add(
            self.conversation_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # 添加一个标志来跟踪当前请求
        self.active_request_task = None
        self.stop_requested = False
        self.termination_message_sent = False  # 标记是否已发送终止消息
        # 添加一个锁，用于同步终止请求和发送回复
        self.response_lock = asyncio.Lock()
        
        # 初始化全局终止标志
        async with STOP_GENERATION_LOCK:
            STOP_GENERATION_FLAGS[self.conversation_id] = False
            logger.info(f"初始化会话 {self.conversation_id} 的终止标志为 False")
    
    async def disconnect(self, close_code):
        # 离开对话组
        await self.channel_layer.group_discard(
            self.conversation_group_name,
            self.channel_name
        )
        
        # 如果有活跃的请求任务，取消它
        if self.active_request_task:
            self.active_request_task.cancel()
            self.active_request_task = None
        
        # 清理全局终止标志
        async with STOP_GENERATION_LOCK:
            if self.conversation_id in STOP_GENERATION_FLAGS:
                del STOP_GENERATION_FLAGS[self.conversation_id]
                logger.info(f"清理会话 {self.conversation_id} 的终止标志")
    
    async def receive(self, text_data):
        """
        接收WebSocket消息
        """
        try:
            text_data_json = json.loads(text_data)
            
            # 处理终止生成请求
            if text_data_json.get('type') == 'stop_generation':
                logger.info(f"收到终止生成请求: 会话ID {self.conversation_id}")
                
                # 设置全局终止标志
                async with STOP_GENERATION_LOCK:
                    STOP_GENERATION_FLAGS[self.conversation_id] = True
                    logger.info(f"设置会话 {self.conversation_id} 的全局终止标志为 True")
                
                # 获取锁，确保终止请求能够立即生效
                async with self.response_lock:
                    self.stop_requested = True
                    
                    # 如果有活跃的请求任务，取消它
                    if self.active_request_task:
                        logger.info("正在取消活跃的AI请求任务")
                        self.active_request_task.cancel()
                        self.active_request_task = None
                
                # 清除状态消息
                await self.send_status_message('', clear=True)
                
                # 向组内所有连接发送终止消息
                # 这将通过generation_stopped事件处理函数发送确认消息
                await self.channel_layer.group_send(
                    self.conversation_group_name,
                    {
                        'type': 'generation_stopped',
                        'message': '用户终止了生成'
                    }
                )
                return
            
            message = text_data_json.get('message')
            model_id = text_data_json.get('model_id')
            temp_id = text_data_json.get('temp_id', None)  # 获取临时ID
            
            if not message or not model_id:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': '缺少必要参数'
                }))
                return
                
            # 确保conversation_id有效
            try:
                conversation_id = self.conversation_id
                conversation = await self.get_conversation(conversation_id)
                if not conversation:
                    await self.send(text_data=json.dumps({
                        'type': 'error',
                        'message': '会话不存在'
                    }))
                    return
            except Exception as e:
                logger.error(f"获取会话失败: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'获取会话失败: {str(e)}'
                }))
                return
                
            # 保存用户消息
            try:
                user_message = await self.save_user_message(conversation, message, model_id)
            except Exception as e:
                logger.error(f"保存用户消息失败: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'保存用户消息失败: {str(e)}'
                }))
                return
            
            # 记录临时ID和真实ID的映射关系
            if temp_id:
                logger.info(f"WebSocket临时ID映射: {temp_id} -> {user_message['id']}")
                
                # 发送用户消息ID更新
                await self.send(text_data=json.dumps({
                    'type': 'user_message_id_update',
                    'temp_id': temp_id,
                    'user_message_id': user_message['id']
                }))
            
            # 向组发送用户消息（如果没有临时ID才发送，避免重复）
            if not temp_id:
                await self.channel_layer.group_send(
                    self.conversation_group_name,
                    {
                        'type': 'chat_message',
                        'message': message,
                        'is_user': True,
                        'timestamp': str(user_message['timestamp']),
                        'message_id': user_message['id']
                    }
                )
            
            # 处理AI回复
            try:
                # 重置终止标志
                self.stop_requested = False
                await self.process_ai_response(model_id, user_message['id'])
            except Exception as e:
                logger.error(f"处理AI回复失败: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'处理AI回复失败: {str(e)}'
                }))
        except Exception as e:
            logger.error(f"处理消息时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'处理消息时出错: {str(e)}'
            }))
    
    async def chat_message(self, event):
        message = event['message']
        is_user = event.get('is_user', False)
        timestamp = event.get('timestamp', '')
        message_id = event.get('message_id', '')
        
        # 如果是AI回复（非用户消息），检查全局终止标志
        if not is_user:
            async with STOP_GENERATION_LOCK:
                stop_flag = STOP_GENERATION_FLAGS.get(self.conversation_id, False)
            
            if stop_flag:
                logger.info(f"chat_message: 检测到会话 {self.conversation_id} 的全局终止标志为 True，不发送AI回复")
                
                # 如果消息ID存在，尝试删除该消息
                if message_id:
                    await self.delete_ai_message(message_id)
                    logger.info(f"已删除终止后的AI回复消息 ID: {message_id}")
                
                # 重置全局终止标志
                async with STOP_GENERATION_LOCK:
                    STOP_GENERATION_FLAGS[self.conversation_id] = False
                    logger.info(f"重置会话 {self.conversation_id} 的全局终止标志为 False")
                
                # 不再发送额外的终止确认消息，避免重复
                return
        
        # 发送消息到WebSocket
        await self.send(text_data=json.dumps({
            'type': 'chat_message', # Add type field back
            'message': message,
            'is_user': is_user,
            'timestamp': timestamp,
            'message_id': message_id
        }))
    
    async def status_message(self, event):
        """处理状态消息"""
        message = event.get('message', '')
        
        # 发送状态消息到WebSocket
        await self.send(text_data=json.dumps({
            'type': 'status',
            'message': message
        }))
    
    @database_sync_to_async
    def validate_conversation_ownership(self, user):
        try:
            Conversation.objects.get(id=self.conversation_id, user=user)
            return True
        except Conversation.DoesNotExist:
            return False

    @database_sync_to_async
    def save_user_message(self, conversation, message, model_id):
        try:
            # 如果传入的是字典，则获取ID
            if isinstance(conversation, dict):
                conversation_id = conversation['id']
                conversation = Conversation.objects.get(id=conversation_id)
            # 如果传入的是ID，则获取对象
            elif isinstance(conversation, (int, str)):
                conversation_id = conversation
                conversation = Conversation.objects.get(id=conversation_id)
            
            model = AIModel.objects.get(id=model_id)
            
            # 保存用户消息
            user_message = Message.objects.create(
                conversation=conversation,
                content=message,
                is_user=True,
                model_used=model
            )
            
            # 返回消息ID
            return {
                'id': user_message.id,
                'content': user_message.content,
                'timestamp': user_message.timestamp
            }
        except Exception as e:
            logger.error(f"保存用户消息失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise
            
    async def process_ai_response(self, model_id, user_message_id):
        """处理AI回复"""
        try:
            # 重置终止消息标志
            self.termination_message_sent = False
            
            # 获取会话和模型
            conversation = await self.get_conversation(self.conversation_id)
            if not conversation:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': '会话不存在'
                }))
                return
                
            # 获取用户消息
            user_message = await self.get_message(user_message_id)
            if not user_message:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': '用户消息不存在'
                }))
                return
                
            # 获取模型
            model = await self.get_model(model_id)
            if not model:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': '模型不存在'
                }))
                return
                
            # 获取历史消息
            history_messages = await self.get_history_messages(
                conversation['id'], model['max_history_messages']
            )
            
            # 构建请求数据
            messages = []
            for msg in history_messages:
                role = "user" if msg['is_user'] else "assistant"
                messages.append({
                    "role": role,
                    "content": msg['content']
                })
                
            # 获取API信息
            api_info = {
                'url': f"{model['provider_base_url']}/v1/chat/completions",
                'key': model['provider_api_key'],
                'model_name': model['model_name'],
                'params': model['default_params']
            }
            
            # 发送请求到AI服务，并添加重试机制
            response_content = None
            retry_count = 0
            
            # 发送请求前通知用户
            await self.send_status_message('正在生成回复...')
            
            while response_content is None and retry_count <= AI_REQUEST_MAX_RETRIES and not self.stop_requested:
                if retry_count > 0:
                    await self.send_status_message(f'请求超时，正在重试 ({retry_count}/{AI_REQUEST_MAX_RETRIES})...')
                    # 每次重试前等待一小段时间
                    await asyncio.sleep(1)
                
                try:
                    # 创建一个任务并保存引用，以便可以取消它
                    self.active_request_task = asyncio.create_task(
                        self.send_request_to_ai(api_info, messages, model)
                    )
                    
                    try:
                        response_content = await self.active_request_task
                    finally:
                        self.active_request_task = None  # 清除任务引用
                    
                except asyncio.CancelledError:
                    logger.info("AI请求已被取消")
                    if self.stop_requested:
                        response_content = "生成已被用户终止。"
                    raise  # 重新抛出取消异常，让外层捕获
                except asyncio.TimeoutError as e:
                    logger.error(f"AI请求超时 (尝试 {retry_count+1}/{AI_REQUEST_MAX_RETRIES+1}): {str(e)}")
                    retry_count += 1
                    # 如果已达到最大重试次数，将错误信息作为响应
                    if retry_count > AI_REQUEST_MAX_RETRIES:
                        response_content = f"AI服务响应超时，已重试 {AI_REQUEST_MAX_RETRIES} 次。请稍后再试或简化您的请求。"
                except Exception as e:
                    logger.error(f"AI请求失败 (尝试 {retry_count+1}/{AI_REQUEST_MAX_RETRIES+1}): {str(e)}")
                    retry_count += 1
                    # 如果已达到最大重试次数，将错误信息作为响应
                    if retry_count > AI_REQUEST_MAX_RETRIES:
                        response_content = f"AI服务请求失败: {str(e)}"
            
            # 检查是否是因为终止请求而退出循环
            if self.stop_requested:
                logger.info("生成已被用户终止，不保存或发送AI回复")
                # 清除状态消息
                await self.send_status_message('', clear=True)
                # 发送终止消息给客户端
                await self.send(text_data=json.dumps({
                    'type': 'generation_stopped',
                    'message': '生成已被用户终止'
                }))
                # 标记已发送终止消息
                self.termination_message_sent = True
                # 重置终止标志
                self.stop_requested = False
                return
            
            # 清除状态消息
            await self.send_status_message('', clear=True)
            
            if not response_content:
                # 所有重试都失败
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': '获取AI回复失败，请稍后重试'
                }))
                return
            
            # 检查是否是错误消息
            if response_content.startswith("AI服务") or response_content.startswith("请求被取消") or "失败" in response_content:
                # 发送错误消息
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': response_content
                }))
                return
            
            # 在保存AI回复前再次检查终止标志
            if self.stop_requested:
                logger.info("在保存AI回复前检测到终止标志，不保存或发送AI回复")
                # 清除状态消息
                await self.send_status_message('', clear=True)
                # 发送终止消息给客户端
                await self.send(text_data=json.dumps({
                    'type': 'generation_stopped',
                    'message': '生成已被用户终止'
                }))
                # 标记已发送终止消息
                self.termination_message_sent = True
                # 重置终止标志
                self.stop_requested = False
                return
            
            # 检查全局终止标志
            async with STOP_GENERATION_LOCK:
                stop_flag = STOP_GENERATION_FLAGS.get(self.conversation_id, False)
            
            if stop_flag:
                logger.info(f"process_ai_response: 检测到会话 {self.conversation_id} 的全局终止标志为 True，不保存或发送AI回复")
                # 清除状态消息
                await self.send_status_message('', clear=True)
                # 发送终止消息给客户端
                await self.send(text_data=json.dumps({
                    'type': 'generation_stopped',
                    'message': '生成已被用户终止'
                }))
                # 标记已发送终止消息
                self.termination_message_sent = True
                # 重置全局终止标志
                async with STOP_GENERATION_LOCK:
                    STOP_GENERATION_FLAGS[self.conversation_id] = False
                    logger.info(f"重置会话 {self.conversation_id} 的全局终止标志为 False")
                return
                
            # 保存AI回复
            ai_message = await self.save_ai_message(
                conversation['id'], response_content, model['id']
            )
            
            # 最后一次检查终止标志，确保不会在用户请求终止后发送消息
            if self.stop_requested:
                logger.info("在发送AI回复前检测到终止标志，不发送AI回复")
                # 删除已保存的AI回复
                await self.delete_ai_message(ai_message['id'])
                # 清除状态消息
                await self.send_status_message('', clear=True)
                # 发送终止消息给客户端
                await self.send(text_data=json.dumps({
                    'type': 'generation_stopped',
                    'message': '生成已被用户终止'
                }))
                # 标记已发送终止消息
                self.termination_message_sent = True
                # 重置终止标志
                self.stop_requested = False
                return
            
            # 使用锁确保在发送AI回复前再次检查终止标志
            async with self.response_lock:
                # 最终检查终止标志
                if self.stop_requested:
                    logger.info("在获取锁后检测到终止标志，不发送AI回复")
                    # 删除已保存的AI回复
                    await self.delete_ai_message(ai_message['id'])
                    # 清除状态消息
                    await self.send_status_message('', clear=True)
                    # 发送终止消息给客户端
                    await self.send(text_data=json.dumps({
                        'type': 'generation_stopped',
                        'message': '生成已被用户终止'
                    }))
                    # 标记已发送终止消息
                    self.termination_message_sent = True
                    # 重置终止标志
                    self.stop_requested = False
                    return
                
                # 发送AI回复
                await self.channel_layer.group_send(
                    self.conversation_group_name,
                    {
                        'type': 'chat_message',
                        'message': response_content,
                        'is_user': False,
                        'message_id': ai_message['id'],
                        'timestamp': ai_message['timestamp'].isoformat() # Use ISO format
                    }
                )
        except asyncio.CancelledError:
            logger.info("AI响应处理被取消")
            # 发送终止消息
            await self.send(text_data=json.dumps({
                'type': 'generation_stopped',
                'message': '生成已终止'
            }))
            return
        except Exception as e:
            # 发送错误消息
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'处理AI回复时出错: {str(e)}'
            }))
            logger.error(f"处理AI回复时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
    
    async def chat_message_update(self, event):
        """处理消息更新，用于流式响应"""
        content = event['content']
        
        # 发送增量更新到WebSocket
        await self.send(text_data=json.dumps({
            'update': True,
            'content': content
        }))
    
    @database_sync_to_async
    def get_conversation(self, conversation_id):
        """获取会话对象"""
        try:
            conversation = Conversation.objects.get(id=conversation_id)
            # 返回一个字典而不是数据库对象
            return {
                'id': conversation.id,
                'title': conversation.title,
                'user_id': conversation.user_id,
                'selected_model_id': conversation.selected_model_id if conversation.selected_model else None
            }
        except Conversation.DoesNotExist:
            logger.error(f"会话不存在: {conversation_id}")
            return None
        except Exception as e:
            logger.error(f"获取会话失败: {str(e)}")
            raise
    
    @database_sync_to_async
    def get_message(self, message_id):
        """获取消息对象"""
        try:
            message = Message.objects.get(id=message_id)
            # 返回一个字典而不是数据库对象，避免在异步环境中访问数据库对象属性的问题
            return {
                'id': message.id,
                'content': message.content,
                'is_user': message.is_user,
                'conversation_id': message.conversation_id,
                'model_used_id': message.model_used_id if message.model_used else None
            }
        except Message.DoesNotExist:
            logger.error(f"消息不存在: {message_id}")
            return None
        except Exception as e:
            logger.error(f"获取消息失败: {str(e)}")
            raise
            
    @database_sync_to_async
    def get_model(self, model_id):
        """获取模型对象"""
        try:
            model = AIModel.objects.get(id=model_id)
            # 返回一个字典而不是数据库对象
            provider = model.provider
            return {
                'id': model.id,
                'model_name': model.model_name,
                'display_name': model.display_name,
                'max_history_messages': model.max_history_messages,
                'default_params': model.default_params,
                'provider_id': provider.id if provider else None,
                'provider_base_url': provider.base_url if provider else None,
                'provider_api_key': provider.api_key if provider else None
            }
        except AIModel.DoesNotExist:
            logger.error(f"模型不存在: {model_id}")
            return None
        except Exception as e:
            logger.error(f"获取模型失败: {str(e)}")
            raise
            
    @database_sync_to_async
    def get_api_info(self, model):
        """获取API信息"""
        try:
            provider = model.provider
            api_url = f"{provider.base_url}/v1/chat/completions"
            api_key = provider.api_key
            
            return {
                'url': api_url,
                'key': api_key,
                'model_name': model.model_name,
                'params': model.default_params
            }
        except Exception as e:
            logger.error(f"获取API信息失败: {str(e)}")
            raise
            
    @database_sync_to_async
    def get_history_messages(self, conversation_id, max_messages):
        """获取历史消息"""
        try:
            messages = Message.objects.filter(conversation_id=conversation_id).order_by('timestamp')
            
            # 如果消息数量超过限制，只返回最近的消息
            if messages.count() > max_messages:
                messages = messages[messages.count() - max_messages:]
            
            return [
                {
                    'id': msg.id,
                    'content': msg.content,
                    'is_user': msg.is_user,
                    'timestamp': msg.timestamp
                } for msg in messages
            ]
        except Exception as e:
            logger.error(f"获取历史消息失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    
    @database_sync_to_async
    def save_ai_message(self, conversation_id, content, model_id):
        """保存AI回复消息"""
        try:
            conversation = Conversation.objects.get(id=conversation_id)
            model = AIModel.objects.get(id=model_id)
            
            ai_message = Message.objects.create(
                conversation=conversation,
                content=content,
                is_user=False,
                model_used=model
            )
            
            # 更新会话时间
            conversation.save()  # 自动更新updated_at字段
            
            return {
                'id': ai_message.id,
                'content': ai_message.content,
                'timestamp': ai_message.timestamp
            }
        except Exception as e:
            logger.error(f"保存AI回复失败: {str(e)}")
            raise

    @database_sync_to_async
    def delete_ai_message(self, message_id):
        """删除AI回复消息"""
        try:
            # 尝试删除消息
            message = Message.objects.filter(id=message_id).first()
            if message:
                logger.info(f"删除消息ID: {message_id}")
                message.delete()
                return True
            else:
                logger.warning(f"要删除的消息不存在，ID: {message_id}")
                return False
        except Exception as e:
            logger.error(f"删除消息失败，ID: {message_id}, 错误: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def chat_message_id_update(self, event):
        """处理消息ID更新，用于将临时ID替换为实际ID"""
        message_id = event['message_id']
        temp_id = event.get('temp_id', '')
        user_message_id = event.get('user_message_id', '')
        
        # 发送消息ID更新到WebSocket
        await self.send(text_data=json.dumps({
            'id_update': True,
            'message_id': message_id,
            'temp_id': temp_id,
            'user_message_id': user_message_id
        }))

    async def send_request_to_ai(self, api_info, messages, model):
        """发送请求到AI服务"""
        try:
            # 检查是否已经请求终止
            if self.stop_requested:
                logger.info("检测到终止标志，取消发送AI请求")
                raise asyncio.CancelledError("用户请求终止生成")
                
            # 构建请求数据
            request_data = {
                "model": api_info['model_name'],
                "messages": messages,
                "stream": False,  # 使用非流式响应
                **api_info['params']
            }
            
            # 使用aiohttp发送异步请求
            async with aiohttp.ClientSession() as session:
                # 创建一个可取消的任务
                try:
                    async with session.post(
                        api_info['url'],
                        json=request_data,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_info['key']}"
                        },
                        timeout=aiohttp.ClientTimeout(total=AI_REQUEST_TIMEOUT)  # 使用AI_REQUEST_TIMEOUT
                    ) as response:
                        # 处理响应
                        if response.status == 200:
                            # 检查是否已经请求终止
                            if self.stop_requested:
                                logger.info("收到AI响应，但检测到终止标志，丢弃响应")
                                raise asyncio.CancelledError("用户请求终止生成")
                                
                            response_data = await response.json()
                            
                            if 'choices' in response_data and len(response_data['choices']) > 0:
                                # 处理标准OpenAI格式
                                if 'message' in response_data['choices'][0] and 'content' in response_data['choices'][0]['message']:
                                    return response_data['choices'][0]['message']['content']
                            # 处理新格式的响应
                            elif 'role' in response_data and 'content' in response_data:
                                if isinstance(response_data['content'], list):
                                    # 如果content是一个数组，提取所有text类型的内容并拼接
                                    text_contents = []
                                    for item in response_data['content']:
                                        if isinstance(item, dict) and item.get('type') == 'text' and 'text' in item:
                                            text_contents.append(item['text'])
                                    if text_contents:
                                        return ''.join(text_contents)
                                elif isinstance(response_data['content'], str):
                                    # 如果content是字符串，直接返回
                                    return response_data['content']
                        
                        response_text = await response.text()
                        error_msg = f"AI服务响应错误: {response.status} - {response_text}"
                        logger.error(error_msg)
                        # 抛出异常以便重试
                        raise Exception(error_msg)
                except asyncio.CancelledError:
                    logger.info("AI请求被取消")
                    raise  # 重新抛出取消异常
        except asyncio.TimeoutError as e:
            logger.error(f"发送请求到AI服务超时: {str(e)}")
            # 直接抛出超时异常，让调用者处理重试
            raise
        except asyncio.CancelledError:
            logger.info("AI请求被取消")
            raise  # 重新抛出取消异常
        except Exception as e:
            logger.error(f"发送请求到AI服务失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            # 针对超时错误提供更具体的错误信息
            error_message = str(e)
            if isinstance(e, asyncio.TimeoutError) or "TimeoutError" in error_message:
                error_msg = "AI服务响应超时，这可能是因为模型生成内容需要更长时间。请稍后再试或考虑简化您的请求。"
                raise asyncio.TimeoutError(error_msg)
            elif "CancelledError" in error_message:
                error_msg = "请求被取消，可能是因为连接中断或用户请求终止。"
                raise asyncio.CancelledError(error_msg)
            else:
                error_msg = "与AI服务通信时发生错误，请稍后重试。"
                raise Exception(error_msg)

    async def send_status_message(self, message, clear=False):
        """发送状态消息到WebSocket"""
        await self.send(text_data=json.dumps({
            'type': 'clear_status' if clear else 'status',
            'message': message
        }))

    async def generation_stopped(self, event):
        """处理生成终止消息"""
        message = event.get('message', '生成已终止')
        logger.info(f"收到终止生成消息: {message}")
        
        # 检查是否已经发送过终止消息
        if self.termination_message_sent:
            logger.info(f"会话 {self.conversation_id} 已经发送过终止消息，不再重复发送")
            return
            
        # 标记已经发送过终止消息
        self.termination_message_sent = True
        
        # 设置全局终止标志
        async with STOP_GENERATION_LOCK:
            # 检查是否已经处理过终止请求
            if STOP_GENERATION_FLAGS.get(self.conversation_id, False):
                logger.info(f"会话 {self.conversation_id} 已经处理过终止请求，不再重复处理")
                return
                
            STOP_GENERATION_FLAGS[self.conversation_id] = True
            logger.info(f"generation_stopped: 设置会话 {self.conversation_id} 的全局终止标志为 True")
        
        # 获取锁，确保终止请求能够立即生效
        async with self.response_lock:
            # 如果有活跃的请求任务，取消它
            if self.active_request_task:
                logger.info("正在取消活跃的AI请求任务")
                self.active_request_task.cancel()
                self.active_request_task = None
            
            # 设置终止标志
            self.stop_requested = True
        
        # 清除状态消息
        await self.send_status_message('', clear=True)
        
        # 删除可能存在的未完成回复
        try:
            # 获取会话信息
            conversation = await self.get_conversation(self.conversation_id)
            if conversation:
                # 获取最后一条用户消息
                user_messages = await self.get_last_user_message(conversation['id'])
                if user_messages:
                    # 删除该用户消息之后的所有AI回复
                    await self.delete_subsequent_ai_messages(conversation['id'], user_messages['timestamp'])
                    logger.info(f"已删除会话 {self.conversation_id} 中最后一条用户消息后的所有AI回复")
        except Exception as e:
            logger.error(f"尝试删除未完成回复时出错: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
        
        # 发送终止消息到客户端
        await self.send(text_data=json.dumps({
            'type': 'generation_stopped',
            'message': message
        }))

    @database_sync_to_async
    def get_last_user_message(self, conversation_id):
        """获取会话中最后一条用户消息"""
        try:
            # 查找指定会话中最后一条用户消息
            last_user_message = Message.objects.filter(
                conversation_id=conversation_id,
                is_user=True
            ).order_by('-timestamp').first()
            
            if last_user_message:
                return {
                    'id': last_user_message.id,
                    'content': last_user_message.content,
                    'timestamp': last_user_message.timestamp
                }
            return None
        except Exception as e:
            logger.error(f"获取最后一条用户消息失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
            
    @database_sync_to_async
    def delete_subsequent_ai_messages(self, conversation_id, user_message_timestamp):
        """删除指定用户消息后的所有AI回复"""
        try:
            # 删除时间戳晚于用户消息的所有AI回复
            messages_to_delete = Message.objects.filter(
                conversation_id=conversation_id,
                is_user=False,
                timestamp__gt=user_message_timestamp
            )
            
            count = messages_to_delete.count()
            if count > 0:
                logger.info(f"删除会话 {conversation_id} 中的 {count} 条AI回复")
                messages_to_delete.delete()
            return count
        except Exception as e:
            logger.error(f"删除后续AI消息失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return 0
