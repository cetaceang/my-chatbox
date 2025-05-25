import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.exceptions import ObjectDoesNotExist
import requests
import aiohttp
import asyncio
import logging

from .models import Conversation, Message, AIModel

logger = logging.getLogger(__name__)

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
    
    async def disconnect(self, close_code):
        # 离开对话组
        await self.channel_layer.group_discard(
            self.conversation_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """
        接收WebSocket消息
        """
        try:
            text_data_json = json.loads(text_data)
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
        
        # 发送消息到WebSocket
        await self.send(text_data=json.dumps({
            'message': message,
            'is_user': is_user,
            'timestamp': timestamp,
            'message_id': message_id
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
            
            # 发送请求到AI服务
            response_content = await self.send_request_to_ai(api_info, messages, model)
            
            if response_content:
                # 保存AI回复
                ai_message = await self.save_ai_message(
                    conversation['id'], response_content, model['id']
                )
                
                # 发送AI回复
                await self.channel_layer.group_send(
                    self.conversation_group_name,
                    {
                        'type': 'chat_message',
                        'message': response_content,
                        'is_user': False,
                        'message_id': ai_message['id'],
                        'timestamp': ai_message['timestamp'].strftime('%H:%M:%S')
                    }
                )
            else:
                # 发送错误消息
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': '获取AI回复失败'
                }))
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
            # 构建请求数据
            request_data = {
                "model": api_info['model_name'],
                "messages": messages,
                "stream": False,  # 使用非流式响应
                **api_info['params']
            }
            
            # 使用aiohttp发送异步请求
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_info['url'],
                    json=request_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_info['key']}"
                    },
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    # 处理响应
                    if response.status == 200:
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
                    logger.error(f"AI服务响应错误: {response.status} - {response_text}")
                    return None
        except Exception as e:
            logger.error(f"发送请求到AI服务失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return None
