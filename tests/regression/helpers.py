import json
from types import SimpleNamespace

from tutor_screen.local_parser import parse_local
from tutor_screen.models import Order


def order(text, **changes):
    payload = parse_local(text).model_dump()
    payload.update(order_id="ORD-0001", raw_text=text, extraction_source="model")
    payload.update(changes)
    return Order(**payload)


def response(payload):
    content = payload if isinstance(payload, str) or payload is None else json.dumps(payload, ensure_ascii=False)
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
