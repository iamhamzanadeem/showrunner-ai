from __future__ import annotations

import asyncio
import json
import uuid
from typing import AsyncIterator, Dict, List, Optional
from app.models import AgentEvent


class EventBus:
    """In-memory event bus managing per-job event queues and history."""

    def __init__(self) -> None:
        self._history: Dict[str, List[AgentEvent]] = {}
        self._subscribers: Dict[str, List[asyncio.Queue[AgentEvent]]] = {}
        self._lock: Optional[asyncio.Lock] = None

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def publish(
        self,
        job_id: str,
        step: str,
        message: str,
        shot_id: str | None = None,
        scene_id: str | None = None,
        data: dict | None = None,
    ) -> AgentEvent:
        """Publishes an event to all active subscribers of the job and appends to history."""
        event = AgentEvent(
            id=str(uuid.uuid4())[:8],
            job_id=job_id,
            step=step,
            message=message,
            shot_id=shot_id,
            scene_id=scene_id,
            data=data,
        )

        async with self.lock:
            if job_id not in self._history:
                self._history[job_id] = []
            self._history[job_id].append(event)

            subscribers = list(self._subscribers.get(job_id, []))

        for queue in subscribers:
            await queue.put(event)

        return event

    async def get_history(self, job_id: str) -> List[AgentEvent]:
        """Returns the current list of recorded events for a job."""
        async with self.lock:
            return list(self._history.get(job_id, []))

    async def subscribe(
        self,
        job_id: str,
        timeout: float = 1.0,
    ) -> AsyncIterator[AgentEvent]:
        """
        Subscribes to a job's event stream, yielding all past events followed by live events.
        Automatically completes when terminal events (PLAN completion, ERROR, or timeout) occur.
        """
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

        async with self.lock:
            if job_id not in self._subscribers:
                self._subscribers[job_id] = []
            self._subscribers[job_id].append(queue)
            past_events = list(self._history.get(job_id, []))

        # Replay past events. Only a terminal event that is the LAST one in history ends the
        # stream; earlier terminals belong to previous runs (e.g. before a chat refinement/resume).
        last_idx = len(past_events) - 1
        for idx, past_event in enumerate(past_events):
            yield past_event
            if idx == last_idx and self._is_terminal(past_event):
                async with self.lock:
                    if job_id in self._subscribers and queue in self._subscribers[job_id]:
                        self._subscribers[job_id].remove(queue)
                return

        # Stream future events until terminal or timeout
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                    yield event
                    if self._is_terminal(event):
                        return
                except asyncio.TimeoutError:
                    return
        except asyncio.CancelledError:
            pass
        finally:
            async with self.lock:
                if job_id in self._subscribers and queue in self._subscribers[job_id]:
                    self._subscribers[job_id].remove(queue)

    @staticmethod
    def _is_terminal(event: AgentEvent) -> bool:
        """Determines if an event signals end of the full production pipeline."""
        if event.step in ("DONE", "ERROR", "COMPLETED"):
            return True
        return False


# Singleton event bus instance
event_bus = EventBus()


async def sse_event_stream(job_id: str) -> AsyncIterator[str]:
    """Generates Server-Sent Events formatted strings for a given job."""
    yield ": connected\n\n"
    async for event in event_bus.subscribe(job_id):
        payload = json.dumps(event.model_dump())
        yield f"event: agent_event\ndata: {payload}\n\n"
