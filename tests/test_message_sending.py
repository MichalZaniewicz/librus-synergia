"""Writing messages: recipients, sending, replying and withdrawing.

The request shapes follow Librus's own web app; recipient reads were
checked live (2026-10-12). Sending is never retried."""

from __future__ import annotations

import base64

import aiohttp
import pytest

from librus_synergia import Librus, LibrusMessageSendError, parsers
from librus_synergia.const import MESSAGES_BASE_URL, MESSAGES_BOOTSTRAP_URL

from .helpers import MockedSession, mock_successful_login

ME_URL = f"{MESSAGES_BASE_URL}/me"
SEND_URL = f"{MESSAGES_BASE_URL}/messages"
REPLY_URL = f"{MESSAGES_BASE_URL}/messages/reply"
SENT = {"data": {"messageId": 987, "status": "sent"}}


def _b64(text: str) -> str:
    return base64.b64decode(text).decode("utf-8")


def _ready(mocked: MockedSession, session: aiohttp.ClientSession) -> None:
    mock_successful_login(session, mocked)
    mocked.get(MESSAGES_BOOTSTRAP_URL, text_data="<html>ok</html>")
    mocked.get(ME_URL, json_data={"accountId": 1, "groupName": "rodzic"})


async def test_send_message_posts_once_and_returns_the_confirmation() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.post(SEND_URL, status=201, json_data=SENT)
            librus = Librus("1234567u", "pw", session=session)
            result = await librus.send_message(["3511568"], " Pytanie ", "Dzień dobry,\na < b & c")
            assert (result.message_id, result.status) == (987, "sent")
            assert mocked.post_calls[SEND_URL] == 1
            body = mocked.kwargs[SEND_URL]["json"]
            assert body["receivers"] == {"schoolReceivers": [{"accountId": "3511568"}]}
            assert _b64(body["topic"]) == "Pytanie"
            assert _b64(body["content"]) == "Dzień dobry,\na &lt; b &amp; c"
            assert body["storageId"] is None and body["category"] == "normal"
            assert "originalMessageId" not in body


@pytest.mark.parametrize(
    ("status", "sent"),
    [(422, False), (400, False), (500, None)],
)
async def test_failed_send_is_not_retried(status: int, sent: bool | None) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.post(SEND_URL, status=status, json_data={"errors": [{"code": "X"}]})
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusMessageSendError) as err:
                await librus.send_message(["1"], "T", "Text")
            assert err.value.sent is sent
            assert mocked.post_calls[SEND_URL] == 1


async def test_rejected_session_on_send_means_not_sent_and_no_retry() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.post(SEND_URL, status=401, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusMessageSendError) as err:
                await librus.send_message(["1"], "T", "Text")
            assert err.value.sent is False
            assert mocked.post_calls[SEND_URL] == 1


async def test_success_answer_with_errors_is_unknown() -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.post(SEND_URL, status=200, json_data={"errors": [{"code": "X"}]})
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusMessageSendError) as err:
                await librus.send_message(["1"], "T", "Text")
            assert err.value.sent is None


@pytest.mark.parametrize(
    "answer",
    [{"data": {}}, {}, {"data": True}, None],
)
async def test_success_answer_without_an_id_means_sent(answer: dict | None) -> None:
    # A reply's answer has no message id (CONFIRMED live 2026-10-12: the
    # reply went out while the old check called it unconfirmed). None = an
    # empty, non-JSON body.
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            if answer is None:
                mocked.post(SEND_URL, status=200, text_data="")
            else:
                mocked.post(SEND_URL, status=200, json_data=answer)
            librus = Librus("1234567u", "pw", session=session)
            result = await librus.send_message(["1"], "T", "Text")
            assert result.message_id is None
            assert mocked.post_calls[SEND_URL] == 1


def test_parse_sent_message_shapes() -> None:
    assert parsers.parse_sent_message(SENT).message_id == 987
    assert parsers.parse_sent_message({"data": {"id": "12"}}).message_id == 12
    assert parsers.parse_sent_message({"messageId": 5}).message_id == 5
    assert parsers.parse_sent_message({"data": {"status": "failed"}}) is None


