#!/usr/bin/env python3
"""Offline test of the at-rest encryption (vault.py): sealed data comes back only with the right key and unchanged;
a flipped bit, a cut, a swapped chunk or another key is refused; both backends (the cryptography package and OpenSSL
through ctypes) read each other's files; files stream; the key inbox on Linux takes a key through its FIFO only.
No network, no cluster.

    python live/test_vault.py
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vault  # noqa: E402


def refused(fn, *args):
    try:
        fn(*args)
    except vault.VaultError:
        return True
    return False


def check_round_trip():
    key = vault.new_key()
    for size in (0, 1, 1000, vault.CHUNK - 1, vault.CHUNK, vault.CHUNK + 1, 3 * vault.CHUNK + 17):
        data = os.urandom(size)
        blob = vault.seal(key, data)
        assert blob[:8] == vault.MAGIC and vault.is_sealed(blob)
        assert len(blob) == vault.HEADER.size + size + vault.TAG * max(1, -(-size // vault.CHUNK)), (size, len(blob))
        assert vault.unseal(key, blob) == data, size
        assert data[:64] not in blob or size < 16
    assert vault.seal(key, b"same") != vault.seal(key, b"same"), "every file needs its own salt"
    print(f"round trip ({vault.AEAD.name}): 0 B to 3 MiB, chunk edges included; the same data seals differently each time")


def check_tampering():
    key, other = vault.new_key(), vault.new_key()
    data = os.urandom(2 * vault.CHUNK + 500)
    blob = bytearray(vault.seal(key, data))
    assert refused(vault.unseal, other, bytes(blob))
    for where in (vault.HEADER.size + 5, vault.HEADER.size + vault.CHUNK + 20, len(blob) - 3, 20):
        bad = bytearray(blob)
        bad[where] ^= 1
        assert refused(vault.unseal, key, bytes(bad)), where
    step = vault.CHUNK + vault.TAG
    first, second = blob[vault.HEADER.size:vault.HEADER.size + step], blob[vault.HEADER.size + step:vault.HEADER.size + 2 * step]
    swapped = blob[:vault.HEADER.size] + second + first + blob[vault.HEADER.size + 2 * step:]
    assert refused(vault.unseal, key, bytes(swapped))
    assert refused(vault.unseal, key, bytes(blob[:vault.HEADER.size + 2 * step]))  # cut at a chunk boundary
    assert refused(vault.unseal, key, bytes(blob[:-1]))
    assert refused(vault.unseal, key, b"not sealed at all")
    assert refused(vault.unseal, key, bytes(blob[:10]))
    assert refused(vault.unseal, b"short", bytes(blob))
    print("tampering: another key, a flipped bit (header, chunks, tag), swapped chunks, a cut file and junk are refused")


def check_backends():
    """Files sealed by one backend open with the other (OpenSSL through ctypes is what Iridis uses)."""
    code = ("import sys,os;sys.path.insert(0,sys.argv[1]);os.environ['VAULT_BACKEND']=sys.argv[2];import vault;"
            "k=bytes.fromhex(sys.argv[3]);d=sys.stdin.buffer.read();"
            "sys.stdout.buffer.write(vault.seal(k,d) if sys.argv[4]=='seal' else vault.unseal(k,d));print(vault.AEAD.name,file=sys.stderr)")
    here = str(Path(__file__).resolve().parent)
    key, data = vault.new_key(), os.urandom(vault.CHUNK + 1234)
    names = []

    def run(backend, mode, payload):
        done = subprocess.run([sys.executable, "-c", code, here, backend, key.hex(), mode], input=payload, capture_output=True, timeout=60)
        assert done.returncode == 0, done.stderr.decode()[-400:]
        names.append(done.stderr.decode().strip())
        return done.stdout

    sealed_ctypes = run("ctypes", "seal", data)
    assert vault.unseal(key, sealed_ctypes) == data
    assert run("ctypes", "unseal", vault.seal(key, data)) == data
    assert run("default", "unseal", sealed_ctypes) == data
    print(f"backends: {sorted(set(names) | {vault.AEAD.name})} read each other's files")


def check_files():
    key = vault.new_key()
    with tempfile.TemporaryDirectory() as folder:
        src, sealed, back = Path(folder) / "clip.mp4", Path(folder) / "clip.mp4.jv", Path(folder) / "back.mp4"
        src.write_bytes(os.urandom(5 * vault.CHUNK + 3))
        t0 = time.perf_counter()
        vault.seal_file(key, src, sealed)
        vault.unseal_file(key, sealed, back)
        ms = 1000 * (time.perf_counter() - t0)
        assert back.read_bytes() == src.read_bytes() and vault.is_sealed(sealed) and not vault.is_sealed(src)
        broken = bytearray(sealed.read_bytes())
        broken[-100] ^= 1
        sealed.write_bytes(bytes(broken))
        assert refused(vault.unseal_file, key, sealed, Path(folder) / "never.mp4")
        assert not (Path(folder) / "never.mp4").exists() and not (Path(folder) / "never.mp4.part").exists()
        rate = 2 * 5 / (ms / 1000)
    print(f"files: 5 MiB sealed and opened in {ms:.0f} ms ({rate:.0f} MiB/s both ways); a changed file leaves nothing behind")


def check_inbox():
    if not hasattr(os, "mkfifo"):
        print("inbox: skipped (no FIFOs on this system; run on Linux)")
        return
    key = vault.new_key()
    with tempfile.TemporaryDirectory() as folder:
        inbox = vault.KeyInbox(Path(folder) / "box" / "keys.fifo")
        assert inbox.path.is_fifo() and oct(inbox.path.stat().st_mode & 0o777) == "0o600"
        assert inbox.get(vault.key_id(key), wait=0.2) is None

        def send():
            with open(inbox.path, "wb") as fifo:
                fifo.write(key.hex().encode() + b"\n")

        threading.Thread(target=send).start()
        assert inbox.get(vault.key_id(key), wait=5) == key
        inbox.close()
        assert not inbox.path.exists()
    print("inbox: a key written into the FIFO reaches the job's memory by its id; the FIFO is 0600 and removed after")


def main():
    check_round_trip()
    check_tampering()
    check_backends()
    check_files()
    check_inbox()
    print("vault: all checks passed")


if __name__ == "__main__":
    main()
