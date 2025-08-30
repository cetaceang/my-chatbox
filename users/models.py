from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver

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

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """
    当一个新的User对象被创建时，自动创建一个关联的UserProfile。
    """
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """
    当User对象被保存时，确保其关联的UserProfile也被保存。
    """
    instance.profile.save()
