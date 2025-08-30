import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.exceptions import ObjectDoesNotExist
import requests
import aiohttp
import asyncio
import logging
import threading
import json
import uuid # Import uuid
import aiohttp
import asyncio
import logging
# REMOVED: from asgiref.sync import sync_to_async (No longer needed here)

from .models import Conversation, Message, AIModel
# --- Import new state utils ---
from .state_utils import get_stop_requested_sync, set_stop_requested_sync, clear_stop_request_sync, touch_stop_request_sync
# --- Import response handlers ---
from .response_handlers import extract_response_content, ResponseExtractionError
from .services import generate_ai_response, generate_ai_response_with_image # 导入新的服务函数
import asyncio # 导入 asyncio

logger = logging.getLogger(__name__)

# 配置常量
# AI_REQUEST_TIMEOUT 和 AI_REQUEST_MAX_RETRIES 将在 services.py 中使用

# --- REMOVED Old State Management ---
# STOP_STATE, SYNC_STOP_STATE_LOCK, _get_stop_state_sync, _set_stop_state_sync,
# _delete_stop_state_sync, get_stop_state_async, set_stop_state_async,
# delete_stop_state_async, update_stop_state_sync are removed.
# Use functions from state_utils instead.
# --- END REMOVED ---


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # 验证用户权限
        user = self.scope["user"]
        if user.is_anonymous:
            await self.close()
            return

        conversation_id_str = self.scope['url_route']['kwargs'].get('conversation_id')

        # 处理新会话的情况
        if not conversation_id_str or conversation_id_str.lower() == 'new':
            self.conversation_id = None
            self.conversation_group_name = f'user_{user.id}_new_conversation'
        else:
            self.conversation_id = conversation_id_str
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
        self.current_generation_id = None # Add generation ID tracking
        # 添加一个锁，用于同步终止请求和发送回复
        self.response_lock = asyncio.Lock()

        # 连接时无需进行状态清理
        logger.info(f"Consumer connected for conversation {self.conversation_id or 'new'}.")

    async def disconnect(self, close_code):
        # 检查属性是否存在，如果存在才离开对话组
        if hasattr(self, 'conversation_group_name'):
            await self.channel_layer.group_discard(
                self.conversation_group_name,
                self.channel_name
            )

        # 如果有活跃的请求任务，取消它
        if self.active_request_task:
            self.active_request_task.cancel()
            self.active_request_task = None

        # 断开连接时，状态由TTL自动管理，无需手动清理
        logger.info(f"Consumer disconnected for conversation {self.conversation_id or 'new'}.")

    async def receive(self, text_data):
        """
        接收WebSocket消息 (Refactored try/except structure)
        """
        try: # Outer try block for overall message handling
            text_data_json = json.loads(text_data)

            # --- Handle Stop Generation Request ---
            if text_data_json.get('type') == 'stop_generation':
                generation_id_to_stop = text_data_json.get('generation_id')
                logger.info(f"收到终止生成请求 (来自WebSocket): 会话ID {self.conversation_id}, 目标 GenID: {generation_id_to_stop}")

                if generation_id_to_stop:
                    # 设置初始的停止信号，后续由 aiserivce 的心跳机制来维持
                    set_stop_requested_sync(generation_id_to_stop)
                else:
                    logger.warning(f"停止请求缺少 'generation_id'，无法处理。")

                async with self.response_lock:
                    if self.active_request_task:
                        logger.info("正在取消活跃的AI请求任务 (Consumer.receive)")
                        self.active_request_task.cancel()
                        self.active_request_task = None

                await self.status_message({'message': '', 'clear': True})
                return # Stop processing after handling stop request

            # --- 新的统一消息处理逻辑 ---
            message_type = text_data_json.get('type', 'chat_message') # 默认为聊天消息

            # 如果是新会话，先创建会话
            if not self.conversation_id:
                if message_type not in ['chat_message', 'regenerate', 'image_upload']:
                    await self.send_error("新会话的第一个事件必须是 'chat_message'、'regenerate' 或 'image_upload'")
                    return
                
                new_conversation = await self.create_new_conversation(self.scope["user"])
                if not new_conversation:
                    await self.send_error("创建新会话失败")
                    return
                
                self.conversation_id = new_conversation.id
                
                # 更新 group name 并重新订阅
                old_group_name = self.conversation_group_name
                self.conversation_group_name = f'chat_{self.conversation_id}'
                await self.channel_layer.group_discard(old_group_name, self.channel_name)
                await self.channel_layer.group_add(self.conversation_group_name, self.channel_name)
                
                # 通知客户端新的会话ID
                await self.send(text_data=json.dumps({
                    'type': 'new_conversation_created',
                    'data': {
                        'conversation_id': self.conversation_id,
                        'title': new_conversation.title
                    }
                }))

            if message_type == 'chat_message':
                message = text_data_json.get('message')
                model_id = text_data_json.get('model_id')
                generation_id = text_data_json.get('generation_id') # This is the single, unique ID from the frontend
                is_streaming = text_data_json.get('is_streaming', True)

                if not message or not model_id or not generation_id:
                    await self.send_error("缺少必要参数 (message, model_id, generation_id)")
                    return

                # 保存用户消息
                user_message = await self.save_user_message(self.conversation_id, message, model_id)
                if not user_message:
                    await self.send_error("保存用户消息失败")
                    return
                
                # 向客户端确认用户消息已保存，并更新ID
                # The 'temp_id' for the user message div is the generation_id
                await self.send(text_data=json.dumps({
                    'type': 'user_message_id_update',
                    'temp_id': generation_id,
                    'user_message_id': user_message['id']
                }))

                # Pass the single, trusted generation_id to the service
                asyncio.create_task(
                    generate_ai_response(
                        conversation_id=self.conversation_id,
                        model_id=model_id,
                        user_message_id=user_message['id'],
                        is_regenerate=False,
                        generation_id=generation_id,
                        temp_id=generation_id, # temp_id is the same as generation_id
                        is_streaming=is_streaming
                    )
                )

            elif message_type == 'regenerate':
                message_id = text_data_json.get('message_id')
                model_id = text_data_json.get('model_id')
                generation_id = text_data_json.get('generation_id') # This is the single, unique ID from the frontend
                is_streaming = text_data_json.get('is_streaming', True)

                if not message_id or not model_id or not generation_id:
                    await self.send_error("缺少必要参数 (message_id, model_id, generation_id)")
                    return
                
                # Pass the single, trusted generation_id to the service
                asyncio.create_task(
                    generate_ai_response(
                        conversation_id=self.conversation_id,
                        model_id=model_id,
                        user_message_id=message_id,
                        is_regenerate=True,
                        generation_id=generation_id,
                        temp_id=generation_id, # temp_id is the same as generation_id
                        is_streaming=is_streaming
                    )
                )
                # --- END CORE CHANGE ---

            elif message_type == 'image_upload':
                # 处理图片上传消息
                message = text_data_json.get('message', '')  # 用户输入的文本（可选）
                model_id = text_data_json.get('model_id')
                generation_id = text_data_json.get('generation_id')
                temp_id = text_data_json.get('temp_id')
                file_data = text_data_json.get('file_data')  # Base64编码的文件数据
                file_name = text_data_json.get('file_name')
                file_type = text_data_json.get('file_type')
                is_streaming = text_data_json.get('is_streaming', True)

                if not model_id or not generation_id or not temp_id or not file_data:
                    await self.send_error("缺少必要参数 (model_id, generation_id, temp_id, file_data)")
                    return

                # 保存用户消息（包含文本和图片信息）
                display_message = message if message.strip() else '[图片上传]'
                user_message = await self.save_user_message(self.conversation_id, display_message, model_id)
                if not user_message:
                    await self.send_error("保存用户消息失败")
                    return
                
                # 向客户端确认用户消息已保存，并更新ID
                await self.send(text_data=json.dumps({
                    'type': 'user_message_id_update',
                    'temp_id': temp_id,
                    'user_message_id': user_message['id']
                }))

                # 调用图片处理服务 - 使用generation_id作为temp_id（与纯文本发送保持一致）
                asyncio.create_task(
                    generate_ai_response_with_image(
                        conversation_id=self.conversation_id,
                        model_id=model_id,
                        user_message_id=user_message['id'],
                        generation_id=generation_id,
                        temp_id=generation_id,  # 关键修复：使用generation_id作为temp_id
                        message=message,
                        file_data=file_data,
                        file_name=file_name,
                        file_type=file_type,
                        is_streaming=is_streaming
                    )
                )
            
            else:
                logger.warning(f"收到未知的WebSocket消息类型: {message_type}")
            # End of AI processing block

        except json.JSONDecodeError as e:
             logger.error(f"接收到的WebSocket数据无效 (JSONDecodeError): {text_data} - Error: {str(e)}")
             await self.send(text_data=json.dumps({'type': 'error', 'message': '接收到的数据格式无效。'}))
        except Exception as e: # Outer except for any other unexpected errors
            logger.error(f"处理消息时发生意外错误 (Outer Catch): {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            # Try to send a generic error, but ignore if sending fails
            try:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'处理消息时发生意外错误。' # Keep message generic
                }))
            except Exception:
                pass # Ignore errors during error reporting

    async def chat_message(self, event):
        message = event['message']
        is_user = event.get('is_user', False)
        timestamp = event.get('timestamp', '')
        message_id = event.get('message_id', '')
        event_generation_id = event.get('generation_id') # Get generation ID from event

        # 如果是AI回复（非用户消息），进行最终的发送前检查
        if not is_user:
            # 使用新的、以 generation_id 为中心的检查
            if get_stop_requested_sync(event_generation_id):
                logger.warning(f"chat_message: 检测到针对 GenID {event_generation_id} 的停止请求。在发送到前端前拦截消息 (MsgID: {message_id})。")
                # 消息已经保存在数据库中，但我们阻止它被发送到前端
                return  # 停止处理，不发送消息

        # 如果检查通过（或为用户消息），则发送消息
        logger.info(f"chat_message SENDING: ConvID={self.conversation_id}, MsgID={message_id}, IsUser={is_user}, EventGenID={event_generation_id}")
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': message,
            'is_user': is_user,
            'timestamp': timestamp,
            'message_id': message_id,
            'generation_id': event_generation_id
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
        if not self.conversation_id:
            return True # Allow connection for new conversations
        try:
            Conversation.objects.get(id=self.conversation_id, user=user)
            return True
        except Conversation.DoesNotExist:
            return False

    @database_sync_to_async
    def create_new_conversation(self, user):
        """Creates a new conversation for the given user."""
        try:
            new_conv = Conversation.objects.create(user=user, title="新对话")
            logger.info(f"为用户 {user.id} 创建了新的会话 {new_conv.id}")
            return new_conv
        except Exception as e:
            logger.error(f"为用户 {user.id} 创建新会话失败: {e}")
            return None

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
    async def broadcast_event(self, event_data):
        """
        接收来自 channel layer 的事件并将其广播到客户端。
        'event_data' 的格式为: {'event': {'type': '...', 'data': {...}}}
        """
        # 直接将 'event' 字典发送给客户端
        await self.send(text_data=json.dumps(event_data['event']))

    async def send_error(self, message):
        """向客户端发送格式化的错误消息"""
        await self.send(text_data=json.dumps({'type': 'error', 'message': message}))

    # DEPRECATED: The 'generation_stopped' event is no longer used.
    # The 'generation_end' event with a 'stopped' status provides more precise control.
    # async def generation_stopped(self, event):
    #     """处理生成终止消息"""
    #     pass

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
    def get_current_generation_id(self, conversation_id):
        """获取当前会话正在进行的 Generation ID"""
        try:
            conversation = Conversation.objects.filter(id=conversation_id).only('current_generation_id').first()
            if conversation:
                return conversation.current_generation_id
            return None
        except Exception as e:
            logger.error(f"获取当前 Generation ID 失败: {str(e)}")
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

    # --- ADDED: Database helper functions for generation ID ---
    @database_sync_to_async
    def set_db_generation_id(self, conversation_id, generation_id):
        """Sets the current_generation_id in the Conversation model."""
        try:
            conversation = Conversation.objects.filter(id=conversation_id).first()
            if conversation:
                conversation.current_generation_id = generation_id
                conversation.save(update_fields=['current_generation_id', 'updated_at'])
                logger.info(f"DB: Set current_generation_id to {generation_id} for conversation {conversation_id}")
            else:
                logger.error(f"DB: Failed to set generation ID - Conversation {conversation_id} not found.")
        except Exception as e:
            logger.error(f"DB: Error setting generation ID for conversation {conversation_id}: {e}")

    @database_sync_to_async
    def clear_db_generation_id(self, conversation_id, generation_id_to_clear):
        """Clears the current_generation_id in the Conversation model ONLY IF it matches generation_id_to_clear."""
        try:
            conversation = Conversation.objects.filter(id=conversation_id).first()
            if conversation:
                # Compare as strings for safety with UUIDs
                if str(conversation.current_generation_id) == str(generation_id_to_clear):
                    conversation.current_generation_id = None
                    conversation.save(update_fields=['current_generation_id'])
                    logger.info(f"DB: Cleared matching generation ID {generation_id_to_clear} for conversation {conversation_id}")
                elif conversation.current_generation_id is not None:
                    logger.info(f"DB: Did not clear generation ID for conversation {conversation_id}. DB ID ({conversation.current_generation_id}) != Provided ID ({generation_id_to_clear}).")
                # else: logger.debug(f"DB: Generation ID already None for conversation {conversation_id}.")
            # else: logger.warning(f"DB: Failed to clear generation ID - Conversation {conversation_id} not found.") # Less noisy
        except Exception as e:
            logger.error(f"DB: Error clearing generation ID for conversation {conversation_id}: {e}")
    # --- END ADDED ---

    # --- ADDED: Handle generation_start ---
    async def generation_start(self, event):
        """Handles the generation_start signal from the API view."""
        generation_id = event.get('generation_id')
        temp_id = event.get('temp_id') # User message ID for regeneration, or temp ID for new message
        logger.info(f"Consumer {self.channel_name}: Received generation_start signal for GenID {generation_id}, TempID/UserMsgID: {temp_id}")

        # Store the current generation ID being processed by this consumer instance
        # This helps track which generation this specific connection is waiting for
        # Note: We store the string representation for consistency if needed elsewhere,
        # but self.current_generation_id itself might be used directly if type consistency is maintained.
        self.current_generation_id = generation_id

        # Forward the start signal to the client, including the generation_id and temp_id
        await self.send(text_data=json.dumps({
            'type': 'generation_start',
            'data': {
                'generation_id': generation_id,
                'temp_id': temp_id
            }
        }))
        logger.info(f"Consumer {self.channel_name}: Forwarded 'generation_start' to client for GenID {generation_id}")
    # --- END: Handle generation_start ---

    async def handle_image_upload_async(self, conversation_id, model_id, user_message_id, generation_id, temp_id, message, file_data, file_name, file_type, is_streaming):
        """处理图片上传的异步方法"""
        import base64
        import tempfile
        import os
        from .services import generate_ai_response_with_image
        
        try:
            logger.info(f"开始处理图片上传: ConvID={conversation_id}, GenID={generation_id}")
            
            # 解码Base64文件数据
            try:
                file_content = base64.b64decode(file_data)
            except Exception as e:
                logger.error(f"解码Base64文件数据失败: {e}")
                await self.send_error("文件数据格式错误")
                return
            
            # 创建临时文件
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type.split('/')[-1]}") as temp_file:
                temp_file.write(file_content)
                temp_file_path = temp_file.name
            
            try:
                # 调用图片处理服务
                await generate_ai_response_with_image(
                    conversation_id=conversation_id,
                    model_id=model_id,
                    user_message_id=user_message_id,
                    generation_id=generation_id,
                    temp_id=temp_id,
                    message=message,
                    image_path=temp_file_path,
                    is_streaming=is_streaming
                )
            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_file_path)
                except Exception as e:
                    logger.warning(f"清理临时文件失败: {e}")
                    
        except Exception as e:
            logger.error(f"处理图片上传时出错: {e}")
            import traceback
            logger.error(traceback.format_exc())
            await self.send_error(f"图片处理失败: {str(e)}")

    @database_sync_to_async
    def delete_ai_message(self, message_id):
        """删除AI消息"""
        try:
            message = Message.objects.filter(id=message_id, is_user=False).first()
            if message:
                message.delete()
                logger.info(f"已删除AI消息 ID: {message_id}")
                return True
            else:
                logger.warning(f"未找到要删除的AI消息 ID: {message_id}")
                return False
        except Exception as e:
            logger.error(f"删除AI消息失败: {e}")
            return False
