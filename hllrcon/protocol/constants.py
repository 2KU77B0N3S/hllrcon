from typing import Final

DO_ALLOW_CONCURRENT_REQUESTS: Final[bool] = False
DO_POP_V1_XORKEY: Final[bool] = True
DO_USE_REQUEST_HEADERS: Final[bool] = True  # Enable for v2 headers
DO_XOR_RESPONSES: Final[bool] = True

HEADER_FORMAT: Final[str] = "<II"
