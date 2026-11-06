from __future__ import annotations

import requests

UA = "XBot/0.1 (+https://github.com/yyinus/xbot; crypto research aggregator)"
_session = requests.Session()
_session.headers.update({"User-Agent": UA})


def get(url: str, timeout: int = 30, **kw) -> requests.Response:
    r = _session.get(url, timeout=timeout, **kw)
    r.raise_for_status()
    return r
