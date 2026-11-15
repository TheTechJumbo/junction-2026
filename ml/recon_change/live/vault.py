#!/usr/bin/env python3
"""Files at rest on Iridis encrypted with a key that only the trusted side keeps (PRIVACY.md). AES-256-GCM in 1 MiB
chunks, so a 4 GB video goes through in constant memory, and a changed, reordered, cut or swapped file is refused.

    from vault import new_key, seal, unseal, seal_file, unseal_file, key_id
    key = new_key()                              # 32 random bytes, one per session, made on the trusted side
    blob = seal(key, data)                       # bytes -> sealed bytes (36-byte header + 16 bytes per MiB)
    data = unseal(key, blob)                     # raises VaultError on a wrong key or a changed file
    seal_file(key, "clip.mp4", "clip.mp4.jv")    # streamed; unseal_file writes to a temporary name, then renames

On the GPU node the key lives in the job's memory only. It arrives through Slurm, not through any file on scratch:
    the job:     inbox = KeyInbox()              # a FIFO in /dev/shm (RAM), mode 0600, named after the Slurm job
                 key = inbox.get(kid, wait=60)   # kid = key_id(key), read from a sealed file's header
    the laptop:  push_key(key, job_id)           # ssh iridis srun --jobid=J --overlap ... with the key on stdin
                                                 # (stdin, never argv: argv shows in `ps` to every user)

The file: b"JVAULT1\\0" | key id (8 bytes, HMAC-SHA256(key, "key-id")) | salt (16 random bytes) | chunk size (4, big-endian),
then chunks of AES-256-GCM(file key, nonce = chunk number as 12 bytes big-endian, AAD = header | chunk number (8) |
last-chunk flag (1)) with their 16-byte tags. file key = HMAC-SHA256(key, "file" | salt): every file has its own key, so
nonces never repeat under one key. Only the last chunk is sealed with the flag set: a file cut at a chunk boundary fails.

Backends: the `cryptography` package where it is installed (the laptop's Python), otherwise the OpenSSL library this
Python already links for `ssl`, through ctypes (Iridis's .venv-recon has no `cryptography`; nothing is installed).
VAULT_BACKEND=ctypes forces the second, for tests. Both write the same format.

Honest limits: root on the GPU node can read the job's memory and so the key and every decrypted frame while a job
runs. This protects scratch files against other users, backups, a later compromise and the storage operator; it does
not protect against the machine the job runs on.
"""
import ctypes
import ctypes.util
import glob
import hashlib
import hmac
import io
import os
import secrets
import shlex
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

MAGIC = b"JVAULT1\0"
CHUNK = 1 << 20
HEADER = struct.Struct(">8s8s16sI")  # magic, key id, salt, chunk size: 36 bytes
TAG = 16


class VaultError(ValueError):
    """A wrong key, a changed or cut file, or not a sealed file at all."""


def new_key():
    return secrets.token_bytes(32)


def key_id(key):
    """8 bytes that name the key without revealing it (a sealed file says which key it needs)."""
    return hmac.new(_check(key), b"key-id", hashlib.sha256).digest()[:8]


def derive(key, label):
    """A separate 32-byte key for one purpose (for example "results"), from the session key."""
    return hmac.new(_check(key), b"derive|" + label.encode(), hashlib.sha256).digest()


def _check(key):
    if not isinstance(key, (bytes, bytearray)) or len(key) != 32:
        raise VaultError("a vault key is 32 bytes")
    return bytes(key)


# ---- AES-256-GCM: the cryptography package, or OpenSSL through ctypes ----
class _Cryptography:
    name = "cryptography"

    def __init__(self):
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._aes, self._bad = AESGCM, InvalidTag

    def seal(self, key, nonce, data, aad):
        return self._aes(key).encrypt(nonce, data, aad)

    def open(self, key, nonce, data, aad):
        try:
            return self._aes(key).decrypt(nonce, data, aad)
        except self._bad:
            raise VaultError("wrong key, or the file was changed") from None


