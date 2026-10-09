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
  module. That is a normal outcome, not an error.
- ✅ The messages session **expires independently of the main session,
  and much more often** (sometimes within the hour). On any failure
  except a 404 (no such mailbox), bootstrap again and retry once.

## Endpoints

Base: `https://wiadomosci.librus.pl/api`

| Request | Notes |
|---|---|
| `GET /inbox/unreadMessagesCount` | ✅ `{"data": {"inbox": 2, "notes": 0, "alerts": 1, "substitutions": 0, "absences": 0, "justifications": 0, "trash": 0}}`: counts for every mailbox in one call. |
| `GET /<mailbox>/messages?limit=10[&unreadOnly=1]` | ✅ `{"data": [...], "total": N}`. ✅ At least one secondary mailbox returns a **bare JSON array** without the `data` envelope. ✅ Listing **does not** mark anything read. |
| `GET /<mailbox>/messages/<id>` | ✅ The full message. ⚠️ **Marks the message read**, exactly like opening it in the app. |
| `GET /me` | ✅ The logged-in account: `firstName`, `lastName`, `groupName` ("rodzic"), `accountId`, `accessToAttachments`. |
| `GET /outbox/messages` | ✅ Messages you sent (`receiverName`, `topic`, `content`, `sendDate`). No read-marking involved. `messages("outbox")` fills `MessageData.receiver_name` (falls back to `receiverFirstName`/`receiverLastName`, 📖 not seen). |
| `GET /archive/inbox/messages` | ✅ Archived (past years) inbox, same item shape, `total` and `archivingInProgress`. `messages("archive/inbox")`. ❓ Opening one archived message (`/archive/inbox/messages/<id>`) not tried. |
| `GET /receivers/student-subjects` | ✅ `[{"teacherIdentifier", "subject"}]` - the student's teachers with their subjects. |

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
does steps 1-3.
