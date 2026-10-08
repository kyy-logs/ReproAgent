"""Deterministic human review view of saved reproduction evidence."""
import html
import re
from importlib.resources import files
from string import Template
from urllib.parse import quote


def _excerpt(value, limit=2000):
    value = str(value)
    return value if len(value) <= limit else value[:limit] + '\n（节选；完整内容见链接文件）'


def _text(value):
    value = html.escape(_excerpt(value), quote=False).replace('\r', '').replace('\n', ' ')
    return re.sub(r'([\\`*_\[\]!|])', r'\\\1', value)


def _link(label, path):
    return f'[{_text(label)}](./{quote(path, safe="/")})'


def _block(value):
    value = _excerpt(value)
    fence = '`' * max(3, 1 + max((len(part) for part in re.findall(r'`+', value)), default=0))
    return f'{fence}text\n{value}\n{fence}'


def _code(value):
    value = _excerpt(value).replace('\n', ' ').replace('\r', '')
    fence = '`' * (1 + max((len(part) for part in re.findall(r'`+', value)), default=0))
    return f'{fence} {value} {fence}'


def render_report(report, previews=None, *, check=lambda: None, template=None):
    """Fill the packaged template without model calls or evidence changes."""
    check()
    sections = {}
    def section(name, parts, limit):
        kept, size = [], 0
        for part in parts:
            check()
            length = len(part.encode('utf-8')) + 1
            if size + length > limit:
                kept.append('（本节为节选，完整记录见 report.json。）')
                break
            kept.append(part)
            size += length
        sections[name] = '\n'.join(kept).strip()
    def limited(items, count=8):
        for item in items[:count]:
            check()
            yield item
        if len(items) > count:
            lines.append('（列表为节选，完整记录见 report.json。）')
    previews = previews or {}
    verified = report.get('verified') is True and report.get('package_kind') == 'reproduction'
    level = report.get('evidence_level', 'NONE')
    # A repeated observation of the original failure and a validated fixed version are
    # different claims; the headline states which one this package actually holds.
    fix_status = report.get('fix_validation_status', 'not_provided')
    fix_labels = {'passed': '修复版对照通过', 'failed': '修复版未通过', 'blocked': '修复版验证受阻',
                  'not_provided': '未进行修复版对照'}
    sections['title'] = 'Bug 复现报告' if verified else 'Bug 复现诊断：未确认复现'
    lines = []
    if not verified:
        lines.append('未确认复现。本包为诊断材料，部分观察不代表成功交付。')
    elif level == 'DIFFERENTIAL_VALIDATED':
        lines.append('问题版重复观察已确认，同一候选的修复版对照通过。此结论不等于完整根因证明。')
    else:
        lines.append('已确认问题版的重复观察；未获得修复版通过的差异验证。')
        if fix_status == 'failed':
            lines.append('仅确认原版重复失败，差分复现未成立：修复版运行了同一候选但没有通过。')
        elif fix_status == 'blocked':
            lines.append('修复版环境或夹具不可用，差分验证未执行；仅确认原版重复失败。')
    lines += ['', f'- 任务：{_text(report.get("task_id", ""))}',
              f'- 导出时状态：{_code(report.get("status", ""))}',
              f'- 证据等级：{_code(level)}',
              f'- 修复版验证：{_code(fix_status)}（{_text(fix_labels.get(fix_status, "未记录"))}）',
              f'- 事件截止序号：{_text(report.get("event_cutoff", ""))}',
              '- 这是导出时的记录，最终任务状态以原任务目录的 task.json 为准。']
    if report.get('stop_reason'):
        lines.append(f'- 停止原因：{_text(report["stop_reason"])}')
    backends = report.get('backends', {})
    if backends:
        # The product's own component identity, when the package recorded one; a package
        # written before it existed keeps its recorded backends and renders without it.
        if backends.get('infrastructure'):
            lines.append(f'- 基础设施：{_code(backends["infrastructure"])}')
        if backends.get('strategy'):
            version = backends.get('strategy_version')
            lines.append(f'- 复现策略：{_code(backends["strategy"])}（版本 {_code(version) if version else "未记录"}）')
        lines += [f'- 模型后端：{_code(backends.get("model_backend", ""))}',
                  f'- Agent 策略：{_code(backends.get("agent_backend", ""))}',
                  f'- 模型：{_code(backends.get("model", ""))}']
        if backends.get('agentscope_version'):
            lines.append(f'- AgentScope 版本：{_code(backends["agentscope_version"])}')
    section('conclusion', lines, 6000)
    lines = ['1. 对照原始来源，确认模型理解的触发条件和预期行为。',
              '2. 阅读候选测试，确认真实调用目标代码、断言正确行为。',
              '3. 核对执行证据与问题对应关系，再查看重复观察和修复版结果。', '']
    for entry in limited(report.get('candidate_files', []), 16):
        lines.append(f'- {_link(entry["path"], "candidate/" + entry["path"])}'
                     f'（{_text(entry.get("role", "test"))}；安装路径保持不变）')
    if not report.get('candidate_files'):
        lines.append('本包没有已接受并交付的候选测试。')
    section('review', lines, 6000)
    lines = []
    contract = report.get('contract', {})
    if not contract:
        lines.append('诊断包未导出已接受候选的问题契约，请结合停止原因和运行日志审查。')
    for source in limited(report.get('source_mapping', []), 4):
        path = source['export_path']
        label = '原始 Bug 描述' if source['source_path'] == 'input/issue.md' else source['source_path']
        lines += ['', _link(label, path) +
                  f'（引用行 {source["start_line"]}–{source["end_line"]}；以下为节选，全文见链接）']
        if path in previews:
            lines += ['', _block(previews[path])]
    if report.get('source_mapping'):
        lines += ['', '以上是保存的原始来源，模型理解不能替代它；措辞冲突需要用户审查。']
    for key, label in (('trigger', '触发条件'), ('expected', '模型理解的预期行为'), ('reported_actual', '报告中的错误现象')):
        if contract.get(key):
            lines.append(f'- {label}：{_text(contract[key])}')
    section('problem', lines, 10000)
    lines = ['| 版本 | 运行 ID | pytest 退出码 | 清理完成 | 证据 |',
              '| --- | --- | --- | --- | --- |']
    candidate_id = report.get('candidate_id') if verified else None
    runs, fixed, total = [], None, 0
    for run in report.get('runs', []):
        check()
        if candidate_id and run['candidate_id'] != candidate_id:
            continue
        total += 1
        if len(runs) < 16:
            runs.append(run)
        if fixed is None and run['role'] == 'fixed':
            fixed = run
    if fixed and fixed not in runs:
        runs[-1:] = [fixed]
    selected = {run['run_id'] for run in runs}
    logs = {run_id: [] for run_id in selected}
    for item in report.get('log_mapping', []):
        check()
        parts = item['source_path'].split('/', 2)
        if len(parts) == 3 and parts[0] == 'runs' and parts[1] in selected and len(logs[parts[1]]) < 3:
            logs[parts[1]].append(item)
    first_log = None
    for run in runs:
        check()
        mappings = logs[run['run_id']]
        links = [_link(item['source_path'].rsplit('/', 1)[-1], item['export_path']) for item in mappings]
        role = {'original': '问题版', 'fixed': '修复版'}.get(run['role'], run['role'])
        lines.append(f'| {_text(role)} | {_text(run["run_id"])} | {_text(run["exit_code"])} | '
                     f'{"是" if run["cleanup_ok"] else "否"} | {" / ".join(links) or "无导出日志"} |')
        if first_log is None:
            first_log = next((item['export_path'] for item in mappings
                              if item['source_path'].endswith('/stdout.log') and item['export_path'] in previews), None)
    if not runs:
        lines += ['', '没有可用执行记录。']
    if total > len(runs):
        lines += ['', '（执行列表为节选，完整记录见 report.json。）']
    lines += ['', '退出码只表示 pytest 结果，不能单独证明失败对应目标 Bug。']
    if first_log:
        lines += ['', '### 首次可用运行日志节选', '', _link('完整日志', first_log), '', _block(previews[first_log])]
    if report.get('verdict_mapping'):
        lines += ['', '### 模型核验意见节选', '', '这些意见仍需结合原始来源和实际失败审查。']
        for item in limited(report['verdict_mapping'], 2):
            path = item['export_path']
            lines += ['', _link('完整核验记录', path)]
            if path in previews:
                lines += ['', _block(previews[path])]
    section('evidence', lines, 18000)
    lines = []
    if verified:
        lines += [
                  '在本包目录运行以下命令。替换占位符，使用全新的兼容仓库副本、已准备依赖和 pytest 的目标解释器，以及尚不存在的输出目录。', '',
                  '```text\npython replay.py --repo "<全新仓库副本>" --python "<目标 Python>" --output "<全新日志目录>" --install\n```', '',
                  _link('独立重跑工具', 'replay.py') +
                  ' 校验包文件后安装并执行候选，拒绝覆盖已有同名文件；不调用模型，不自动安装依赖。', '',
                  '问题版测试失败是预期现象，但仍应核对具体失败。重跑退出码不是对历史失败语义的一次新验收。', '',
                  f'- 目标 Python：{_text(report.get("python", ""))}',
                  f'- pytest：{_text(report.get("pytest_version", ""))}',
                  f'- 目标模块：{_text(", ".join(report.get("target_modules", [])))}',
                  f'- 源码根：{_text(", ".join(report.get("source_roots", [])))}']
    else:
        lines.append('诊断包没有已接受的复现测试，不提供成功复跑命令。')
        for path in limited(report.get('environment_probes', [])):
            lines += ['', _link('环境诊断日志', path)]
    section('replay', lines, 6000)
    lines = []
    for label, items in (('不确定项', report.get('uncertainties', [])),
                         ('环境限制', report.get('limitations', [])),
                         ('外部前置条件', report.get('preconditions', [])),
                         ('契约假设', contract.get('assumptions', [])),
                         ('缺失信息', contract.get('missing_information', []))):
        for item in limited(items):
            lines.append(f'- {label}：{_text(item)}')
    lines += ['- 语义核验可能误判；文件副本不构成安全沙箱。']
    section('limitations', lines, 10000)
    lines = [
              _link('结构化报告', 'report.json') + ' · ' + _link('文件哈希清单', 'manifest.json'), '',
              '本报告由保存的结构化记录确定性生成，未增加模型调用。测试和详细证据通过链接查看，未新增测试副本。', '']
    section('details', lines, 2000)
    check()
    template = template or Template(files('reproagent').joinpath('resources/report.md.template').read_text(encoding='utf-8'))
    text = template.substitute(sections)
    check()
    if len(text.encode('utf-8')) > 65536:
        raise ValueError('report template exceeds 64 KiB output limit')
    return text
