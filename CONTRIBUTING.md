# Contributing

Bug reports should include:

- Distribution and version
- `uname -r`
- `upower --version`
- Output of `sudo /usr/local/libexec/apple-hid-battery-override --status`
- Relevant logs from
  `journalctl -u apple-hid-battery-override.service --no-pager`

Run the local checks before opening a pull request:

```sh
make lint
make test
```
