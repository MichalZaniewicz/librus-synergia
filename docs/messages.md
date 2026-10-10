# Messages (Wiadomości)

Messages are a separate subsystem on a separate domain, with its own
session.

## Bootstrap

```
GET https://synergia.librus.pl/wiadomosci3
```

Run this with the main session cookies. It sets the cookies for
`wiadomosci.librus.pl`.

- ✅ If the body contains **`Brak dostępu`**, the school has no messages
  module. That is a normal outcome, not an error. `Librus` asks again
  after 5 minutes for the first three such answers in a row, then hourly
  (and after every password login) - a session kept alive through
  `/refreshToken` never logs in again, so a passing "Brak dostępu" would
  otherwise switch messages off for as long as the session lives.
- ✅ The messages session **expires independently of the main session,
  and much more often** (sometimes within the hour). When the session is
  rejected (401/403) or another 4xx comes back - except a 404 (no such
  mailbox) - `Librus` first only bootstraps again (one request) and
  retries. Only a **401** goes further: if the retry is rejected with 401
  too (or the new bootstrap answers "Brak dostępu", which can mean the main
  session is what died), it logs in with the password, bootstraps and
  retries once more. When even that didn't fix the call, the next 10
  minutes of Wiadomości calls raise the error instead of logging in again.
  A 403 or another 4xx after the extra bootstrap, a 5xx, a network error or
  timeout, a 429/502/503/504, and an answer without an error status that
  isn't what was expected (not JSON, a failed download), are raised as they
  are: a fresh login doesn't change them, and a Wiadomości outage must not
  turn every snapshot into password logins. Every password login (wherever
  it came from) makes `Librus` bootstrap again before the next Wiadomości
  call, and concurrent calls share one bootstrap.

## Endpoints

Base: `https://wiadomosci.librus.pl/api`

| Request | Notes |
|---|---|
| `GET /inbox/unreadMessagesCount` | ✅ `{"data": {"inbox": 2, "notes": 0, "alerts": 1, "substitutions": 0, "absences": 0, "justifications": 0, "trash": 0}}`: counts for every mailbox in one call. ✅ Also `archiveInbox`, `archiveNotes`, ... - one archive counter per mailbox (14 in all). |
| `GET /<mailbox>/messages?limit=10[&unreadOnly=1]` | ✅ `{"data": [...], "total": N}`. ✅ At least one secondary mailbox returns a **bare JSON array** without the `data` envelope. ✅ Listing **does not** mark anything read. |
| `GET /<mailbox>/messages/<id>` | ✅ The full message. ⚠️ **Marks the message read**, exactly like opening it in the app. |
| `GET /me` | ✅ The logged-in account: `firstName`, `lastName`, `groupName` ("rodzic"), `accountId`, `accessToAttachments`. |
| `GET /outbox/messages` | ✅ Messages you sent (`receiverName`, `topic`, `content`, `sendDate`). No read-marking involved. `messages("outbox")` fills `MessageData.receiver_name`. ✅ An item has `receiverName` and also `receiverFirstName`/`receiverLastName` (used when `receiverName` is empty), plus `messageId`, `tags`, `category`, `isAnyFileAttached`. |
| `GET /archive/inbox/messages` | ✅ Archived (past years) inbox, same item shape, `total` and `archivingInProgress`. `messages("archive/inbox")`. ✅ Opening one (`/archive/inbox/messages/<id>`, checked 2026-10-09) returns the same `{"data": {...}}` shape as an inbox message - base64 `Message`, `attachments` (`filename`, `id`) - and `parse_message(payload, "archive/inbox", id)` reads it. ❓ The tested message was already read, so whether opening an unread archived one marks it read wasn't seen (assume it does). ✅ **An archived message's attachment can't be downloaded through the API** (checked 2026-10-09): `attachments/<file>/messages/<msg>` answers 401 for it, `archive/attachments/...` and `archive/inbox/attachments/...` 401/404, and the message's `attachementInfo` says (in Polish) to download it from Synergia's own pages. |
| `GET /receivers/student-subjects` | ✅ `[{"teacherIdentifier", "subject"}]` - the student's teachers with their subjects. |
| `GET /inbox/messages/senders?page=1&limit=50` | ✅ `{"data": [{"senderId", "senderFirstName", "senderLastName"}]}` - everyone who wrote to the account (a group sender comes with the whole name in `senderFirstName`). |
| `GET /outbox/messages/receivers?page=1&limit=50` | ✅ `{"data": [{"receiverId", "receiverFirstName", "receiverLastName"}]}` |
| `GET /outbox/messages/<id>` | ✅ A sent message in full: the same shape as an inbox message plus **`receivers`** - one entry per recipient with `firstName`, `lastName`, `group` and **`readed`** (when they read it), and `receiversCount` / `readedCount`. No side effect. `parse_message` fills `FullMessageData.receivers` (`name`, `group`, `read_date`). |
| `GET /archive/outbox/messages` | ✅ Archived sent messages, the outbox item shape plus `total` and `archivingInProgress`. |
| `GET /receivers/types?includeClass=true` | ✅ `{"data": {"defaultGroup", "list": [{"id", "name"}]}}` - the recipient groups a parent can write to (15 on the tested school). |

