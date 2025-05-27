import json
import logging
import requests
import traceback

from django.shortcuts import get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required

from chat.models import AIProvider, AIModel
from .utils import ensure_valid_api_url # Import from local utils
from .decorators import admin_required # Import from local decorators
from users.models import UserProfile # Assuming UserProfile is in users.models

logger = logging.getLogger(__name__)

# API接口 - 管理功能 (通常需要管理员权限)

@login_required
@csrf_exempt
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def get_models_api(request):
    """获取和管理AI模型 (管理员可管理，普通用户可查看活跃模型)"""
    # 检查用户是否为管理员
    is_admin = False
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            is_admin = profile.is_admin
        except UserProfile.DoesNotExist:
            # Profile might not exist yet for a user
            is_admin = False
        except AttributeError:
             # Handle cases where 'profile' might not be directly accessible
             is_admin = False


    if request.method == 'GET':
        # 获取单个模型详情 (对所有登录用户开放)
        model_id = request.GET.get('id')
        if model_id:
            try:
                model = get_object_or_404(AIModel, id=model_id)
                # Optionally restrict if model or provider is inactive for non-admins
                if not is_admin and (not model.is_active or not model.provider.is_active):
                     return JsonResponse({'success': False, 'message': "模型不可用"}, status=404)

                return JsonResponse({
                    'models': [{ # Keep structure consistent with list view
                        'id': model.id,
                        'provider_id': model.provider.id,
                        'provider_name': model.provider.name, # Add provider name
                        'model_name': model.model_name,
                        'display_name': model.display_name,
                        'max_context': model.max_context,
                        'max_history_messages': model.max_history_messages,
                        'is_active': model.is_active,
                        'default_params': model.default_params, # Include default params
                    }]
                })
            except Exception as e:
                logger.error(f"获取模型详情失败: {e}")
                return JsonResponse({
                    'success': False,
                    'message': f"获取模型详情失败: {str(e)}"
                }, status=400)

        # 获取所有模型列表
        if is_admin:
            # 管理员可以看到所有模型
            models = AIModel.objects.all().order_by('provider__name', 'display_name')
        else:
            # 普通用户只能看到活跃的模型
            models = AIModel.objects.filter(is_active=True, provider__is_active=True).order_by('provider__name', 'display_name')

        models_data = []
        for model in models:
            models_data.append({
                'id': model.id,
                'provider': model.provider.name, # Use provider name for display
                'provider_id': model.provider.id,
                'model_name': model.model_name,
                'display_name': model.display_name,
                'max_context': model.max_context,
                'max_history_messages': model.max_history_messages,
                'is_active': model.is_active, # Include active status
                'default_params': model.default_params, # Include default params
            })

        return JsonResponse({'models': models_data})

    # --- 以下操作需要管理员权限 ---
    if not is_admin:
        return JsonResponse({
            'success': False,
            'message': "权限不足，只有管理员可以管理AI模型"
        }, status=403)

    # Use the admin_required decorator for POST, PUT, DELETE
    if request.method == 'POST':
        return add_model(request)
    elif request.method == 'PUT':
        return update_model(request)
    elif request.method == 'DELETE':
        return delete_model(request)

    return HttpResponseBadRequest("不支持的请求方法")

@admin_required # Apply decorator here
def add_model(request):
    """添加新模型 (管理员)"""
    try:
        data = json.loads(request.body)
        provider = get_object_or_404(AIProvider, id=data.get('provider_id'))

        # Validate required fields
        required_fields = ['model_name', 'display_name']
        if not all(field in data and data[field] for field in required_fields):
             return JsonResponse({'success': False, 'message': "缺少必要的模型信息 (model_name, display_name)"}, status=400)


        model = AIModel.objects.create(
            provider=provider,
            model_name=data.get('model_name'),
            display_name=data.get('display_name'),
            max_context=data.get('max_context', 4096),
            max_history_messages=data.get('max_history_messages', 10),
            is_active=data.get('is_active', True),
            default_params=data.get('default_params', {}) # Add default params
        )

        return JsonResponse({
            'success': True,
            'model_id': model.id,
            'message': f"成功添加模型: {model.display_name}"
        })
    except Exception as e:
        logger.error(f"添加模型失败: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"添加失败: {str(e)}"
        }, status=400)

