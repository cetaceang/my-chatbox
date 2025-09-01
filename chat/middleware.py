import logging
from django.utils import timezone
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, reverse
from django.conf import settings

logger = logging.getLogger(__name__)

class BanCheckMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # 从 settings 中获取允许未登录或被封禁用户访问的 URL 名称列表
        # 确保这些 URL 名称存在于你的 urls.py 中
        self.allowed_url_names = getattr(settings, 'ALLOWED_URLS_FOR_BANNED_USERS', [
            'login', 'logout', 'signup', # 假设你有这些用户认证相关的 URL 名称
            'admin:index', 'admin:login', # 允许访问 Django admin 登录
            # 可以添加其他允许访问的页面，例如服务条款、联系我们等
        ])
        # 允许访问所有 admin 命名空间下的 URL
        self.allow_admin_namespace = getattr(settings, 'ALLOW_ADMIN_NAMESPACE_FOR_BANNED', True)


    def __call__(self, request):
        # 仅对已登录用户进行检查
        if request.user.is_authenticated:
            try:
                profile = request.user.profile
                is_banned = profile.is_banned
                ban_expires_at = profile.ban_expires_at

                # 检查封禁是否已过期
                if is_banned and ban_expires_at and ban_expires_at < timezone.now():
                    logger.info(f"用户 {request.user.username} 的临时封禁已到期，自动解封。")
                    profile.is_banned = False
                    profile.ban_expires_at = None
                    profile.save()
                    is_banned = False # 更新当前请求的状态

                # 如果用户仍处于封禁状态
                if is_banned:
                    # 检查当前访问的 URL 是否在允许列表中
                    current_url_name = request.resolver_match.url_name if request.resolver_match else None
                    current_namespace = request.resolver_match.namespace if request.resolver_match else None

                    is_allowed = False
                    if current_url_name in self.allowed_url_names:
                        is_allowed = True
                    elif self.allow_admin_namespace and current_namespace == 'admin':
                         is_allowed = True


                    if not is_allowed:
                        logger.warning(f"已封禁用户 {request.user.username} 尝试访问受限 URL: {request.path}")

                        # 根据请求类型返回不同的响应
                        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or 'api/' in request.path:
                            # API 请求或 AJAX 请求，返回 JSON 错误
                            return JsonResponse({
                                'success': False,
                                'message': '您的账户已被封禁，无法执行此操作。'
                            }, status=403)
                        else:
                            # 普通页面请求，可以重定向到特定页面或显示禁止访问消息
                            # 这里简单返回 403 Forbidden 页面
                            # 你可以创建一个专门的 "banned" 页面并重定向过去
                            # return redirect(reverse('banned_page'))
                            return HttpResponseForbidden("<h1>访问被禁止</h1><p>您的账户已被封禁。</p>")

            except AttributeError:
                # 用户可能没有 profile，或者 profile 没有 is_banned 属性
                # 这通常不应该发生，因为 UserProfile 会在用户创建或首次访问时创建
                # 但为了健壮性，这里选择忽略错误，允许访问
                logger.warning(f"用户 {request.user.username} 缺少 profile 或相关属性，跳过封禁检查。")
                pass
            except Exception as e:
                 logger.error(f"检查用户 {request.user.username} 封禁状态时出错: {e}", exc_info=True)
                 # 出现意外错误时，为安全起见，可以选择阻止访问或允许访问
                 # 这里选择允许访问，但记录错误
                 pass


        response = self.get_response(request)
        return response