Mailboxes: `inbox`, `notes`, `alerts`, `substitutions`, `absences`,
`justifications`, `trash`. ✅ For some accounts `alerts`/`substitutions`
return 404.

✅ A confirmation that an absence was justified arrived as an ordinary
**inbox** message from the sender "e-Usprawiedliwienia", not in
`justifications`.

## List item fields

✅ `messageId` (string), `senderName` (or `senderFirstName` +
`senderLastName`), `topic`, `content`, `sendDate`, `readDate` (`null` =
unread), `isAnyFileAttached`.

## Content encoding

✅ `content` (list) and `Message` (single message) are **base64-encoded
UTF-8**.

- ✅ The list `content` is a preview **truncated to a fixed byte length**,
  and the cut can fall inside a multi-byte Polish character (`ą`, `ę`, ...).
  Decode with `errors="ignore"`; strict decoding fails.
- ✅ The single-message `Message` field, once decoded, is wrapped in XML:
  `<Message><Content><![CDATA[ ... ]]></Content></Message>`.
- ✅ The body is HTML: `<br>`, `<p>`, and every link rewritten to
  `<a href="https://liblink.pl/..." title="Link został skonwertowany ze względów bezpieczeństwa systemu.">...</a>`.

`decode_message_content` handles all three.

## Attachments

✅ The single-message response lists `attachments: [{"id", "filename"}]`.

✅ **Download (confirmed live 2026-10-07)**, with the same session and
without opening the message:

1. `GET /attachments/<attachment id>/messages/<message id>` →
   `{"data": {"status": "ok", "downloadLink": "https://sandbox.librus.pl/GetFile/<key>"}}`.
2. `GET <downloadLink>` → an HTML waiting page.
3. `GET <downloadLink>/get` (send `Referer: <downloadLink>`) → the file
   itself, with `Content-Disposition: attachment; filename="..."`. Third-party
   code retries while this still answers `text/html`; the tested PDF came on
   the first try.

The attachment ids still come from the single-message response, so getting
them for an **unread** message would mark it read. `Librus.download_attachment`
does steps 1-3. It only follows an `https://sandbox.librus.pl/...`
`downloadLink` (anything else, or a link that isn't a valid URL, is
`LibrusUnexpectedResponseError`), and gives up with `LibrusConnectionError`
after 150 s (`DOWNLOAD_TIMEOUT_SECONDS`) - the whole call, logins and
retries included. Only when the session was rejected (or a 4xx came
back) does it bootstrap Wiadomości again - and only a repeated 401 leads to
one password login (see "Bootstrap" above); a 5xx, a timeout or an odd
answer (a link outside the sandbox, a failed download) is raised straight
away. At most two downloads run at once (`MAX_CONCURRENT_DOWNLOADS`),
outside the limit for ordinary requests, so slow downloads never hold them
up.
