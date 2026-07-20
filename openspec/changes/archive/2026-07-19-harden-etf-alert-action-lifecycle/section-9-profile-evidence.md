## Section 9 bounded replay profile

- Recorded: 2026-07-15 Asia/Shanghai
- External command timeout: 55 seconds; completed in 13.5 seconds wall time
- Command: `backend/.venv/Scripts/python.exe -m app.services.strategy_lab.etf_action_replay.profiling --store C:\Users\19535\AppData\Local\Temp\fundscope-section9-profile-a8493702fa0e46a29cd4dfba2c34c45d.sqlite3`
- Environment: `synthetic_windows_process_no_cpu_affinity`; one worker; CPU affinity was not restricted
- Workload: 1,200 ETF codes across 22 weekday sessions, with a 20-session warm-up and two output/replay sessions. The run wrote 26,400 source rows and 2,400 feature rows to a fresh SQLite store, sealed completeness manifests, persisted Stage A and Stage B checkpoints, and replayed two candidate configurations against the same feature rows.
- Source rows written/read: 26,400 / 26,400
- Feature rows processed/read by replay: 2,400 / 2,400
- Replay output: two ranking rows, 24 events, eight buy fills, four equity rows, eight final positions
- Elapsed seconds measured inside the profiler: 11.399826
- Source-row throughput: 2,315.82 rows/second
- Peak process RSS: 95,928,320 bytes (91.48 MiB)
- Instrumented application SQLite statements: 28
- Instrumented row bound: 28,832
- Final replay checkpoint payload: 6,355 bytes
- Final SQLite database: 32,280,576 bytes (30.79 MiB)
- Ending equity: 200,191.512028; cumulative fees: 199.787972
- Default memory gate: 2.5 GiB
- Gate result: pass

This is a deterministic synthetic representative batch, not a production full-history run and not a hardware-equivalent 2-core CPU clamp. It nevertheless exercises the real bounded SQLite source, feature, manifest, ranking, event, equity, and checkpoint paths with a single replay worker. The measured process peak remained well below the 2.5 GiB gate reserved for the 2-core/4-GB deployment target.
