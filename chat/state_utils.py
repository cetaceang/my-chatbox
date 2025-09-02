import os
import json
import logging
import redis
import threading
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# --- 常量 ---
DEFAULT_STOP_TTL = 120  # 停止请求的默认TTL（秒）

# --- 缓存抽象基类 ---
class StopRequestCache(ABC):
    """处理停止请求状态的抽象基类。"""

    @abstractmethod
    def get_stop_requested(self, generation_id):
        """检查给定的 generation_id 是否已被请求停止。"""
        pass

    @abstractmethod
    def set_stop_requested(self, generation_id, ttl=DEFAULT_STOP_TTL):
        """为给定的 generation_id 设置停止请求。"""
        pass

    @abstractmethod
    def touch_stop_request(self, generation_id, ttl=DEFAULT_STOP_TTL):
        """“触摸”一个停止请求以延长其生命周期（心跳）。"""
        pass

    @abstractmethod
    def clear_stop_request(self, generation_id):
        """清除给定 generation_id 的停止请求。"""
        pass

# --- Redis 缓存实现 ---
class RedisCache(StopRequestCache):
    """使用 Redis 作为后端的停止请求处理器。"""
    
    STOP_REQUEST_PREFIX = "stop_request:"

    def __init__(self):
        try:
            redis_host = os.environ.get('REDIS_HOST', 'localhost')
            redis_port = int(os.environ.get('REDIS_PORT', 6379))
            redis_db = int(os.environ.get('REDIS_DB_STOP_STATE', 1))
            redis_password = os.environ.get('REDIS_PASSWORD', None)

            pool = redis.ConnectionPool(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=redis_password,
                decode_responses=True
            )
            self.client = redis.Redis(connection_pool=pool)
            # 测试连接
            self.client.ping()
            logger.info("用于 StopRequestCache 的 Redis 连接成功。")
        except redis.RedisError as e:
            logger.error(f"连接 Redis (用于 StopRequestCache) 失败: {e}")
            raise

    def _get_key(self, generation_id):
        return f"{self.STOP_REQUEST_PREFIX}{generation_id}"

    def get_stop_requested(self, generation_id):
        if not generation_id:
            return False
        key = self._get_key(str(generation_id))
        try:
            return self.client.exists(key) > 0
        except redis.RedisError as e:
            logger.error(f"从 Redis 获取键 '{key}' 时出错: {e}")
            return False

    def set_stop_requested(self, generation_id, ttl=DEFAULT_STOP_TTL):
        if not generation_id:
            logger.warning("尝试设置停止请求但未提供 generation_id。")
            return
        key = self._get_key(str(generation_id))
        try:
            self.client.set(key, "1", ex=ttl)
            logger.info(f"为 generation_id '{generation_id}' 设置了停止请求，TTL={ttl}s。")
        except redis.RedisError as e:
            logger.error(f"在 Redis 中设置键 '{key}' 时出错: {e}")

    def touch_stop_request(self, generation_id, ttl=DEFAULT_STOP_TTL):
        if not generation_id:
            return
        key = self._get_key(str(generation_id))
        try:
            if self.client.expire(key, ttl):
                logger.debug(f"为 generation_id '{generation_id}' 的停止请求续期，新的 TTL={ttl}s。")
        except redis.RedisError as e:
            logger.error(f"为键 '{key}' 续期 TTL 时出错: {e}")

    def clear_stop_request(self, generation_id):
        if not generation_id:
            return
        key = self._get_key(str(generation_id))
        try:
            if self.client.delete(key) > 0:
                logger.info(f"清除了 generation_id '{generation_id}' 的停止请求。")
        except redis.RedisError as e:
            logger.error(f"从 Redis 清除键 '{key}' 时出错: {e}")

# --- 内存缓存实现 ---
class InMemoryCache(StopRequestCache):
    """使用简单字典的内存停止请求处理器。"""
    
    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()
        logger.info("正在为停止请求使用内存缓存。")

    def get_stop_requested(self, generation_id):
        if not generation_id:
            return False
        with self._lock:
            return str(generation_id) in self._cache

    def set_stop_requested(self, generation_id, ttl=DEFAULT_STOP_TTL):
        if not generation_id:
            logger.warning("尝试设置停止请求但未提供 generation_id。")
            return
        with self._lock:
            self._cache[str(generation_id)] = True
            logger.info(f"在内存中为 generation_id '{generation_id}' 设置了停止请求。")

    def touch_stop_request(self, generation_id, ttl=DEFAULT_STOP_TTL):
        # 内存缓存没有 TTL，所以这是一个空操作，但为了日志一致性而记录。
        if self.get_stop_requested(generation_id):
             logger.debug(f"内存中 generation_id '{generation_id}' 的停止请求心跳。")

    def clear_stop_request(self, generation_id):
        if not generation_id:
            return
        with self._lock:
            if self._cache.pop(str(generation_id), None):
                logger.info(f"从内存中清除了 generation_id '{generation_id}' 的停止请求。")

# --- 工厂和单例实例 ---
def _get_cache_instance():
    """根据环境变量创建缓存实例的工厂函数。"""
    cache_type = os.environ.get('CACHE_TYPE', 'memory').lower()
    if cache_type == 'redis':
        try:
            return RedisCache()
        except redis.RedisError:
            logger.warning("Redis 连接失败。回退到内存缓存。")
            return InMemoryCache()
    return InMemoryCache()

# --- 公共 API ---
# 缓存的单例实例
stop_request_manager = _get_cache_instance()

# 暴露包装了管理器方法的函数，以实现向后兼容
def get_stop_requested_sync(generation_id):
    return stop_request_manager.get_stop_requested(generation_id)

def set_stop_requested_sync(generation_id, ttl=DEFAULT_STOP_TTL):
    stop_request_manager.set_stop_requested(generation_id, ttl)

def touch_stop_request_sync(generation_id, ttl=DEFAULT_STOP_TTL):
    stop_request_manager.touch_stop_request(generation_id, ttl)

def clear_stop_request_sync(generation_id):
    stop_request_manager.clear_stop_request(generation_id)
