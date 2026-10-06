from .models import AgentAction, EvidenceRef, ToolResult
from .serialization import bytes_hash
from reproagent.store import safe_child
from reproagent.workspace import sensitive_path
from .protocol import object_schema

SCHEMAS = {
    'search_code': {'query': str, 'scope': str},
    'read_file': {'path': str, 'start': int, 'end': int},
    'write_candidate': {'files': list, 'hypothesis': str},
    'run_candidate': {'candidate_id': str},
    'submit_candidate': {'candidate_id': str},
    'request_information': {'question': str},
    'revise_contract': {'source_refs': list, 'reason': str},
}

SEARCH_SCOPES = ('snapshot', 'candidates', 'all')


def tool_schemas():
    types = {str:'string', int:'integer', list:'array'}
    schemas = {name:object_schema({key:{'type':types[kind]} for key,kind in parameters.items()})
               for name,parameters in SCHEMAS.items()}
    schemas['search_code']['properties']['scope']['enum'] = list(SEARCH_SCOPES)
    schemas['search_code']['properties']['query']['description'] = 'Literal substring, not a regular expression.'
    schemas['search_code']['properties']['scope']['description'] = 'Search source snapshot, candidates, or both. Never a filename or directory.'
    for field in ('start','end'):
        schemas['read_file']['properties'][field]['minimum'] = 1
    schemas['read_file']['properties']['path']['description'] = 'Repository-relative path from files, e.g. src/package/module.py. Not an evidence_refs.path such as snapshots/... or input/issue.md.'
    schemas['write_candidate']['properties']['files'].update({'minItems':1, 'items':object_schema({
        'path':{'type':'string','description':'Install path including candidate_parent, e.g. tests/test_repro.py.'},
        'content':{'type':'string'}, 'role':{'type':'string','enum':['test','data']}})})
    schemas['revise_contract']['properties']['source_refs'].update({'minItems':1,'items':object_schema({
        'path':{'type':'string'}, 'content_hash':{'type':'string'},
        'start_line':{'type':'integer','minimum':1}, 'end_line':{'type':'integer','minimum':1}})})
    return schemas


def validate_action(data):
    if set(data) != {'name', 'parameters'} or type(data.get('name')) is not str or data['name'] not in SCHEMAS:
        raise ValueError('unknown or malformed action')
    name, parameters = data['name'], data['parameters']
    schema = SCHEMAS[name]
    if not isinstance(parameters, dict) or set(parameters) != set(schema):
        raise ValueError('unexpected action arguments')
    if any(type(parameters[k]) is not kind for k, kind in schema.items()):
        raise ValueError('incorrect action argument type')
    if name == 'read_file' and not 1 <= parameters['start'] <= parameters['end']:
        raise ValueError('invalid line range')
    if name == 'search_code' and (not parameters['query'] or parameters['scope'] not in SEARCH_SCOPES):
        raise ValueError('search_code requires a nonempty literal query and scope snapshot, candidates, or all; scope is not a file path')
    if name == 'write_candidate':
        if not parameters['files']:
            raise ValueError('candidate has no files')
        for item in parameters['files']:
            if not isinstance(item, dict) or set(item) != {'path', 'content', 'role'} or any(type(value) is not str for value in item.values()) or item['role'] not in ('test', 'data'):
                raise ValueError('invalid candidate file')
    if name == 'revise_contract':
        from .models import EvidenceRef
        if not parameters['source_refs']:
            raise ValueError('revision requires newly read sources')
        for ref in parameters['source_refs']:
            if not isinstance(ref, dict): raise ValueError('invalid source reference')
            try: EvidenceRef(**ref)
            except TypeError as exc: raise ValueError('invalid source reference') from exc
    return AgentAction(name, parameters)


class Tools:
    def __init__(self, project, workspace, context):
        self.project, self.workspace, self.context = project, workspace, context

    def _entries(self, scope='all'):
        if scope not in SEARCH_SCOPES:
            raise ValueError('scope must be snapshot, candidates, or all; use read_file for a repository-relative file path')
        if scope in ('snapshot', 'all'):
            for entry in self.project.snapshot.files:
                yield self.project.snapshot.root, entry
        if scope in ('candidates', 'all'):
            for candidate in self.project.candidates:
                for entry in candidate.files:
                    yield candidate.storage_root, entry

    def _read(self, root, entry):
        self.context.budget.check()
        if sensitive_path(entry.path):
            raise ValueError('sensitive file excluded')
        path = safe_child(root, entry.path)
        data = path.read_bytes()
        if bytes_hash(data) != entry.content_hash:
            raise ValueError('registered file hash changed')
        text = data.decode('utf-8')
        if '\x00' in text:
            raise ValueError('binary content excluded')
        ref = EvidenceRef(path.relative_to(self.workspace.root).as_posix(), entry.content_hash)
        return text, ref

    def _result(self, text, refs):
        cap = self.context.budget.limits.tool_response_bytes
        encoded = text.encode('utf-8')
        if len(encoded) > cap:
            text = encoded[:max(0, cap - 32)].decode('utf-8', errors='ignore') + '\n[truncated; see references]'
        return ToolResult('ok', text, tuple(refs))

    def read_file(self, path, start, end):
        safe_child(self.project.snapshot.root, path)
        if sensitive_path(path) or type(start) is not int or type(end) is not int or not 1 <= start <= end:
            raise ValueError('invalid read request')
        for root, entry in self._entries():
            if entry.path == path:
                text, ref = self._read(root, entry)
                lines = text.splitlines()
                if start > max(1, len(lines)):
                    raise ValueError('line range outside file')
                text = '\n'.join(f'{index + 1}: {line}' for index, line in enumerate(lines[start - 1:end], start - 1))
                from dataclasses import replace
                return self._result(text, (replace(ref, start_line=start, end_line=min(end, max(1, len(lines)))),))
        raise FileNotFoundError('file is not registered; use a repository-relative path from files, not an evidence path. The issue is already supplied in the contract')

    def search_code(self, query, scope):
        if not query:
            raise ValueError('empty search')
        parts, refs = [], []
        for root, entry in self._entries(scope):
            try:
                text, ref = self._read(root, entry)
            except (UnicodeError, ValueError):
                continue
            for index, line in enumerate(text.splitlines(), 1):
                self.context.budget.check()
                if query in line:
                    parts.append(f'{entry.path}:{index}: {line}')
                    refs.append(ref)
                    if sum(len(part.encode()) + 1 for part in parts) >= self.context.budget.limits.tool_response_bytes:
                        return self._result('\n'.join(parts), refs)
        return self._result('\n'.join(parts), refs)

    def write_candidate(self, draft):
        self.context.budget.check()
        candidate = self.workspace.publish(draft, self.project.snapshot)
        ref = self.workspace.store.save_record('candidates', candidate.candidate_id, candidate)
        from dataclasses import replace
        self.project = replace(self.project, candidates=(*self.project.candidates, candidate))
        return ToolResult('ok', 'Candidate published; run it to gather evidence.', (ref,), candidate.candidate_id)