class _OpenSSL:
    name = "openssl-ctypes"
    SET_IVLEN, GET_TAG, SET_TAG = 0x9, 0x10, 0x11

    def __init__(self):
        self.lib = lib = self._load()
        vp, ip = ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)
        lib.EVP_CIPHER_CTX_new.restype = vp
        lib.EVP_CIPHER_CTX_free.argtypes = [vp]
        lib.EVP_aes_256_gcm.restype = vp
        for name in ("EVP_EncryptInit_ex", "EVP_DecryptInit_ex"):
            getattr(lib, name).argtypes = [vp, vp, vp, ctypes.c_char_p, ctypes.c_char_p]
        for name in ("EVP_EncryptUpdate", "EVP_DecryptUpdate"):
            getattr(lib, name).argtypes = [vp, vp, ip, ctypes.c_char_p, ctypes.c_int]
        for name in ("EVP_EncryptFinal_ex", "EVP_DecryptFinal_ex"):
            getattr(lib, name).argtypes = [vp, vp, ip]
        lib.EVP_CIPHER_CTX_ctrl.argtypes = [vp, ctypes.c_int, ctypes.c_int, vp]

    @staticmethod
    def _load():
        """The libcrypto this Python's ssl module uses (already in the process), else one the system has."""
        import ssl  # noqa: F401  loads it
        found = [os.environ.get("VAULT_LIBCRYPTO", "")]
        try:
            with open("/proc/self/maps") as maps:
                found += [line.split()[-1] for line in maps if "libcrypto" in line]
        except OSError:
            pass
        found += sorted(glob.glob(os.path.join(sys.base_prefix, "DLLs", "libcrypto*.dll")), reverse=True)
        found += [ctypes.util.find_library("crypto") or ""]
        for path in dict.fromkeys(p for p in found if p):
            try:
                lib = ctypes.CDLL(path)
                lib.EVP_aes_256_gcm  # noqa: B018  the function we need is there
                return lib
            except (OSError, AttributeError):
                continue
        raise RuntimeError("no OpenSSL libcrypto found for AES-GCM (and the cryptography package is not installed)")

    def _run(self, encrypt, key, nonce, data, aad, tag=None):
        lib, n = self.lib, ctypes.c_int(0)
        ctx = lib.EVP_CIPHER_CTX_new()
        if not ctx:
            raise MemoryError("EVP_CIPHER_CTX_new")
        init, update, final = ((lib.EVP_EncryptInit_ex, lib.EVP_EncryptUpdate, lib.EVP_EncryptFinal_ex) if encrypt
                               else (lib.EVP_DecryptInit_ex, lib.EVP_DecryptUpdate, lib.EVP_DecryptFinal_ex))
        try:
            ok = init(ctx, lib.EVP_aes_256_gcm(), None, None, None) == 1
            ok = ok and lib.EVP_CIPHER_CTX_ctrl(ctx, self.SET_IVLEN, len(nonce), None) == 1
            ok = ok and init(ctx, None, None, key, nonce) == 1
            ok = ok and (not aad or update(ctx, None, ctypes.byref(n), aad, len(aad)) == 1)
            out = ctypes.create_string_buffer(len(data) + 16)
            ok = ok and update(ctx, out, ctypes.byref(n), data, len(data)) == 1
            size = n.value
            if not ok:
                raise RuntimeError("OpenSSL AES-GCM failed")
            if encrypt:
                if final(ctx, ctypes.byref(out, size), ctypes.byref(n)) != 1:
                    raise RuntimeError("OpenSSL AES-GCM failed")
                size += n.value
                tag_buf = ctypes.create_string_buffer(TAG)
                if lib.EVP_CIPHER_CTX_ctrl(ctx, self.GET_TAG, TAG, tag_buf) != 1:
                    raise RuntimeError("OpenSSL AES-GCM tag")
                return out.raw[:size] + tag_buf.raw
            tag_buf = ctypes.create_string_buffer(tag, TAG)
            if lib.EVP_CIPHER_CTX_ctrl(ctx, self.SET_TAG, TAG, tag_buf) != 1 or final(ctx, ctypes.byref(out, size), ctypes.byref(n)) != 1:
                raise VaultError("wrong key, or the file was changed")
            return out.raw[:size + n.value]
        finally:
            lib.EVP_CIPHER_CTX_free(ctx)

    def seal(self, key, nonce, data, aad):
        return self._run(True, key, nonce, bytes(data), aad)

    def open(self, key, nonce, data, aad):
        if len(data) < TAG:
            raise VaultError("cut short")
        return self._run(False, key, nonce, bytes(data[:-TAG]), aad, tag=bytes(data[-TAG:]))


