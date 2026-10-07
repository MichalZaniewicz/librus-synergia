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
| `oauth_token` | synergia.librus.pl | ✅ `Max-Age=600` per response, session usable ~24 h | The actual session. ❓ The 10-minute Max-Age suggests it's re-issued on every request (sliding expiry). |
| `DZIENNIKSID`, `SDZIENNIKSID` | both | session | Session companions. |
| `DeviceCookie` | api.librus.pl, ✅ **`Path=/OAuth`** | ✅ 1 year (`Max-Age=31536000`) | Marks a known device. ❓ Probably why normal logins get no captcha. **Persist it, with its path.** |

⚠️ Because `DeviceCookie` lives under `/OAuth`, asking a cookie jar for
"cookies for `https://api.librus.pl/`" does **not** return it. Walk the jar
(or filter for an `/OAuth/...` URL) when you persist the session.

- There is **no refresh token**. When `oauth_token` expires you have to log
  in again with the password, so unattended long-running use needs the
  password stored.
- ✅ `GET https://synergia.librus.pl/refreshToken` (with the session cookies)
  answers 200 with an empty body and a **new `oauth_token`** cookie; the API
  keeps working afterwards (confirmed live 2026-10-07). Calling it before the
  session dies should extend it without a password login. ❓ How long the
  extended session lasts was not measured. `Me` also reports `Refresh: 900`.
  `async_ensure_session_valid` calls it (`async_refresh_session`) once the
  session is 2 hours old and falls back to a password login if it fails.
- ✅ The session can die **before** 24 h. The client assumes a 20 h
  lifetime, and on an HTTP 401 from a data endpoint it logs in again once
  and retries. `Librus` does this for you.
- To resume later, persist `LibrusSessionData` (via `export_session()` or
  `Librus.session_data`) and pass it back via `import_session()` or
  `Librus(..., session_data=...)`.

## One account per cookie jar

The session lives in the cookie jar, and a jar holds only one
`oauth_token` per domain. ✅ Two accounts sharing one
`aiohttp.ClientSession` will overwrite each other's session and receive
each other's data, intermittently. Use one session per account.
