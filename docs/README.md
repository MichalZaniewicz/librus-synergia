# Unofficial Librus Synergia API notes

Librus publishes no public API documentation. These pages describe what this
library actually talks to: the flows and response shapes **as observed
against real parent/student accounts**, including the places where older
reverse-engineered descriptions turned out to be wrong.

Each claim is marked with how sure we are:

| Marker | Meaning |
|---|---|
| ✅ **Confirmed** | Seen in a real response from a real account. |
| 📖 **Reference** | Field names taken from another client's parser (mainly [szkolny-eu/szkolny-android](https://github.com/szkolny-eu/szkolny-android)), not yet seen populated on a real account. |
| ❓ **Unverified** | A reasonable inference with no direct evidence yet. |

## Pages

- [Authentication](authentication.md): the cookie-based login flow, session lifetime, what to persist.
- [Data endpoints](endpoints.md): every `gateway/api/2.0` endpoint with root keys, shapes and quirks.
- [Messages (Wiadomości)](messages.md): the separate messaging subsystem.
- [Kindergarten accounts](kindergarten.md): a different timetable API for preschool accounts.
- [Errors and status codes](errors.md): what 401, 403 and 503 really mean here.

## The short version

```
login:  synergia.librus.pl/loguj/portalRodzina
        → api.librus.pl/OAuth/Authorization?client_id=46   (form POST + redirect chain)
data:   GET https://synergia.librus.pl/gateway/api/2.0/<Endpoint>   (cookies only, no bearer token)
msgs:   GET https://synergia.librus.pl/wiadomosci3                   (bootstrap)
        GET https://wiadomosci.librus.pl/api/<mailbox>/...
```

## Contributing

If you see a response that contradicts these notes, or data for something
marked 📖/❓, please open an issue with the **shape** of the JSON (keys and
value types). Redact names, ids and message text first.
