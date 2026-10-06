"""One resolution of the provider request options shared by both transports."""
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderRequestOptions:
    output_limit: int
    extra_body: dict


def resolve_model_options(config, request):
    """Bound the output budget and build the provider's extra request fields.

    An unspecified request limit uses the model configuration, an explicit one is
    capped by it, so raising the configuration alone is enough. The thinking switch
    is only sent when it is explicitly configured; ``None`` keeps provider behaviour.
    """
    limit = config.max_output_tokens
    if request.max_output_tokens is not None:
        limit = min(request.max_output_tokens, limit)
    extra_body = {} if config.thinking_mode is None else {'thinking': {'type': config.thinking_mode}}
    return ProviderRequestOptions(limit, extra_body)
