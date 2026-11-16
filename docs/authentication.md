# Authentication

## History: the dead password grant

Older clients (szkolny-android, `lomber1/py-librus-api`) log in with an OAuth
password grant:

```
POST https://api.librus.pl/OAuth/Token
grant_type=password&client_id=28&username=...&password=...
```

✅ **Dead.** Since around 2026-03-28 Librus answers this with HTTP 400
`unsupported_grant_type`, even with correct credentials. Do not use it.

## Current flow (4 steps)

The current flow behaves like a browser logging in to the parent portal.
Everything is carried in cookies, so every request must use the same cookie
jar. Implemented in `LibrusApiClient.async_login`, and based on
[emsi/librus_pyapi](https://github.com/emsi/librus_pyapi) (MIT).

1. **Portal redirect.**
   `GET https://synergia.librus.pl/loguj/portalRodzina` (do not follow
   redirects). The `Location` header points at
   `api.librus.pl/OAuth/Authorization?client_id=46&response_type=code&scope=mydata&state=...`.
2. **Authorization page.**
   `GET` that `Location` URL (do not follow redirects). This sets API-side
   session cookies. The body is not used.
3. **Credentials.**
   `POST https://api.librus.pl/OAuth/Authorization?client_id=46` with the
   form body `action=login&login=<login>&pass=<password>` and browser-like
   XHR headers: `Origin`/`Referer` on `api.librus.pl`, the `Sec-Fetch-*`
   headers, `X-Requested-With: XMLHttpRequest` and a desktop Chrome
   `User-Agent`.

   ✅ On success the response is JSON:
   ```json
   {"status": "ok", "goTo": "/OAuth/Authorization/2FA?client_id=46"}
   ```
   "2FA" is only Librus's internal name for the next step. No second factor
   was actually asked for on the tested accounts.

   ❓ A response without `goTo` is treated as wrong credentials. This was
   never tested with a real wrong password, to avoid tripping abuse
   detection on a real family's account.
4. **Redirect chain.**
   Starting from `goTo` (resolved against the authorization URL), follow
   `Location` headers manually, keeping cookies, until a response has no
   `Location` (at most 10 hops). After that, an `oauth_token` cookie must
   exist for `synergia.librus.pl`.

If any response body in steps 3–4 contains a captcha marker (`captcha`,
`recaptcha`, `g-recaptcha`, `hcaptcha`), the client raises
`LibrusCaptchaRequiredError`. ❓ This was never observed.

## Cookies and session lifetime

| Cookie | Domain | Lifetime | Why it matters |
|---|---|---|---|
| `oauth_token` | synergia.librus.pl | ✅ `Max-Age=600` per response; the session dies after ~10 min without a request | The actual session. ✅ Idle expiry confirmed live 2026-11-14: 11.5 idle minutes after a login, `Me` answered 401. A request every few minutes keeps it alive; after a quiet spell, `refreshToken` brings it back (below). |
| `DZIENNIKSID`, `SDZIENNIKSID` | both | session | Session companions. |
| `DeviceCookie` | api.librus.pl, ✅ **`Path=/OAuth`** | ✅ 1 year (`Max-Age=31536000`) | Marks a known device. ❓ Probably why normal logins get no captcha. **Persist it, with its path.** |

⚠️ Because `DeviceCookie` lives under `/OAuth`, asking a cookie jar for
"cookies for `https://api.librus.pl/`" does **not** return it. Walk the jar
(or filter for an `/OAuth/...` URL) when you persist the session.

- There is **no OAuth refresh token** to keep instead of the password. The
  session cookies can be renewed with `refreshToken` (next point), even after
  `oauth_token` expired from idling, but a session that is gone for good
  (logged out, cookies lost, many hours later ❓) needs a password login, so
  unattended long-running use needs the password stored.
- ✅ `GET https://synergia.librus.pl/refreshToken` (with the session cookies)
  answers 200 with an empty body and a **new `oauth_token`** cookie (and a
  new `SDZIENNIKSID`); the API keeps working afterwards (confirmed live
  2026-10-07). ✅ It also works **after** the session idled out (confirmed
  live 2026-11-14): log in, wait 11.5 minutes, `Me` answers 401,
  `refreshToken` answers 200 with new cookies, `Me` works again - no password
  login. So the cheap way to recover from a 401 is `async_refresh_session()`
  first and a password login only when it returns False - `Librus` does this
  since 0.3.31, the Home Assistant integration since 0.12.49. ❓ How long after the last request a
  refresh still works was not measured. `Me` also reports `Refresh: 900`.
  `async_ensure_session_valid` calls it (`async_refresh_session`) once the
  session is 2 hours old, and (since 0.3.32) when Synergia hasn't answered
  for 8 minutes or nothing is known since `import_session` - before the
  next request, so it isn't rejected first. It falls back to a password
  login if the refresh fails.
  A refresh only counts when that very answer sets a new `oauth_token`
  (a 200 without one is a failure); after a failure the client doesn't try
  `refreshToken` again for 30 minutes.
- ✅ The session dies after ~10 minutes without a request, long before the
  20 h lifetime the client assumes. On an HTTP 401 from a data endpoint
  `Librus` renews the session once and retries: `refreshToken` first, a
  password login only when that fails (since 0.3.31; before, it always
  logged in with the password, so a client polling less often than every 10
  minutes did that on nearly every poll). When several requests hit the same
  dead session at once, they all wait for one renewal instead of each
  renewing it.
- To resume later, persist `LibrusSessionData` (via `export_session()` or
  `Librus.session_data`) and pass it back via `import_session()` or
  `Librus(..., session_data=...)`. `Librus.session_data` can still be read
  after `close()` (it holds the cookies the session had when it closed) and
  reading it never opens a new session.

## One account per cookie jar

The session lives in the cookie jar, and a jar holds only one
`oauth_token` per domain. ✅ Two accounts sharing one
`aiohttp.ClientSession` will overwrite each other's session and receive
each other's data, intermittently. Use one session per account.
