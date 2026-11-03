"""Plays two RustDesk devices through a relayed session against a RustDesk server.

Usage: probe.py <host> <id port> <relay port> <key>

Device A signs in over UDP like a device that can be controlled. Device B asks over TCP to connect to it.
The probe then follows the client's messages through the server's local-network check (behind a proxy both
devices share one address, so the server takes them for neighbours), the client's fallback to the relay,
and the relay handshake, and finally passes data between A and B through the relay. It also checks that the
server refuses a connection request with a wrong key. Uses only the standard library.
"""

import os
import random
import socket
import sys
import time
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


def mangle(ip, port):
    """AddrMangle.encode with a zero timestamp."""
    a, b, c, d = (int(x) for x in ip.split("."))
    value = ((a | b << 8 | c << 16 | d << 24) << 49) | port
    raw = value.to_bytes(16, "little")
    return raw.rstrip(b"\0")


class Framed:
    """A TCP connection using the RustDesk length-prefix framing."""

    def __init__(self, host, port):
        self.sock = socket.create_connection((host, port), timeout=15)

    def send(self, payload):
        size = len(payload)
        if size <= 0x3F:
            header = bytes([size << 2])
        elif size <= 0x3FFF:
            header = ((size << 2) | 1).to_bytes(2, "little")
        else:
            header = ((size << 2) | 2).to_bytes(3, "little")
        self.sock.sendall(header + payload)

    def exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise RuntimeError("connection closed")
            data += chunk
        return data

    def recv(self):
        first = self.exact(1)[0]
        extra = first & 3
        header = first | int.from_bytes(self.exact(extra), "little") << 8 if extra else first
        return self.exact(header >> 2)

    def close(self):
        self.sock.close()


def main():
    host, id_port, relay_port, key = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    relay = f"{host}:{relay_port}"
    peer_id = str(random.randint(100000000, 999999999))

    # A signs in: RegisterPeer (6), then RegisterPk (15) when the server asks for the key.
    a = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    a.settimeout(15)
    a.connect((socket.gethostbyname(host), id_port))
    a.send(field(6, field(1, peer_id)))
    number, _ = message(a.recv(1024))
    if number != 7:
        raise RuntimeError(f"expected register_peer_response, got field {number}")
    a.send(field(15, field(1, peer_id) + field(2, uuid.uuid4().bytes) + field(3, os.urandom(32))))
    number, payload = message(a.recv(1024))
    if number != 16 or parse(payload).get(1, 0) != 0:
        raise RuntimeError(f"expected register_pk_response OK, got field {number} {parse(payload)}")
    print(f"Device {peer_id} signed in over UDP")

    # With PROBE_IDLE set, stay signed in that many seconds first, re-registering every 15 seconds as clients do,
    # so a proxy has to keep the device's UDP session open until the server needs it.
    idle = int(os.environ.get("PROBE_IDLE", "0"))
    while idle > 0:
        time.sleep(min(15, idle))
        idle -= 15
        a.send(field(6, field(1, peer_id)))
        number, _ = message(a.recv(1024))
        if number != 7:
            raise RuntimeError(f"expected register_peer_response, got field {number}")
    if os.environ.get("PROBE_IDLE"):
        print(f"Device stayed signed in for {os.environ['PROBE_IDLE']} seconds")

    # A wrong key is refused: PunchHoleRequest (8) gets PunchHoleResponse (11) with LICENSE_MISMATCH (3).
    intruder = Framed(host, id_port)
    intruder.send(field(8, field(1, peer_id) + field(3, "not-the-key")))
    number, payload = message(intruder.recv())
    if number != 11 or parse(payload).get(3) != 3:
        raise RuntimeError(f"a wrong key wasn't refused: field {number} {parse(payload)}")
    intruder.close()
    print("Connection request with a wrong key refused")

    # B asks to connect to A over TCP.
    b = Framed(host, id_port)
    b.send(field(8, field(1, peer_id) + field(3, key)))

    # A gets FetchLocalAddr (12) or PunchHole (9) over UDP: the server reaching a device through the proxy.
    number, payload = message(a.recv(1024))
    if number not in (9, 12):
        raise RuntimeError(f"expected punch_hole or fetch_local_addr, got field {number}")
    b_addr = parse(payload)[1]
    print("Server reached the device over UDP" + (" (local-network check)" if number == 12 else ""))

    if number == 12:
        # The client answers a local-network check with LocalAddr (13) on a new TCP connection.
        la = Framed(host, id_port)
        la.send(field(13, field(1, b_addr) + field(2, mangle("192.0.2.10", 40000)) + field(3, relay) + field(4, peer_id)))
        number, payload = message(b.recv())
        response = parse(payload)
        if number != 11 or response.get(6) != 1 or response.get(4, b"").decode() != relay:
            raise RuntimeError(f"expected a local punch_hole_response naming the relay, got field {number} {response}")
        la.close()
        print("Controller got the device's local address and relay")

    # The controller can't reach a local address on another network and asks for the relay on a new
    # connection: RequestRelay (18) through the server, which passes it to A over UDP.
    b.close()
    b = Framed(host, id_port)
    session = str(uuid.uuid4())
    b.send(field(18, field(1, peer_id) + field(2, session) + field(4, relay) + field(6, key)))
    number, payload = message(a.recv(1024))
    if number != 18 or parse(payload).get(2, b"").decode() != session:
        raise RuntimeError(f"expected request_relay for the session, got field {number}")
    print("Server passed the relay request to the device over UDP")

    # A joins the relay and answers with RelayResponse (19), which the server passes to B.
    a_relay = Framed(host, relay_port)
    a_relay.send(field(18, field(2, session) + field(6, key)))
    rr = Framed(host, id_port)
    rr.send(field(19, field(1, parse(payload)[3]) + field(2, session) + field(3, relay) + field(4, peer_id)))
    number, payload = message(b.recv())
    if number != 19 or parse(payload).get(2, b"").decode() != session:
        raise RuntimeError(f"expected relay_response for the session, got field {number}")
    print("Controller got the relay response")

    # B joins the relay; once paired, the relay passes bytes through unchanged.
    b_relay = Framed(host, relay_port)
    b_relay.send(field(18, field(2, session) + field(6, key)))
    b_relay.sock.sendall(b"ping")
    if a_relay.exact(4) != b"ping":
        raise RuntimeError("the relay didn't pass data to the device")
    a_relay.sock.sendall(b"pong")
    if b_relay.exact(4) != b"pong":
        raise RuntimeError("the relay didn't pass data back")
    print("Relay passed data both ways")

    for connection in (b, rr, a_relay, b_relay):
        connection.close()
    a.close()


if __name__ == "__main__":
    main()
