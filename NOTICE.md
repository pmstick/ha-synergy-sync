# Notices

This project is a derivative of
[sniereffo/synergy-battery-analyser](https://github.com/sniereffo/synergy-battery-analyser),
which is licensed under the [Elastic License 2.0](LICENSE). All credit for working out how to
log in to the My Synergy portal and fetch half-hourly interval data goes to that project.

This project is distributed under the same Elastic License 2.0 (see [LICENSE](LICENSE)).

## Files derived from synergy-battery-analyser

| File in this repo | Source file | Changes |
|---|---|---|
| `synergy_sync/synergy_sync/aura.py` | `src/aura.py` | Copied unchanged except for an added notice header |
| `synergy_sync/synergy_sync/chartdata.py` | `src/chartdata.py` | Modified: trimmed to the half-hourly parser only (rate/feed-in code removed) |
| `synergy_sync/synergy_sync/browser_client.py` | `src/browser_client.py` | Modified: persistent browser profile, virtual-display window options; `get_day` and `finalize_login` removed |
| `synergy_sync/tests/fixtures/chartdata_halfhourly_day.json` | `tests/fixtures/chartdata_halfhourly_day.json` | Copied unchanged |

All other files are original to this project.
