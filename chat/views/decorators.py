from django.shortcuts import redirect
from django.contrib.auth.models import User
from users.models import UserProfile # Assuming UserProfile is in users.models

def admin_required(view_func):
    """检查用户是否为管理员的装饰器"""
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            # 如果用户未登录，重定向到登录页面
            return redirect('login') # Or your login URL name

        try:
            # 尝试获取用户资料
            # Use related name 'profile' if defined, otherwise access directly
            profile = getattr(request.user, 'profile', None)
            if not profile:
                 # If profile doesn't exist, maybe create one or handle differently
                 # For now, assume non-admin if no profile
                 profile = UserProfile.objects.create(user=request.user) # Or handle error

            if not profile.is_admin:
                # 如果不是管理员，重定向到主页或其他页面
                return redirect('chat-main') # Or another appropriate URL name
        except UserProfile.DoesNotExist:
             # Handle case where profile genuinely doesn't exist after check/creation attempt
             return redirect('chat-main') # Or handle error
        except Exception as e:
            # Log the error for debugging
            print(f"Error checking admin status: {e}") # Replace with proper logging
            return redirect('chat-main') # Fallback redirect

        # 如果是管理员，继续执行视图函数
        return view_func(request, *args, **kwargs)

    return wrapper