@pytest.mark.parametrize(
    ("recipients", "topic", "text"),
    [
        ([], "T", "x"),
        (["abc"], "T", "x"),
        (["1", "1"], "T", "x"),
        (["1"], " ", "x"),
        (["1"], "T", ""),
    ],
)
async def test_invalid_message_is_refused_before_any_request(
    recipients: list[str], topic: str, text: str
) -> None:
    async with aiohttp.ClientSession() as session:
        with MockedSession(session):
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(ValueError):
                await librus.send_message(recipients, topic, text)


async def test_reply_goes_to_the_sender_in_the_thread() -> None:
    message_url = f"{MESSAGES_BASE_URL}/inbox/messages/55"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.get(
                message_url,
                json_data={
                    "data": {
                        "topic": "Zebranie",
                        "senderId": "3009333",
                        "noReply": 0,
                        "Message": base64.b64encode(b"Tekst").decode(),
                    }
                },
            )
            mocked.post(REPLY_URL, status=201, json_data=SENT)
            librus = Librus("1234567u", "pw", session=session)
            result = await librus.reply_to_message("55", "Dziękuję")
            assert result.message_id == 987
            body = mocked.kwargs[REPLY_URL]["json"]
            assert body["originalMessageId"] == 55
            assert body["receivers"] == {"schoolReceivers": [{"accountId": "3009333"}]}
            assert _b64(body["topic"]) == "Re: Zebranie"
            assert _b64(body["content"]) == "Dziękuję"


async def test_no_reply_message_is_not_answered() -> None:
    message_url = f"{MESSAGES_BASE_URL}/inbox/messages/56"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.get(
                message_url,
                json_data={"data": {"topic": "System", "senderId": "2", "noReply": 1}},
            )
            librus = Librus("1234567u", "pw", session=session)
            with pytest.raises(LibrusMessageSendError) as err:
                await librus.reply_to_message("56", "x")
            assert err.value.sent is False
            assert REPLY_URL not in mocked.post_calls


async def test_withdraw_message_puts_the_withdrawal() -> None:
    url = f"{MESSAGES_BASE_URL}/messages/987/withdrawal"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.put(url, json_data={"data": {}})
            librus = Librus("1234567u", "pw", session=session)
            await librus.withdraw_message("987")
            assert mocked.put_calls[url] == 1


async def test_message_recipients_merge_groups_and_subjects() -> None:
    groups_url = f"{MESSAGES_BASE_URL}/receivers/groups/school-employees"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.get(
                f"{MESSAGES_BASE_URL}/receivers/types",
                json_data={
                    "data": {
                        "list": [
                            {"id": "tutors", "name": "Wychowawcy"},
                            {"id": "teachers", "name": "Nauczyciele"},
                            {"id": "secretary", "name": "Sekretariat"},
                            {"id": "parentsCouncil", "name": "Rada rodziców"},
                        ]
                    }
                },
            )
            tutor = {
                "accountId": "11",
                "label": "Nowak Anna",
                "userId": "1",
                "availabilityStatus": {"isAvailable": True, "returnAt": None},
            }
            mocked.get(f"{groups_url}?receiverType=tutors", json_data={"receivers": [tutor]})
            mocked.get(
                f"{groups_url}?receiverType=teachers",
                json_data={"receivers": [tutor, {"accountId": "12", "label": "Kowal Jan"}]},
            )
            mocked.get(
                f"{groups_url}?receiverType=secretary",
                json_data={"receivers": [{"accountId": "13", "label": "sekretariat"}]},
            )
            mocked.get(
                f"{MESSAGES_BASE_URL}/receivers/student-subjects",
                json_data={
                    "data": [
                        {"teacherIdentifier": 11, "subject": "Biologia"},
                        {"teacherIdentifier": 12, "subject": "Matematyka"},
                    ]
                },
            )
            librus = Librus("1234567u", "pw", session=session)
            recipients = await librus.message_recipients()
    assert [(r.account_id, r.group, r.subjects) for r in recipients] == [
        ("11", "tutors", ["Biologia"]),
        ("12", "teachers", ["Matematyka"]),
        ("13", "secretary", []),
    ]
    assert recipients[0].group_name == "Wychowawcy" and recipients[0].available is True
    assert f"{groups_url}?receiverType=parentsCouncil" not in mocked.get_calls


