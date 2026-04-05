# bvcpcli

[![Python](https://img.shields.io/badge/python-3.6%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-FreeBSD%2015-red.svg)](https://www.freebsd.org/)
[![Bhyve](https://img.shields.io/badge/hypervisor-bhyve-orange.svg)](https://bhyve.npulse.net/)
[![BVCP](https://img.shields.io/badge/BVCP-2.2.2-blue.svg)](https://bhyve.npulse.net/)
[![Protocol](https://img.shields.io/badge/protocol-DProtocol%20v2-orange.svg)](https://bhyve.npulse.net/)
[![Encryption](https://img.shields.io/badge/encryption-AES--128--CBC-red.svg)](../../)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> A command-line client for [BVCP](https://bhyve.npulse.net/) — the Bhyve VM Control Panel for FreeBSD.

`bvcpcli` was built to make provisioning bhyve VMs easier from the command line,
without needing the BVCP web interface. Communicates with the BVCP backend over TCP
using the DProtocol v2 protocol, encrypting traffic with **AES-128-CBC** using the
provided API key.

Key features:

- Run a single command directly from the shell (`vm list`, `version`, `vm create`, ...)
- **Batch mode** — provision multiple VMs or run bulk operations from a file or stdin
- Continue on error with a pass/fail summary (`-c`)
- JSON output for scripting and automation (`-j`)
- Flexible configuration via CLI flags, environment variables, or config file

[BVCP](https://bhyve.npulse.net/) is a management panel for [bhyve](https://en.wikipedia.org/wiki/Bhyve),
the hypervisor built into FreeBSD.

Tested with BVCP 2.2.2 running on FreeBSD 15.

---

## BVCP server configuration

By default BVCP listens only on localhost. To use `bvcpcli` from a remote machine,
edit `/var/lib/nPulse/BVCP/bvcp.conf` on the FreeBSD host and change `ipv4_listen`
in the `api` section from `127.0.0.1` to the server's IP address:

```
api
{
    ...
    ipv4_listen = 192.168.1.4
    ...
}
```

Then restart BVCP. If running `bvcpcli` on the same host as BVCP, no change is needed.

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

### Virtual environments

It is recommended to run `bvcpcli` inside an isolated Python environment.
On macOS, `venv` is the default and requires no extra installation.

**venv** (standard library):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pycryptodome
```

**uv** (fast drop-in):
```bash
uv venv
source .venv/bin/activate
uv pip install pycryptodome
```

**Conda**:
```bash
conda create -n bvcpcli python=3.11
conda activate bvcpcli
pip install pycryptodome
```

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