@admin_required # Apply decorator here
def update_model(request):
    """更新模型 (管理员)"""
    try:
        data = json.loads(request.body)
        model_id = data.get('id')

        if not model_id:
            return JsonResponse({'success': False, 'message': "缺少模型ID"}, status=400)

        model = get_object_or_404(AIModel, id=model_id)

        # 更新字段
        if 'provider_id' in data:
            provider = get_object_or_404(AIProvider, id=data.get('provider_id'))
            model.provider = provider
        if 'model_name' in data:
            model.model_name = data['model_name']
        if 'display_name' in data:
            model.display_name = data['display_name']
        if 'max_context' in data:
            model.max_context = data['max_context']
        if 'max_history_messages' in data:
            model.max_history_messages = data['max_history_messages']
        if 'is_active' in data:
            model.is_active = data['is_active']
        if 'default_params' in data: # Add default params update
            model.default_params = data['default_params']


        model.save()

        return JsonResponse({
            'success': True,
            'message': f"成功更新模型: {model.display_name}"
        })
    except Exception as e:
        logger.error(f"更新模型失败: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"更新失败: {str(e)}"
        }, status=400)

@admin_required # Apply decorator here
def delete_model(request):
    """删除模型 (管理员)"""
    try:
        # Assuming ID comes from URL parameter or request body
        model_id = request.GET.get('id') # Or read from request body if DELETE has body
        if not model_id:
             try:
                 data = json.loads(request.body)
                 model_id = data.get('id')
             except json.JSONDecodeError:
                 pass # Keep model_id as None if body is not valid JSON

        if not model_id:
            return JsonResponse({'success': False, 'message': "缺少模型ID"}, status=400)

        model = get_object_or_404(AIModel, id=model_id)
        model_name = model.display_name
        model.delete()

        return JsonResponse({
            'success': True,
            'message': f"成功删除模型: {model_name}"
        })
    except Exception as e:
        logger.error(f"删除模型失败: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"删除失败: {str(e)}"
        }, status=400)


@login_required # Login required, but admin check inside
@csrf_exempt
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def manage_providers_api(request):
    """管理AI服务提供商 (管理员)"""
    # Check admin status first for all methods
    is_admin = False
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            is_admin = profile.is_admin
        except UserProfile.DoesNotExist:
            is_admin = False
        except AttributeError:
             is_admin = False

    if not is_admin:
         # Allow GET for listing active providers for non-admins?
         # For now, restrict all provider management to admins.
         return JsonResponse({'success': False, 'message': "权限不足"}, status=403)


    if request.method == 'GET':
        # 获取所有服务提供商 (管理员)
        providers = AIProvider.objects.all().order_by('name')
        providers_data = []

        for provider in providers:
            providers_data.append({
                'id': provider.id,
                'name': provider.name,
                'base_url': provider.base_url,
                'is_active': provider.is_active,
                'created_at': provider.created_at,
                # Do NOT return api_key here for security
            })

        return JsonResponse({'providers': providers_data})

    elif request.method == 'POST':
        # 添加新的服务提供商 (管理员)
        try:
            data = json.loads(request.body)
            required_fields = ['name', 'base_url', 'api_key']
            if not all(field in data and data[field] for field in required_fields):
                 return JsonResponse({'success': False, 'message': "缺少必要的提供商信息 (name, base_url, api_key)"}, status=400)

            provider = AIProvider.objects.create(
                name=data['name'],
                base_url=data['base_url'],
                api_key=data['api_key'],
                is_active=data.get('is_active', True)
            )

            return JsonResponse({
                'success': True,
                'provider_id': provider.id,
                'message': f"成功添加服务提供商: {provider.name}"
            })
        except Exception as e:
            logger.error(f"添加服务提供商失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"添加失败: {str(e)}"
            }, status=400)

    elif request.method == 'PUT':
        # 更新服务提供商 (管理员)
        try:
            data = json.loads(request.body)
            provider_id = data.get('id')

            if not provider_id:
                return JsonResponse({'success': False, 'message': "缺少提供商ID"}, status=400)

            provider = get_object_or_404(AIProvider, id=provider_id)

            # 更新字段
            if 'name' in data:
                provider.name = data['name']
            if 'base_url' in data:
                provider.base_url = data['base_url']
            # Allow updating API key only if explicitly provided
            if 'api_key' in data and data['api_key']: # Check if key is provided and not empty
                provider.api_key = data['api_key']
            if 'is_active' in data:
                provider.is_active = data['is_active']

            provider.save()

            return JsonResponse({
                'success': True,
                'message': f"成功更新服务提供商: {provider.name}"
            })
        except Exception as e:
            logger.error(f"更新服务提供商失败: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"更新失败: {str(e)}"
            }, status=400)

    elif request.method == 'DELETE':
        # 删除服务提供商 (管理员)
        try:
            # Assuming ID comes from URL parameter or request body
            provider_id = request.GET.get('id') # Or read from request body
            if not provider_id:
                 try:
                     data = json.loads(request.body)
                     provider_id = data.get('id')
                 except json.JSONDecodeError:
                     pass

            if not provider_id:
                return JsonResponse({'success': False, 'message': "缺少提供商ID"}, status=400)

            provider = get_object_or_404(AIProvider, id=provider_id)
            provider_name = provider.name
            provider.delete()

            return JsonResponse({
                'success': True,
                'message': f"成功删除服务提供商: {provider_name}"
            })
        except Exception as e:
            logger.error(f"删除服务提供商失败: {str(e)}")
            # Check for protected error if models depend on it
            from django.db.models import ProtectedError
            if isinstance(e, ProtectedError):
                 return JsonResponse({
                    'success': False,
                    'message': f"删除失败: 无法删除提供商 '{provider_name}'，因为它仍被一个或多个AI模型使用。"
                 }, status=400)
            return JsonResponse({
                'success': False,
                'message': f"删除失败: {str(e)}"
            }, status=400)

    return HttpResponseBadRequest("不支持的请求方法")


