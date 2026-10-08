from importlib.metadata import PackageNotFoundError, version

SDK_VERSION = '2.0.9'


def require_agentscope():
    try:
        installed = version('agentscope')
    except PackageNotFoundError:
        # AgentScope is a main dependency now, so a missing one means the environment was
        # built without dependencies -- never that another runtime should run instead.
        raise ValueError('the AgentScope infrastructure requires agentscope to be installed; '
                         f'install reproagent-local so its dependencies include agentscope=={SDK_VERSION}') from None
    if installed != SDK_VERSION:
        raise ValueError(f'the AgentScope infrastructure requires agentscope=={SDK_VERSION}; installed {installed}')
    return installed
