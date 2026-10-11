"""Bounded local projection of real OpenTelemetry spans and program events."""
from __future__ import annotations
import copy
import hashlib
import json
import time
from threading import RLock
from opentelemetry import trace
from .observability_content import ContentStore, redact
from .observability import (ALLOWED_ATTRIBUTES, MAX_ATTRIBUTE_BYTES, MAX_SPANS,
    WARNING_OBSERVATION_FAILED, WARNING_ATTRIBUTE_TOO_LARGE, WARNING_SPAN_CAP,
    HTTP_ATTEMPT_SPAN, LOGICAL_SPAN, _sum_tokens, _sum_cost)

class TaskTraceSink:
    def __init__(self, *, secrets=(), clock=time.monotonic, wall_clock=time.time):
        self._clock, self._wall = clock, wall_clock
        self._secrets = tuple(s for s in secrets if s)
        self._content = ContentStore(secrets=self._secrets)
        self._spans, self._entries, self._warnings = [], {}, []
        self._partial = self._failed = self._active = self._frozen = False
        self._started_clock = self._started_at = None
        self.trace_id = self.root_span_id = None
        self._lock = RLock()
        self._started = {}
        self._event_ordinals = {}
        self._session = None
        self._issues = self._content._issues
        self._terminal = {}
        self._counts_partial = False
        self._captured_sources = set()
    def _redact(self, value):
        return redact(value, self._secrets)
    def _explore_scope(self):
        current = trace.get_current_span().get_span_context()
        walker = format(current.span_id, "016x") if current.is_valid else self.root_span_id
        seen = set()
        while walker and walker not in seen:
            seen.add(walker)
            entry = self._entries.get(walker)
            if entry is None: break
            if entry["name"] == "explore": return walker
            walker = entry["parent_span_id"]
        return self.root_span_id or "root"
    def _admit(self, entry):
        from . import observability
        if self._frozen: return False
        if len(self._spans) >= observability.MAX_SPANS:
            self._partial = True
            self._counts_partial = True
            self._warn(WARNING_SPAN_CAP)
            self.note_capture_issue("SPAN_LIMIT", span_id=entry["span_id"], limit_scope="spans", limit_value=observability.MAX_SPANS)
            return False
        self._entries[entry["span_id"]] = entry
        self._spans.append(entry)
        return True
    def started(self, span):
        with self._lock:
            now = self._clock()
            ident = format(span.context.span_id, "016x")
            parent = format(span.parent.span_id,"016x") if span.parent else None
            entry = dict(span_id=ident, parent_span_id=parent, name=self._redact(span.name),
                kind="span",offset_seconds=max(0.,now-self._started_clock),duration_seconds=None,
                status="incomplete",attributes={},content_refs=[],content_status=None,
                otel_span_id=ident,instrumentation_scope=span.instrumentation_scope.name,
                otel_status="UNSET",otel_start_time_ns=span.start_time,otel_end_time_ns=None)
            if span.instrumentation_scope.name == 'agentscope':
                operation=(span.attributes or {}).get('gen_ai.operation.name')
                entry['name']={'chat':'sdk.model_call','invoke_agent':'sdk.agent_reply',
                               'execute_tool':'tool.unknown'}.get(operation,'sdk.operation')
            if self._admit(entry):
                self._started[ident] = now
    def current_entry(self):
        sc = trace.get_current_span().get_span_context()
        return self._entries.get(format(sc.span_id,"016x")) if sc.is_valid else None
    def note_event(self, name, attributes=None):
        with self._lock:
            if self._frozen: return None
            if name=='task.stopped':
                self._terminal={k:self._redact(v) for k,v in (attributes or {}).items()
                                if k in ('task_status','stop_reason','budget_dimension')}
            host = self.current_entry()
            if host is None:
                self._partial=True
                self.note_capture_issue("PARENT_MISSING")
                return None
            index = self._event_ordinals.get(host["span_id"],0)
            self._event_ordinals[host["span_id"]] = index+1
            # The event is also emitted to OTel; the local snapshot stays bounded.
            name=self._redact(name)
            if len(name.encode("utf-8"))>256:
                name="event.omitted_name";self._partial=True
                self.note_capture_issue("METADATA_LIMIT", limit_scope="event_name_bytes", limit_value=256)
            trace.get_current_span().add_event(name)
            entry = dict(span_id=f"point:{host['span_id']}:{index}",parent_span_id=host['span_id'],
                name=name,kind="point",offset_seconds=max(0.,self._clock()-self._started_clock),
                duration_seconds=None,status="ok",attributes={},content_refs=[],content_status=None,
                otel_span_id=None,instrumentation_scope="reproagent",otel_status="UNSET")
            if not self._admit(entry): return None
            self._annotate(entry, attributes or {})
            return entry
    def finish(self, *, task_id, status, main_duration, learning=None, stop_reason=None, budget=None):
        if self._session is not None: self._session.close()
        with self._lock:
            if self._started:
                self._partial=True
                self._warn("some spans had not ended when the task trace closed")
                for ident in self._started: self.note_capture_issue("SPAN_UNFINISHED", span_id=ident)
            self._frozen=True
            # Enforce combined metadata limits, not only each attribute key.
            total=0
            for entry in self._spans:
                if len(json.dumps(entry,ensure_ascii=False).encode()) > MAX_ATTRIBUTE_BYTES:
                    self._counts_partial |= entry['name']==HTTP_ATTEMPT_SPAN
                    entry['attributes']={};self._partial=True
                    self.note_capture_issue('METADATA_LIMIT',span_id=entry['span_id'],limit_scope='entry_bytes',limit_value=MAX_ATTRIBUTE_BYTES)
                    if len(entry['name'].encode('utf-8'))>256: entry['name']='operation.omitted_name'
                    while entry['content_refs'] and len(json.dumps(entry,ensure_ascii=False).encode())>MAX_ATTRIBUTE_BYTES:
                        entry['content_refs'].pop()
                        entry['content_status']='omitted_limit'
                        self._content.complete=False
                total += len(json.dumps(entry,ensure_ascii=False).encode())
            if total > 1<<20:
                self._partial=True
                self._counts_partial=True
                self.note_capture_issue("METADATA_LIMIT",limit_scope="metadata_total_bytes",limit_value=1<<20)
                while total > 1<<20 and len(self._spans)>1:
                    item=self._spans.pop()
                    total-=len(json.dumps(item,ensure_ascii=False).encode())
            kept={x['span_id'] for x in self._spans}
            if any(s['parent_span_id'] and s['parent_span_id'] not in kept for s in self._spans):
                self._partial=True
                self.note_capture_issue('PARENT_MISSING')
            refs={ref for s in self._spans for ref in s['content_refs']}
            issues=self._content.issues()
            for item in issues:
                if item['severity']!='info': self._warn('capture loss: '+item['code'])
            if self._issues.dropped: self._warn('capture issue limit reached; additional losses counted')
            if (self._partial or self._failed) and not any(i['severity']!='info' for i in issues):
                self.note_capture_issue('OBSERVATION_EXCEPTION')
                issues=self._content.issues()
                self._warn('capture loss: OBSERVATION_EXCEPTION')
            counts_complete=not(self._counts_partial or self._failed)
            summary=self._summarise(learning)
            if not counts_complete:
                summary['usage']['complete']=False
                summary['cost']['complete']=False
            final_reason=stop_reason if stop_reason is not None else self._terminal.get('stop_reason')
            final_dimension=(self._terminal.get('budget_dimension') if
                final_reason==self._terminal.get('stop_reason') and
                status==self._terminal.get('task_status',status) else None)
            doc=dict(schema_version=2,trace_id=self.trace_id,root_span_id=self.root_span_id,
                task_id=task_id,status=status,capture_mode="content",started_at=self._started_at,
                main_duration=main_duration,total_duration=max(0.,self._clock()-self._started_clock),
                partial=self._partial or self._failed or not self._content.complete,metrics_complete=counts_complete,
                content_complete=self._content.complete and not self._failed,warnings=list(self._warnings),
                spans=self._spans,contents=[c for c in self._content.records() if c['content_id'] in refs],
                summary=summary,
                diagnostic_summary=dict(status=status,stop_reason=final_reason,budget=self._redact(budget),budget_dimension=final_dimension),
                capture_health=dict(structure_complete=not(self._partial or self._failed),counts_complete=counts_complete,
                    content_complete=self._content.complete and not self._failed,
                    issues=issues,issues_dropped=self._issues.dropped))
            return copy.deepcopy(doc)

    def note_capture_issue(self, code, **fields):
        self._issues.add(code, **fields)

    def _warn(self, message: str) -> None:
            if message not in self._warnings:
                self._warnings.append(message)

    def _fault(self) -> None:
            self._failed = True
            self._counts_partial = True
            self.note_capture_issue("OBSERVATION_EXCEPTION")
            self._warn(WARNING_OBSERVATION_FAILED)

    def _annotate(self, entry: dict, fields: dict) -> None:
            if self._frozen:
                return
            try:
                for key, raw in fields.items():
                    if key not in ALLOWED_ATTRIBUTES:
                        continue
                    value = self._redact(raw)
                    try:
                        encoded = json.dumps({key: value}, ensure_ascii=False).encode("utf-8")
                    except (TypeError, ValueError):
                        # A value the whitelist admits but cannot be written down.
                        self._fault()
                        continue
                    if len(encoded) > MAX_ATTRIBUTE_BYTES:
                        # Whole or not at all: a truncated fact would read as complete.
                        self._warn(WARNING_ATTRIBUTE_TOO_LARGE)
                        self._partial = True
                        self._counts_partial |= key in ("usage","cost")
                        self.note_capture_issue("METADATA_LIMIT",span_id=entry["span_id"],source=key,limit_scope="attribute_bytes",limit_value=MAX_ATTRIBUTE_BYTES)
                        continue
                    entry["attributes"][key] = value
            except Exception:  # noqa: BLE001 - observation never escapes
                self._fault()

    def _capture(self, entry: dict, kind: str, value: Any, *, source: str,
                     availability: str = "captured", **metadata) -> str | None:
            """Copy one piece of content, attributing it to the span that produced it."""
            content_id = self._content.capture(owner_span_id=entry["span_id"], kind=kind, value=value,
                                               source=source, availability=availability, **metadata)
            if content_id is not None:
                if content_id not in entry["content_refs"]: entry["content_refs"].append(content_id)
                availability=next(c["availability"] for c in self._content.records() if c["content_id"]==content_id)
                if availability=='captured': self._captured_sources.add((entry['span_id'],source))
            else:
                availability = "omitted_limit"
            # The first capture decides the span's status: a later successful copy must not
            # overwrite the record of something that was missing.
            if entry["content_status"] is None:
                entry["content_status"] = availability
            elif availability in ("omitted_limit", "capture_error"):
                entry["content_status"] = availability
            return content_id

    def _tool_call_key(self, sdk_call_id: str, step_index: int) -> str:
            """A stable key joining one call's permission with its execution.

            Derived and one-way: a provider that reuses one call id across steps still gets
            distinct keys, and the raw SDK id is not what is written down.
            """
            material = f"{self.trace_id}|{self._explore_scope()}|{step_index}|{sdk_call_id}"
            return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def _summarise(self, learning: dict | None) -> dict:
            leaves = [entry for entry in self._spans if entry["name"] == HTTP_ATTEMPT_SPAN]
            return {
                "span_count": len(self._spans),
                "logical_calls": sum(1 for entry in self._spans if entry["name"] == LOGICAL_SPAN),
                "http_attempts": len(leaves),
                "tool_executions": sum(1 for entry in self._spans
                                       if entry["kind"] == "span" and entry["name"].startswith("tool.") and entry.get("instrumentation_scope")=="agentscope"),
                "usage": _sum_tokens(leaves),
                "cost": _sum_cost(leaves),
                "learning": learning,
            }