@admin_required # Only admins should test connections directly
@require_http_methods(["GET"])
def test_api_connection(request, provider_id):
    """测试与AI提供商API的连接 (管理员)"""
    try:
        provider = get_object_or_404(AIProvider, id=provider_id)

        # 构建测试URL (e.g., list models endpoint)
        api_url = ensure_valid_api_url(provider.base_url, "/v1/models")

        # 发送请求
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {provider.api_key}"
        }

        logger.info(f"测试API连接: {api_url}")

        try:
            response = requests.get(
                api_url,
                headers=headers,
                timeout=15 # Slightly longer timeout for testing
            )

            status_code = response.status_code
            response_data = {}
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                response_data = {"raw_text": response.text[:500]} # Limit raw text size

            if status_code == 200:
                # 成功连接
                models_count = len(response_data.get('data', [])) if isinstance(response_data.get('data'), list) else 'N/A'
                return JsonResponse({
                    'success': True,
                    'message': f"连接成功! 发现 {models_count} 个可用模型。",
                    'status_code': status_code,
                    'models_count': models_count,
                    'response_preview': response_data # Include preview
                })
            elif status_code == 404:
                # API端点未找到
                return JsonResponse({
                    'success': False,
                    'message': f"API端点未找到(404)。请检查基础URL ({provider.base_url}) 和端点路径 (/v1/models) 是否正确。",
                    'status_code': status_code,
                    'details': "您的API可能使用了不同的端点路径。如果您使用的是反向代理，请确保正确配置了路径。",
                    'response_preview': response_data
                })
            elif status_code == 401:
                # 认证失败
                return JsonResponse({
                    'success': False,
                    'message': "认证失败(401)。请检查API密钥是否正确。",
                    'status_code': status_code,
                     'response_preview': response_data
                })
            else:
                # 其他错误
                error_message = response_data.get('error', {}).get('message', '未知API错误') if isinstance(response_data.get('error'), dict) else '未知API错误'
                return JsonResponse({
                    'success': False,
                    'message': f"API请求失败: {status_code} - {error_message}",
                    'status_code': status_code,
                    'response_preview': response_data
                })

        except requests.exceptions.Timeout:
             logger.error(f"API连接测试超时: {api_url}")
             return JsonResponse({
                 'success': False,
                 'message': f"连接超时 ({api_url})。请检查网络连接和基础URL。",
                 'error': 'Timeout'
             }, status=408) # Request Timeout
        except requests.exceptions.ConnectionError as e:
             logger.error(f"API连接测试失败 (ConnectionError): {str(e)}")
             return JsonResponse({
                 'success': False,
                 'message': f"无法连接到API ({api_url})。请检查基础URL是否正确以及服务是否正在运行。",
                 'error': str(e)
             }, status=503) # Service Unavailable
        except requests.exceptions.RequestException as e:
            logger.error(f"API连接测试失败 (RequestException): {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f"无法连接到API: {str(e)}",
                'error': str(e)
            }, status=500)

    except Exception as e:
        logger.error(f"测试API连接时出错: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f"处理请求失败: {str(e)}"
        }, status=500)


