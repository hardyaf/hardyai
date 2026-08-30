from __future__ import annotations

from enum import Enum


class SessionState(str, Enum):
    IDLE = "IDLE"
    FAST_COMMAND = "FAST_COMMAND"
    CONVERSATIONAL = "CONVERSATIONAL"
    AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
    AWAITING_EXTERNAL_RESULT = "AWAITING_EXTERNAL_RESULT"
    ERROR_RECOVERY = "ERROR_RECOVERY"


class SessionOwner(str, Enum):
    SYSTEM = "system"
    MICRO = "micro_jarvis"
    MAIN = "main_jarvis"


class PowerState(str, Enum):
    AWAKE = "AWAKE"
    ASLEEP = "ASLEEP"


class Intent(str, Enum):
    LIST_CREATE_LIST = "lists.create_list"
    LIST_ADD_ITEM = "lists.add_item"
    LIST_GET_ITEMS = "lists.get_items"
    LIST_DELETE_LIST = "lists.delete_list"
    LIST_REMOVE_ITEM = "lists.remove_item"
    LIST_MARK_ITEM_DONE = "lists.mark_item_done"
    CALENDAR_ADD_EVENT = "calendar.add_event"
    CALENDAR_VIEW = "calendar.view"
    CALENDAR_UPDATE_EVENT = "calendar.update_event"
    CALENDAR_DELETE_EVENT = "calendar.delete_event"
    HOME_SET_SWITCH = "home.set_switch"
    EMAIL_LIST_RECENT = "email.list_recent"
    EMAIL_SEARCH = "email.search"
    EMAIL_GET_MESSAGE = "email.get_message"
    EMAIL_GET_THREAD = "email.get_thread"
    EMAIL_SUMMARIZE = "email.summarize"
    EMAIL_DISCUSS = "email.discuss"
    EMAIL_STATUS = "email.status"
    EMAIL_MARK_REVIEWED = "email.mark_reviewed"
    EMAIL_SNOOZE = "email.snooze"
    EMAIL_DISMISS = "email.dismiss"
    EMAIL_CORRECT_CATEGORY = "email.correct_category"
    EMAIL_MARK_NEEDS_REPLY = "email.mark_needs_reply"
    EMAIL_MARK_COMPLETE = "email.mark_complete"
    EMAIL_MARK_SPAM = "email.mark_spam"
    EMAIL_SYNC = "email.sync"
    EMAIL_PROMOTE_TO_LIST = "email.promote_to_list"
    EMAIL_PROMOTE_TO_CALENDAR = "email.promote_to_calendar"
    EMAIL_PROMOTE_TO_TASK = "email.promote_to_task"
    EMAIL_PROMOTE_TO_WAVE = "email.promote_to_wave"
    DOCUMENTS_INGEST = "documents.ingest"
    DOCUMENTS_STATUS = "documents.status"
    DOCUMENTS_FIND = "documents.find"
    DOCUMENTS_GET = "documents.get"
    DOCUMENTS_SHOW_SOURCE = "documents.show_source"
    DOCUMENTS_REPROCESS = "documents.reprocess"
    DOCUMENTS_ESCALATE_OCR = "documents.escalate_ocr"
    DOCUMENTS_LIST_REVIEWS = "documents.list_reviews"
    DOCUMENTS_PROPOSE_METADATA = "documents.propose_metadata"
    DOCUMENTS_CORRECT_FIELD = "documents.correct_field"
    DOCUMENTS_CONFIRM_FIELDS = "documents.confirm_fields"
    SYSTEM_WAKE = "system.wake"
    SYSTEM_SLEEP = "system.sleep"
    CONVERSATIONAL = "conversation.general"
    UNKNOWN = "unknown"


FAST_COMMAND_INTENTS = {
    Intent.LIST_CREATE_LIST,
    Intent.LIST_ADD_ITEM,
    Intent.LIST_GET_ITEMS,
    Intent.LIST_DELETE_LIST,
    Intent.LIST_REMOVE_ITEM,
    Intent.LIST_MARK_ITEM_DONE,
    Intent.CALENDAR_ADD_EVENT,
    Intent.CALENDAR_VIEW,
    Intent.CALENDAR_UPDATE_EVENT,
    Intent.CALENDAR_DELETE_EVENT,
    Intent.HOME_SET_SWITCH,
}


EMAIL_AGENT_INTENTS = {
    Intent.EMAIL_LIST_RECENT,
    Intent.EMAIL_SEARCH,
    Intent.EMAIL_GET_MESSAGE,
    Intent.EMAIL_GET_THREAD,
    Intent.EMAIL_SUMMARIZE,
    Intent.EMAIL_DISCUSS,
    Intent.EMAIL_STATUS,
    Intent.EMAIL_MARK_REVIEWED,
    Intent.EMAIL_SNOOZE,
    Intent.EMAIL_DISMISS,
    Intent.EMAIL_CORRECT_CATEGORY,
    Intent.EMAIL_MARK_NEEDS_REPLY,
    Intent.EMAIL_MARK_COMPLETE,
    Intent.EMAIL_MARK_SPAM,
    Intent.EMAIL_SYNC,
    Intent.EMAIL_PROMOTE_TO_LIST,
    Intent.EMAIL_PROMOTE_TO_CALENDAR,
    Intent.EMAIL_PROMOTE_TO_TASK,
    Intent.EMAIL_PROMOTE_TO_WAVE,
}


DOCUMENT_INTENTS = {
    Intent.DOCUMENTS_INGEST,
    Intent.DOCUMENTS_STATUS,
    Intent.DOCUMENTS_FIND,
    Intent.DOCUMENTS_GET,
    Intent.DOCUMENTS_SHOW_SOURCE,
    Intent.DOCUMENTS_REPROCESS,
    Intent.DOCUMENTS_ESCALATE_OCR,
    Intent.DOCUMENTS_LIST_REVIEWS,
    Intent.DOCUMENTS_PROPOSE_METADATA,
    Intent.DOCUMENTS_CORRECT_FIELD,
    Intent.DOCUMENTS_CONFIRM_FIELDS,
}


# Main may semantically repair both the low-latency household commands and
# Main-owned domain actions.  Keep this separate from FAST_COMMAND_INTENTS so
# adding a Main-owned skill does not accidentally authorize Micro execution.
MAIN_ACTION_INTENTS = FAST_COMMAND_INTENTS | EMAIL_AGENT_INTENTS | DOCUMENT_INTENTS