def _backend():
    if os.environ.get("VAULT_BACKEND") != "ctypes":
        try:
            return _Cryptography()
        except ImportError:
            pass
    return _OpenSSL()


AEAD = _backend()


# ---- the format ----
def _file_key(key, salt):
    return hmac.new(key, b"file|" + salt, hashlib.sha256).digest()


def _aad(header, number, last):
    return header + struct.pack(">QB", number, 1 if last else 0)


def _nonce(number):
    return number.to_bytes(12, "big")


def seal_stream(key, src, dst, chunk=CHUNK):
    """Read src (a binary file object) to its end, write the sealed form to dst. Returns the bytes written."""
    key = _check(key)
    salt = secrets.token_bytes(16)
    header = HEADER.pack(MAGIC, key_id(key), salt, chunk)
    fkey = _file_key(key, salt)
    dst.write(header)
    written, number = len(header), 0
    piece = src.read(chunk)
    while True:
        after = src.read(chunk) if len(piece) == chunk else b""
        last = not after
        sealed = AEAD.seal(fkey, _nonce(number), piece, _aad(header, number, last))
        dst.write(sealed)
        written += len(sealed)
        if last:
            return written
        piece, number = after, number + 1


def read_header(src):
    """(key id, salt, chunk size, header bytes) of a sealed file object, read from its start."""
    header = src.read(HEADER.size)
    if len(header) != HEADER.size:
        raise VaultError("not a sealed file (too short)")
    magic, kid, salt, chunk = HEADER.unpack(header)
    if magic != MAGIC or not 1 <= chunk <= 1 << 26:
        raise VaultError("not a sealed file")
    return kid, salt, chunk, header


def unseal_stream(key, src, dst):
    """Check and decrypt a sealed file object into dst, chunk by chunk. Each chunk is checked before it is written; a
    file cut short fails at its end, after earlier chunks were written: write to a temporary file (unseal_file does)."""
    key = _check(key)
    kid, salt, chunk, header = read_header(src)
    if kid != key_id(key):
        raise VaultError("sealed with another key")
    fkey = _file_key(key, salt)
    number, size = 0, chunk + TAG
    piece = src.read(size)
    written = 0
    while True:
        after = src.read(size) if len(piece) == size else b""
        last = not after
        plain = AEAD.open(fkey, _nonce(number), piece, _aad(header, number, last))
        dst.write(plain)
        written += len(plain)
        if last:
            return written
        piece, number = after, number + 1


def seal(key, data, chunk=CHUNK):
    out = io.BytesIO()
    seal_stream(key, io.BytesIO(bytes(data)), out, chunk)
    return out.getvalue()


def unseal(key, blob):
    out = io.BytesIO()
    unseal_stream(key, io.BytesIO(bytes(blob)), out)
    return out.getvalue()


def is_sealed(data_or_path):
    try:
        head = Path(data_or_path).open("rb").read(8) if isinstance(data_or_path, (str, Path)) else bytes(data_or_path[:8])
    except OSError:
        return False
    return head == MAGIC


def seal_file(key, src, dst, chunk=CHUNK):
    dst = Path(dst)
    tmp = dst.with_name(dst.name + ".part")
    with open(src, "rb") as fin, open(tmp, "wb") as fout:
        n = seal_stream(key, fin, fout, chunk)
    os.replace(tmp, dst)
    return n


def unseal_file(key, src, dst):
    """Decrypt to dst only if the whole file checks out (a temporary name until then)."""
    dst = Path(dst)
    tmp = dst.with_name(dst.name + ".part")
    try:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            n = unseal_stream(key, fin, fout)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    os.replace(tmp, dst)
    return n


