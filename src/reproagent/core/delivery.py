"""Conservative, non-executing audit of candidate pytest platform guards.

This is negative evidence only: even an apparently runnable test needs execution
on the target. Unsupported collection patterns and expressions remain unverified.
"""
import ast


_UNKNOWN = object()
_PLATFORMS = {'linux': 'posix', 'win32': 'nt', 'darwin': 'posix'}


def _reference(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, '')
    if isinstance(node, ast.Attribute):
        parent = _reference(node.value, aliases)
        return parent + '.' + node.attr if parent else ''
    return ''


def _value(node, aliases, platform):
    """Interpret a small expression whitelist; never import or eval candidate text."""
    if isinstance(node, ast.Constant) and type(node.value) in (bool, str):
        return node.value
    reference = _reference(node, aliases)
    if reference == 'sys.platform':
        return platform
    if reference == 'os.name':
        return _PLATFORMS[platform]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _value(node.operand, aliases, platform)
        return not value if value is not _UNKNOWN else _UNKNOWN
    if isinstance(node, ast.BoolOp):
        for item in node.values:
            value = _value(item, aliases, platform)
            if type(value) is not bool:
                return _UNKNOWN
            if isinstance(node.op, ast.And) and not value:
                return False
            if isinstance(node.op, ast.Or) and value:
                return True
        return isinstance(node.op, ast.And)
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        left = _value(node.left, aliases, platform)
        right = _value(node.comparators[0], aliases, platform)
        if left is not _UNKNOWN and right is not _UNKNOWN:
            if isinstance(node.ops[0], ast.Eq):
                return left == right
            if isinstance(node.ops[0], ast.NotEq):
                return left != right
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'startswith' and not node.keywords and len(node.args) == 1):
        value = _value(node.func.value, aliases, platform)
        prefix = _value(node.args[0], aliases, platform)
        if type(value) is str and type(prefix) is str:
            return value.startswith(prefix)
    return _UNKNOWN


def _mark(node, aliases, platform):
    if not isinstance(node, ast.Call):
        return _UNKNOWN
    name = _reference(node.func, aliases)
    if len(node.keywords) > 1 or any(keyword.arg != 'reason' or not isinstance(keyword.value, ast.Constant)
           or type(keyword.value.value) is not str for keyword in node.keywords):
        return _UNKNOWN
    if name == 'pytest.mark.skip' and not node.args:
        return True
    if name == 'pytest.mark.skipif' and len(node.args) == 1 and node.keywords:
        value = _value(node.args[0], aliases, platform)
        # Strings are pytest expressions evaluated later, with a different namespace.
        return value if type(value) is bool else _UNKNOWN
    return _UNKNOWN


def _marks(nodes, aliases, platform):
    values = [_mark(node, aliases, platform) for node in nodes]
    if any(value is _UNKNOWN for value in values):
        return _UNKNOWN
    return any(values)


def _scope(body, inherited_aliases, platform):
    """Only imports, definitions, docstrings and mark assignments are recognized.

    A rebinding anywhere in the scope invalidates its imported alias, even where
    source ordering might allow a stronger conclusion. Attribute writes and dynamic
    statements invalidate the scope as a whole.
    """
    aliases = dict(inherited_aliases)
    bindings = {}
    marks = []
    safe = True
    for node in body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname or alias.name.split('.')[0]
                if alias.name not in ('sys', 'os', 'pytest'):
                    safe = False
                bindings.setdefault(name, []).append(alias.name if alias.name in ('sys', 'os', 'pytest') else '')
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ('sys', 'os', 'pytest') or any(alias.name == '*' for alias in node.names):
                safe = False
            for alias in node.names:
                reference = f'{node.module}.{alias.name}' if not node.level and node.module in ('sys', 'os', 'pytest') else ''
                bindings.setdefault(alias.asname or alias.name, []).append(reference)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bindings.setdefault(node.name, []).append('')
            if node.name.startswith('pytest_') or node.name == '__getattr__' or getattr(node, 'type_params', ()):
                safe = False
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defaults = [*node.args.defaults, *(value for value in node.args.kw_defaults if value is not None)]
                arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                arguments += [arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None]
                annotations = [arg.annotation for arg in arguments if arg.annotation is not None]
                if node.returns is not None:
                    annotations.append(node.returns)
                if any(not isinstance(value, ast.Constant) for value in (*defaults, *annotations)):
                    safe = False
        elif isinstance(node, ast.Assign):
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
                bindings.setdefault(name, []).append('')
                if name == 'pytestmark':
                    marks.append(node.value)
                elif name == '__test__' or name.startswith('pytest_'):
                    safe = False
                elif not isinstance(node.value, ast.Constant):
                    safe = False
            else:
                safe = False
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and type(node.value.value) is str:
            pass
        elif isinstance(node, ast.Pass):
            pass
        else:
            safe = False
    for name, references in bindings.items():
        aliases[name] = references[0] if len(references) == 1 else ''
    if len(marks) > 1:
        safe = False
    mark_nodes = []
    for mark in marks:
        mark_nodes.extend(mark.elts if isinstance(mark, (ast.List, ast.Tuple)) else [mark])
    status = _marks(mark_nodes, aliases, platform)
    return aliases, safe and status is not _UNKNOWN, status


