"""One optional, bounded reminder after a recoverable exploration blocker."""
from __future__ import annotations

HINT_TEXT = ("这次操作未通过。当前有历史经验目录可查；若与该阻塞相关，可浏览相应分类并读取已展示条目。"
             "历史建议仍需对照当前事实核对。")
MAX_HINT_BYTES = 256


class ExperienceHintState:
    def __init__(self, enabled: bool, *, record=None) -> None:
        self.enabled = bool(enabled)
        self.observed = False
        self.prepared = False
        self.delivered = False
        self.omitted = False
        self.reason = ""
        self.trigger = {}
        self._record_event = record
        self._reported = set()
        self._next_call_index = None

    def observe(self, fact: dict) -> None:
        if not self.enabled or self.observed:
            return
        if isinstance(fact, dict) and (fact.get("kind") in ("tool_error", "permission")
                                       or fact.get("verification_continue") is True):
            self.observed = True
            self.trigger = {key: fact[key] for key in ('kind', 'tool', 'code', 'call_id') if key in fact}
            if fact.get('verification_continue') is True:
                self.trigger['kind'] = 'verification_continue'

    def _report(self, code, **fields):
        if code in self._reported:
            return
        self._reported.add(code)
        if self._record_event is not None:
            try:
                self._record_event({'code': code, 'trigger': dict(self.trigger),
                                    'next_call_index': self._next_call_index, **fields})
            except Exception:
                pass  # Optional observation cannot change exploration.

    def _suppress(self, reason):
        self.reason = reason
        self._report(reason)
        return None

    def prepare(self, *, remaining_before_call: int, reserved_steps: int,
                allowed_tools: tuple[str, ...], view, browse_state: str,
                deadline_ok: bool, cancelled: bool, phase_finished: bool,
                max_bytes: int = 8192, permissions_available: bool = True,
                next_call_index: int | None = None) -> dict | None:
        if not self.enabled or not self.observed or self.prepared or self.delivered or self.omitted:
            return None
        self._next_call_index = next_call_index
        if cancelled:
            return self._suppress('cancelled')
        if phase_finished:
            return self._suppress('phase_finished')
        if not deadline_ok:
            return self._suppress('deadline')
        if remaining_before_call < reserved_steps + 3:
            return self._suppress('insufficient_steps')
        if (not permissions_available or "list_experiences" not in allowed_tools
                or "read_experience" not in allowed_tools):
            return self._suppress('permission_unavailable')
        if view is None or getattr(view, "notice", lambda: None)() is None:
            return self._suppress('catalog_unavailable')
        if browse_state != "satisfied":
            return self._suppress('browse_not_satisfied')
        availability = getattr(view, 'hint_unavailable_reason', None)
        if availability is None:
            return self._suppress('catalog_unavailable')
        reason = availability(max_bytes=max_bytes)
        if reason is not None:
            return self._suppress(reason)
        if len(HINT_TEXT.encode("utf-8")) > MAX_HINT_BYTES:  # pragma: no cover
            return None
        self.prepared = True
        return {"message": HINT_TEXT}

    def finish(self, *, response_received: bool, reason: str = 'not_delivered', stop_reason: str = '') -> None:
        if not self.prepared or self.delivered:
            return
        if response_received:
            self.delivered = True
            self.reason = "delivered"
        else:
            self.prepared = False
            self.omitted = True
            self.reason = reason
        self._report(self.reason, **({'stop_reason': stop_reason} if stop_reason else {}))


__all__ = ["ExperienceHintState", "HINT_TEXT", "MAX_HINT_BYTES"]
