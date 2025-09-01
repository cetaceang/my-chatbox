import logging
from asyncio import iscoroutinefunction
from django.utils import timezone
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, reverse
from django.conf import settings
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)

class BanCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.is_async = iscoroutinefunction(self.get_response)
        self.allowed_url_names = getattr(settings, 'ALLOWED_URLS_FOR_BANNED_USERS', [
            'login', 'logout', 'signup',
            'admin:index', 'admin:login',
        ])
        self.allow_admin_namespace = getattr(settings, 'ALLOW_ADMIN_NAMESPACE_FOR_BANNED', True)

    @database_sync_to_async
    def _check_and_update_ban_status(self, user):
        try:
            profile = user.profile
            is_banned = profile.is_banned
            ban_expires_at = profile.ban_expires_at

            if is_banned and ban_expires_at and ban_expires_at < timezone.now():
                logger.info(f"用户 {user.username} 的临时封禁已到期，自动解封。")
                profile.is_banned = False
                profile.ban_expires_at = None
                profile.save()
                return False # 返回解封后的状态
            return is_banned
        except AttributeError:
            logger.warning(f"用户 {user.username} 缺少 profile 或相关属性，跳过封禁检查。")
            return False
        except Exception as e:
            logger.error(f"检查用户 {user.username} 封禁状态时出错: {e}", exc_info=True)
            return False # 出现意外错误时，为安全起见，允许访问

    async def __call__(self, request):
        if request.user.is_authenticated:
            is_banned = await self._check_and_update_ban_status(request.user)

            if is_banned:
                current_url_name = request.resolver_match.url_name if request.resolver_match else None
                current_namespace = request.resolver_match.namespace if request.resolver_match else None

                is_allowed = False
                if current_url_name in self.allowed_url_names:
                    is_allowed = True
                elif self.allow_admin_namespace and current_namespace == 'admin':
                    is_allowed = True

                if not is_allowed:
                    logger.warning(f"已封禁用户 {request.user.username} 尝试访问受限 URL: {request.path}")
                    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'api/' in request.path:
                        return JsonResponse({
                            'success': False,
                            'message': '您的账户已被封禁，无法执行此操作。'
                        }, status=403)
                    else:
                        return HttpResponseForbidden("<h1>访问被禁止</h1><p>您的账户已被封禁。</p>")

        response = await self.get_response(request)
        return response
