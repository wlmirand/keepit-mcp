"""MCP tools for keepITCore v1, based on the supplied OpenAPI 3.1 contract."""
import base64
import asyncio
from datetime import timedelta, timezone
import os
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

KEEPIT_URL = os.environ.get('KEEPIT_URL', 'http://keepit:80').rstrip('/')
KEEPIT_EMAIL = os.environ.get('KEEPIT_EMAIL', '').strip()
KEEPIT_PASSWORD = os.environ.get('KEEPIT_PASSWORD', '')
KEEPIT_TOKEN = os.environ.get('KEEPIT_TOKEN', '').strip()  # Optional fallback for token-only setups
_token: str | None = None
_token_expires_at: datetime | None = None
_login_lock = asyncio.Lock()
MCP_API_KEY = os.environ.get('MCP_API_KEY', '').strip()
MAX_MEDIA_BYTES = int(os.environ.get('MAX_MEDIA_BYTES', str(8 * 1024 * 1024)))

mcp = FastMCP(
    'keepit-mcp',
    stateless_http=True,
    json_response=True,
    host='0.0.0.0',
    port=int(os.environ.get('PORT', '8000')),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def uid(value: str) -> str:
    return str(UUID(value))


def iso_date(value: str) -> str:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Date/time must include a timezone (for example, 2026-10-20T10:00:00Z)')
    return value


async def access_token(force_login: bool = False) -> str:
    """Log in with keepIT credentials and reuse the JWT until shortly before expiry."""
    global _token, _token_expires_at
    if not KEEPIT_EMAIL or not KEEPIT_PASSWORD:
        if KEEPIT_TOKEN:
            return KEEPIT_TOKEN
        raise ValueError('Set KEEPIT_EMAIL and KEEPIT_PASSWORD (or KEEPIT_TOKEN)')
    async with _login_lock:
        now = datetime.now(timezone.utc)
        if not force_login and _token and _token_expires_at and now < _token_expires_at - timedelta(seconds=60):
            return _token
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            try:
                response = await client.post(
                    f'{KEEPIT_URL}/api/auth/login',
                    json={'email': KEEPIT_EMAIL, 'password': KEEPIT_PASSWORD},
                    headers={'Accept': 'application/json'},
                )
                response.raise_for_status()
                data = response.json()
            except httpx.HTTPStatusError as exc:
                raise ValueError(f'keepIT login failed (HTTP {exc.response.status_code}): {exc.response.text[:300]}') from None
            except (httpx.RequestError, ValueError) as exc:
                raise ValueError(f'keepIT login request failed: {exc}') from None
        token = data.get('accessToken')
        expiry = data.get('accessTokenExpiresAtUtc')
        if not isinstance(token, str) or not token or not isinstance(expiry, str):
            raise ValueError('keepIT login response is missing accessToken or accessTokenExpiresAtUtc')
        _token = token
        _token_expires_at = datetime.fromisoformat(expiry.replace('Z', '+00:00'))
        if _token_expires_at.tzinfo is None:
            _token_expires_at = _token_expires_at.replace(tzinfo=timezone.utc)
        return _token


async def api(method: str, path: str, *, payload: Any = None, params: Any = None,
              files: Any = None, binary: bool = False) -> Any:
    async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
        for attempt in range(2):
            token = await access_token(force_login=attempt == 1)
            try:
                response = await client.request(
                    method, f'{KEEPIT_URL}{path}',
                    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'},
                    json=payload, params=params, files=files,
                )
                if response.status_code == 401 and attempt == 0 and KEEPIT_EMAIL and KEEPIT_PASSWORD:
                    continue
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                raise ValueError(f'keepIT HTTP {exc.response.status_code}: {exc.response.text[:500]}') from None
            except httpx.RequestError as exc:
                raise ValueError(f'Failed to communicate with keepIT: {exc}') from None
        if binary:
            if len(response.content) > MAX_MEDIA_BYTES:
                raise ValueError('Downloaded file exceeds MAX_MEDIA_BYTES')
            return {'contentType': response.headers.get('content-type', 'application/octet-stream'),
                    'base64': base64.b64encode(response.content).decode('ascii')}
        if not response.content:
            return {'ok': True}
        if 'json' in response.headers.get('content-type', ''):
            return response.json()
        return {'ok': True, 'message': response.text[:500]}


@mcp.tool()
async def list_notes(list_ids: list[str] | None = None, archived: bool = False,
                     trashed: bool = False, reminders: bool = False) -> Any:
    """List notes with optional list, archive, trash, and reminder filters."""
    params: list[tuple[str, str]] = [('archived', str(archived).lower()),
                                     ('trashed', str(trashed).lower()),
                                     ('reminders', str(reminders).lower())]
    params.extend(('listId', uid(i)) for i in (list_ids or []))
    return await api('GET', '/api/notes', params=params)


@mcp.tool()
async def search_notes(query: str, archived: bool = False, trashed: bool = False,
                       list_ids: list[str] | None = None, limit: int = 50) -> Any:
    """Search note titles, bodies, and checklist items locally after fetching notes."""
    if not query.strip():
        raise ValueError('query must not be empty')
    if not 1 <= limit <= 500:
        raise ValueError('limit must be between 1 and 500')
    notes = await list_notes(list_ids=list_ids, archived=archived, trashed=trashed)
    needle = query.casefold()
    results = []
    for note in notes:
        fields = [note.get('title') or '', note.get('body') or '']
        fields += [i.get('text') or '' for i in note.get('checklistItems') or []]
        if any(needle in field.casefold() for field in fields):
            results.append(note)
    return {'total': len(results), 'notes': results[:limit], 'truncated': len(results) > limit}


@mcp.tool()
async def get_note(note_id: str) -> Any:
    """Retrieve a note by UUID."""
    return await api('GET', f'/api/notes/{uid(note_id)}')


@mcp.tool()
async def create_note(note_type: Literal['Text', 'Checklist', 'Image'] = 'Text',
                      title: str | None = None, body: str | None = None,
                      color: str | None = None, checklist_items: list[dict[str, Any]] | None = None,
                      list_ids: list[str] | None = None) -> Any:
    """Create a note. Checklist items require text, isChecked, and order; id is optional."""
    data: dict[str, Any] = {'type': note_type}
    for k, v in [('title', title), ('body', body), ('color', color),
                 ('checklistItems', checklist_items), ('listIds', list_ids)]:
        if v is not None:
            data[k] = v
    if list_ids is not None:
        data['listIds'] = [uid(i) for i in list_ids]
    return await api('POST', '/api/notes', payload=data)


@mcp.tool()
async def update_note(note_id: str, title: str | None = None, body: str | None = None,
                      color: str | None = None,
                      checklist_items: list[dict[str, Any]] | None = None,
                      note_type: Literal['Text', 'Checklist', 'Image'] | None = None) -> Any:
    """Update only provided fields using UpdateNoteDto.fields without overwriting unrelated fields."""
    changes = [('Title', 'title', title), ('Body', 'body', body),
               ('Color', 'color', color), ('ChecklistItems', 'checklistItems', checklist_items),
               ('Type', 'type', note_type)]
    provided = [(field, key, value) for field, key, value in changes if value is not None]
    if not provided:
        raise ValueError('Provide at least one field to update')
    existing = await get_note(note_id)
    data: dict[str, Any] = {'type': note_type or existing['type'],
                            'fields': [field for field, _, _ in provided]}
    data.update({key: value for _, key, value in provided})
    return await api('PUT', f'/api/notes/{uid(note_id)}', payload=data)

async def _update_checklist_items(
    note_id: str,
    note: dict[str, Any],
    items: list[dict[str, Any]],
) -> Any:
    """Update checklist items without changing unrelated note fields."""
    return await api(
        "PUT",
        f"/api/notes/{uid(note_id)}",
        payload={
            "type": note["type"],
            "fields": ["ChecklistItems"],
            "checklistItems": items,
        },
    )


@mcp.tool()
async def append_checklist_item(
    note_id: str,
    texts: str | list[str],
) -> Any:
    """Append one or more unchecked items to the end of a checklist."""
    if isinstance(texts, str):
        texts = [texts]

    if not texts:
        raise ValueError("At least one item is required")

    for text in texts:
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Each item must contain 1 to 2000 characters")

    note = await get_note(note_id)

    if note.get("type") != "Checklist":
        raise ValueError("The specified note is not a checklist")

    if not note.get("canEdit", False):
        raise ValueError("You do not have permission to edit this note")

    items = list(note.get("checklistItems") or [])

    if len(items) + len(texts) > 500:
        raise ValueError("Checklist cannot exceed 500 items")

    next_order = max(
        (item["order"] for item in items),
        default=-1,
    ) + 1

    for index, text in enumerate(texts):
        items.append({
            "text": text.strip(),
            "isChecked": False,
            "order": next_order + index,
        })

    await _update_checklist_items(note_id, note, items)

    #Lets return something smaller than the full list
    return {
        "added": texts,
        "count": len(texts),
    }


@mcp.tool()
async def remove_checklist_item_by_id(
    note_id: str,
    item_ids: str | list[str],
) -> Any:
    """Remove existing checklist items by UUID, ignoring missing IDs."""
    if isinstance(item_ids, str):
        item_ids = [item_ids]

    if not item_ids:
        raise ValueError("At least one item ID is required")

    target_ids = {uid(item_id) for item_id in item_ids}

    note = await get_note(note_id)

    if note.get("type") != "Checklist":
        raise ValueError("The specified note is not a checklist")

    if not note.get("canEdit", False):
        raise ValueError("You do not have permission to edit this note")

    items = list(note.get("checklistItems") or [])

    existing_ids = {
        uid(item["id"])
        for item in items
        if item.get("id")
    }

    found_ids = target_ids & existing_ids
    ignored_ids = target_ids - existing_ids

    if not found_ids:
        return {
            "removed": [],
            "ignored": sorted(ignored_ids),
            "message": "No matching checklist items found",
        }

    remaining = [
        item
        for item in items
        if not item.get("id") or uid(item["id"]) not in found_ids
    ]

    await _update_checklist_items(note_id, note, remaining)

    return {
        "removed": sorted(found_ids),
        "ignored": sorted(ignored_ids),
    }

@mcp.tool()
async def remove_checklist_item_by_name(
    note_id: str,
    texts: str | list[str],
) -> Any:
    """Remove checklist items by text, ignoring missing or ambiguous names."""
    if isinstance(texts, str):
        texts = [texts]

    if not texts:
        raise ValueError("At least one item name is required")

    targets = {
        text.strip().casefold(): text.strip()
        for text in texts
        if isinstance(text, str) and text.strip()
    }

    if not targets:
        raise ValueError("At least one valid item name is required")

    note = await get_note(note_id)

    if note.get("type") != "Checklist":
        raise ValueError("The specified note is not a checklist")

    if not note.get("canEdit", False):
        raise ValueError("You do not have permission to edit this note")

    items = list(note.get("checklistItems") or [])

    counts = {}
    for item in items:
        key = (item.get("text") or "").strip().casefold()
        counts[key] = counts.get(key, 0) + 1

    removable = {
        key for key in targets
        if counts.get(key, 0) == 1
    }

    ignored = [
        targets[key]
        for key in targets
        if counts.get(key, 0) == 0
    ]

    ambiguous = [
        targets[key]
        for key in targets
        if counts.get(key, 0) > 1
    ]

    if not removable:
        return {
            "removed": [],
            "ignored": ignored,
            "ambiguous": ambiguous,
            "message": "No unambiguous matching items found",
        }

    remaining = [
        item
        for item in items
        if (item.get("text") or "").strip().casefold()
        not in removable
    ]

    await _update_checklist_items(note_id, note, remaining)

    #Lets not return the full list
    return {
        "removed": [targets[key] for key in removable],
        "ignored": ignored,
        "ambiguous": ambiguous,
    }

@mcp.tool()
async def clear_checklist(note_id: str) -> Any:
    """Remove all items from a checklist without deleting the note."""
    note = await get_note(note_id)

    if note.get("type") != "Checklist":
        raise ValueError("The specified note is not a checklist")

    if not note.get("canEdit", False):
        raise ValueError("You do not have permission to edit this note")

    items = note.get("checklistItems") or []

    if not items:
        return {
            "cleared": True,
            "removed_count": 0,
        }

    await _update_checklist_items(note_id, note, [])

    return {
        "cleared": True,
        "removed_count": len(items),
    }

@mcp.tool()
async def delete_note(note_id: str, confirm: bool = False) -> Any:
    """Delete a note. Requires confirm=True; keepIT determines trash behavior."""
    if not confirm:
        raise ValueError('To delete the note, retry with confirm=True')
    return await api('DELETE', f'/api/notes/{uid(note_id)}')


@mcp.tool()
async def update_note_state(note_id: str, is_pinned: bool | None = None,
                            is_archived: bool | None = None,
                            is_trashed: bool | None = None) -> Any:
    """Pin, archive, trash, or restore a note."""
    changes = {'isPinned': is_pinned, 'isArchived': is_archived, 'isTrashed': is_trashed}
    data = {k: v for k, v in changes.items() if v is not None}
    if not data:
        raise ValueError('Provide at least one state field')
    return await api('PATCH', f'/api/notes/{uid(note_id)}/state', payload=data)


@mcp.tool()
async def set_note_reminder(note_id: str, remind_at_utc: str,
                            recurrence: Literal['None', 'Daily', 'Weekly', 'Monthly', 'Yearly'] = 'None',
                            time_zone: str | None = None, first_at_utc: str | None = None) -> Any:
    """Set a reminder using timezone-aware ISO 8601 timestamps."""
    data = {'remindAtUtc': iso_date(remind_at_utc), 'recurrence': recurrence}
    if time_zone is not None:
        data['timeZone'] = time_zone
    if first_at_utc is not None:
        data['firstAtUtc'] = iso_date(first_at_utc)
    return await api('PUT', f'/api/notes/{uid(note_id)}/reminder', payload=data)


@mcp.tool()
async def delete_note_reminder(note_id: str) -> Any:
    """Remove a note reminder."""
    return await api('DELETE', f'/api/notes/{uid(note_id)}/reminder')


@mcp.tool()
async def set_note_lists(note_id: str, list_ids: list[str]) -> Any:
    """Replace the lists associated with a note using the supplied UUIDs."""
    return await api('PUT', f'/api/notes/{uid(note_id)}/lists',
                     payload={'listIds': [uid(i) for i in list_ids]})


@mcp.tool()
async def empty_trash(note_ids: list[str], confirm: bool = False) -> Any:
    """Permanently delete the specified trashed notes. Requires confirm=True."""
    if not confirm:
        raise ValueError('Destructive operation: retry with confirm=True')
    if not note_ids:
        raise ValueError('Provide the UUIDs of the notes to delete')
    return await api('POST', '/api/notes/trash/empty',
                     payload={'noteIds': [uid(i) for i in note_ids]})


@mcp.tool()
async def list_lists() -> Any:
    """List keepIT lists."""
    return await api('GET', '/api/lists')


@mcp.tool()
async def create_list(name: str, color: str | None = None, icon: str | None = None) -> Any:
    """Create a list."""
    data = {'name': name}
    if color is not None:
        data['color'] = color
    if icon is not None:
        data['icon'] = icon
    return await api('POST', '/api/lists', payload=data)


@mcp.tool()
async def update_list(list_id: str, name: str | None = None,
                      color: str | None = None, icon: str | None = None) -> Any:
    """Update list properties."""
    data = {k: v for k, v in [('name', name), ('color', color), ('icon', icon)] if v is not None}
    if not data:
        raise ValueError('Provide at least one field')
    return await api('PATCH', f'/api/lists/{uid(list_id)}', payload=data)


@mcp.tool()
async def delete_list(list_id: str, confirm: bool = False) -> Any:
    """Delete a list, requiring explicit confirmation."""
    if not confirm:
        raise ValueError('To delete the list, retry with confirm=True')
    return await api('DELETE', f'/api/lists/{uid(list_id)}')


@mcp.tool()
async def list_note_shares(note_id: str) -> Any:
    """List shares for a note."""
    return await api('GET', f'/api/notes/{uid(note_id)}/shares')


@mcp.tool()
async def share_note(note_id: str, email: str,
                     role: Literal['Viewer', 'Editor'] = 'Viewer') -> Any:
    """Share a note by email with the Viewer or Editor role."""
    return await api('POST', f'/api/notes/{uid(note_id)}/shares',
                     payload={'email': email, 'role': role})


@mcp.tool()
async def update_note_share(note_id: str, grantee_id: str,
                            role: Literal['Viewer', 'Editor']) -> Any:
    """Change the role of a user who has access to a note."""
    return await api('PATCH', f'/api/notes/{uid(note_id)}/shares/{uid(grantee_id)}',
                     payload={'role': role})


@mcp.tool()
async def delete_note_share(note_id: str, grantee_id: str,
                            confirm: bool = False) -> Any:
    """Revoke a note share, requiring confirmation."""
    if not confirm:
        raise ValueError('To revoke the share, retry with confirm=True')
    return await api('DELETE', f'/api/notes/{uid(note_id)}/shares/{uid(grantee_id)}')


@mcp.tool()
async def upload_note_media(note_id: str, filename: str, content_base64: str,
                            content_type: str = 'application/octet-stream') -> Any:
    """Attach a base64-encoded file to a note; maximum size is configurable."""
    try:
        content = base64.b64decode(content_base64, validate=True)
    except (ValueError, base64.binascii.Error):
        raise ValueError('Invalid content_base64') from None
    if len(content) > MAX_MEDIA_BYTES:
        raise ValueError('Attachment exceeds MAX_MEDIA_BYTES')
    return await api('POST', f'/api/notes/{uid(note_id)}/media',
                     files={'file': (filename, content, content_type)})


@mcp.tool()
async def get_note_media(note_id: str, media_id: str, size: str | None = None) -> Any:
    """Download a note attachment as base64 (configurable size limit)."""
    params = {'size': size} if size else None
    return await api('GET', f'/api/notes/{uid(note_id)}/media/{uid(media_id)}',
                     params=params, binary=True)


@mcp.tool()
async def delete_note_media(note_id: str, media_id: str,
                            confirm: bool = False) -> Any:
    """Delete an attachment, requiring confirmation."""
    if not confirm:
        raise ValueError('To delete the attachment, retry with confirm=True')
    return await api('DELETE', f'/api/notes/{uid(note_id)}/media/{uid(media_id)}')


class BearerAuthASGI:
    """Require a shared MCP bearer key for every HTTP request."""
    def __init__(self, app: Any, key: str):
        self.app, self.key = app, key

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope['type'] == 'http':
            from hmac import compare_digest
            auth = Headers(scope=scope).get('authorization', '')
            if not compare_digest(auth, f'Bearer {self.key}'):
                response = JSONResponse({'error': 'Unauthorized'}, status_code=401,
                                        headers={'WWW-Authenticate': 'Bearer'})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


if __name__ == '__main__':
    if not MCP_API_KEY or not ((KEEPIT_EMAIL and KEEPIT_PASSWORD) or KEEPIT_TOKEN):
        raise SystemExit('Set MCP_API_KEY and KEEPIT_EMAIL/KEEPIT_PASSWORD (or KEEPIT_TOKEN)')
    import uvicorn
    uvicorn.run(BearerAuthASGI(mcp.streamable_http_app(), MCP_API_KEY),
                host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))
