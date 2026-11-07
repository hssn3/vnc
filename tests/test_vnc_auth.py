import socket, struct, subprocess, sys, time, winreg

FIXED_KEY = bytes([23, 82, 107, 6, 35, 78, 88, 7])
REV_KEY = bytes(int(f'{b:08b}'[::-1], 2) for b in FIXED_KEY)

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

def des_ecb(data: bytes, key: bytes) -> bytes:
    c = Cipher(algorithms.TripleDES(key * 3), modes.ECB()).encryptor()
    return c.update(data) + c.finalize()

def vnc_encrypt_password(pw: str) -> bytes:
    raw = pw.encode()[:8].ljust(8, b'\x00')
    return des_ecb(raw, REV_KEY)

PASSWORD = sys.argv[1] if len(sys.argv) > 1 else 'Test1234'

key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, r'Software\TightVNC\Server')
winreg.SetValueEx(key, 'Password', 0, winreg.REG_BINARY, vnc_encrypt_password(PASSWORD))
winreg.SetValueEx(key, 'UseVncAuthentication', 0, winreg.REG_DWORD, 1)
winreg.SetValueEx(key, 'UseControlAuthentication', 0, winreg.REG_DWORD, 0)
winreg.SetValueEx(key, 'QueryAcceptOnTimeout', 0, winreg.REG_DWORD, 1)
winreg.SetValueEx(key, 'QueryTimeout', 0, winreg.REG_DWORD, 3)
winreg.SetValueEx(key, 'LoopbackOnly', 0, winreg.REG_DWORD, 1)
winreg.SetValueEx(key, 'AllowLoopback', 0, winreg.REG_DWORD, 1)
winreg.CloseKey(key)
print('registry written')

s = socket.create_connection(('127.0.0.1', 5900), timeout=5)
banner = s.recv(12)
print('banner:', banner)
s.sendall(b'RFB 003.008\n')
n = s.recv(1)[0]
if n == 0:
    ln = struct.unpack('>I', s.recv(4))[0]
    print('FAIL reason:', s.recv(ln)); sys.exit(1)
types = list(s.recv(n))
print('security types:', types)
s.sendall(bytes([2]))
challenge = s.recv(16)
resp = des_ecb(challenge, REV_KEY)
s.sendall(resp)
result = struct.unpack('>I', s.recv(4))[0]
print('auth result:', result, '(0 = OK)')
if result == 0:
    s.sendall(b'\x01')  # ClientInit shared
    hdr = s.recv(24)
    w, h = struct.unpack('>HH', hdr[:4])
    print('desktop name len:', struct.unpack('>I', hdr[20:24])[0], 'resolution:', w, h)
    name = s.recv(struct.unpack('>I', hdr[20:24])[0])
    print('desktop name:', name)
s.close()