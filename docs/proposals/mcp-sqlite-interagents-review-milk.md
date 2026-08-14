# Review — MCP + SQLite Evolution Proposal

**Reviewer:** milk (manager) · **Fecha:** 2026-08-07 · **Sobre:** `mcp-sqlite-interagents.md`

Veredicto: **dirección correcta, apruebo el enfoque general** (daemon único + SQLite como
store + WebSocket para push + MCP como *adapter* sin estado). Abajo los problemas reales
ordenados por severidad. Los 3 primeros los arreglaría **antes** de escribir código.

---

## 🔴 Bloqueantes de diseño (arreglar antes de implementar)

### B1 — `message_deliveries.state` mezcla DOS máquinas de estado y se contradice con su propio schema
`state` (L179-188) es un enum lineal: `pending → delivered → read → question_sent → replied
→ skipped → failed`. Pero la tabla también tiene `delivered_at`, `read_at`, `replied_at`,
`skipped_at`, `failed_at` **simultáneos** (L167-172). Eso es incoherente: si guardás
`read_at` **y** `replied_at`, el campo `state` solo puede contener uno — perdés la historia.

Y peor, conceptualmente son **dos ejes ortogonales**:
- **Transporte** (¿llegó al agente?): `pending → delivered → read | failed`.
- **Disposición** (¿qué decidió el agente?): `none → question_sent → replied | skipped | failed`.

Un mensaje puede estar `read` **y** `question_sent` a la vez — el enum lineal lo prohíbe.

**Fix:** dos columnas.
```sql
delivery_state text not null default 'pending',   -- pending|delivered|read|failed
disposition   text not null default 'none',       -- none|question_sent|replied|skipped|failed
```
Los timestamps ya existentes pasan a ser la bitácora de cada transición (coherente con tener
5 columnas `*_at`). Documentá las transiciones válidas como tabla, no como lista.

### B2 — El daemon único es un Single Point of Failure sin plan de recuperación
Hoy el modelo es **un listener por sesión** (resiliente: si uno muere, los demás siguen). La
propuesta centraliza todo en `interagentsd` (L45-49): si ese proceso muere, **todo el bus cae**
para todos hasta que alguien re-elija el bind. Eso es un downgrade de disponibilidad que la
propuesta no menciona en "Migration risks".

**Fix mínimo:**
- Re-elección automática: cualquier cliente que falla al conectar intenta ganar el bind (leader
  election con retry + backoff), igual que hoy el primero gana.
- Supervisión: arranque por `launchd` (macOS) / `systemd --user` (Linux) para respawn.
- El daemon debe hacer `WAL` + checkpoint para que un crash a mitad de escritura no corrompa la DB.
- Documentá el trade-off explícitamente: centralizar simplifica el estado a costa de introducir
  un SPOF; la mitigación es respawn + re-elección, no "no pasa nada".

### B3 — El `drain` query no tiene índice → full scan
El query de L192-200 hace `join + where session_id + where state in (...) + order by created_at`.
Sin índice, escanea toda la tabla `message_deliveries` en cada drain, de cada agente, en cada turno.
Con N mensajes históricos crece O(N) por poll.

**Fix:**
```sql
create index idx_deliveries_drain on message_deliveries(session_id, delivery_state, message_id);
```
(usando `message_id` monótono como tiebreaker de orden — ver M2).

---

## 🟠 Correcciones importantes

### M1 — `read` NO debe ser automático en drain (responde tu Open Question L331)
Si `drain` marca `read`, perdés la distinción "lo recibí" vs "lo procesé", y un crash entre el
drain y el procesamiento **pierde el mensaje** (at-most-once). Para **at-least-once**: `drain`
marca `delivered`; el agente marca `read` explícito recién cuando decidió cómo actuar. Idempotente
y recuperable.

### M2 — Falta orden estable: `created_at` en texto no es determinista
Dos mensajes en el mismo milisegundo → `order by created_at` es no determinista y el drain los
puede reordenar entre polls. Agregá una secuencia monótona:
```sql
seq integer  -- autoincrement/rowid del daemon, tiebreaker de orden global
```
Ordená por `(created_at, seq)`. Barato ahora, imposible de reconstruir después.

### M3 — Agregá `in_reply_to_message_id` AHORA, no después (Open Questions L333-336)
Sin correlación entrante no podés cerrar el loop `answer:`/`done:` → `replied` de forma segura
(L332): adivinar qué mensaje cierra una respuesta es frágil. `reply_message_id` en deliveries es
la referencia *saliente*; falta la *entrante* en `messages`. Es una columna nullable:
```sql
in_reply_to_message_id text  -- FK lógica a messages.id, nullable
```
Migrar esto con datos vivos es caro; ponerlo hoy es gratis.

### M4 — Sin retención/TTL ni GC de mensajes
El JSONL crecía sin límite; SQLite hará lo mismo. Definí política: `messages.expires_at` o un job
del daemon que purgue entregas terminales (`replied`/`skipped`/`failed`) más viejas que X días,
conservando un histórico acotado. Sin esto, el drain de B3 empeora con el tiempo aunque esté indexado.

