import os

"""
图片上下文处理配置

这个文件包含了图片在AI对话上下文中的处理策略配置。
您可以根据需要调整这些设置来平衡功能性和上下文使用效率。
"""

# 图片上下文策略
# 从环境变量 "IMAGE_CONTEXT_STRATEGY" 读取，默认为 "latest_only"
# 可选值:
# - "all": 所有历史图片都包含在上下文中（占用最多上下文空间，但AI能看到所有图片）
# - "latest_only": 只包含最新的几张图片（平衡方案，推荐）
# - "none": 不包含任何图片内容，只发送图片描述文本（最节省上下文空间）
IMAGE_CONTEXT_STRATEGY = os.environ.get("IMAGE_CONTEXT_STRATEGY", "latest_only")

# 当策略为 "latest_only" 时，最多保留的图片数量
# 从环境变量 "MAX_IMAGES_IN_CONTEXT" 读取，默认为 1
# 建议值: 1-5 张图片
# 注意: 每张图片大约占用 1000-2000 个 token，请根据模型的上下文限制调整
try:
    MAX_IMAGES_IN_CONTEXT = int(os.environ.get("MAX_IMAGES_IN_CONTEXT", 1))
except (ValueError, TypeError):
    MAX_IMAGES_IN_CONTEXT = 1

# 图片质量设置（未来扩展用）
# 可以在这里添加图片压缩、尺寸调整等配置
IMAGE_QUALITY_SETTINGS = {
    'max_width': 1024,
    'max_height': 1024,
    'quality': 85,  # JPEG 质量 (1-100)
}

# 支持的图片格式
SUPPORTED_IMAGE_FORMATS = [
    'image/jpeg',
    'image/png', 
    'image/gif',
    'image/webp'
]

# 图片大小限制 (字节)
MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5MB
