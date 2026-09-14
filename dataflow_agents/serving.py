"""Endpoint conventions shared by resource forms and standalone pipelines."""
from urllib.parse import urlsplit, urlunsplit


def normalize_chat_url(url):
    """Accept an origin or /v1 base; preserve explicit compatible endpoints."""
    parts = urlsplit(url.strip())
    path = parts.path.rstrip('/')
    if not path:
        path = '/v1/chat/completions'
    elif path.endswith('/v1'):
        path += '/chat/completions'
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))
