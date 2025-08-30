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
    path('api/fetch-models/<int:provider_id>/', admin_api.fetch_provider_models, name='api-fetch-models'),
    path('api/batch-add-models/', admin_api.batch_add_models, name='api-batch-add-models'),
    path('api/admin/users/', admin_api.list_users_api, name='api-admin-list-users'),
    path('api/admin/manage_user_ban/', admin_api.manage_user_ban_status, name='api-admin-manage-user-ban'),
    path('api/admin/set_admin_status/', admin_api.set_admin_status, name='api-admin-set-admin-status'),
    path('api/admin/delete_user/', admin_api.delete_user_api, name='api-admin-delete-user'), # 新增：删除用户
    path('api/debug/', admin_api.debug_api_response, name='api-debug'),

    # WebSocket测试（保留）
    path('test_ws/', pages.ws_test, name='ws-test'), # Moved to pages.py
]
