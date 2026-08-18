# Runtime stall review — 2026-08-18

## Root cause

The strongest root cause is lifecycle supervision, not the strategy gates. `start_engine()` starts ingest, the Redis subscriber, PaperTrader, health logging, and Scalp health tasks, then sets `engine_running=true`. The guarded ingest, subscriber, and PaperTrader methods catch broad exceptions, log them, and return. No supervisor watched those task objects, so a task could be finished while the in-memory and Redis engine flags still reported running. The system then processed the initial batch and stopped growing.

The Redis candle channel is best-effort Pub/Sub. It has no replay queue, consumer offset, or buffer. If the subscriber connection dies, candles published while it is absent are lost; reconnecting the subscriber alone cannot recover those messages. This makes silent task death especially damaging.

The WebSocket health loop only emitted `ws_stale`/WARNING when a stream stopped producing messages; it did not close the stale socket or force the outer reconnect loop. A connected-but-stale stream could therefore remain stuck indefinitely. Existing health logger code also periodically forced `Ingest` and `PaperTrader` to `OK`, which could mask a dead worker if its task had exited.

## Applied design

- Add `RuntimeSupervisor` in `app/main.py`, polling worker task state every 5 seconds.
- Restart missing or completed ingest, orchestrator-subscriber, and PaperTrader tasks while the engine is running.
- Mark the owning component ERROR during restart, record `RuntimeTaskStopped`, and emit `runtime_task_stopped` and `runtime_task_restarted` events.
- Mark worker components OK only from the supervisor when their tasks are actually alive; stop health logger from masking them.
- On stale WebSocket pairs, close the socket with a controlled reason so BinanceWSClient's existing outer loop performs backoff/reconnect.
- Add an in-memory bounded structured event buffer in `monitoring/logger.py`, newest-first API access, and event counts. It captures the existing structured events without changing trading decisions.
- Add `/api/dashboard/runtime_activity?limit=200` returning recent events, counts, and the same `CycleSummaryResponse` used by the dashboard.
- Add a dedicated dashboard button/panel showing recent movement, worker restarts, and the current formatted Cycle Summary; it refreshes every 10 seconds while open.

## External checks

Supabase `query_logs` and a direct activity query were attempted for the current runtime, but the configured Supabase MCP connection returned permission errors (`-32600 You do not have permission to perform this action`). Source-level evidence is therefore the current definitive diagnosis; the new runtime event panel will provide direct evidence after restart.