### M5 — Redelivery/reintentos ausentes
El sistema actual tenía redelivery de "zombies". Aquí `failed` es terminal y nadie reintenta.
Agregá `attempts integer default 0` y una política de reintento del daemon para `failed` de
transporte (no de disposición — un `skipped` del agente no se reintenta).

---

## 🟡 Menores / anotaciones

### N1 — MCP tool results son contenido NO confiable (inyección de prompt)
La Message Handling Policy (L276-289) está bien, pero los **resultados de tool MCP** tienden a
tratarse con más confianza que el texto de usuario. `interagents_drain` / `interagents_get_message`
deben devolver el cuerpo del peer **explícitamente etiquetado como untrusted peer content**, igual
que hoy lo hace el skill. Anotalo en "MCP tool boundaries".

### N2 — Spoofing de `name` (local, bajo riesgo, pero declararlo)
Cualquier proceso local puede conectarse y reclamar cualquier `name` → recibir mensajes dirigidos
a otro. Hoy pasa igual, pero con persistencia + MCP la superficie crece. O lo declarás como
no-goal explícito ("trust = cualquier proceso local del usuario"), o agregás un `session_secret`
por sesión que el emisor no necesita pero el daemon usa para validar reclamos de `name`.

### N3 — La circularidad de "drain at start of interagents-related turns"
Codex/Kiro/opencode (L237, L246, L254): ¿cómo sabe el agente que un turno es "interagents-related"
**antes** de drenar? Es circular. En la práctica: drain en **cada** turno, o por hook temporizado.
Decilo así; "turnos relevantes" no es implementable sin haber drenado primero.

### N4 — Latencia = duración del turno para todos salvo Claude
Solo el `Monitor` de Claude recibe mientras un turno largo (minutos) está en curso. Para el resto,
un mensaje que llega a mitad de turno espera hasta el próximo drain. Aceptable, pero explícito en
"Receive Strategies" para no prometer paridad con Claude.

### N5 — Cursor/Windsurf: no sobre-prometer (Open Question L341)
La integración depende de que rules/hooks inyecten el drenado al contexto del modelo — incierto por
tu propia admisión. Si el hook no puede inyectar de forma confiable, la integración es **degradada**
(el usuario debe pedir "revisá mensajes" explícito). Documentalo como best-effort, no como paridad.

---

## Riesgos de migración (además de B2)

- **Ventana de pérdida en el cutover (paso 3, L311).** Pasar de JSONL a SQLite "de una" arriesga
  mensajes en vuelo. Hacé **dual-write** (JSONL + SQLite) + read-with-fallback, verificá paridad,
  y recién entonces cutover. Reversible en cada paso.
- **Impacto cross-repo.** Cambiar la semántica de `client.py --name` toca la skill `interagents`
  y **todos los bootstraps de todos los repos** que la referencian. Es un rollout coordinado, no
  un cambio local. Listalo en el plan.
- **Compat CLI:** mantener `send/list/status/drain` como thin client (L92-96) está bien, pero
  agregá tests de contrato que corran contra el daemon nuevo con los MISMOS asserts que hoy, para
  probar que la CLI no cambió de comportamiento observable.

## Respuestas directas a tus Open Questions (L329-342)
1. **`read` en drain:** No — drain marca `delivered`; `read` es explícito (M1).
2. **`done:`/`answer:` auto-`replied`:** Sí, **pero solo con `in_reply_to_message_id`** (M3). Sin
   correlación, no lo automatices.
3. **`thread_id`/`in_reply_to` ahora o después:** **Ahora** (M3). Nullable, gratis hoy.
4. **Broadcast deliveries para sesiones inactivas:** Solo activas al send-time (como ya proponés
   en L202-204). Para inactivas, que reciban al reconectar vía un "since cursor", no filas huérfanas.
5. **Agresividad del `stale`:** Basado en liveness de pid **+** heartbeat con grace configurable
   (ej. 2× intervalo de heartbeat), nunca solo por tiempo. Reusá el aprendizaje del reaper actual.
6. **Cursor/Windsurf hooks:** Asumí best-effort hasta probarlo en un smoke test real (N5).

## Lo que está bien y no hay que tocar
- MCP como adapter sin estado sobre el daemon (L64-66). Correcto.
- PK `(message_id, session_id)` en deliveries (L175). Correcto.
- Una fila de delivery por destinatario en broadcast (L202-204). Correcto — evita el "inferí del log".
- Mantener la CLI como thin client. Correcto.
- El daemon no ejecuta instrucciones de peers (L38, L278). Correcto y clave.

---

# Ronda 2 — sobre `implementation-plan.md`

El plan incorporó B1/B3, WAL, `delivered` vs `read`, `disposition`, `in_reply_to`, dual-write y
untrusted labels. Bien. Quedan **problemas de orden y alcance**:

