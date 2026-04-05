# bvcpcli

[![Python](https://img.shields.io/badge/python-3.6%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-FreeBSD-red.svg)](https://www.freebsd.org/)
[![Bhyve](https://img.shields.io/badge/hypervisor-bhyve-orange.svg)](https://bhyve.npulse.net/)
[![Protocol](https://img.shields.io/badge/protocol-DProtocol%20v2-orange.svg)](https://bhyve.npulse.net/)
[![Encryption](https://img.shields.io/badge/encryption-AES--128--CBC-red.svg)](../../)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> A command-line client for [BVCP](https://bhyve.npulse.net/) — the Bhyve VM Control Panel for FreeBSD.

Communicates with the BVCP backend over TCP using the DProtocol v2 protocol,
encrypting traffic with **AES-128-CBC** using the provided API key.
Supports sending commands such as `vm list`, `version`, and `vm create`
directly from the shell or via batch files.

[BVCP](https://bhyve.npulse.net/) is a management panel for [bhyve](https://en.wikipedia.org/wiki/Bhyve),
the hypervisor built into FreeBSD.

---

## Quick start

```bash
pip install pycryptodome
chmod +x bvcpcli.py
./bvcpcli.py -h 192.168.1.4 -k AABBCC... version
```

Or save your settings once and skip the flags forever:

```bash
./bvcpcli.py -h 192.168.1.4 -k AABBCC... --save-config
./bvcpcli.py version
```

---

## Requirements

- Python 3.6+
- [pycryptodome](https://pypi.org/project/pycryptodome/) — `pip install pycryptodome`

---

## Usage

```
bvcpcli.py [options] <command> [args...]
bvcpcli.py [options] "vm list"
bvcpcli.py [options] -f <file>
bvcpcli.py [options] -f -          (read from stdin)
echo "vm list" | bvcpcli.py [options]  (stdin auto-detected)
```

---

## Options

| Short | Long | Description |
|-------|------|-------------|
| `-h <host>` | `--host` | API host |
| `-p <port>` | `--port` | API port (default: 8628) |
| `-k <key>` | `--key` | API key in hex (32 hex chars, 16 bytes) |
| `-t <secs>` | `--timeout` | Connection timeout in seconds (default: 5) |
| `-o <file>` | `--output` | Write output to file instead of stdout |
| `-f <file>` | `--file` | Read commands from file, one per line (`-` for stdin) |
| `-v` | `--verbose` | Print raw server response |
| `-j` | `--json` | JSON output mode |
| `-c` | `--continue` | Continue on error in batch mode, print summary at end |
| `-q` | `--quiet` | Suppress success messages, show results only |
| `-n` | `--no-ping` | Skip the PING connectivity check |
| | `--save-config` | Save current settings to `~/.bvcpcli.conf` |
| | `--help` | Show help and exit |

---

## Configuration

Settings are resolved in this priority order (highest wins):

1. Command-line arguments
2. Environment variables
3. Config file (`~/.bvcpcli.conf`)
4. Defaults hardcoded in the script

### Environment variables

| Variable | Description |
|----------|-------------|
| `BVCP_HOST` | API host |
| `BVCP_KEY` | API key in hex |
| `BVCP_PORT` | API port |
| `BVCP_TIMEOUT` | Timeout in seconds |

### Config file

Save your settings once and never pass them on the command line again:

```bash
./bvcpcli.py -h 192.168.1.4 -k AABBCC... --save-config
```

`--save-config` writes the config file and exits immediately — no command is sent to the backend.

> **Note:** the API key must be a hex string of exactly 32 characters (16 bytes for AES-128), e.g. `E5739065D1B843F2AA83B4328DC12C4F`.

This writes `~/.bvcpcli.conf`:

```ini
[bvcp]
host = 192.168.1.4
key = AABBCC...
port = 8628
timeout = 5
```

---

## Examples

### Single command

```bash
./bvcpcli.py -h 192.168.1.4 -k AABBCC... version
./bvcpcli.py -h 192.168.1.4 -k AABBCC... vm list
./bvcpcli.py -h 192.168.1.4 -k AABBCC... "vm create myvm template"
```

### Batch file

`commands.txt` is a plain text file with one command per line. Lines starting with `#` are treated as comments and ignored.

```
# commands.txt
version
vm list
vm create myvm template
```

```bash
./bvcpcli.py -f commands.txt
```

### Stdin / heredoc

```bash
echo "vm list" | ./bvcpcli.py

./bvcpcli.py -f - <<EOF
version
vm list
EOF
```

### Continue on error and show summary

```bash
./bvcpcli.py -c -f commands.txt
Summary: 12 succeeded, 2 failed
```

### JSON output (pipe to jq)

```bash
./bvcpcli.py -j "vm list" | jq .
```

### Save output to file

```bash
./bvcpcli.py -o results.txt -f commands.txt
```

---

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All commands succeeded |
| 1 | One or more commands failed |
| 10 | Connection error |
| 130 | Interrupted (Ctrl+C) |
