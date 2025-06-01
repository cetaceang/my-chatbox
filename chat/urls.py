from django.urls import path
from .views import pages, api, admin_api

urlpatterns = [
    # 主要页面
    path('', pages.chat_view, name='chat-main'),
    path('history/', pages.history_view, name='chat-history'),
    path('settings/', pages.settings_view, name='chat-settings'),
    path('api_debug/', pages.api_debug_view, name='api-debug-page'), # Moved to pages.py
    path('conversation_list/', pages.conversation_list_view, name='conversation-list'), # 新增：获取对话列表HTML片段

    # API接口 - Admin/Management
    path('api/models/', admin_api.get_models_api, name='api-models'),
    path('api/providers/', admin_api.manage_providers_api, name='api-providers'),
    path('api/test-connection/<int:provider_id>/', admin_api.test_api_connection, name='api-test-connection'),
    path('api/admin/users/', admin_api.list_users_api, name='api-admin-list-users'), # 新增：获取用户列表
    path('api/admin/manage_user_ban/', admin_api.manage_user_ban_status, name='api-admin-manage-user-ban'), # 新增：管理用户封禁状态
    path('api/debug/', admin_api.debug_api_response, name='api-debug'),
    path('api/debug_response/', api.debug_response_api, name='api-debug-response'),

    # API接口 - Core Chat
    path('api/chat/', api.chat_api, name='api-chat'),
    path('api/upload_file/', api.upload_file_api, name='api-upload-file'),  # 新增：文件上传API
    path('api/conversations/', api.conversations_api, name='api-conversations'),
    path('api/messages/edit/', api.edit_message_api, name='api-edit-message'),
    path('api/messages/regenerate/', api.regenerate_message_api, name='api-regenerate-message'),
    path('api/messages/delete/', api.delete_message_api, name='api-delete-message'),
    path('api/messages/<str:conversation_id>/', api.messages_api, name='api-messages'),
    path('api/sync_conversation/', api.sync_conversation_api, name='api-sync-conversation'),
    path('api/stop_generation/', api.stop_generation_api, name='api-stop-generation'),  # 新增：终止生成API

    # WebSocket测试（保留）
    path('test_ws/', pages.ws_test, name='ws-test'), # Moved to pages.py
]
