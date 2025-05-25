from django.urls import re_path
from . import consumers

# 使用延迟导入避免循环导入问题
websocket_urlpatterns = [
    re_path(r'ws/chat/(?P<conversation_id>\d+)/$', consumers.ChatConsumer.as_asgi()),
]
