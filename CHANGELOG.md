# Changelog

## Unreleased

### Added
- **`ChangeTracker`** reports what's new between snapshots: grades, behaviour notes, announcements, messages, agenda entries, absences and timetable changes. The first update seeds silently, and the seen ids serialize to JSON so "new" survives restarts.
- **Kindergarten accounts in `Librus`.** After a `Timetables` 403, `timetable()` finds the child once and uses the kindergarten timetable API. The lookups include kindergarten activities, rooms and the group.
- **Command line**: `librus-synergia` (or `python -m librus_synergia`) prints an account summary, `--json` dumps everything, and `--watch` checks periodically and prints what's new.

### Changed
- `subjects()`, `teachers()` and `classrooms()` return `dict[int | str, str]`, because kindergarten ids are strings.

## 0.1.0

First release, extracted from the [ha-librus-synergia](https://github.com/MichalZaniewicz/ha-librus-synergia) Home Assistant integration (v0.7.8).

- `Librus`: a high-level typed client with lazy login, one automatic re-login after an early session expiry, and a separate Wiadomości session that bootstraps itself and retries once.
- `LibrusApiClient`: the low-level client, one method per endpoint, returning raw JSON.
- `librus_synergia.parsers`: pure JSON → dataclass parsers.
- `docs/`: unofficial notes on the Librus API.
- Session export includes `DeviceCookie`, which Librus sets under `Path=/OAuth`, and import restores its path.
- A mailbox the account doesn't have (HTTP 404) comes back as an empty list, without a pointless re-login.