# ---- the key's way to a GPU job, in memory only ----
def inbox_path(job_id=None, uid=None):
    """Where a Slurm job listens for keys: /dev/shm/jvault-<uid>-<job>/keys.fifo (RAM, on the job's node)."""
    job_id = job_id or os.environ.get("SLURM_JOB_ID") or f"pid{os.getpid()}"
    uid = os.getuid() if uid is None else uid
    return Path("/dev/shm") / f"jvault-{uid}-{job_id}" / "keys.fifo"


class KeyInbox:
    """Keys sent to this job (push_key) as lines of 64 hex digits, kept in this process's memory by key id. The FIFO
    holds nothing itself: bytes written to it are only in the kernel's pipe buffer until this process reads them."""

    def __init__(self, path=None):
        self.path = Path(path) if path else inbox_path()
        self.keys, self._got = {}, threading.Condition()
        self.path.parent.mkdir(mode=0o700, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        if self.path.exists() and not self.path.is_fifo():
            self.path.unlink()  # a key someone wrote as a plain file is not accepted, and is removed
        if not self.path.exists():
            os.mkfifo(self.path, 0o600)
        threading.Thread(target=self._listen, daemon=True, name="key-inbox").start()

    def _listen(self):
        while True:
            try:
                with open(self.path, "rb") as fifo:  # waits for a writer
                    lines = fifo.read(4096).split(b"\n")
            except OSError:
                if not self.path.exists():
                    return
                time.sleep(1)
                continue
            for line in lines:
                try:
                    key = bytes.fromhex(line.strip().decode())
                    with self._got:
                        self.keys[key_id(key)] = key
                        self._got.notify_all()
                except (ValueError, VaultError):
                    continue

    def get(self, kid, wait=0.0):
        """The key with this id, waiting up to `wait` seconds for it to arrive. None if it does not."""
        with self._got:
            self._got.wait_for(lambda: kid in self.keys, timeout=wait)
            return self.keys.get(kid)

    def close(self):
        self.keys.clear()
        try:
            self.path.unlink()
            self.path.parent.rmdir()
        except OSError:
            pass


def push_key(key, job_id, host="iridis", uid=None, timeout=60):
    """Send the key to Slurm job `job_id`'s KeyInbox: ssh to the login node, then `srun --jobid --overlap` onto the job's
    node, where `cat` writes stdin into the FIFO. The key travels on stdin only (SSH-encrypted to the login node, then
    Slurm's own I/O forwarding inside the cluster). Refuses unless the FIFO exists, so it never lands in a plain file."""
    _check(key)
    job = str(int(job_id))
    fifo = f"/dev/shm/jvault-{uid if uid is not None else '$(id -u)'}-{job}/keys.fifo"
    inner = f'f={fifo}; [ -p "$f" ] || {{ echo "no key inbox at $f" >&2; exit 3; }}; exec cat > "$f"'
    remote = f"srun --jobid={job} --overlap -N1 -n1 --mem=0 --gres=none bash -c {shlex.quote(inner)}"
    done = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, f"bash -lc {shlex.quote(remote)}"],
                          input=(key.hex() + "\n").encode(), capture_output=True, timeout=timeout)
    if done.returncode != 0:
        raise RuntimeError(f"key not delivered to job {job}: {done.stderr.decode(errors='replace').strip()[-200:]}")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Seal or unseal a file with a hex key from VAULT_KEY_HEX (stdin if unset).")
    ap.add_argument("mode", choices=("seal", "unseal", "backend"))
    ap.add_argument("src", nargs="?")
    ap.add_argument("dst", nargs="?")
    a = ap.parse_args()
    if a.mode == "backend":
        print(AEAD.name)
        sys.exit(0)
    k = bytes.fromhex(os.environ.get("VAULT_KEY_HEX") or sys.stdin.readline().strip())
    print((seal_file if a.mode == "seal" else unseal_file)(k, a.src, a.dst), "bytes")
