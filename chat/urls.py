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
    path('api/debug/', admin_api.debug_api_response, name='api-debug'),

    # API接口 - Core Chat
    path('api/chat/', api.chat_api, name='api-chat'),
    path('api/conversations/', api.conversations_api, name='api-conversations'),
    path('api/messages/edit/', api.edit_message_api, name='api-edit-message'),
    path('api/messages/regenerate/', api.regenerate_message_api, name='api-regenerate-message'),
    path('api/messages/delete/', api.delete_message_api, name='api-delete-message'),
    path('api/messages/<str:conversation_id>/', api.messages_api, name='api-messages'),
    path('api/sync_conversation/', api.sync_conversation_api, name='api-sync-conversation'),

    # WebSocket测试（保留）
    path('test_ws/', pages.ws_test, name='ws-test'), # Moved to pages.py
]
