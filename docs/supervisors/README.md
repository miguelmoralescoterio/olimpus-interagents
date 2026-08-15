# Interagents Supervisor Examples

These examples are optional. The normal interagents flow still starts the local
server lazily from the first listener. Use a supervisor only when you want a
more persistent local bus during development.

The supervised process should run a background listener, not the MCP server.
MCP stdio remains owned by each host application.

## macOS launchd

Template: `local.olimpus.interagents.plist`

Install:

```bash
mkdir -p ~/Library/LaunchAgents
cp docs/supervisors/local.olimpus.interagents.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/local.olimpus.interagents.plist
launchctl start local.olimpus.interagents
```

Uninstall:

```bash
launchctl stop local.olimpus.interagents
launchctl unload ~/Library/LaunchAgents/local.olimpus.interagents.plist
rm ~/Library/LaunchAgents/local.olimpus.interagents.plist
```

## Linux systemd --user

Template: `olimpus-interagents.service`

Install:

```bash
mkdir -p ~/.config/systemd/user
cp docs/supervisors/olimpus-interagents.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now olimpus-interagents.service
```

Uninstall:

```bash
systemctl --user disable --now olimpus-interagents.service
rm ~/.config/systemd/user/olimpus-interagents.service
systemctl --user daemon-reload
```

## Notes

- Review and edit the `--name`, `--label`, and repo path before installing.
- The examples use `connect --daemon`, so the supervised command exits after
  spawning the actual listener. Supervisors may report the unit as completed;
  the listener remains tracked by `interagents status`.
- For a stricter always-running setup, point the supervisor directly at
  `skills/interagents/bin/client.py --name <name> --label <label>`.
