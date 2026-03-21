"""Abstract publisher/consumer interfaces for the message broker layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class Publisher(ABC):
    """Abstract interface for publishing messages to a stream."""

    @abstractmethod
    async def publish(self, channel: str, message: dict[str, Any]) -> str:
        """Publish a message to a channel.

        Args:
            channel: Target channel/stream name.
            message: Message payload as a dictionary.

        Returns:
            Message ID assigned by the broker.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release underlying connection resources."""


class Consumer(ABC):
    """Abstract interface for consuming messages from a stream."""

    @abstractmethod
    async def subscribe(
        self,
        channels: list[str],
        group: str,
        consumer_name: str,
    ) -> None:
        """Subscribe to one or more channels as part of a consumer group.

        Args:
            channels: List of channel/stream names to consume from.
            group: Consumer group name (for at-least-once delivery).
            consumer_name: Unique name for this consumer within the group.
        """

    @abstractmethod
    async def listen(self) -> AsyncIterator[tuple[str, str, dict[str, Any]]]:
        """Yield messages from subscribed channels.

        Yields:
            Tuples of (channel, message_id, message_payload).
        """
        yield ("", "", {})  # pragma: no cover

    @abstractmethod
    async def ack(self, channel: str, group: str, message_id: str) -> None:
        """Acknowledge a message as successfully processed.

        Args:
            channel: Channel the message was received from.
            group: Consumer group name.
            message_id: Message ID to acknowledge.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release underlying connection resources."""
