import json
import os

import redis.asyncio as redis

redis_client = redis.from_url(
    os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True
)


async def publish_command(channel: str, command: dict) -> None:
    """Publish a JSON command to a Redis pub/sub channel."""
    await redis_client.publish(channel, json.dumps(command))
