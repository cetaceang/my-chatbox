import redis
import json
import logging
import os

logger = logging.getLogger(__name__)

# --- Redis Configuration ---
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB = int(os.environ.get('REDIS_DB_STOP_STATE', 1))
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', None)

redis_pool = redis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True
)
redis_client = redis.Redis(connection_pool=redis_pool)

# --- Redis Key Prefix ---
STOP_REQUEST_PREFIX = "stop_request:"
DEFAULT_STOP_TTL = 120  # 默认的初始和心跳TTL（秒）

def get_stop_request_key(generation_id):
    """为给定的 generation_id 生成 Redis 键。"""
    return f"{STOP_REQUEST_PREFIX}{generation_id}"

def get_stop_requested_sync(generation_id):
    """
    同步检查指定的 generation_id 是否被请求停止。
    返回: True 如果停止被请求, 否则 False。
    """
    if not generation_id:
        return False
    redis_key = get_stop_request_key(str(generation_id))
    try:
        return redis_client.exists(redis_key) > 0
    except redis.RedisError as e:
        logger.error(f"从 Redis 获取 Key '{redis_key}' 状态时出错: {e}")
        return False

def set_stop_requested_sync(generation_id, ttl=DEFAULT_STOP_TTL):
    """
    同步为指定的 generation_id 设置初始的停止请求标志。
    """
    if not generation_id:
        logger.warning("尝试设置停止请求但未提供 generation_id")
        return
    redis_key = get_stop_request_key(str(generation_id))
    try:
        redis_client.set(redis_key, "1", ex=ttl)
        logger.info(f"为 generation_id '{generation_id}' 设置了停止请求，TTL={ttl}s")
    except redis.RedisError as e:
        logger.error(f"向 Redis 设置 Key '{redis_key}' 状态时出错: {e}")

def touch_stop_request_sync(generation_id, ttl=DEFAULT_STOP_TTL):
    """
    “触摸”一个停止请求键，以延长其生命周期（心跳）。
    只有当键存在时才会更新TTL。
    """
    if not generation_id:
        return
    redis_key = get_stop_request_key(str(generation_id))
    try:
        # 使用 EXPIRE 命令来更新 TTL，如果键存在，它会返回 1
        if redis_client.expire(redis_key, ttl):
            logger.debug(f"为 generation_id '{generation_id}' 的停止请求续期，新的 TTL={ttl}s")
    except redis.RedisError as e:
        logger.error(f"为 Key '{redis_key}' 续期时出错: {e}")

def clear_stop_request_sync(generation_id):
    """
    同步清理指定 generation_id 的停止请求标志。
    """
    if not generation_id:
        return
    redis_key = get_stop_request_key(str(generation_id))
    try:
        deleted_count = redis_client.delete(redis_key)
        if deleted_count > 0:
            logger.info(f"清理了 generation_id '{generation_id}' 的停止请求标志")
    except redis.RedisError as e:
        logger.error(f"从 Redis 清理 Key '{redis_key}' 状态时出错: {e}")
