from redis.exceptions import BusyLoadingError, ClusterDownError, MasterDownError, ReadOnlyError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

REDIS_UNAVAILABLE_ERRORS = (
    RedisConnectionError,
    RedisTimeoutError,
    BusyLoadingError,
    ClusterDownError,
    MasterDownError,
    ReadOnlyError,
    OSError,
)
