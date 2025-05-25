from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import login, logout
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
import json

from .models import UserProfile

# Create your views here.

def register(request):
    """用户注册视图"""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            # 创建用户资料
            UserProfile.objects.create(user=user)
            login(request, user)
            messages.success(request, '注册成功！')
            return redirect('chat-main') # 注册成功后跳转到聊天主页
    else:
        form = UserCreationForm()
    return render(request, 'users/register.html', {'form': form})

def login_view(request):
    """用户登录视图"""
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, '登录成功！')
            # 登录成功后跳转到用户之前尝试访问的页面，如果没有则跳转到聊天主页
            next_page = request.GET.get('next', 'chat-main')
            return redirect(next_page)
    else:
        form = AuthenticationForm()
    return render(request, 'users/login.html', {'form': form})

def logout_view(request):
    """用户注销视图"""
    logout(request)
    messages.success(request, '已注销。')
    return redirect('chat-main') # 注销后跳转到聊天主页或其他页面

@login_required
@csrf_exempt
def manage_user_role(request):
    """管理用户角色"""
    # 检查当前用户是否是管理员
    try:
        current_user_profile = request.user.profile
    except UserProfile.DoesNotExist:
        # 如果当前用户没有资料，创建一个
        current_user_profile = UserProfile.objects.create(user=request.user)
    
    # 只有管理员可以管理用户角色
    if not current_user_profile.is_admin:
        return JsonResponse({
            'success': False,
            'message': '权限不足，只有管理员可以管理用户角色'
        }, status=403)
    
    if request.method == 'GET':
        # 获取所有用户及其角色
        users_data = []
        for user in User.objects.all():
            try:
                profile = user.profile
            except UserProfile.DoesNotExist:
                profile = UserProfile.objects.create(user=user)
            
            users_data.append({
                'id': user.id,
                'username': user.username,
                'is_admin': profile.is_admin,
                'date_joined': user.date_joined.strftime('%Y-%m-%d %H:%M:%S')
            })
        
        return JsonResponse({'users': users_data})
    
    elif request.method == 'POST':
        # 更新用户角色
        try:
            data = json.loads(request.body)
            user_id = data.get('user_id')
            is_admin = data.get('is_admin', False)
            
            if not user_id:
                return JsonResponse({
                    'success': False,
                    'message': '缺少用户ID'
                }, status=400)
            
            user = get_object_or_404(User, id=user_id)
            
            try:
                profile = user.profile
            except UserProfile.DoesNotExist:
                profile = UserProfile.objects.create(user=user)
            
            profile.is_admin = is_admin
            profile.save()
            
            return JsonResponse({
                'success': True,
                'message': f"已{'设置' if is_admin else '取消'}{user.username}为管理员"
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f"操作失败: {str(e)}"
            }, status=400)
    
    return JsonResponse({
        'success': False,
        'message': '不支持的请求方法'
    }, status=405)

@login_required
@csrf_exempt
def create_first_admin(request):
    """创建第一个管理员用户，不需要管理员权限检查"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            user_id = data.get('user_id')
            
            if not user_id:
                return JsonResponse({
                    'success': False,
                    'message': '缺少用户ID'
                }, status=400)
            
            # 检查是否已经存在管理员
            admin_exists = UserProfile.objects.filter(is_admin=True).exists()
            if admin_exists:
                return JsonResponse({
                    'success': False,
                    'message': '已存在管理员用户，无法使用此API'
                }, status=403)
            
            # 确保用户ID与当前登录用户匹配
            if str(request.user.id) != str(user_id):
                return JsonResponse({
                    'success': False,
                    'message': '只能将自己设为管理员'
                }, status=403)
            
            # 设置当前用户为管理员
            try:
                profile = request.user.profile
            except UserProfile.DoesNotExist:
                profile = UserProfile.objects.create(user=request.user)
            
            profile.is_admin = True
            profile.save()
            
            return JsonResponse({
                'success': True,
                'message': f"已成功将{request.user.username}设为第一个管理员"
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f"操作失败: {str(e)}"
            }, status=400)
    
    return JsonResponse({
        'success': False,
        'message': '不支持的请求方法'
    }, status=405)
