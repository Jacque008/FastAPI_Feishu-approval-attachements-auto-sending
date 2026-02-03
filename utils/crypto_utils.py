"""Simple encryption utilities for sensitive config values."""
import base64
import os
import hashlib
from typing import Optional

# Use a machine-specific key (or set ENCRYPTION_KEY env var)
def _get_key() -> bytes:
    """Get encryption key from environment or generate from machine ID."""
    env_key = os.environ.get("ENCRYPTION_KEY")
    if env_key:
        return hashlib.sha256(env_key.encode()).digest()

    # Fallback: use a combination of username and hostname
    machine_id = f"{os.getenv('USER', 'default')}@{os.uname().nodename}"
    return hashlib.sha256(machine_id.encode()).digest()


def encrypt(plaintext: str) -> str:
    """Encrypt a string using XOR with the key (simple but effective for local use)."""
    key = _get_key()
    encrypted = bytes(a ^ b for a, b in zip(plaintext.encode(), key * (len(plaintext) // len(key) + 1)))
    return "ENC:" + base64.b64encode(encrypted).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a string."""
    if not ciphertext.startswith("ENC:"):
        return ciphertext  # Not encrypted, return as-is

    key = _get_key()
    encrypted = base64.b64decode(ciphertext[4:])
    decrypted = bytes(a ^ b for a, b in zip(encrypted, key * (len(encrypted) // len(key) + 1)))
    return decrypted.decode()


def encrypt_value_interactive():
    """Interactive tool to encrypt a value."""
    print("Enter the value to encrypt (input hidden):")
    import getpass
    value = getpass.getpass("> ")
    encrypted = encrypt(value)
    print(f"\nEncrypted value (copy to .env):\n{encrypted}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "encrypt":
        encrypt_value_interactive()
    else:
        print("Usage: python crypto_utils.py encrypt")
