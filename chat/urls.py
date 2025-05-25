from django.urls import path
from . import views

urlpatterns = [
    # 主要页面
    path('', views.chat_view, name='chat-main'),
    path('history/', views.history_view, name='chat-history'),
    path('settings/', views.settings_view, name='chat-settings'),
    path('api_debug/', views.api_debug_view, name='api-debug-page'),
    
    # API接口
    path('api/models/', views.get_models_api, name='api-models'),
    path('api/providers/', views.manage_providers_api, name='api-providers'),
    path('api/chat/', views.chat_api, name='api-chat'),
    path('api/conversations/', views.conversations_api, name='api-conversations'),
    path('api/messages/edit/', views.edit_message_api, name='api-edit-message'),
    path('api/messages/regenerate/', views.regenerate_message_api, name='api-regenerate-message'),
    path('api/messages/delete/', views.delete_message_api, name='api-delete-message'),
    path('api/messages/<str:conversation_id>/', views.messages_api, name='api-messages'),
    path('api/test-connection/<int:provider_id>/', views.test_api_connection, name='api-test-connection'),
    path('api/debug/', views.debug_api_response, name='api-debug'),
    path('api/sync_conversation/', views.sync_conversation_api, name='api-sync-conversation'),
    
    # WebSocket测试（保留）
    path('test_ws/', views.ws_test, name='ws-test'),
]
