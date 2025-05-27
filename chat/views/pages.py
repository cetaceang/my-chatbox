import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

from chat.models import AIProvider, AIModel, Conversation
from users.models import UserProfile # Assuming UserProfile is in users.models

logger = logging.getLogger(__name__)

# 页面视图
@login_required
def chat_view(request):
    """聊天主页视图"""
    # 获取可用的AI模型供选择
    models = AIModel.objects.filter(is_active=True, provider__is_active=True)
    # 获取用户的对话列表，并预取每个对话的消息以高效获取最后一条消息
    # 使用正确的 related_name 'message_set' (因为 Message.conversation 没有指定 related_name)
    conversations = Conversation.objects.filter(user=request.user).order_by('-updated_at').prefetch_related('message_set') # Order by most recent

    # 获取当前对话，如果有指定的话
    conversation_id = request.GET.get('conversation_id')
    no_new = request.GET.get('no_new', '1') == '1'  # 默认不创建新对话
    conversation = None

    logger.info(f"加载聊天页面: conversation_id={conversation_id}, no_new={no_new}, 用户={request.user.username}")

    if conversation_id:
        try:
            conversation = Conversation.objects.get(id=conversation_id, user=request.user)
            logger.info(f"找到指定会话: {conversation.id} - {conversation.title}")
        except Conversation.DoesNotExist:
            # 如果指定的对话不存在或不属于当前用户，则忽略
            logger.warning(f"指定的会话ID {conversation_id} 不存在或不属于当前用户")
            pass # Keep conversation as None

    # 如果没有指定对话或指定的对话无效，且no_new不为True，则创建一个新对话
    if not conversation and not no_new and models.exists():
        # 选择第一个可用的模型作为默认模型
        default_model = models.first()
        conversation = Conversation.objects.create(
            user=request.user,
            title=f"新对话 {Conversation.objects.filter(user=request.user).count() + 1}",
            selected_model=default_model
        )
        logger.info(f"创建了新会话: {conversation.id} - {conversation.title}, 准备重定向...")
        # 创建后立即重定向到新会话的URL
        return redirect(f"{request.path}?conversation_id={conversation.id}")
    # 如果no_new为True且没有指定对话，则尝试使用最近的对话
    elif not conversation and no_new and conversations.exists():
        conversation = conversations.first()
        logger.info(f"使用最近的会话: {conversation.id} - {conversation.title}")

    if conversation:
        logger.info(f"最终使用的会话: {conversation.id} - {conversation.title}")
    else:
        logger.warning("无法获取或创建有效会话") # This might happen if no models exist or if ID was invalid

    context = {
        'models': models,
        'conversations': conversations,
        'conversation': conversation,
    }
    return render(request, 'chat/chat.html', context)

@login_required
def history_view(request):
    """聊天历史记录视图"""
    conversations = Conversation.objects.filter(user=request.user).order_by('-updated_at')
    context = {
        'conversations': conversations,
    }
    return render(request, 'chat/history.html', context)

@login_required
def settings_view(request):
    """API设置视图"""
    # 检查用户是否为管理员
    try:
        profile = request.user.profile
        is_admin = profile.is_admin
    except UserProfile.DoesNotExist:
        is_admin = False
        # 如果用户没有资料，创建一个
        profile = UserProfile.objects.create(user=request.user)
    except AttributeError: # Handle case where user object might not have 'profile' yet
        is_admin = False
        profile = UserProfile.objects.create(user=request.user)


    # 所有用户都可以访问设置页面，但内容会有所不同
    providers = AIProvider.objects.all()
    models = AIModel.objects.all()
    users = []

    # 如果是管理员，还获取用户列表
    if is_admin:
        for user in User.objects.all():
            try:
                user_profile = user.profile
            except UserProfile.DoesNotExist:
                user_profile = UserProfile.objects.create(user=user)

            users.append({
                'id': user.id,
                'username': user.username,
                'is_admin': user_profile.is_admin,
                'date_joined': user.date_joined
            })

    context = {
        'providers': providers,
        'models': models,
        'users': users,
        'is_admin': is_admin,
        'current_user': request.user
    }
    return render(request, 'chat/settings.html', context)

def ws_test(request):
    """WebSocket测试视图"""
    return render(request, 'chat/ws_test.html')

# Note: api_debug_view was previously defined but not used in urls.py based on initial file structure.
# Including it here as per the plan. Ensure it's added to urls.py if needed.
@login_required # Assuming login is required, adjust if not
def api_debug_view(request):
    """API调试页面视图"""
    providers = AIProvider.objects.filter(is_active=True)
    models = AIModel.objects.filter(is_active=True)

    context = {
        'providers': providers,
        'models': models,
    }
    return render(request, 'chat/api_debug.html', context)


from django.template.loader import render_to_string
from django.http import HttpResponse

@login_required
def conversation_list_view(request):
    """获取并返回渲染后的对话列表HTML片段"""
    # 查询对话列表，并确保预取消息以供模板使用
    conversations = Conversation.objects.filter(user=request.user).order_by('-updated_at').prefetch_related('message_set')
    # 使用 render_to_string 只渲染模板片段
    # 路径相对于 Django 查找模板的目录
    html = render_to_string('chat/conversation_list.html', {'conversations': conversations, 'user': request.user})
    return HttpResponse(html)
