"""VNC password encryption compatible with TightVNC / standard VNC auth.

TightVNC's DesCrypt uses a bit-reversed key schedule compared to standard
DES, so we reverse the bits of each key byte and use standard single DES
(via TripleDES with the key repeated three times).
"""
import warnings

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

FIXED_KEY = bytes([23, 82, 107, 6, 35, 78, 88, 7])


def _reverse_bits(data: bytes) -> bytes:
    return bytes(int(f"{b:08b}"[::-1], 2) for b in data)


def _des_encrypt(data: bytes, key: bytes) -> bytes:
    enc = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
    return enc.update(data) + enc.finalize()


def encrypt_password(password: str) -> bytes:
    """Encrypt for storage in TightVNC's registry 'Password' value."""
    raw = password.encode("ascii")[:8].ljust(8, b"\x00")
    return _des_encrypt(raw, _reverse_bits(FIXED_KEY))


def challenge_response(challenge: bytes, password: str) -> bytes:
    """Encrypt the 16-byte RFB auth challenge with the user password."""
    raw = password.encode("ascii")[:8].ljust(8, b"\x00")
    return _des_encrypt(challenge, _reverse_bits(raw))