def _nodes(tree, path, platform):
    aliases, safe, module_skip = _scope(tree.body, {}, platform)
    nodes = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            marked = _marks(node.decorator_list, aliases, platform)
            safe = safe and marked is not _UNKNOWN
            if node.name.startswith('test'):
                nodes[path + '::' + node.name] = module_skip is True or marked is True
        elif isinstance(node, ast.ClassDef):
            marked = _marks(node.decorator_list, aliases, platform)
            class_aliases, class_safe, class_skip = _scope(node.body, aliases, platform)
            safe = safe and marked is not _UNKNOWN and class_safe
            if node.bases or node.keywords:
                safe = False
            if any(isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and child.name in ('__init__', '__new__') for child in node.body):
                safe = False
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_skip = _marks(child.decorator_list, class_aliases, platform)
                    safe = safe and method_skip is not _UNKNOWN
                    if node.name.startswith('Test') and child.name.startswith('test'):
                        nodes[path + '::' + node.name + '::' + child.name] = any(
                            item is True for item in (module_skip, marked, class_skip, method_skip))
                elif isinstance(child, ast.ClassDef):
                    safe = False
    return nodes, safe


def assess_delivery(candidate_files, selectors, target_platform, *, collection_context_known=False):
    """Return incompatibility only for a complete, definitely all-skipped selection.

    ``candidate_files`` maps relative candidate paths to text or UTF-8 bytes. No
    path is opened. ``selectors`` contains explicit pytest file/class/node selectors.
    Unknown node names are retained so a missing selection cannot disappear from
    the all-skipped decision. By default original collection hooks, plugins and import
    resolution are unknown. Only a caller with an independently audited standard
    collection environment may set collection_context_known=True. This audit never
    reports target validation.
    """
    if target_platform not in _PLATFORMS:
        raise ValueError('unsupported delivery platform')
    files = {str(path).replace('\\', '/'): content for path, content in candidate_files.items()}
    skipped, unknown = set(), set()
    dynamic_collection = not collection_context_known or any(path.rsplit('/', 1)[-1] == 'conftest.py' for path in files)
    cache = {}
    for raw_selector in selectors:
        selector = str(raw_selector).replace('\\', '/')
        path = selector.split('::', 1)[0]
        if path not in cache:
            try:
                cache[path] = _nodes(ast.parse(files[path]), path, target_platform)
            except (KeyError, SyntaxError, ValueError, TypeError, UnicodeError, RecursionError):
                cache[path] = ({}, False)
        nodes, safe = cache[path]
        matched = [node for node in nodes if node == selector or node.startswith(selector + '::')]
        if not matched or '[' in selector:
            unknown.add(selector)
        else:
            for node in matched:
                (skipped if safe and not dynamic_collection and nodes[node] else unknown).add(node)
    incompatible = bool(skipped) and not unknown
    return {'status': 'incompatible' if incompatible else 'target_unverified',
            'target_platform': target_platform, 'skipped_tests': sorted(skipped),
            'unknown_tests': sorted(unknown),
            'reason': ('All selected test nodes are statically guarded to skip on the delivery platform.'
                       if incompatible else 'Static inspection cannot establish target execution; run the candidate on the delivery platform.')}
