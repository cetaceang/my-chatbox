from django.db import models
from django.conf import settings
import json
import uuid
import re
import logging
from django.core.files.storage import default_storage
from django.db.models.signals import post_delete
from django.dispatch import receiver

logger = logging.getLogger(__name__)

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
    system_prompt = models.TextField(blank=True, null=True, verbose_name="系统提示词")

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
    generation_id = models.UUIDField(null=True, blank=True, help_text="与此消息相关的生成事件的唯一ID")
    
    class Meta:
        verbose_name = "消息"
        verbose_name_plural = "消息"
        ordering = ['timestamp']
    
    def __str__(self):
        return f"{'用户' if self.is_user else 'AI'}: {self.content[:50]}..."

@receiver(post_delete, sender=Message)
def delete_message_file_on_delete(sender, instance, **kwargs):
    """
    当一个Message对象被删除时，通过信号触发，检查其内容是否包含文件引用。
    如果包含，则删除对应的物理文件。
    这个方法能确保所有删除方式（单条、批量、级联）都能清理文件。
    """
    try:
        # 使用正则表达式从消息内容中提取文件路径
        # 格式为 [file:path/to/your/file.jpg]
        file_match = re.search(r'\[file:(.*?)\]', instance.content)
        if file_match:
            file_path = file_match.group(1)
            # 检查文件是否存在于默认存储中
            if default_storage.exists(file_path):
                # 删除文件
                default_storage.delete(file_path)
                logger.info(f"成功删除与消息 {instance.id} 关联的文件: {file_path}")
            else:
                logger.warning(f"尝试删除与消息 {instance.id} 关联的文件，但文件不存在: {file_path}")
    except Exception as e:
        logger.error(f"删除消息 {instance.id} 的关联文件时发生错误: {e}", exc_info=True)
