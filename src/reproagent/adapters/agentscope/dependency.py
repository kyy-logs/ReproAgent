from importlib.metadata import PackageNotFoundError, version

SDK_VERSION = '2.0.9'


def require_agentscope():
    try:
        installed = version('agentscope')
    except PackageNotFoundError:
        raise ValueError('agentscope backend requires installation: pip install "reproagent-local[agentscope]"') from None
    if installed != SDK_VERSION:
        raise ValueError(f'agentscope backend requires agentscope=={SDK_VERSION}; installed {installed}')
    return installed
