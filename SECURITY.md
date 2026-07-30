# Security

## Privilege model

The service runs as root because affected `/dev/hidraw*` nodes are mode `0600`
and file-level bind mounts require `CAP_SYS_ADMIN`. It does not open network
sockets or accept external input.

The systemd unit restricts the capability bounding set to `CAP_SYS_ADMIN` and
allows only Unix-domain sockets. Filesystem namespace isolation is deliberately
not enabled because UPower must see the corrective bind mounts.

## Scope and safeguards

The program only considers Bluetooth HID devices with Apple vendor IDs. It
queries input report `0x90`, validates the percentage range, matches the
device to its own `power_supply` entry through sysfs, and mounts an override
only when the kernel percentage exactly equals the report's status byte.

If the kernel percentage already matches the real percentage, the program
does nothing. Unexpected mismatches and foreign mounts are never overwritten.

## Reporting vulnerabilities

Please open a private security advisory in the GitHub repository rather than
filing a public issue for vulnerabilities.
