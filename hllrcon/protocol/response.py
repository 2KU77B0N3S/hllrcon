import json
from enum import IntEnum
from typing import Any, Self

from hllrcon.exceptions import HLLCommandError


class RconResponseStatus(IntEnum):
    """Enumeration of RCON response status codes."""
    OK = 200  # The request was successful.
    BAD_REQUEST = 400  # The request was invalid.
    UNAUTHORIZED = 401  # Insufficient or invalid authorization.
    INTERNAL_ERROR = 500  # An internal server error occurred.


class RconResponse:
    """Represents a RCON response."""

    def __init__(
        self,
        request_id: int,
        command: str,
        version: int,
        status_code: RconResponseStatus,
        status_message: str,
        content_body: str,
    ) -> None:
        """Initializes a new RCON response."""
        self.request_id = request_id
        self.name = command
        self.version = version
        self.status_code = status_code
        self.status_message = status_message
        self.content_body = content_body

    @property
    def content_dict(self) -> dict[str, Any]:
        """JSON-deserialize the content body of the response."""
        parsed_content = json.loads(self.content_body)
        if not isinstance(parsed_content, dict):
            msg = f"Expected JSON content to be a dict, got {type(parsed_content)}"
            raise TypeError(msg)
        return parsed_content

    def __str__(self) -> str:
        content: str | dict[str, Any]
        try:
            content = self.content_dict
        except (json.JSONDecodeError, TypeError):
            content = self.content_body
        return f"{self.status_code} {self.name} {content}"

    @classmethod
    def unpack(cls, pkt_id: int, body_encoded: bytes, version: int = 2) -> Self:
        """Unpacks a RCON response from its bytes representation."""
        if version == 2:
            body = json.loads(body_encoded.decode('utf-8'))
            return cls(
                request_id=pkt_id,
                command=str(body["name"]),
                version=int(body["version"]),
                status_code=RconResponseStatus(int(body["statusCode"])),
                status_message=str(body["statusMessage"]),
                content_body=body["contentBody"],
            )
        else:
            content_body = body_encoded.decode('utf-8')
            status_code = (
                RconResponseStatus.OK
                if content_body else RconResponseStatus.INTERNAL_ERROR
            )
            return cls(
                request_id=pkt_id,
                command="",
                version=1,
                status_code=status_code,
                status_message="OK" if status_code == RconResponseStatus.OK else "Error",
                content_body=content_body,
            )

    def raise_for_status(self) -> None:
        """Raises an exception if the response status is not OK."""
        if self.status_code != RconResponseStatus.OK:
            raise HLLCommandError(self.status_code, self.status_message)