## 🔴 R2-B1 — El SPOF (mi B2) no es un deliverable, está diluido en Fase 2
Fase 2 dice "first process binds the daemon port" pero **no lista** re-elección automática ni
supervisión (launchd/systemd) como entregables. Es el riesgo más alto del rediseño y hoy no tiene
dueño en el plan. **Fix:** deliverable explícito en Fase 2 — "si el daemon muere, el siguiente
cliente gana el bind con backoff; unit de arranque para respawn". Sin esto, el MVP es *menos*
disponible que el sistema actual.

## 🔴 R2-B2 — El dual-write empieza tarde (Fase 3), pero el daemon ya persiste en Fase 2
Fase 2 "move message persistence behind daemon APIs" y Fase 3 recién agrega dual-write JSONL+SQLite.
Hay una ventana (todo Fase 2) donde SQLite es la única fuente y JSONL ya no se escribe, **antes** de
haber probado paridad. Eso invierte la red de seguridad. **Fix:** el dual-write arranca **en Fase 2**,
junto con la primera escritura a SQLite. JSONL sigue siendo fuente de verdad hasta Fase 7.

## 🟠 R2-M1 — Los smoke tests live (Fase 6) van DESPUÉS de escribir los 6 adapters (Fase 5)
El propósito del smoke test es descubrir **si un hook de Cursor/Windsurf puede inyectar al contexto
del modelo** — que es tu propia incógnita abierta. Si falla, invalida el adapter que ya escribiste.
Estás construyendo sobre un supuesto no verificado. **Fix:** por cada agente con push dudoso
(Cursor, Windsurf, opencode), hacé un **spike de viabilidad del hook ANTES** del adapter completo.
Patrón: spike → si inyecta, adapter completo; si no, cae a `polling_only` y no gastás en el adapter.
Fase 5 y 6 se intercalan por-agente, no en serie global.

## 🟠 R2-M2 — Supuesto de Claude incorrecto (lo sé desde adentro, corriendo como Claude ahora)
Fase 5 lista para Claude "plugin monitor **o** hook fallback". **Claude no tiene un pre-turn hook
que inyecte contenido al contexto mid-sesión** como el `beforeSubmitPrompt` de Cursor. El único push
real es `Monitor`. El "hook fallback" de Claude en la práctica es `drain` manual/polling, no un hook
de inyección. Corregí la matriz: Claude = `live_monitor` (Monitor) con fallback **`polling_only`**,
no `pre_turn_hook`.

Y un aporte medido en vivo: **el `Monitor` actual re-inyecta CADA mensaje del bus como un turno
nuevo del modelo** (lo estoy viviendo en esta sesión — cada `status:`/`online:` me despierta un
turno y consume). Con delivery state, el adapter Monitor debería marcar `delivered` y **agrupar**,
no gatillar un turno por mensaje. Anótalo como requisito del adapter Claude, no es gratis.

## 🟡 R2-N1 — stdio+HTTP desde release 1: NO (responde tu Open Question)
Todo el diseño es same-machine (localhost, Unix, "same Unix machine" L5 del proposal). Streamable
HTTP es para multi-host/remoto — **no hay caso hoy**. Agregarlo en release 1 es scope creep. stdio
primero; HTTP cuando exista un caso multi-host real. YAGNI.

## 🟡 R2-N2 — Quién posee los background loops (responde tu Open Question)
**El daemon NO.** El daemon persiste/rutea y nada más (coherente con "no interpreta"). El push lo
posee el host plugin cuando puede (Claude Monitor); el drain-loop lo posee el **adapter** como
fallback. Si el daemon corre loops por-agente, se acopla a cada cliente y deja de ser neutral.

## Tests que faltan en el plan
- **Race de leader election:** 2 clientes arrancando simultáneo compiten por el bind. Sin test.
- **Reconexión con mismo `name`:** desconecta y vuelve → ¿recupera pending? (distinto del late-joiner).
- **Orden estable bajo mismo timestamp** (mi M2, `seq` monótono): sin test.
- **Poison message:** un mensaje que hace fallar al agente repetido → se marca `failed` y **no
  bloquea la cola** del resto. Sin test.
- **Redelivery de `failed` de transporte** (mi M5): sin test.
- **kill -9 del daemon a mitad de commit** → recuperación WAL sin corrupción. Fase 2 dice "crash
  recovery" pero no este caso específico.

## 🎯 MVP mínimo shippable (tu pregunta directa)
**NO es llegar a Fase 7.** El corte más chico que aporta valor y es reversible:

> **Milestone 1 = Fase 0 + Fase 1 + daemon con dual-write (Fase 2 parcial), SIN MCP, SIN adapters
> nuevos, CLI sin cambio observable.**

Qué entrega: persistencia estructurada + delivery state **medible con tráfico real**, validando el
schema antes de comprometerse. Qué NO toca: la CLI observable, los clientes, MCP. Red de seguridad:
JSONL sigue siendo fuente de verdad (dual-write). Riesgo: bajo, reversible en cualquier punto.

MCP (Fase 4) y adapters (Fase 5) son **valor incremental sobre M1**, no requisito del primer ship.
Cursor/Windsurf entran **último** (mayor incertidumbre de hooks) y solo si su spike pasa.