def project_native_span(span, *, sink):
    with sink._lock:
        ident=format(span.context.span_id,"016x")
        entry=sink._entries.get(ident)
        if entry is None:
            sink._partial=True
            sink._counts_partial=True
            sink.note_capture_issue("SPAN_LIMIT",span_id=ident)
            return
        start=sink._started.pop(ident,None)
        entry['duration_seconds']=max(0.,sink._clock()-start) if start is not None else None
        entry['otel_start_time_ns']=span.start_time
        entry['otel_end_time_ns']=span.end_time
        entry['otel_status']=span.status.status_code.name
        entry['status']=entry.get('_display_status') or ('error' if entry['otel_status']=='ERROR' else 'ok')
        entry.pop('_display_status',None)
        attributes=dict(span.attributes or {})
        operation=attributes.get('gen_ai.operation.name')
        native=span.instrumentation_scope.name=='agentscope'
        if native:
            entry['name']={'chat':'sdk.model_call','invoke_agent':'sdk.agent_reply',
                'execute_tool':'tool.'+entry['attributes'].get('tool','unknown')}.get(operation,'sdk.operation')
            sources={'gen_ai.input.messages': 'sdk_agent_input' if operation=='invoke_agent' else 'sdk_model_input',
                     'gen_ai.output.messages':'sdk_agent_output' if operation=='invoke_agent' else 'sdk_model_output',
                     'gen_ai.tool.call.arguments':'sdk_tool_input','gen_ai.tool.call.result':'sdk_tool_result'}
            for key,source in sources.items():
                raw=attributes.get(key)
                if not isinstance(raw,str): continue
                # Already captured terminal tool payloads are not stored again.
                if (ident,source) in sink._captured_sources: continue
                availability='captured'
                if len(raw)>=65536:
                    availability='omitted_limit'
                try: value=json.loads(raw) if availability=='captured' else raw
                except (ValueError,TypeError): value=raw
                if source.startswith('sdk_agent_') or source.startswith('sdk_model_'):
                    value=project_sdk_messages(value)
                sink._capture(entry,source,value,source=source,availability=availability,
                    **({'reason_code':'OTEL_ATTRIBUTE_POSSIBLY_TRUNCATED','limit_scope':'otel_attribute_characters',
                        'limit_value':65536} if availability=='omitted_limit' else {}))
            usage={k:attributes.get('gen_ai.usage.'+v) for k,v in
                   [('input_tokens','input_tokens'),('output_tokens','output_tokens')]}
            if any(v is not None for v in usage.values()): sink._annotate(entry,{'usage':usage})
        if span.status.description:
            sink._capture(entry,'exception',span.status.description,source='program_artifact')
        for event in span.events:
            if event.name=='exception':
                allowed={k:v for k,v in event.attributes.items() if k in
                         ('exception.type','exception.message','exception.stacktrace')}
                sink._capture(entry,'exception',allowed,source='program_artifact')
        if any(getattr(span,k,0) for k in ('dropped_attributes','dropped_events','dropped_links')):
            sink._partial=True
            sink._warn('OpenTelemetry omitted some attributes, events or links')
            sink.note_capture_issue('OTEL_DROPPED_FIELDS',span_id=ident)
            if getattr(span,'dropped_attributes',0): sink._counts_partial=True


