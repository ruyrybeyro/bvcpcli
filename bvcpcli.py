#!/usr/bin/env python3
"""
DProtocol v2 client for the BVCP VM backend.

Connects to the backend over TCP, authenticates with an AES-128-CBC encrypted
session using the provided API key, and sends commands (e.g. "vm list",
"version"). Commands can be passed directly on the command line or read from
a file with one command per line.

Configuration priority (highest to lowest):
  1. Command-line arguments (-h, -k, -p, -t)
  2. Environment variables (BVCP_HOST, BVCP_KEY, BVCP_PORT, BVCP_TIMEOUT)
  3. Config file (~/.bvcpcli.conf)
  4. Defaults hardcoded in the script

Requires: pip install pycryptodome
"""

import argparse
import configparser
import gzip
import hashlib
import json
import os
import random
import socket
import sys
import time
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# ANSI colour codes — only used when writing to a real terminal
_GREEN  = "\033[32m"
_RED    = "\033[31m"
_RESET  = "\033[0m"

def _colour(text, code, outfile=None):
    """Wrap text in an ANSI colour code if the output is a terminal."""
    out = outfile or sys.stdout
    if out.isatty():
        return f"{code}{text}{_RESET}"
    return text

# Default connection settings — override with arguments, env vars, or config file
API_KEY_HEX = ""
API_HOST    = ""
API_PORT    = 8628
TIMEOUT     = 5

CONFIG_FILE = os.path.expanduser("~/.bvcpcli.conf")


# ---------------------------------------------------------------------------
# serialize / unserialize
# ---------------------------------------------------------------------------

def dp_serialize(obj):
    """Serialize a Python object to the DProtocol wire format (bytes).
    Supports arrays, strings, ints, floats, bools, and None.
    bool must be checked before int since bool is a subclass of int in Python.
    String lengths are byte counts (not character counts) to handle multibyte UTF-8."""
    if isinstance(obj, list):
        body = b"".join(dp_serialize(i) + dp_serialize(v) for i, v in enumerate(obj))
        return f"a:{len(obj)}:{{".encode() + body + b"}"
    if isinstance(obj, dict):
        body = b"".join(dp_serialize(k) + dp_serialize(v) for k, v in obj.items())
        return f"a:{len(obj)}:{{".encode() + body + b"}"
    if isinstance(obj, bool):
        return f"b:{1 if obj else 0};".encode()
    if isinstance(obj, int):
        return f"i:{obj};".encode()
    if isinstance(obj, float):
        return f"d:{obj};".encode()
    if obj is None:
        return b"N;"
    # String: declare byte length, then the raw UTF-8 bytes
    encoded = obj.encode("utf-8")
    return f's:{len(encoded)}:"'.encode() + encoded + b'";'


def dp_unserialize(raw):
    """Deserialize DProtocol wire format back to Python objects.
    Operates on raw bytes throughout so string byte-length offsets are correct
    for multibyte UTF-8 characters."""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")

    def _parse(s, pos):
        if pos >= len(s):
            return None, pos
        t = chr(s[pos])
        if t == "N":                                        # null
            return None, pos + 2
        if t == "b":                                        # bool: b:0; or b:1;
            return chr(s[pos + 2]) == "1", pos + 4
        if t == "i":                                        # int: i:123;
            end = s.index(ord(";"), pos)
            return int(s[pos + 2:end]), end + 1
        if t == "d":                                        # float: d:1.5;
            end = s.index(ord(";"), pos)
            return float(s[pos + 2:end]), end + 1
        if t == "s":                                        # string: s:N:"...";
            c1 = s.index(ord(":"), pos + 2)                # colon after "s:"
            length = int(s[pos + 2:c1])                    # byte length
            start = c1 + 2                                  # skip :"
            val = s[start:start + length].decode("utf-8", errors="replace")
            return val, start + length + 2                  # skip ";
        if t == "a":                                        # array: a:N:{k v k v ...}
            c1 = s.index(ord(":"), pos + 2)                # colon after "a:"
            count = int(s[pos + 2:c1])
            pos = c1 + 2                                    # skip :{
            result = {}
            for _ in range(count):
                k, pos = _parse(s, pos)
                v, pos = _parse(s, pos)
                result[k] = v
            return result, pos + 1                          # skip }
        return None, pos + 1

    result, _ = _parse(raw, 0)
    return result


