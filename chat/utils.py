import re

def ensure_valid_api_url(base_url, endpoint):
    """
    确保API URL格式正确，处理可能的反向代理情况

    Args:
        base_url: 提供商的基础URL
        endpoint: API端点路径，如 '/v1/chat/completions'

    Returns:
        完整的API URL
    """
    # 移除尾部斜杠
    base_url = base_url.rstrip('/')

    # 确保endpoint以斜杠开头
    if not endpoint.startswith('/'):
        endpoint = '/' + endpoint

    # 检查是否是反向代理URL（通常不包含http://或https://）
    # If base_url doesn't start with http:// or https://, prepend https://
    if not re.match(r'^https?://', base_url):
        # Defaulting to https for security if protocol is missing
        base_url = 'https://' + base_url

    return base_url + endpoint
