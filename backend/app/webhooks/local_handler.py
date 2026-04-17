"""Local webhook handler — thin FastAPI adapter over the event consumer.

Creates a ``LocalEventConsumer`` and exposes its router for mounting
in the FastAPI app. This module should ONLY be mounted when ``ENV=local``.
"""

from app.webhooks.consumer import LocalEventConsumer

_consumer = LocalEventConsumer()
router = _consumer.router
