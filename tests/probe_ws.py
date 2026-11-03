"""Plays two RustDesk devices with "Use WebSocket" turned on through a relayed session.

Usage: probe_ws.py <base URL> <key>, for example probe_ws.py wss://rustdesk.example.com <key>

Device A signs in over the /ws/id WebSocket the way the RustDesk app does in WebSocket mode, answers the server's
heartbeats, and gets device B's connection request over that same WebSocket. Both then meet on /ws/relay and pass
data. The probe also checks that a connection request with a wrong key is refused. With PROBE_IDLE set, device A
stays signed in that many seconds first. Uses only the standard library.
"""

import base64
import os
import random
import socket
import ssl
import struct
import sys
import time
import urllib.parse
import uuid


def varint(value):
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out += bytes([byte | 0x80])
        else:
            return out + bytes([byte])


def field(number, payload):
    """Encodes a length-delimited protobuf field."""
    if isinstance(payload, str):
        payload = payload.encode()
    return varint(number << 3 | 2) + varint(len(payload)) + payload


def read_varint(data, pos):
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def parse(data):
    """Returns {field number: value} for a protobuf message, with varints as ints and the rest as bytes."""
    fields, pos = {}, 0
    while pos < len(data):
        tag, pos = read_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            fields[number], pos = read_varint(data, pos)
        elif wire == 2:
            size, pos = read_varint(data, pos)
            fields[number], pos = data[pos:pos + size], pos + size
        else:
            raise RuntimeError(f"unexpected wire type {wire}")
    return fields


def message(data):
    """Splits a RendezvousMessage into the number of its set field and that field's message."""
    fields = parse(data)
    if len(fields) != 1:
        raise RuntimeError(f"expected one message, got fields {sorted(fields)}")
    number, payload = next(iter(fields.items()))
    return number, payload if isinstance(payload, bytes) else b""


class WebSocket:
    def __init__(self, base, path):
        url = urllib.parse.urlsplit(base)
        secure = url.scheme == "wss"
        port = url.port or (443 if secure else 80)
        sock = socket.create_connection((url.hostname, port), timeout=20)
        if secure:
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=url.hostname)
        self.sock = sock
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {url.hostname}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self.sock.recv(1)
            if not chunk:
                raise RuntimeError(f"{path}: connection closed during the handshake")
            response += chunk
        status = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError(f"{path}: unexpected handshake response {status!r}")
        self.heartbeats = 0

    def send(self, payload):
        mask = os.urandom(4)
        header = bytes([0x82])
        if len(payload) < 126:
            header += bytes([0x80 | len(payload)])
        else:
            header += bytes([0x80 | 126]) + struct.pack("!H", len(payload))
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("connection closed")
            data += chunk
        return data

    def frame(self):
        """Returns the next data frame's payload, or None once the server closes the connection."""
        while True:
            header = self.exact(2)
            opcode, length = header[0] & 0x0F, header[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", self.exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self.exact(8))[0]
            payload = self.exact(length) if length else b""
            if opcode == 0x8:
                return None
            if opcode == 0x9:
                continue
            if opcode in (0x1, 0x2):
                return payload

    def recv(self):
        """Returns the next message, answering heartbeats (empty frames) on the way, as the RustDesk app does."""
        while True:
            payload = self.frame()
            if payload is None:
                raise RuntimeError("the server closed the connection")
            if payload:
                return payload
            self.heartbeats += 1
            self.send(b"")

    def close(self):
        self.sock.close()


def main():
    base, key = sys.argv[1].rstrip("/"), sys.argv[2]
    peer_id = str(random.randint(100000000, 999999999))

    # A signs in like the app in WebSocket mode: RegisterPk (15) over /ws/id, answered with RegisterPkResponse (16).
    a = WebSocket(base, "/ws/id")
    a.send(field(15, field(1, peer_id) + field(2, uuid.uuid4().bytes) + field(3, os.urandom(32))))
    number, payload = message(a.recv())
    if number != 16 or parse(payload).get(1, 0) != 0:
        raise RuntimeError(f"expected register_pk_response OK, got field {number} {parse(payload)}")
    print(f"Device {peer_id} signed in over WebSocket")

    idle = int(os.environ.get("PROBE_IDLE", "0"))
    if idle:
        a.sock.settimeout(1)
        deadline = time.monotonic() + idle
        while time.monotonic() < deadline:
            try:
                payload = a.frame()
            except (socket.timeout, TimeoutError):
                continue
            if payload is None:
                raise RuntimeError("the server closed the signed-in connection")
            if payload:
                raise RuntimeError("unexpected message while idle")
            a.heartbeats += 1
            a.send(b"")
        a.sock.settimeout(20)
        if a.heartbeats < idle // 15:
            raise RuntimeError(f"only {a.heartbeats} heartbeats in {idle} seconds")
        print(f"Device stayed signed in for {idle} seconds, answering {a.heartbeats} heartbeats")

    # A wrong key is refused: PunchHoleRequest (8) gets PunchHoleResponse (11) with LICENSE_MISMATCH (3).
    intruder = WebSocket(base, "/ws/id")
    intruder.send(field(8, field(1, peer_id) + field(3, "not-the-key")))
    number, payload = message(intruder.recv())
    if number != 11 or parse(payload).get(3) != 3:
        raise RuntimeError(f"a wrong key wasn't refused: field {number} {parse(payload)}")
    intruder.close()
    print("Connection request with a wrong key refused")

    # B asks to connect to A. The server must deliver PunchHole (9) to A over A's own WebSocket.
    b = WebSocket(base, "/ws/id")
    b.send(field(8, field(1, peer_id) + field(3, key)))
    number, payload = message(a.recv())
    if number != 9:
        raise RuntimeError(f"expected punch_hole on the device's WebSocket, got field {number}")
    punch = parse(payload)
    print("Server delivered the connection request over the device's WebSocket")

    # Like the app in WebSocket mode, A joins the relay and answers with RelayResponse (19) on a new connection.
    session = str(uuid.uuid4())
    a_relay = WebSocket(base, "/ws/relay")
    a_relay.send(field(18, field(2, session) + field(6, key)))
    rr = WebSocket(base, "/ws/id")
    relay_server = punch.get(2, b"")
    rr.send(field(19, field(1, punch[1]) + field(2, session) + field(3, relay_server) + field(4, peer_id)))
    number, payload = message(b.recv())
    if number != 19 or parse(payload).get(2, b"").decode() != session:
        raise RuntimeError(f"expected relay_response for the session, got field {number}")
    print("Controller got the relay response")

    b_relay = WebSocket(base, "/ws/relay")
    b_relay.send(field(18, field(2, session) + field(6, key)))
    b_relay.send(b"ping")
    if a_relay.recv() != b"ping":
        raise RuntimeError("the relay didn't pass data to the device")
    a_relay.send(b"pong")
    if b_relay.recv() != b"pong":
        raise RuntimeError("the relay didn't pass data back")
    print("Relay passed data both ways over WebSocket")

    for connection in (a, b, rr, a_relay, b_relay):
        connection.close()


if __name__ == "__main__":
    main()
