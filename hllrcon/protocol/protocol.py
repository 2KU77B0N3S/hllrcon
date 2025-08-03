import json
import itertools
import struct
import array
import asyncio
import base64
import contextlib
import logging
from enum import IntEnum
from collections import deque
from collections.abc import Callable
from typing import Any, ClassVar, Self
from typing_extensions import override

from hllrcon.exceptions import (
    HLLAuthError,
    HLLConnectionError,
    HLLConnectionLostError,
    HLLConnectionRefusedError,
    HLLCommandError,
    HLLMessageError,
)
from hllrcon.protocol.constants import (
    DO_ALLOW_CONCURRENT_REQUESTS,
    DO_POP_V1_XORKEY,
    DO_USE_REQUEST_HEADERS,
    DO_XOR_RESPONSES,
    HEADER_FORMAT,
)

class RconResponseStatus(IntEnum):
    OK = 200
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    INTERNAL_ERROR = 500

class RconResponse:
    def __init__(
        self,
        request_id: int,
        command: str,
        version: int,
        status_code: RconResponseStatus,
        status_message: str,
        content_body: str,
    ) -> None:
        self.request_id = request_id
        self.name = command
        self.version = version
        self.status_code = status_code
        self.status_message = status_message
        self.content_body = content_body

    @property
    def content_dict(self) -> dict[str, Any]:
        parsed_content = json.loads(self.content_body)
        if not isinstance(parsed_content, dict):
            raise TypeError(f"Expected JSON content to be a dict, got {type(parsed_content)}")
        return parsed_content

    def __str__(self) -> str:
        try:
            content: str | dict[str, Any] = self.content_dict
        except (json.JSONDecodeError, TypeError):
            content = self.content_body
        return f"{self.status_code} {self.name} {content}"

    @classmethod
    def unpack(cls, request_id: int, body_encoded: bytes, version: int) -> Self:
        if version == 2:
            body = json.loads(body_encoded)
            return cls(
                request_id=request_id,
                command=str(body["name"]),
                version=int(body["version"]),
                status_code=RconResponseStatus(int(body["statusCode"])),
                status_message=str(body["statusMessage"]),
                content_body=body["contentBody"],
            )
        else:
            body_str = body_encoded.decode('utf-8')
            status_code = RconResponseStatus.OK if body_str else RconResponseStatus.INTERNAL_ERROR
            return cls(
                request_id=request_id,
                command="",
                version=1,
                status_code=status_code,
                status_message="OK" if status_code == 200 else "Error",
                content_body=body_str,
            )

    def raise_for_status(self) -> None:
        if self.status_code != RconResponseStatus.OK:
            raise HLLCommandError(self.status_code, self.status_message)

class RconRequest:
    __request_id_counter: ClassVar["itertools.count[int]"] = itertools.count(start=1)

    def __init__(
        self,
        command: str,
        version: int,
        auth_token: str | None,
        content_body: dict[str, Any] | str = "",
    ) -> None:
        self.name = command
        self.version = version
        self.auth_token = auth_token
        self.content_body = content_body
        self.request_id: int = next(self.__request_id_counter)

    def pack(self) -> bytes:
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

