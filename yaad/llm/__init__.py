"""Model-client factory. Cloud stays the default; local must be explicit.

An unknown provider name raises instead of silently picking a side.
"""


def is_local(settings) -> bool:
    return str(getattr(settings, "model_provider", "cloud") or "cloud").strip().lower() == "local"


def get_client(settings):
    provider = str(getattr(settings, "model_provider", "cloud") or "cloud").strip().lower()
    if provider == "local":
        from yaad.llm.local_client import LocalClient
        return LocalClient(settings)
    if provider == "cloud":
        from yaad.llm.meta_client import MetaClient
        return MetaClient(settings)
    raise ValueError(f"Unknown model provider: {getattr(settings, 'model_provider', None)!r}")
