from django.db import models
from django.conf import settings
import json
import uuid

# Create your models here.

class AIProvider(models.Model):
    """AI服务提供商"""
    name = models.CharField(max_length=100, verbose_name="服务名称")
    base_url = models.URLField(verbose_name="基础URL")
    api_key = models.CharField(max_length=500, verbose_name="API密钥")
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    
    class Meta:
        verbose_name = "AI服务提供商"
        verbose_name_plural = "AI服务提供商"
    
    def __str__(self):
        return self.name

class AIModel(models.Model):
    """AI模型"""
    provider = models.ForeignKey(AIProvider, on_delete=models.CASCADE, verbose_name="服务提供商")
    model_name = models.CharField(max_length=100, verbose_name="模型名称")
    display_name = models.CharField(max_length=100, verbose_name="显示名称")
    max_context = models.IntegerField(default=4096, verbose_name="最大上下文长度")
    max_history_messages = models.IntegerField(default=20, verbose_name="历史消息数量限制")
    default_params = models.JSONField(default=dict, verbose_name="默认参数",blank=True)
    is_active = models.BooleanField(default=True, verbose_name="是否启用")
    
    class Meta:
        verbose_name = "AI模型"
        verbose_name_plural = "AI模型"
    
    def __str__(self):
        return f"{self.provider.name} - {self.display_name}"

class Conversation(models.Model):
    """对话"""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, verbose_name="用户")
    title = models.CharField(max_length=200, default="新对话", verbose_name="对话标题")
    selected_model = models.ForeignKey(AIModel, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="选择的模型")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")
    sync_id = models.UUIDField(default=uuid.uuid4, unique=True, verbose_name="同步标识符")
    # 新增字段，用于跟踪当前API驱动的生成ID
    current_generation_id = models.UUIDField(null=True, blank=True, editable=False, help_text="当前正在处理的API生成的唯一ID")

    class Meta:
        verbose_name = "对话"
        verbose_name_plural = "对话"
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"{self.user.username} - {self.title}"

class Message(models.Model):
    """消息"""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, verbose_name="所属对话")
    content = models.TextField(verbose_name="消息内容")
    is_user = models.BooleanField(default=True, verbose_name="是否用户消息")
    model_used = models.ForeignKey(AIModel, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="使用的模型")
    timestamp = models.DateTimeField(auto_now_add=True, verbose_name="时间戳")
    
    class Meta:
        verbose_name = "消息"
        verbose_name_plural = "消息"
        ordering = ['timestamp']
    
    def __str__(self):
        return f"{'用户' if self.is_user else 'AI'}: {self.content[:50]}..."