def classify_error(exc):
    code=getattr(getattr(exc,'response',None),'status_code',None)
    if isinstance(code,int):
        category={400:'input',422:'input',401:'authentication',403:'authentication',429:'rate_limit'}.get(code,
            'provider' if 500<=code<=599 else 'unknown')
        return dict(error_category=category,classification_source='http_status')
    if getattr(exc,'code',None):
        return dict(error_category='protocol',classification_source='program_code')
    import asyncio
    import httpx
    if isinstance(exc,(asyncio.CancelledError,GeneratorExit)):
        category='cancelled'
    elif isinstance(exc,httpx.TransportError): category='transport'
    elif isinstance(exc,TimeoutError): category='runtime'
    else: category='unknown'
    return dict(error_category=category,classification_source='exception_type')


def project_sdk_messages(value):
    """Constrain tool identifiers/names and media parts inside SDK message views."""
    if isinstance(value,list): return [project_sdk_messages(item) for item in value]
    if not isinstance(value,dict): return value
    kind=value.get('type')
    if kind in ('image','audio','video','file','data','blob','uri'):
        return {'type':kind,'availability':'unsupported'}
    if kind in ('tool_call','tool_call_response','tool_result'):
        # Call correlation lives in controlled span fields, never the provider's ID.
        value={k:v for k,v in value.items() if k not in ('id','tool_call_id')}
        if 'name' in value:
            from .adapters.agentscope.tools import TOOL_NAMES
            if value['name'] not in (*TOOL_NAMES,'read_experience'): value['name']='unknown'
    return {k:project_sdk_messages(v) for k,v in value.items()}
