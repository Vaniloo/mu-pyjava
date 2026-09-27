"""Small line protocol: tab fields with UTF-8 text encoded as Base64."""

import base64
from typing import Tuple


def encode_text(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def decode_text(value: str) -> str:
    return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")


def parse_request(line: str) -> Tuple[str, str, str]:
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 3 or parts[0] != "CHAT" or not parts[1]:
        raise ValueError("Expected CHAT<TAB>id<TAB>base64-text")
    return parts[0], parts[1], decode_text(parts[2])


def event_line(request_id: str, kind: str, message: str) -> str:
    if "\t" in request_id or "\n" in request_id:
        raise ValueError("Invalid request id")
    return "EVENT\t" + request_id + "\t" + kind + "\t" + encode_text(message)
