# RAILY SmartScan updates

Current stable release: **RAILY v72.0** for Windows 10/11.

- [Download RAILY v72.0](releases/RAILY_SmartScan_v72_0.py)
- [Official stable update manifest](https://raw.githubusercontent.com/heydunpehrson-byte/raily-smartscan-updates/main/update_manifest.json)

RAILY v72.0 uses this official GitHub manifest by default on new installations.
On startup, existing installations with a blank manifest URL receive the official
URL through the existing configuration save path after the release-backup step.
Custom manifest URLs and existing update mode, channel and idle preferences are
preserved. In RAILY Setup, use **Check for Updates Now** to check the channel.
For v71.2, enter the official manifest URL in RAILY Setup to receive v72.0 through
the built-in updater.

The existing safe updater validates the downloaded file's SHA-256 and Python
syntax, creates a pre-update archive of the application and state files, and
stages the replacement. On restart, its Windows helper checks the startup health
marker and restores the previous application if the marker is missing. Existing
backup and rollback behavior is unchanged.

This release preserves learned document types and dates, Railroad → Location
filing, person-name filename metadata, duplicate detection and review,
multi-document splitting, full-page visual learning, RAILY Conductor Review,
Windows startup/tray integration, configuration files and user data. The v71.2
release remains available unchanged.
