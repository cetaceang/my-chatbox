from django.contrib import admin
from .models import AIProvider, AIModel, Conversation, Message

@admin.register(AIProvider)
class AIProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'base_url', 'is_active', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'base_url')

@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'provider', 'model_name', 'max_context', 'is_active')
    list_filter = ('provider', 'is_active')
    search_fields = ('display_name', 'model_name')

@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('title', 'user', 'selected_model', 'created_at', 'updated_at')
    list_filter = ('user', 'selected_model')
    search_fields = ('title',)
    date_hierarchy = 'created_at'

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('get_snippet', 'conversation', 'is_user', 'model_used', 'timestamp')
    list_filter = ('is_user', 'model_used', 'conversation')
    search_fields = ('content',)
    date_hierarchy = 'timestamp'
    
    def get_snippet(self, obj):
        return obj.content[:50] + "..." if len(obj.content) > 50 else obj.content
    get_snippet.short_description = '消息内容'
