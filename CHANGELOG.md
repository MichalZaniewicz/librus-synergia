# Changelog

## 0.1.0

First release, extracted from the [ha-librus-synergia](https://github.com/MichalZaniewicz/ha-librus-synergia) Home Assistant integration (v0.7.8).

- `Librus`: a high-level typed client with lazy login, one automatic re-login after an early session expiry, and a separate Wiadomości session that bootstraps itself and retries once.
- `LibrusApiClient`: the low-level client, one method per endpoint, returning raw JSON.
- `librus_synergia.parsers`: pure JSON → dataclass parsers.
- `docs/`: unofficial notes on the Librus API.
- Session export includes `DeviceCookie`, which Librus sets under `Path=/OAuth`, and import restores its path.
- A mailbox the account doesn't have (HTTP 404) comes back as an empty list, without a pointless re-login.
