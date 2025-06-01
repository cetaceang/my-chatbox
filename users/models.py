from django.db import models
from django.contrib.auth.models import User

# Create your models here.
class UserProfile(models.Model):
    """用户个人资料扩展"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    is_admin = models.BooleanField(default=False, verbose_name="是否管理员")
    is_banned = models.BooleanField(default=False, verbose_name="是否被封禁")
    ban_expires_at = models.DateTimeField(null=True, blank=True, verbose_name="封禁到期时间")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    
    class Meta:
        verbose_name = "用户资料"
        verbose_name_plural = "用户资料"
    
    def __str__(self):
        return f"{self.user.username} {'(管理员)' if self.is_admin else ''}"
