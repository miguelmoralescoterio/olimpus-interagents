# Plan: Interagents Phase 7 Migration

**Fecha:** 2026-08-14 | **Estado:** En progreso | **Prioridad:** Alta

## Objetivo

Cerrar de forma reversible los gaps operativos de la migración JSONL → SQLite:
corregir el contexto `.agents`, agregar exportación explícita y permitir
desactivar SQLite sin alterar el comportamiento por defecto. El fallback JSONL
permanece activo hasta recibir GO humano explícito para el cutover.

## Decisiones y notas

- 2026-08-14 — El manager aprobó trabajar desde `main`, que contiene el WIP
  previo; el PR tendrá `develop` como base.
- 2026-08-14 — Guardrail: no retirar el fallback JSONL ni cambiar defaults sin
  autorización humana explícita.
- 2026-08-14 — El WIP preexistente se preserva; los cambios nuevos deben ser
  pequeños, compatibles y cubiertos por pruebas.
- 2026-08-14 — Gates locales verdes: `poetry check`, 258 pruebas rápidas y
  278 pruebas completas. Smoke de dos sesiones validó directo, broadcast,
  drain y export; SQLite-off conservó send/drain JSONL sin crear base SQLite.

## Tareas

### T001: Corregir contexto de agentes

- **Estado:** Completado | **Prioridad:** Alta
- **Descripción:** Adaptar bootstraps, perfiles y README de `.agents` al repo
  `olimpus-interagents` sin referencias operativas heredadas de `olimpus-auth`.
- **Criterios de aceptación:**
  - [x] Todos los roles/clientes apuntan al repo y comandos correctos.
  - [x] Los bootstraps usan el CLI local y reportan `olimpus-interagents`.
- **Dependencias:** Ninguna
- **Inicio:** 2026-08-14 | **Fin:** 2026-08-14

### T002: Exportar estado persistido

- **Estado:** Completado | **Prioridad:** Alta
- **Descripción:** Agregar un comando CLI de exportación estable y seguro para
  sesiones, mensajes y entregas, sin exponer secretos.
- **Criterios de aceptación:**
  - [x] Salida determinista, filtrable y documentada.
  - [x] Texto de mensajes oculto por defecto; opt-in explícito probado.
- **Dependencias:** Ninguna
- **Inicio:** 2026-08-14 | **Fin:** 2026-08-14

### T003: Toggle reversible de SQLite

- **Estado:** Completado | **Prioridad:** Alta
- **Descripción:** Permitir desactivar la persistencia SQLite explícitamente;
  el valor por defecto conserva dual-write + fallback actuales.
- **Criterios de aceptación:**
  - [x] Default sin cambios observables.
  - [x] SQLite-off conserva JSONL, broadcast y fallback de drain.
  - [x] Configuración documentada y probada.
- **Dependencias:** Ninguna
- **Inicio:** 2026-08-14 | **Fin:** 2026-08-14

### T004: Verificación y entrega

- **Estado:** En progreso | **Prioridad:** Alta
- **Descripción:** Sincronizar core/plugin, ejecutar gates y smoke de dos
  sesiones, y abrir PR draft hacia `develop`.
- **Criterios de aceptación:**
  - [x] `poetry check` y suite completa verdes.
  - [x] Core y copia del plugin sincronizados.
  - [x] Smoke directo/broadcast/drain/export/SQLite-off exitoso.
  - [ ] PR draft con riesgos y rollback documentados.
- **Dependencias:** T001, T002, T003
- **Inicio:** 2026-08-14 | **Fin:** —

## Riesgos

- Pérdida de mensajes durante cutover: mitigada manteniendo JSONL como fallback.
- Divergencia entre core y plugin: mitigada con test de paridad y comparación.
- Exportación de datos sensibles: limitar a campos persistidos, respetar
  permisos locales y nunca incluir token/session secret.

## Observabilidad

No se introduce un endpoint de runtime. La evidencia operativa será: estado de
persistencia, logs de fallback sin secretos, conteos exportados y smoke local.

## Progreso

**General:** 95% | **Total:** 4 | **Completadas:** 3 | **En progreso:** 1 | **Bloqueadas:** 0