# ---------------------------------------------------------------------------
# DProtocol class
# ---------------------------------------------------------------------------

class DProtocol:
    PROTO = "2"      # Protocol version: 2 = serialize, 1 = JSON

    def __init__(self, host, port, timeout=TIMEOUT):
        self.host    = host
        self.port    = port
        self.timeout = timeout
        self.key     = None
        self._aes_key_cache = None   # MD5 of the raw key, cached on enable_aes()
        self._sock   = None

    def enable_aes(self, key: bytes):
        """Set the encryption key. AES key is MD5 of the raw binary key."""
        self.key = key
        self._aes_key_cache = hashlib.md5(key).digest()

    # ---- AES helpers -------------------------------------------------------

    def _encrypt(self, data: bytes, iv: bytes) -> bytes:
        """AES-128-CBC encrypt with PKCS7 padding."""
        c = AES.new(self._aes_key_cache, AES.MODE_CBC, iv)
        return c.encrypt(pad(data, AES.block_size))

    def _decrypt(self, data: bytes, iv: bytes) -> bytes:
        """AES-128-CBC decrypt and strip PKCS7 padding."""
        c = AES.new(self._aes_key_cache, AES.MODE_CBC, iv)
        return unpad(c.decrypt(data), AES.block_size)

    # ---- Socket helpers ----------------------------------------------------

    def _connect(self):
        """Open a TCP connection to the server."""
        self._sock = socket.create_connection((self.host, self.port), self.timeout)
        self._sock.settimeout(self.timeout)

    def _disconnect(self):
        """Close the TCP connection."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _send(self, data):
        """Send all bytes, looping until the full buffer is flushed."""
        if isinstance(data, str):
            data = data.encode()
        total = 0
        while total < len(data):
            n = self._sock.send(data[total:])
            if n == 0:
                raise ConnectionError("socket closed during send")
            total += n

    def _read_exactly(self, n: int) -> bytes:
        """Read exactly n bytes, blocking until all arrive."""
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("socket closed during recv")
            buf += chunk
        return buf

    def _read_line(self, max_bytes=4096) -> str:
        """Read one newline-terminated header line from the socket."""
        buf = b""
        while True:
            c = self._sock.recv(1)
            if not c or c == b"\n":
                break
            buf += c
            if len(buf) >= max_bytes:
                raise ConnectionError("server sent header line exceeding maximum length")
        return buf.decode("ascii")

    # ---- Main query --------------------------------------------------------

    def query(self, code: str, arr, retried=False) -> dict:
        """Send a request and return the parsed response.
        If the connection is broken, reconnects once and retries automatically.

        Wire format (send):
          D{CODE}.{PROTO}.{payload_len}.0.{crypt}\n
          [16-byte IV if encrypted]
          [gzip( serialize(arr) ), optionally AES encrypted]

        Wire format (receive):
          D{CODE}.{PROTO}.{meta_len}.{data_len}.{crypt}\n
          [16-byte IV if encrypted]
          [meta bytes]
          [data bytes, if data_len > 0]
        """
        if self._sock is None:
            self._connect()

        # Generate a random IV for this request, seeded from code + random + time
        iv = None
        if self.key:
            seed = f"{code}{random.randint(0, 9999999)}{int(time.time())}"
            iv = hashlib.md5(seed.encode()).digest()

        # Serialize, compress, optionally encrypt the argument array
        serialized = dp_serialize(arr)
        compressed = gzip.compress(serialized, compresslevel=1)
        payload = self._encrypt(compressed, iv) if self.key else compressed

        crypt_flag = "1" if self.key else "0"
        header = f"D{code}.{self.PROTO}.{len(payload)}.0.{crypt_flag}\n"

        try:
            self._send(header)
            if self.key:
                self._send(iv)    # IV must precede the encrypted payload
            self._send(payload)

            # Read and parse the response header
            rheader = self._read_line().strip()
            hbody = rheader[1:]   # strip leading 'D'
        except (ConnectionError, socket.error) as e:
            # Connection dropped — reconnect and retry once
            if retried:
                raise
            self._disconnect()
            return self.query(code, arr, retried=True)

        if hbody.startswith(":"):
            return {"code": "int_error", "error": hbody[1:]}

        parts = hbody.split(".")
        resp = {
            "code":             parts[0],
            "protocol_version": parts[1],
            "meta_len":         int(parts[2]),
            "data_len":         int(parts[3]),
            "crypt":            parts[4],
        }

        # Read IV first if response is encrypted
        riv = None
        if resp["crypt"] == "1":
            riv = self._read_exactly(16)

        meta_raw = self._read_exactly(resp["meta_len"])
        data_raw = self._read_exactly(resp["data_len"]) if resp["data_len"] > 0 else b""

        if resp["crypt"] == "1":
            meta_raw = self._decrypt(meta_raw, riv)
            if resp["data_len"] > 0:
                data_raw = self._decrypt(data_raw, riv)

        # Decompress and deserialize meta according to protocol version
        if resp["protocol_version"] == "2":
            resp["meta"] = dp_unserialize(gzip.decompress(meta_raw))
        else:
            resp["meta"] = json.loads(gzip.decompress(meta_raw))

        if resp["data_len"] > 0:
            resp["data"] = gzip.decompress(data_raw)

        return resp


# ---------------------------------------------------------------------------
# process()
# ---------------------------------------------------------------------------

def process(res, command=None, verbose=False, outfile=None, as_json=False,
            quiet=False, progress=None, elapsed=None):
    """Print the result of a query.
    progress: optional (current, total) tuple for batch counter display.
    elapsed:  optional float seconds the command took.
    Colour output is automatic when writing to a terminal."""
    out = outfile or sys.stdout

    if as_json:
        # JSON mode: always include command name, even for single-command runs
        obj = {"command": command, "code": res["code"], "meta": res.get("meta")}
        if elapsed is not None:
            obj["elapsed"] = round(elapsed, 3)
        print(json.dumps(obj, indent=2), file=out)
        return

    if progress:
        # Batch mode: print separator header with counter, command name, elapsed time
        counter = f"[{progress[0]}/{progress[1]}] "
        timing  = f" ({elapsed:.2f}s)" if elapsed is not None else ""
        print(f"\n--- {counter}{command}{timing} ---", file=out)

    if verbose:
        print(f"[verbose] raw response: {res}", file=sys.stderr)

    if res["code"] == "OK":
        meta = res.get("meta")
        if isinstance(meta, (dict, list)) and len(meta) > 0:
            if not quiet:
                print(_colour("Command was successful, results:", _GREEN, out), file=out)
            _print_meta(meta, outfile=out)
        else:
            if not quiet:
                print(_colour("Command was successful", _GREEN, out), file=out)
    else:
        print(_colour(f"There was an error: {res['code']}", _RED, out), file=out)
        meta = res.get("meta") or {}
        if isinstance(meta, dict):
            if "cli_error" in meta:
                errors = meta["cli_error"]
                items = errors.values() if isinstance(errors, dict) else errors
                for v in items:
                    print(f"      {v}", file=out)
            elif "error" in meta:
                print(f"Error: {meta['error']}", file=out)


def _print_meta(meta, indent=0, outfile=None):
    """Recursively pretty-print a nested dict/list."""
    out = outfile or sys.stdout
    prefix = "  " * indent
    if isinstance(meta, dict):
        for k, v in meta.items():
            if isinstance(v, (dict, list)):
                print(f"{prefix}[{k}] =>", file=out)
                _print_meta(v, indent + 1, outfile=out)
            else:
                print(f"{prefix}[{k}] => {v}", file=out)
    elif isinstance(meta, list):
        for i, v in enumerate(meta):
            if isinstance(v, (dict, list)):
                print(f"{prefix}[{i}] =>", file=out)
                _print_meta(v, indent + 1, outfile=out)
            else:
                print(f"{prefix}[{i}] => {v}", file=out)
    else:
        print(f"{prefix}{meta}", file=out)


# ---------------------------------------------------------------------------
# config file loader
# ---------------------------------------------------------------------------

def load_config():
    """Load settings from ~/.bvcpcli.conf if it exists.
    Returns a dict with any of: host, key, port, timeout.

    Example config file:
      [bvcp]
      host = 192.168.1.4
      key  = AABBCC...
      port = 8628
      timeout = 5
    """
    cfg = {}
    parser = configparser.ConfigParser()
    parser.read(CONFIG_FILE)
    section = "bvcp"
    if parser.has_section(section):
        if parser.has_option(section, "host"):
            cfg["host"] = parser.get(section, "host")
        if parser.has_option(section, "key"):
            cfg["key"] = parser.get(section, "key")
        if parser.has_option(section, "port"):
            cfg["port"] = parser.getint(section, "port")
        if parser.has_option(section, "timeout"):
            cfg["timeout"] = parser.getint(section, "timeout")
    return cfg


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------

def parse_args():
    """Resolve settings from config file, environment variables, and CLI arguments.
    Returns a namespace with: host, port, key_hex, timeout, verbose, as_json,
    cont, quiet, no_ping, outfile, save_config, commands."""

    # 1. Start with hardcoded defaults
    host    = API_HOST
    port    = API_PORT
    key_hex = API_KEY_HEX
    timeout = TIMEOUT

    # 2. Override with config file values
    cfg = load_config()
    if cfg.get("host"):    host    = cfg["host"]
    if cfg.get("key"):     key_hex = cfg["key"]
    if cfg.get("port"):    port    = cfg["port"]
    if cfg.get("timeout"): timeout = cfg["timeout"]

    # 3. Override with environment variables
    if os.environ.get("BVCP_HOST"):    host    = os.environ["BVCP_HOST"]
    if os.environ.get("BVCP_KEY"):     key_hex = os.environ["BVCP_KEY"]
    if os.environ.get("BVCP_PORT"):    port    = int(os.environ["BVCP_PORT"])
    if os.environ.get("BVCP_TIMEOUT"): timeout = int(os.environ["BVCP_TIMEOUT"])

    # 4. Parse command-line arguments (highest priority)
    # add_help=False so we can use -h for host instead of help
    parser = argparse.ArgumentParser(
        prog="bvcpcli.py",
        description="DProtocol v2 client for the BVCP VM backend.",
        epilog=f"Config file: {CONFIG_FILE}  |  Env vars: BVCP_HOST, BVCP_KEY, BVCP_PORT, BVCP_TIMEOUT",
        add_help=False
    )
    parser.add_argument("-h", "--host",        default=None, help="API host")
    parser.add_argument("-p", "--port",        type=int, default=None, help="API port (default: 8628)")
    parser.add_argument("-k", "--key",         default=None, help="API key in hex")
    parser.add_argument("-t", "--timeout",     type=int, default=None, help="Timeout in seconds (default: 5)")
    parser.add_argument("-o", "--output",      default=None, metavar="FILE", help="Write output to file")
    parser.add_argument("-f", "--file",        default=None, metavar="FILE", help="Read commands from file (use - for stdin)")
    parser.add_argument("-v", "--verbose",     action="store_true", help="Print raw server response")
    parser.add_argument("-j", "--json",        action="store_true", help="JSON output mode")
    parser.add_argument("-c", "--continue",    action="store_true", dest="cont", help="Continue on error, print summary at end")
    parser.add_argument("-q", "--quiet",       action="store_true", help="Suppress success messages")
    parser.add_argument("-n", "--no-ping",     action="store_true", dest="no_ping", help="Skip PING connectivity check")
    parser.add_argument("--save-config",       action="store_true", help=f"Save current settings to {CONFIG_FILE}")
    parser.add_argument("--help",              action="help", help="Show this help message and exit")
    parser.add_argument("command",             nargs="*", help="Command to send (e.g. vm list)")

    a = parser.parse_args()

    # Command-line values override config/env where provided
    if a.host:    host    = a.host
    if a.port:    port    = a.port
    if a.key:     key_hex = a.key
    if a.timeout: timeout = a.timeout

    # Build the list of commands to run
    if a.file:
        # Read commands from a file, one per line; lines starting with # are ignored
        try:
            f = sys.stdin if a.file == "-" else open(a.file, encoding="utf-8")
            commands = [line.strip() for line in f if line.strip() and not line.startswith("#")]
            if f is not sys.stdin:
                f.close()
        except FileNotFoundError:
            print(f"Error: file not found: {a.file}", file=sys.stderr)
            sys.exit(1)
        if not commands:
            print("Warning: no commands found in input", file=sys.stderr)
            sys.exit(0)
    elif a.command:
        # Accept either separate args or a single quoted string with spaces
        parts    = a.command[0].split() if len(a.command) == 1 else a.command
        commands = [" ".join(parts)]
    elif not sys.stdin.isatty():
        # No command and no -f but stdin is piped — read commands from stdin
        commands = [line.strip() for line in sys.stdin if line.strip() and not line.startswith("#")]
        if not commands:
            print("Warning: no commands found in input", file=sys.stderr)
            sys.exit(0)
    else:
        parser.print_help(sys.stderr)
        sys.exit(1)

    # Return everything main() needs as a simple namespace
    a.host    = host
    a.port    = port
    a.key_hex = key_hex
    a.timeout = timeout
    a.commands = commands
    return a


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    a = parse_args()

    if not a.host:
        print("Error: API host is not set (use -h, BVCP_HOST, or config file)", file=sys.stderr)
        sys.exit(1)
    if not a.key_hex:
        print("Error: API key is not set (use -k, BVCP_KEY, or config file)", file=sys.stderr)
        sys.exit(1)

    # Validate the API key is valid hex before connecting
    try:
        api_key = bytes.fromhex(a.key_hex)
    except ValueError:
        print("Error: API key is not valid hex", file=sys.stderr)
        sys.exit(1)

    # Save config file if --save-config was specified
    if a.save_config:
        if a.commands:
            print("Warning: --save-config exits immediately; command was not sent", file=sys.stderr)
        cfg_out = configparser.ConfigParser()
        cfg_out["bvcp"] = {"host": a.host, "key": a.key_hex, "port": str(a.port), "timeout": str(a.timeout)}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            cfg_out.write(f)
        print(f"Config saved to {CONFIG_FILE}")
        sys.exit(0)

    # Open output file if -o was specified
    out = None
    if a.output:
        try:
            out = open(a.output, "w", encoding="utf-8")
        except OSError as e:
            print(f"Error: cannot open output file: {e}", file=sys.stderr)
            sys.exit(1)

    dp = DProtocol(a.host, a.port, a.timeout)
    dp.enable_aes(api_key)

    try:
        # Verify connectivity before sending real commands (skipped with -n)
        if not a.no_ping:
            res = dp.query("PING", [""])
            if res.get("code") != "PONG":
                print("Error: Unable to contact VM Backend module", file=sys.stderr)
                sys.exit(10)

        # Execute each command — either the single command from the CLI
        # or all lines from the -f file.
        # With -c, continues on failure and prints a summary at the end.
        # Without -c, stops on first failure.
        total     = len(a.commands)
        succeeded = 0
        failed    = 0
        for i, cmd in enumerate(a.commands, 1):
            parts   = cmd.split()
            argv    = [sys.argv[0]] + parts   # argv[0] is the script name
            prog    = (i, total) if total > 1 else None
            t_start = time.monotonic()
            res     = dp.query("CLIENT", argv)
            elapsed = time.monotonic() - t_start
            process(res, command=cmd,
                    verbose=a.verbose, outfile=out, as_json=a.json,
                    quiet=a.quiet, progress=prog, elapsed=elapsed if total > 1 else None)
            if res["code"] == "OK":
                succeeded += 1
            else:
                failed += 1
                if not a.cont:
                    break   # abort remaining commands on first failure

        # Print summary when running multiple commands
        if total > 1:
            summary = f"\nSummary: {succeeded} succeeded, {failed} failed"
            colour  = _GREEN if failed == 0 else _RED
            print(_colour(summary, colour, out or sys.stdout), file=out or sys.stdout)

    except (socket.timeout, socket.error, ConnectionError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(10)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
    finally:
        dp._disconnect()
        if out:
            out.close()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
