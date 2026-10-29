import socket
import threading

from ps4_mcp.config import DDP_SEARCH, discover_ps4


def test_discover_via_ddp():
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))

    def answer():
        data, addr = srv.recvfrom(2048)
        assert data == DDP_SEARCH
        srv.sendto(b"HTTP/1.1 200 Ok\nhost-id:0\nhost-type:PS4\nhost-name:PS4\n", addr)

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    try:
        assert discover_ps4(timeout=2, ddp_addr=srv.getsockname()) == "127.0.0.1"
    finally:
        t.join(2)
        srv.close()
