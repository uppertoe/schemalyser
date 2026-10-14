"""Signatures over the examination's reports, with the Ed25519 scheme of RFC 8032, written here so that the core needs
no library for it and the service, which has the scheme in its own standard library, verifies the same signatures.

A key is 32 bytes of randomness, kept by the examiner outside any repository; its public key is 32 bytes that anyone
may hold. A signature is 64 bytes over the exact bytes signed. This module knows nothing of any layer: it reads and
writes bytes alone.
"""
import hashlib
import secrets

P = 2 ** 255 - 19
Q = 2 ** 252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
I = pow(2, (P - 1) // 4, P)


def _recover_x(y, sign):
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P)
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = (x * I) % P
    if (x * x - xx) % P != 0:
        raise ValueError("not a point of the curve")
    if (x & 1) != sign:
        x = P - x
    return x


B = (_recover_x(4 * pow(5, P - 2, P) % P, 0), 4 * pow(5, P - 2, P) % P, 1, 0)
B = (B[0], B[1], 1, (B[0] * B[1]) % P)


def _add(a, b):
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    e = (y1 - x1) * (y2 - x2) % P
    f = (y1 + x1) * (y2 + x2) % P
    g = 2 * t1 * t2 * D % P
    h = 2 * z1 * z2 % P
    return ((f - e) * (h - g) % P, (f + e) * (h + g) % P, (h + g) * (h - g) % P, (f - e) * (f + e) % P)


def _multiply(scalar, point):
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _encode(point):
    x, y, z, _ = point
    inverse = pow(z, P - 2, P)
    x, y = x * inverse % P, y * inverse % P
    return ((y | ((x & 1) << 255)).to_bytes(32, "little"))


def _decode(data):
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return (x, y, 1, x * y % P)


def _equal(a, b):
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % P == 0 and (y1 * z2 - y2 * z1) % P == 0


def _secret_expand(key):
    h = hashlib.sha512(key).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def new_key():
    """A fresh private key, 32 bytes."""
    return secrets.token_bytes(32)


def public_key(key):
    """The public key of a private key, 32 bytes."""
    a, _ = _secret_expand(key)
    return _encode(_multiply(a, B))


def sign(key, message):
    """The 64-byte signature of a message under a private key."""
    a, prefix = _secret_expand(key)
    public = _encode(_multiply(a, B))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % Q
    rs = _encode(_multiply(r, B))
    h = int.from_bytes(hashlib.sha512(rs + public + message).digest(), "little") % Q
    s = (r + h * a) % Q
    return rs + s.to_bytes(32, "little")


def verify(public, message, signature):
    """Whether a signature is the public key's holder's signature of the message."""
    if len(public) != 32 or len(signature) != 64:
        return False
    try:
        point = _decode(public)
        rs = _decode(signature[:32])
    except ValueError:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % Q
    return _equal(_multiply(s, B), _add(rs, _multiply(h, point)))
