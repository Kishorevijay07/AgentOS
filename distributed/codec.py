from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from distributed.messages import MESSAGE_REGISTRY, Message, MessageType


@runtime_checkable
class MessageCodec(Protocol):
    """
    Serialises messages to/from the wire.

    The codec is the seam that makes the transport *format-agnostic*. A transport
    moves bytes; the codec turns typed :class:`Message` objects into those bytes
    and back. Redis/Kafka transports use the same codec, so switching brokers
    never touches message definitions or business logic.
    """

    def encode(self, message: Message) -> str:
        """Serialise *message* to a wire string."""
        ...

    def decode(self, raw: str) -> Message:
        """Reconstruct the correct :class:`Message` subclass from *raw*."""
        ...


class JSONMessageCodec:
    """
    Default JSON codec: Pydantic ``model_dump_json`` out, typed reconstruction in.

    Decoding reads the ``type`` discriminator and validates against the matching
    model in :data:`MESSAGE_REGISTRY`, so a corrupt or unknown type fails loudly
    rather than silently producing a half-formed object.
    """

    def encode(self, message: Message) -> str:
        return message.model_dump_json()

    def decode(self, raw: str) -> Message:
        data = json.loads(raw)
        try:
            msg_type = MessageType(data["type"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"Message missing/invalid 'type': {exc}") from exc
        model = MESSAGE_REGISTRY[msg_type]
        return model.model_validate(data)
