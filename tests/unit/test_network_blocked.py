import socket

import pytest
import requests
from pytest_socket import SocketBlockedError

pytestmark = pytest.mark.unit


def test_http_request_is_blocked():
    with pytest.raises(SocketBlockedError):
        requests.get("https://example.com", timeout=5)


def test_raw_socket_is_blocked():
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_dns_lookup_is_blocked():
    with pytest.raises(SocketBlockedError):
        socket.getaddrinfo("example.com", 443)
