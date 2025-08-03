import asyncio
import json
import base64
import struct
from typing import Union, Dict, List

class RconV2Error(Exception):
    pass

class AsyncHLLRconV2:
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self.auth_token: str = None
        self.xor_key: bytes = None
        self.reader: asyncio.StreamReader = None
        self.writer: asyncio.StreamWriter = None

    async def connect(self):
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        await self._server_connect()
        await self._login()

    async def close(self):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def _server_connect(self):
        request = {
            "AuthToken": " ",
            "Version": 2,
            "Name": "ServerConnect",
            "ContentBody": " "
        }
        json_bytes = json.dumps(request).encode('utf-8')
        self.writer.write(json_bytes)
        await self.writer.drain()

        # Response header unencrypted
        header = await self.reader.readexactly(8)
        id_, length = struct.unpack('!II', header)
        content = await self.reader.readexactly(length)
        # No decrypt for ServerConnect
        response = json.loads(content.decode('utf-8'))
        if response['StatusCode'] != 200:
            raise RconV2Error(f"ServerConnect failed: {response['StatusMessage']}")
        self.xor_key = base64.b64decode(response['ContentBody'])

    async def _login(self):
        request = {
            "AuthToken": " ",
            "Version": 2,
            "Name": "Login",
            "ContentBody": self.password
        }
        await self._send_request(request)
        response = await self._receive_response()
        if response['StatusCode'] != 200:
            raise RconV2Error(f"Login failed: {response['StatusMessage']}")
        self.auth_token = response['ContentBody']

    async def execute(self, name: str, content_body: Union[str, Dict, List] = "") -> str:
        if isinstance(content_body, (dict, list)):
            content_body = json.dumps(content_body)
        request = {
            "AuthToken": self.auth_token,
            "Version": 2,
            "Name": name,
            "ContentBody": content_body
        }
        await self._send_request(request)
        response = await self._receive_response()
        if response['StatusCode'] != 200:
            raise RconV2Error(f"Command {name} failed: {response['StatusMessage']}")
        return response['ContentBody']

    async def _send_request(self, request: Dict):
        json_bytes = json.dumps(request).encode('utf-8')
        if self.xor_key:  # No XOR for ServerConnect
            json_bytes = self._xor(json_bytes)
        self.writer.write(json_bytes)
        await self.writer.drain()

    async def _receive_response(self) -> Dict:
        header = await self.reader.readexactly(8)
        id_, length = struct.unpack('!II', header)
        content = await self.reader.readexactly(length)
        if self.xor_key:  # No XOR for ServerConnect
            content = self._xor(content)
        return json.loads(content.decode('utf-8'))

    def _xor(self, data: bytes) -> bytes:
        result = bytearray()
        key_len = len(self.xor_key)
        for i, byte in enumerate(data):
            result.append(byte ^ self.xor_key[i % key_len])
        return bytes(result)
