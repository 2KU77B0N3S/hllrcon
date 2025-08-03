import itertools
import json
import struct
from typing import Any, ClassVar

from hllrcon.protocol.constants import DO_USE_REQUEST_HEADERS, HEADER_FORMAT


class RconRequest:
    """Represents a RCON request."""

    __request_id_counter: ClassVar["itertools.count[int]"] = itertools.count(start=1)

    def __init__(
        self,
        command: str,
        version: int,
        auth_token: str | None,
        content_body: dict[str, Any] | str = "",
    ) -> None:
        """Initializes a new RCON request."""
        self.name = command
        self.version = version
        self.auth_token = auth_token
        self.content_body = content_body
        self.request_id: int = next(self.__request_id_counter)

    def pack(self, version: int = 2) -> bytes:
        """Packs the request into a bytes object."""
        if version == 2:
            body = {
                "AuthToken": self.auth_token or " ",
                "Version": self.version,
                "Name": self.name,
                "ContentBody": (
                    self.content_body
                    if isinstance(self.content_body, str)
                    else json.dumps(self.content_body, separators=(",", ":"))
                ),
            }
            body_encoded = json.dumps(body, separators=(",", ":")).encode()
            if DO_USE_REQUEST_HEADERS:
                header = struct.pack(HEADER_FORMAT, self.request_id, len(body_encoded))
                return header + body_encoded
            return body_encoded
        else:
            content = (
                self.content_body
                if isinstance(self.content_body, str)
                else json.dumps(self.content_body)
            )
            body_encoded = (self.name + " " + content if content else self.name).encode()
            header = struct.pack(HEADER_FORMAT, self.request_id, len(body_encoded))
            return header + body_encoded