@admin_required # Only admins should use the raw debug endpoint
@csrf_exempt
@require_http_methods(["POST"])
def debug_api_response(request):
    """直接转发请求到AI服务并返回原始响应，用于调试 (管理员)"""
    try:
        data = json.loads(request.body)
        provider_id = data.get('provider_id')
        request_payload = data.get('payload') # Expect the full payload here

        if not provider_id or not request_payload or not isinstance(request_payload, dict):
            return JsonResponse({
                'success': False,
                'message': "缺少必要参数 (provider_id, payload object)"
            }, status=400)

        # Get provider
        provider = get_object_or_404(AIProvider, id=provider_id)

        # Determine endpoint (default to chat completions, allow override?)
        # For now, assume /v1/chat/completions
        api_endpoint = data.get('endpoint', "/v1/chat/completions")
        api_url = ensure_valid_api_url(provider.base_url, api_endpoint)
        api_key = provider.api_key

        logger.info(f"调试API - 发送请求到: {api_url}")
        logger.info(f"调试API - Payload: {json.dumps(request_payload)}")


        # Send request
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}"
        }

        is_stream = request_payload.get('stream', False)

        try:
            response = requests.post(
                api_url,
                json=request_payload,
                headers=headers,
                stream=is_stream, # Respect stream flag in payload
                timeout=60 # Longer timeout for debugging
            )

            logger.info(f"调试API - 响应状态码: {response.status_code}")

            # Return raw response
            response_headers = dict(response.headers)

            if is_stream:
                 # Collect stream chunks for preview, but don't block indefinitely
                 stream_content = []
                 try:
                     for i, chunk in enumerate(response.iter_lines()):
                         if chunk:
                             stream_content.append(chunk.decode('utf-8', errors='ignore'))
                         if i > 50: # Limit number of chunks in preview
                             stream_content.append("... (stream truncated)")
                             break
                 except Exception as stream_err:
                     logger.error(f"调试API - 读取流时出错: {stream_err}")
                     stream_content = [f"Error reading stream: {stream_err}"]

                 return JsonResponse({
                     'success': response.ok, # Use response.ok for success status
                     'status_code': response.status_code,
                     'is_stream': True,
                     'stream_preview': stream_content,
                     'headers': response_headers
                 })

            else:
                 # Non-stream response
                 try:
                     response_json = response.json()
                     return JsonResponse({
                         'success': response.ok,
                         'status_code': response.status_code,
                         'is_stream': False,
                         'raw_response': response_json,
                         'headers': response_headers
                     })
                 except json.JSONDecodeError:
                     # Handle non-JSON response
                     return JsonResponse({
                         'success': response.ok,
                         'status_code': response.status_code,
                         'is_stream': False,
                         'raw_text': response.text,
                         'headers': response_headers
                     })

        except requests.exceptions.RequestException as req_err:
             logger.error(f"调试API - 请求失败: {req_err}")
             return JsonResponse({
                 'success': False,
                 'message': f"请求失败: {req_err}",
                 'error_type': type(req_err).__name__
             }, status=500)


    except Exception as e:
        logger.error(f"调试API请求失败: {str(e)}")
        logger.error(traceback.format_exc())
        return JsonResponse({
            'success': False,
            'message': f"处理请求失败: {str(e)}"
        }, status=500)
