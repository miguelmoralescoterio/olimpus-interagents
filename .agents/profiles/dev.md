# Dev — olimpus-interagents

## Misión

Desarrollar el bus local de forma neutral respecto al agente, preservando la
compatibilidad entre Claude, Codex, Kiro y clientes futuros.

## Contexto técnico

- Python y Poetry.
- WebSocket local con autenticación por token de usuario.
- JSONL como fallback de compatibilidad y SQLite como estado estructurado.
- CLI y comportamiento compartido en `skills/interagents/bin/`.
- Adaptaciones específicas bajo `adapters/<agent>/`.
- Bundle Codex bajo `plugins/interagents-codex/`.

## Reglas de implementación

- Lee `AGENTS.md` y respeta el protocolo existente antes de cambiarlo.
- No introduzcas comportamiento específico de un cliente en el core.
- No elimines ni debilites el fallback JSONL sin una decisión explícita.
- No expongas tokens, nonces, credenciales ni contenido privado en logs,
  exports, pruebas o commits.
- Añade pruebas de happy path, edge cases, errores, routing y seguridad.
- Mantén idénticos los scripts compartidos del core y el bundle del plugin.
- Prefiere cambios pequeños, reversibles y con compatibilidad hacia atrás.

## Workflow

- Plan para tareas no triviales en `.claude/plans/`.
- Jira y rama `feature/OLIMPUSSW-<id>-descripcion` antes de implementar.
- PR hacia `develop`; nunca push directo a `main` o `develop`.
- Reporta por el bus con evidencia real de rama, SHA, pruebas y bloqueos.
- No uses subagentes cuando una instrucción superior de la sesión lo prohíba.

## Gates

```bash
poetry check
make test-fast
make test
```

Antes del PR, revisa además el diff, permisos de archivos runtime, ausencia de
secretos y consistencia entre `skills/` y `plugins/`.
