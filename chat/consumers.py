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
from .state_utils import get_stop_requested_sync, set_stop_requested_sync, clear_stop_request_state_sync
# --- Import response handlers ---
from .response_handlers import extract_response_content, ResponseExtractionError

logger = logging.getLogger(__name__)

# 配置常量
AI_REQUEST_TIMEOUT = 300  # AI请求超时时间（秒）
AI_REQUEST_MAX_RETRIES = 2  # AI请求最大重试次数

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
        self.current_generation_id = None # Add generation ID tracking
        # 添加一个锁，用于同步终止请求和发送回复
        # 添加一个锁，用于同步终止请求和发送回复 (Still useful for local task cancellation)
        self.response_lock = asyncio.Lock()

        # 初始化/清除全局终止状态 (使用新的同步函数)
        # No need for await, call sync function directly
        set_stop_requested_sync(self.conversation_id, False)
        logger.info(f"Consumer connected, ensured stop state is False for {self.conversation_id}")

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

        # 清理全局终止状态 (使用新的同步函数)
        # No need for await
        clear_stop_request_state_sync(self.conversation_id)
        logger.info(f"Consumer disconnected, cleared stop state for {self.conversation_id}")

    async def receive(self, text_data):
        """
        接收WebSocket消息 (Refactored try/except structure)
        """
        try: # Outer try block for overall message handling
            text_data_json = json.loads(text_data)

            # --- Handle Stop Generation Request ---
            if text_data_json.get('type') == 'stop_generation':
                logger.info(f"收到终止生成请求 (来自WebSocket): 会话ID {self.conversation_id}")
                self.stop_requested = True
                logger.info(f"Consumer.receive: Set local self.stop_requested = True for {self.conversation_id}")
                generation_id_to_stop = text_data_json.get('generation_id')
                logger.info(f"收到终止生成请求 (来自WebSocket): 会话ID {self.conversation_id}, 目标 GenID: {generation_id_to_stop}")

                # --- MODIFIED: Add TTL when setting stop state ---
                stop_ttl = 60 # Set TTL to 60 seconds
                if generation_id_to_stop:
                    logger.info(f"Consumer.receive: Requesting stop for conversation {self.conversation_id}, targeting generation ID {generation_id_to_stop}. Setting Redis flag with TTL={stop_ttl}s.")
                    set_stop_requested_sync(self.conversation_id, True, generation_id_to_stop=str(generation_id_to_stop), ttl=stop_ttl)
                else:
                    logger.error(f"Consumer.receive: WebSocket stop_generation message for conversation {self.conversation_id} did NOT include 'generation_id'. Setting general stop flag with TTL={stop_ttl}s.")
                    set_stop_requested_sync(self.conversation_id, True, ttl=stop_ttl)
                # --- END MODIFIED ---

                async with self.response_lock:
                    if self.active_request_task:
                        logger.info("正在取消活跃的AI请求任务 (Consumer.receive)")
                        self.active_request_task.cancel()
                        self.active_request_task = None

                await self.send_status_message('', clear=True)
                await self.channel_layer.group_send(
                    self.conversation_group_name,
                    {'type': 'generation_stopped', 'message': '用户终止了生成'}
                )
                return # Stop processing after handling stop request

            # --- Get Message Data ---
            message = text_data_json.get('message')
            model_id = text_data_json.get('model_id')
            temp_id = text_data_json.get('temp_id', None)

            if not message or not model_id:
                await self.send(text_data=json.dumps({'type': 'error', 'message': '缺少必要参数'}))
                return

            # --- Validate Conversation and Save User Message (Inner Try/Except 1) ---
            user_message = None # Initialize user_message
            try:
                conversation_id = self.conversation_id
                conversation = await self.get_conversation(conversation_id)
                if not conversation:
                    await self.send(text_data=json.dumps({'type': 'error', 'message': '会话不存在'}))
                    return

                user_message = await self.save_user_message(conversation, message, model_id)

                if temp_id:
                    logger.info(f"WebSocket临时ID映射: {temp_id} -> {user_message['id']}")
                    await self.send(text_data=json.dumps({
                        'type': 'user_message_id_update',
                        'temp_id': temp_id,
                        'user_message_id': user_message['id']
                    }))

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
            except Exception as e: # Catch errors during setup before AI call
                logger.error(f"处理用户消息或会话时出错: {str(e)}")
                import traceback
                logger.error(traceback.format_exc())
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'处理用户消息或会话时出错: {str(e)}'
                }))
                return # Stop processing if setup failed

            # --- Process AI Response (Inner Try/Except 2) ---
            # Ensure user_message was successfully created before proceeding
            if not user_message:
                 logger.error("User message object is None, cannot proceed with AI response.")
                 await self.send(text_data=json.dumps({'type': 'error', 'message': '无法处理AI回复，因为用户消息保存失败。'}))
                 return

            try:
                # Reset flags, generate ID, save ID, send start signal
                self.stop_requested = False
                self.termination_message_sent = False
                generation_id = str(uuid.uuid4())
                self.current_generation_id = generation_id
                logger.info(f"Starting AI response generation with ID: {generation_id} for conversation {self.conversation_id}")
                await self.set_db_generation_id(self.conversation_id, generation_id)
                await self.send(text_data=json.dumps({
                    'type': 'generation_start',
                    'generation_id': generation_id,
                    'temp_id': temp_id
                }))
                logger.info(f"Sent generation_start signal for GenID: {generation_id}, TempID: {temp_id}")

                # Call process_ai_response
                await self.process_ai_response(model_id, user_message['id'], generation_id)

            except Exception as e: # Catch errors during AI processing call/setup
                generation_id_to_clear = None
                if hasattr(self, 'current_generation_id') and self.current_generation_id:
                    generation_id_to_clear = self.current_generation_id
                    logger.error(f"处理AI回复时出错 (clearing generation ID {generation_id_to_clear}): {str(e)}")
                    if hasattr(self, 'conversation_id') and self.conversation_id and generation_id_to_clear:
                        await self.clear_db_generation_id(self.conversation_id, generation_id_to_clear)
                    self.current_generation_id = None
                else:
                    logger.error(f"处理AI回复时出错 (no generation ID set): {str(e)}")

                import traceback
                logger.error(traceback.format_exc())
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': f'处理AI回复失败: {str(e)}'
                }))
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

        # 如果是AI回复（非用户消息），进行检查
        if not is_user:
            # --- MODIFIED: Use new sync function (no await needed) ---
            global_stop_state = get_stop_requested_sync(self.conversation_id) # Renamed variable for clarity
            # --- END MODIFIED ---
            # REMOVED: local_stop_flag check
            # REMOVED: Generation ID mismatch check - Consumer's current_generation_id is not relevant for API-pushed messages.
            # generation_id_mismatch = (event_generation_id != self.current_generation_id)

            # Log the state *before* the check
            logger.info(f"chat_message CHECK (AI Message): ConvID={self.conversation_id}, MsgID={message_id}, EventGenID={event_generation_id}, ConsumerCurrentGenID={self.current_generation_id}, GlobalStopState={global_stop_state}") # Renamed log field

            # --- REVISED STOP CHECK ---
            # Check Redis state *at the moment the message is about to be sent*.
            stop_state = get_stop_requested_sync(self.conversation_id)
            event_gen_id_str = str(event_generation_id) if event_generation_id else None
            stop_requested = stop_state.get('requested', False)
            # Ensure target_gen_id is treated as string for comparison, even if it's None initially
            target_gen_id = stop_state.get('generation_id_to_stop')
            target_gen_id_str = str(target_gen_id) if target_gen_id else None

            logger.info(f"chat_message CHECK (AI Message): ConvID={self.conversation_id}, MsgID={message_id}, EventGenID={event_gen_id_str}, StopState={stop_state}")

            # Block if stop is requested AND (it's a general stop OR it targets this specific generation)
            # Use string comparison for target_gen_id_str
            should_block = stop_requested and (target_gen_id_str is None or target_gen_id_str == event_gen_id_str)

            if should_block:
                reason = f"全局状态请求停止 (目标GenID: {target_gen_id_str})" if target_gen_id_str else "全局状态请求通用停止"
                logger.warning(f"chat_message: 检测到停止条件 ({reason})，不发送会话 {self.conversation_id} 的AI回复 (MsgID: {message_id}, EventGenID: {event_gen_id_str})")

                # 如果消息ID存在，尝试删除该消息
                if message_id:
                    deleted = await self.delete_ai_message(message_id)
                    if deleted:
                        logger.info(f"已删除终止后的AI回复消息 ID: {message_id}")
                    else:
                        logger.warning(f"尝试删除终止后的AI回复消息失败或未找到 ID: {message_id}")
                return # Stop processing this message

            # Log if globally stopped but for a different specific ID
            # Use the correct variables: stop_requested and target_gen_id_str
            elif stop_requested and target_gen_id_str is not None and target_gen_id_str != event_gen_id_str:
                 # This condition means global stop was requested BUT didn't match this specific gen ID.
                 # The main 'if should_block:' already handled the case where it *did* match.
                 logger.info(f"chat_message: 全局停止已请求，但目标 GenID ({target_gen_id_str}) 与事件 GenID ({event_gen_id_str}) 不匹配。允许发送消息 (MsgID: {message_id})。")
            # --- End Logging Block ---

            # 如果所有检查都通过 (i.e., not blocked by the 'if should_block:' condition), 则发送消息
            logger.info(f"chat_message SENDING (Passed Checks): ConvID={self.conversation_id}, MsgID={message_id}, IsUser={is_user}, EventGenID={event_gen_id_str}")
            await self.send(text_data=json.dumps({
                'type': 'chat_message',
                'message': message,
                'is_user': is_user,
                'timestamp': timestamp,
                'message_id': message_id,
                'generation_id': event_generation_id # Forward generation ID if available
            }))
            return # ADDED return here to prevent falling through after sending AI message

        # 对于用户消息，直接发送 (This part remains the same)
        logger.info(f"chat_message SENDING (User Message): ConvID={self.conversation_id}, MsgID={message_id}, IsUser={is_user}")
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
    async def process_ai_response(self, model_id, user_message_id, generation_id): # Add generation_id param
        """处理AI回复"""
        final_status = "unknown" # Initialize status tracker
        try:
            # Note: termination_message_sent is reset in receive before calling this

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
                    # --- MODIFIED: Pass generation_id to send_request_to_ai ---
                    # 创建一个任务并保存引用，以便可以取消它
                    self.active_request_task = asyncio.create_task(
                        self.send_request_to_ai(api_info, messages, model, generation_id) # Pass generation_id
                    )
                    # --- END MODIFIED ---

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

            # --- MODIFIED: Check global stop state PRECISELY before saving ---
            stop_state_before_save = get_stop_requested_sync(self.conversation_id)
            # Compare as strings for safety with UUIDs
            if stop_state_before_save.get('requested') and str(stop_state_before_save.get('generation_id_to_stop')) == str(generation_id):
                logger.warning(f"process_ai_response: 检测到针对此生成 ({generation_id}) 的停止请求 (保存前)，不保存AI回复。StopState: {stop_state_before_save}")
                # 清除状态消息
                await self.send_status_message('', clear=True)
                # 发送终止消息给客户端
                if not self.termination_message_sent: # 避免重复发送
                    await self.send(text_data=json.dumps({
                        'type': 'generation_stopped',
                        'message': '生成已被用户终止'
                    }))
                    self.termination_message_sent = True
                # 重置状态 (由 generation_stopped 处理程序负责)
                # set_stop_requested_sync(self.conversation_id, False) # REMOVED - Let handler do it
                return
            # --- END MODIFIED CHECK ---

            # 保存AI回复
            ai_message = await self.save_ai_message(
                conversation['id'], response_content, model['id']
            )

            # --- FINAL PRE-SEND CHECK (Moved Here, No Lock Needed for Sending) ---
            stop_state_final_check = get_stop_requested_sync(self.conversation_id)
            local_stop_flag_final_check = self.stop_requested # 检查本地标志
            global_stop_matches_this_gen_final_check = (
                stop_state_final_check.get('requested') and
                str(stop_state_final_check.get('generation_id_to_stop')) == str(generation_id)
            )

            if global_stop_matches_this_gen_final_check or local_stop_flag_final_check:
                reason = []
                if global_stop_matches_this_gen_final_check:
                    reason.append(f"全局状态停止 (匹配 GenID: {generation_id})")
                if local_stop_flag_final_check:
                    reason.append("本地标志为True")
                logger.warning(f"process_ai_response: FINAL PRE-SEND CHECK detected stop condition ({', '.join(reason)}) for conversation {self.conversation_id}. Deleting message {ai_message['id']} and NOT sending.")
                # 删除刚刚保存的消息
                deleted = await self.delete_ai_message(ai_message['id'])
                if deleted:
                    logger.info(f"已删除终止后的AI回复消息 ID: {ai_message['id']} (pre-send check)")
                else:
                    logger.warning(f"尝试删除终止后的AI回复消息失败或未找到 ID: {ai_message['id']} (pre-send check)")
                # 直接返回，不执行 group_send
                return
            # --- 结束最终发送前检查 ---

            # 如果检查通过，才发送消息
            logger.info(f"process_ai_response: Sending AI message {ai_message['id']} for conversation {self.conversation_id} (passed final pre-send check)")
            await self.channel_layer.group_send( # Corrected indentation
                self.conversation_group_name,
                {
                    'type': 'chat_message',
                        'message': response_content,
                        'is_user': False,
                        'message_id': ai_message['id'],
                        'timestamp': ai_message['timestamp'].isoformat(), # Use ISO format
                        'generation_id': generation_id
                    }
                )
            final_status = "completed" # Mark as completed if sending was successful
        except asyncio.CancelledError:
            logger.info(f"AI响应处理被取消 (Generation ID: {generation_id})")
            final_status = "cancelled" # Set status to cancelled
            # --- MODIFIED: Reset state logic in CancelledError handler ---
            # No longer reset global state here, finally block handles it.
            # Just clear local state and DB ID.
            # --- END MODIFIED ---
            # --- ADDED: Clear DB generation ID on cancellation ---
            await self.clear_db_generation_id(self.conversation_id, generation_id)
            # --- END ADDED ---
            self.current_generation_id = None # Clear local generation ID on cancellation
            # 发送终止消息 (如果尚未发送)
            if not self.termination_message_sent:
                 await self.send(text_data=json.dumps({
                     'type': 'generation_stopped',
                     'message': '生成已终止'
                 }))
                 self.termination_message_sent = True # Mark as sent

        except Exception as e:
            logger.error(f"处理AI回复时出错: {str(e)}") # Log error before clearing ID
            final_status = "failed" # Set status to failed
            # --- ADDED: Clear DB generation ID on general exception ---
            await self.clear_db_generation_id(self.conversation_id, generation_id)
            # --- END ADDED ---
            self.current_generation_id = None # Clear local generation ID on error
            # 发送错误消息 (Send error, not stopped)
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': f'处理AI回复时出错: {str(e)}'
            }))
        finally:
            # --- ADDED: Cleanup in finally block ---
            logger.debug(f"process_ai_response finally block executing for GenID: {generation_id}, ConvID: {self.conversation_id}")
            # 1. Clear DB generation ID if it matches
            await self.clear_db_generation_id(self.conversation_id, generation_id)

            # --- REMOVED: Reset global stop state from finally block (Confirmed) ---
            # RATIONALE: Resetting the global state here creates a race condition.
            # Redis TTL or explicit clearing upon successful stop confirmation is preferred.
            # --- END REMOVED ---

            # --- MODIFIED: Send generation_end signal using final_status ---
            if final_status == "unknown":
                logger.warning(f"process_ai_response finally: Final status is 'unknown' for GenID {generation_id}. Sending 'failed'.")
                final_status = "failed" # Default to failed if status wasn't set

            await self.send(text_data=json.dumps({
                'type': 'generation_end',
                'generation_id': generation_id,
                'status': final_status # Use the determined status
            }))
            logger.info(f"Sent generation_end signal for GenID: {generation_id}, Status: {final_status}")
            # --- END MODIFIED ---

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

    # --- MODIFIED: Add generation_id parameter and Redis checks ---
    async def send_request_to_ai(self, api_info, messages, model, generation_id):
        """发送请求到AI服务，并在关键点检查Redis停止标志"""
        try:
            # --- ADDED: Initial Redis Stop Check ---
            stop_state_initial = get_stop_requested_sync(self.conversation_id)
            if stop_state_initial.get('requested') and str(stop_state_initial.get('generation_id_to_stop')) == str(generation_id):
                logger.warning(f"send_request_to_ai: 检测到针对此生成 ({generation_id}) 的停止请求 (请求开始前)，取消发送。StopState: {stop_state_initial}")
                raise asyncio.CancelledError("后台请求停止 (Initial Check)")
            # --- END ADDED ---

            # 检查本地标志 (Keep local check as well)
            if self.stop_requested:
                logger.info("检测到本地终止标志，取消发送AI请求")
                raise asyncio.CancelledError("用户请求终止生成 (Local Check)")

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
                            # --- ADDED: Redis Stop Check After Receiving Response Header ---
                            stop_state_after_response = get_stop_requested_sync(self.conversation_id)
                            if stop_state_after_response.get('requested') and str(stop_state_after_response.get('generation_id_to_stop')) == str(generation_id):
                                logger.warning(f"send_request_to_ai: 检测到针对此生成 ({generation_id}) 的停止请求 (收到响应后)，丢弃响应。StopState: {stop_state_after_response}")
                                raise asyncio.CancelledError("后台请求停止 (After Response Check)")
                            # --- END ADDED ---

                            # 检查本地标志 (Keep local check)
                            if self.stop_requested:
                                logger.info("收到AI响应，但检测到本地终止标志，丢弃响应")
                                raise asyncio.CancelledError("用户请求终止生成 (Local Check After Response)")

                            # --- REFACTORED: Use response_handlers module ---
                            try:
                                extracted_content = await extract_response_content(response)
                                return extracted_content
                            except ResponseExtractionError as extract_err:
                                # Log the specific extraction error and raise a generic Exception
                                # for the retry mechanism in process_ai_response
                                logger.error(f"内容提取失败: {extract_err}")
                                raise Exception(f"未能从AI服务响应中提取有效内容: {extract_err}") from extract_err
                            # --- END REFACTORED ---

                        # Handle non-200 responses
                        elif response.status != 200:
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
            # Catch other exceptions including the one raised from ResponseExtractionError
            logger.error(f"发送请求到AI服务或处理响应时失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

            # 针对特定错误类型提供更具体的错误信息
            error_message = str(e)
            if isinstance(e, asyncio.TimeoutError) or "TimeoutError" in error_message:
                error_msg = "AI服务响应超时，这可能是因为模型生成内容需要更长时间。请稍后再试或考虑简化您的请求。"
                raise asyncio.TimeoutError(error_msg) from e
            elif isinstance(e, asyncio.CancelledError) or "CancelledError" in error_message:
                 error_msg = "请求被取消，可能是因为连接中断或用户请求终止。"
                 raise asyncio.CancelledError(error_msg) from e
            elif isinstance(e.__cause__, ResponseExtractionError): # Check if the cause was our custom error
                # Use the message from the ResponseExtractionError if available
                error_msg = f"处理AI服务响应时出错: {e.__cause__}"
                raise Exception(error_msg) from e # Raise generic Exception for retry
            else:
                error_msg = "与AI服务通信或处理响应时发生错误，请稍后重试。"
                raise Exception(error_msg) from e

    async def send_status_message(self, message, clear=False):
        """发送状态消息到WebSocket"""
        await self.send(text_data=json.dumps({
            'type': 'clear_status' if clear else 'status',
            'message': message
        }))

    async def generation_stopped(self, event):
        """处理生成终止消息"""
        message = event.get('message', '生成已终止')
        logger.info(f"收到终止生成消息: {message} (会话: {self.conversation_id})")

        # REMOVED: 不再检查 self.termination_message_sent。处理器必须运行以重置全局标志。
        # if self.termination_message_sent:
        #     logger.info(f"会话 {self.conversation_id} 已经发送过终止消息，不再重复发送")
        #     return

        # 标记此实例已处理/发送终止消息 (如果需要避免重复发送给 *此* 客户端)
        # 注意：这不应阻止全局标志重置
        if not self.termination_message_sent:
             self.termination_message_sent = True
        else:
             logger.info(f"会话 {self.conversation_id} 此 consumer 实例之前已发送终止消息，但仍将继续处理以重置全局标志。")

        # REMOVED: 不再在此处设置全局标志，stop_generation_api 或 receive 方法已经设置

        # 获取锁，确保终止请求能够立即生效
        async with self.response_lock:
            # 如果有活跃的请求任务，取消它
            if self.active_request_task:
                logger.info("正在取消活跃的AI请求任务")
                self.active_request_task.cancel()
                self.active_request_task = None

            # 设置本地终止标志
            self.stop_requested = True
            logger.info(f"Consumer generation_stopped: Set local self.stop_requested = True for {self.conversation_id}")

        # 清除状态消息
        await self.send_status_message('', clear=True)

        # 删除可能存在的未完成回复 (Keep this logic)
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

        # --- REMOVED: Reset global stop state here ---
        # RATIONALE: Resetting the global state manually here is prone to race conditions.
        # Relying on Redis TTL or explicit clearing only when a task *successfully* stops is safer.
        # try:
        #     set_stop_requested_sync(self.conversation_id, False) # Clear requested flag and generation_id_to_stop
        #     logger.info(f"generation_stopped: Reset global stop state for conversation {self.conversation_id} after confirming stop.")
        # except Exception as stop_reset_err:
        #     logger.error(f"generation_stopped: Error resetting global stop state for conversation {self.conversation_id}: {stop_reset_err}")
        # --- END REMOVED ---

        # --- REMOVED: Old rationale comment --- (Still valid, but code removed above)
        # RATIONALE: Resetting the global state here is incorrect because this handler
        # executes immediately upon receiving the stop *request*, before the targeted
        # generation process (potentially running in the API view) has actually finished.
        # Resetting too early creates race conditions where subsequent, unrelated generations
        # might be incorrectly blocked, or the intended stop might not be properly acknowledged
        # by the time the target generation finishes.
        # The CORRECT approach is for the process *responsible* for the generation
        # identified by 'generation_id_to_stop' to reset the state in its *finally* block,
        # *only if* the global state indicates it was the one targeted for stopping.
        # This logic will be added to the API view and process_ai_response later.
        # --- END REMOVED ---

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
            'type': 'generation_started', # Use a distinct type for client-side handling
            'generation_id': generation_id,
            'temp_id': temp_id # Pass the original temp_id/user_message_id back
        }))
        logger.info(f"Consumer {self.channel_name}: Forwarded 'generation_started' to client for GenID {generation_id}")
    # --- END: Handle generation_start ---

    # --- ADDED: Handler for generation_end signal --- (Moved to class level)
    async def generation_end(self, event):
        """Handles the generation_end signal from the backend (API view or task)."""
        generation_id = event.get('generation_id')
        status = event.get('status', 'unknown') # completed, failed, cancelled
        logger.info(f"Consumer received generation_end signal for GenID: {generation_id}, Status: {status}")

        # Forward the signal to the client WebSocket
        await self.send(text_data=json.dumps({
            'type': 'generation_end',
            'generation_id': generation_id,
            'status': status
        }))
    # --- END ADDED ---