async def test_kindergarten_recipients_any_offered_group_and_no_relogin() -> None:
    # Seen live 2026-10-12 on a kindergarten account: no tutors group, a
    # "speechTherapist" group, and a 403 on the subject list.
    from librus_synergia.const import API_OAUTH_AUTHORIZATION_URL

    groups_url = f"{MESSAGES_BASE_URL}/receivers/groups/school-employees"
    async with aiohttp.ClientSession() as session:
        with MockedSession(session) as mocked:
            _ready(mocked, session)
            mocked.get(
                f"{MESSAGES_BASE_URL}/receivers/types",
                json_data={
                    "data": {
                        "list": [
                            {"id": "speechTherapist", "name": "Logopeda"},
                            {"id": "teachers", "name": "Nauczyciele"},
                            {"id": "customGroups", "name": "Grupy odbiorców"},
                            {"id": "sadmin", "name": "Pomoc techniczna"},
                        ]
                    }
                },
            )
            mocked.get(
                f"{groups_url}?receiverType=teachers",
                json_data={"receivers": [{"accountId": "12", "label": "Kowal Jan"}]},
            )
            mocked.get(
                f"{groups_url}?receiverType=speechTherapist",
                json_data={"receivers": [{"accountId": "14", "label": "Lis Ewa"}]},
            )
            mocked.get(f"{MESSAGES_BASE_URL}/receivers/student-subjects", status=403, json_data={})
            librus = Librus("1234567u", "pw", session=session)
            recipients = await librus.message_recipients()
            assert mocked.post_calls[API_OAUTH_AUTHORIZATION_URL] == 1  # no relogin
            assert f"{groups_url}?receiverType=sadmin" not in mocked.get_calls
            assert f"{groups_url}?receiverType=customGroups" not in mocked.get_calls
    assert [(r.account_id, r.group, r.group_name) for r in recipients] == [
        ("12", "teachers", "Nauczyciele"),
        ("14", "speechTherapist", "Logopeda"),
    ]

    marked = parsers.mark_class_tutor(recipients, "Jan Kowal")
    assert [(r.account_id, r.group, r.group_name) for r in marked] == [
        ("12", "tutors", "Wychowawcy"),
        ("14", "speechTherapist", "Logopeda"),
    ]
    assert parsers.mark_class_tutor(recipients, "Ktoś Inny") == recipients
    assert parsers.mark_class_tutor(marked, "Lis Ewa") == marked  # tutors exist


def test_message_recipient_groups_order() -> None:
    types = {
        "data": {
            "list": [
                {"id": "librarian"},
                {"id": "speechTherapist"},
                {"id": "therapist"},
                {"id": "tutors"},
                {"id": "parentsCouncil"},
            ]
        }
    }
    assert parsers.message_recipient_groups(types) == [
        "tutors",
        "speechTherapist",
        "librarian",
        "therapist",
    ]


def test_full_message_reply_and_withdraw_fields() -> None:
    message = parsers.parse_message(
        {
            "data": {
                "senderId": 3009333,
                "noReply": "1",
                "enableMessageWithdrawn": 0,
                "isMessageWithdrawn": False,
                "topic": "x",
            }
        },
        "inbox",
        "5",
    )
    assert message is not None
    assert (message.sender_id, message.no_reply, message.can_withdraw, message.withdrawn) == (
        "3009333",
        True,
        False,
        False,
    )
    plain = parsers.parse_message({"data": {"topic": "x"}}, "inbox", "6")
    assert plain is not None and plain.sender_id is None and plain.can_withdraw is None
