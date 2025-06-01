import redis
import json
import logging
import os # Import os to get environment variables

logger = logging.getLogger(__name__)

# --- Redis Configuration ---
# Get Redis connection details from environment variables with defaults
REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB = int(os.environ.get('REDIS_DB_STOP_STATE', 1)) # Use a separate DB for this state
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', None)

# Create a Redis connection pool
redis_pool = redis.ConnectionPool(
    host=REDIS_HOST,
    port=REDIS_PORT,
    db=REDIS_DB,
    password=REDIS_PASSWORD,
    decode_responses=True # Decode responses to strings automatically
)

# Create a Redis client instance
redis_client = redis.Redis(connection_pool=redis_pool)

# --- REMOVED In-Memory State ---
# STOP_REQUEST_STATE = {}
# SYNC_STOP_STATE_LOCK = threading.Lock() # No longer needed with Redis atomic operations

# --- Redis Key Prefix ---
# Use a prefix to avoid key collisions if Redis is used for other purposes
STOP_STATE_PREFIX = "stop_state:"

def get_redis_key(conversation_id):
    """Generates the Redis key for a given conversation ID."""
    return f"{STOP_STATE_PREFIX}{conversation_id}"

def get_stop_requested_sync(conversation_id):
    """同步获取指定会话的完整终止请求状态 (包括 generation_id) from Redis"""
    conv_id_str = str(conversation_id)
    redis_key = get_redis_key(conv_id_str)
    try:
        # Use HGETALL to get all fields of the hash
        state_data = redis_client.hgetall(redis_key)

        if not state_data:
            # Key doesn't exist, return default state
            return {'requested': False, 'generation_id_to_stop': None}

        # Convert stored strings back to appropriate types
        requested = state_data.get('requested') == 'True' # Compare string 'True'
        generation_id_to_stop = state_data.get('generation_id_to_stop') or None # Empty string becomes None

        state = {'requested': requested, 'generation_id_to_stop': generation_id_to_stop}
        # logger.debug(f"读取 Redis Key '{redis_key}' 的终止请求状态: {state}")
        return state

    except redis.RedisError as e:
        logger.error(f"从 Redis 获取 Key '{redis_key}' 状态时出错: {e}")
        # Fallback to default state on Redis error
        return {'requested': False, 'generation_id_to_stop': None}


def set_stop_requested_sync(conversation_id, requested, generation_id_to_stop=None, ttl=None):
    """
    同步设置指定会话的终止请求状态到 Redis。
    如果 requested 为 True，则必须提供 generation_id_to_stop。
    如果 requested 为 False，则清除 generation_id_to_stop。

    Args:
        conversation_id: 会话ID。
        requested (bool): 是否请求停止。
        generation_id_to_stop (str, optional): 要停止的特定生成ID。Defaults to None.
        ttl (int, optional): Redis 键的生存时间（秒）。如果提供，键将在指定时间后自动删除。Defaults to None.
    """
    conv_id_str = str(conversation_id)
    redis_key = get_redis_key(conv_id_str)

    try:
        if requested and not generation_id_to_stop:
            logger.error(f"尝试为会话 {conv_id_str} 设置停止请求，但未提供 generation_id_to_stop")
            # Set requested=True but generation_id_to_stop as empty string
            state_to_set = {
                'requested': 'True',
                'generation_id_to_stop': ''
            }
        else:
            state_to_set = {
                'requested': 'True' if requested else 'False',
                # Store None as empty string, otherwise store the ID string
                'generation_id_to_stop': str(generation_id_to_stop) if requested and generation_id_to_stop else ''
            }

        # Use HMSET to set multiple fields in the hash atomically
        # Use HMSET to set multiple fields in the hash atomically
        success = redis_client.hmset(redis_key, state_to_set)
        logger.info(f"更新 Redis Key '{redis_key}' 的终止请求状态为: {state_to_set} (Success: {success})")

        # Set TTL if provided and valid
        if ttl is not None:
            try:
                ttl_int = int(ttl)
                if ttl_int > 0:
                    redis_client.expire(redis_key, ttl_int)
                    logger.info(f"为 Redis Key '{redis_key}' 设置 TTL 为 {ttl_int} 秒")
                else:
                    logger.warning(f"提供的 TTL ({ttl}) 无效，必须为正整数。未设置 TTL。")
            except (ValueError, TypeError):
                 logger.warning(f"提供的 TTL ({ttl}) 无效，必须为整数。未设置 TTL。")

    except redis.RedisError as e:
        logger.error(f"向 Redis 设置 Key '{redis_key}' 状态时出错: {e}")


def clear_stop_request_state_sync(conversation_id):
    """同步清理指定会话在 Redis 中的终止请求状态"""
    conv_id_str = str(conversation_id)
    redis_key = get_redis_key(conv_id_str)
    try:
        deleted_count = redis_client.delete(redis_key)
        if deleted_count > 0:
            logger.info(f"清理 Redis Key '{redis_key}' 的终止请求状态")
        # else: logger.debug(f"Redis Key '{redis_key}' 不存在，无需清理")
    except redis.RedisError as e:
        logger.error(f"从 Redis 清理 Key '{redis_key}' 状态时出错: {e}")