class RconProtocol(asyncio.Protocol):
    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        timeout: float | None = None,
        logger: logging.Logger | None = None,
        on_connection_lost: Callable[[Exception | None], Any] | None = None,
    ) -> None:
        self._transport: asyncio.Transport | None = None
        self._buffer: bytes = b""
        self.loop = loop
        self.timeout = timeout
        self.logger = logger or logging.getLogger()
        self.on_connection_lost = on_connection_lost
        self.xorkey: bytes | None = None
        self.auth_token: str | None = None

        if DO_USE_REQUEST_HEADERS:
            self._waiters: dict[int, asyncio.Future[RconResponse]] = {}
        else:
            self._queue: deque[asyncio.Future[RconResponse]] = deque()

        if not DO_ALLOW_CONCURRENT_REQUESTS:
            self._lock = asyncio.Lock()

        if DO_POP_V1_XORKEY:
            self._seen_v1_xorkey: bool = False

    @classmethod
    async def connect(cls: type[Self], host: str, port: int, password: str, timeout: float | None = 10,
                      loop: asyncio.AbstractEventLoop | None = None, logger: logging.Logger | None = None,
                      on_connection_lost: Callable[[Exception | None], Any] | None = None) -> Self:
        loop = loop or asyncio.get_running_loop()

        def protocol_factory() -> Self:
            return cls(loop=loop, timeout=timeout, logger=logger, on_connection_lost=on_connection_lost)

        try:
            _, self = await asyncio.wait_for(loop.create_connection(protocol_factory, host=host, port=port), timeout=15)
        except TimeoutError:
            raise HLLConnectionError(f"Address {host} could not be resolved")
        except ConnectionRefusedError:
            raise HLLConnectionRefusedError(f"The server refused connection over port {port}")

        self.logger.info("Connected!")

        try:
            if DO_POP_V1_XORKEY:
                await asyncio.sleep(0.1)
            await self.authenticate(password)
        except HLLAuthError:
            self.disconnect()
            raise

        return self

    def disconnect(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    def is_connected(self) -> bool:
        return self._transport is not None and not self._transport.is_closing()

    @override
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        if not isinstance(transport, asyncio.Transport):
            raise TypeError("Transport must be an instance of asyncio.Transport")
        self.logger.info("Connection made! Transport: %s", transport)
        self._transport = transport

    @override
    def data_received(self, data: bytes) -> None:
        self.logger.debug("Incoming: (%s) %s", self._xor(data).count(b"\t"), data[:10])
        if DO_POP_V1_XORKEY and not self._seen_v1_xorkey:
            self.logger.info("Ignoring V1 XOR-key: %s", data[:4])
            self._seen_v1_xorkey = True
            data = data[4:]
            if not data:
                return
        self._buffer += data
        self._read_from_buffer()

    def _read_from_buffer(self) -> None:
        header_len = struct.calcsize(HEADER_FORMAT)
        if len(self._buffer) < header_len:
            self.logger.debug("Buffer too small (%s < %s)", len(self._buffer), header_len)
            return
        pkt_id, pkt_len = struct.unpack(HEADER_FORMAT, self._buffer[:header_len])
        pkt_size = header_len + pkt_len
        if len(self._buffer) >= pkt_size:
            decoded_body = self._xor(self._buffer[header_len:pkt_size])
            pkt = RconResponse.unpack(pkt_id, decoded_body, version=2)
            self._buffer = self._buffer[pkt_size:]

            if DO_USE_REQUEST_HEADERS:
                waiter = self._waiters.pop(pkt_id, None)
                if waiter:
                    waiter.set_result(pkt)
                else:
                    self.logger.warning("No waiter for packet ID %s", pkt_id)
            elif not self._queue:
                self.logger.warning("No waiter for packet ID %s", pkt_id)
            else:
                self._queue.popleft().set_result(pkt)

            if self._buffer:
                self._read_from_buffer()

    @override
    def connection_lost(self, exc: Exception | None) -> None:
        self._transport = None
        waiters = list(self._waiters.values()) if DO_USE_REQUEST_HEADERS else list(self._queue)
        if DO_USE_REQUEST_HEADERS:
            self._waiters.clear()
        else:
            self._queue.clear()

        if exc:
            self.logger.warning("Connection lost: %s", exc)
            for waiter in waiters:
                if not waiter.done():
                    waiter.set_exception(HLLConnectionLostError(str(exc)))
        else:
            self.logger.info("Connection closed")
            for waiter in waiters:
                waiter.cancel()

        if self.on_connection_lost:
            try:
                self.on_connection_lost(exc)
            except Exception:
                self.logger.exception("Failed to invoke on_connection_lost hook")

    def _xor(self, message: bytes, offset: int = 0) -> bytes:
        if not self.xorkey:
            return message
        n = [c ^ self.xorkey[(i + offset) % len(self.xorkey)] for i, c in enumerate(message)]
        res = array.array("B", n).tobytes()
        if len(res) != len(message):
            raise ValueError("XOR operation resulted in a different length")
        return res

    async def execute(self, command: str, version: int, content_body: dict[str, Any] | str = "") -> RconResponse:
        if not self._transport:
            raise HLLConnectionError("Connection is closed")
        request = RconRequest(command, version, self.auth_token, content_body)
        packed = request.pack()
        xor_packed = self._xor(packed)
        self.logger.debug("Writing: %s", packed)
        self._transport.write(xor_packed)
        waiter: asyncio.Future[RconResponse] = self.loop.create_future()
        if DO_USE_REQUEST_HEADERS:
            self._waiters[request.request_id] = waiter
        else:
            self._queue.append(waiter)
        response = await asyncio.wait_for(waiter, timeout=self.timeout)
        waiter.cancel()
        if DO_USE_REQUEST_HEADERS:
            self._waiters.pop(request.request_id, None)
        else:
            with contextlib.suppress(ValueError):
                self._queue.remove(waiter)
        return response

    async def authenticate(self, password: str) -> None:
        self.logger.debug("Waiting to login...")
        xorkey_resp = await self.execute("ServerConnect", 2, "")
        xorkey_resp.raise_for_status()
        if not isinstance(xorkey_resp.content_body, str):
            raise HLLMessageError("ServerConnect response content_body is not a string")
        self.xorkey = base64.b64decode(xorkey_resp.content_body)
        auth_token_resp = await self.execute("Login", 2, password)
        auth_token_resp.raise_for_status()
        self.auth_token = auth_token_resp.content_body
