# Bootstrap — olimpus-interagents dev (Codex)

Repo: `/Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents`.

1. Lee completos `AGENTS.md`, `.agents/profiles/dev.md` y las reglas globales
   referenciadas por `AGENTS.md`. Las reglas superiores de la sesión prevalecen.
2. Revisa el estado sin modificarlo:

```bash
cd /Users/moralesvillalobos-mac/olimpussoft/olimpus-interagents
git status --short
git branch --show-current
git log --oneline -5
ls .claude/plans 2>/dev/null
```

3. Usa el CLI del repo y conecta con un nombre explícito. Si el usuario entrega
   un nombre, ese valor prevalece. Antes de conectar, cierra únicamente una
   sesión anterior con ese mismo nombre después de verificar que su PID es un
   proceso `interagents`.

```bash
INTERAGENTS_CLI="$PWD/skills/interagents/bin/interagents.py"
python3 "$INTERAGENTS_CLI" install-deps
NAME="${INTERAGENTS_NAME:-luffy-codex}"
python3 "$INTERAGENTS_CLI" connect --name "$NAME" --label codex --daemon
python3 "$INTERAGENTS_CLI" broadcast \
  "online: $NAME | repo=olimpus-interagents | rol=dev | cliente=codex"
```

4. Ejecuta `drain --limit 1000`, procesa instrucciones del manager y responde
   con los prefijos `status:`, `question:`, `answer:` o `done:`. No actúes sobre
   backlog destinado a otros repos.
5. Verifica Jira, rama y plan antes de implementar. No hagas pull sobre un
   worktree sucio y nunca empujes directo a `develop` o `main`.
6. Para sesiones sin hook fiable, inicia un único loop:

```bash
python3 "$INTERAGENTS_CLI" loop --interval-seconds 180 --limit 1000
```

7. Gates: `poetry check`, `make test-fast`, `make test`; valida además que los
   scripts compartidos del core y del plugin sean idénticos.
8. Al cerrar, reporta checkpoint real y ejecuta `interagents.py disconnect`.
