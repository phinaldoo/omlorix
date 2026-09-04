"""SQL access predicates and bounded keyset pages for workspace read models."""

import base64
import binascii
from datetime import datetime
import hashlib
import json

from sqlalchemy import and_, case, or_, select


def shared_access(model, subscription, item_column, user_id):
    """Start from indexed user/subscriber IDs; duplicate grants yield one row."""
    active = or_(
        and_(
            subscription.share_type == "live",
            model.live_share_id.isnot(None),
            model.live_share_id != "",
        ),
        and_(
            subscription.share_type == "collaborate",
            model.collaborate_share_id.isnot(None),
            model.collaborate_share_id != "",
        ),
    )
    subscriptions = (
        select(subscription.share_type)
        .where(
            item_column == model.id,
            subscription.subscriber_id == user_id,
            active,
        )
        .correlate(model)
    )
    owned_ids = select(model.id).where(model.user_id == user_id).correlate(None)
    shared_ids = (
        select(item_column)
        .select_from(subscription)
        .join(model, item_column == model.id)
        .where(subscription.subscriber_id == user_id, active)
        .correlate(None)
    )
    access = model.id.in_(owned_ids.union(shared_ids))
    share_type = case(
        (model.user_id == user_id, None),
        else_=subscriptions.order_by(subscription.share_type.asc())
        .limit(1)
        .scalar_subquery(),
    ).label("share_type")
    return access, share_type


def text_pattern(value):
    text = str(value or "").strip()
    return (
        "%" + text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )


def keyset_page(query, *, order, scope, limit=20, offset=0, cursor=None):
    """Select only a page plus lookahead; cursors are data, never authority.

    ``order`` contains (SQL expression, selected column name, descending).
    Include a unique final key and use non-null expressions. The scope binds
    cursors to the current user, collection, and filters; access is rechecked.
    """
    limit, offset = int(limit), int(offset or 0)
    if not 1 <= limit <= 200 or not 0 <= offset <= 10000:
        raise ValueError("invalid_page_bounds")
    scope_key = hashlib.sha256(
        json.dumps(scope, sort_keys=True, default=str).encode()
    ).hexdigest()[:24]
    if cursor:
        try:
            if not isinstance(cursor, str) or len(cursor) > 4096 or offset:
                raise ValueError()
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            )
            if payload["scope"] != scope_key or len(payload["keys"]) != len(order):
                raise ValueError()
            values = [
                datetime.fromisoformat(value["datetime"])
                if isinstance(value, dict)
                else value
                for value in payload["keys"]
            ]
            for value, (column, _, _) in zip(values, order):
                expected_type = column.type.python_type
                if isinstance(value, bool) or not isinstance(value, expected_type):
                    raise ValueError()
            clauses = []
            for index, (column, _, descending) in enumerate(order):
                clauses.append(
                    and_(
                        *(
                            previous[0] == values[i]
                            for i, previous in enumerate(order[:index])
                        ),
                        column < values[index]
                        if descending
                        else column > values[index],
                    )
                )
            query = query.filter(or_(*clauses))
        except (TypeError, ValueError, KeyError, UnicodeError, binascii.Error) as exc:
            raise ValueError("invalid_page_cursor") from exc
    query = query.order_by(None).order_by(
        *(
            column.desc() if descending else column.asc()
            for column, _, descending in order
        )
    )
    rows = query.offset(offset).limit(limit + 1).all()
    more = len(rows) > limit
    rows = [dict(row._mapping) for row in rows[:limit]]
    next_cursor = None
    if more and rows:
        keys = [rows[-1][name] for _, name, _ in order]
        keys = [
            {"datetime": value.isoformat()} if isinstance(value, datetime) else value
            for value in keys
        ]
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {"scope": scope_key, "keys": keys}, separators=(",", ":")
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
    return rows, {
        "limit": limit,
        "offset": offset,
        "count": len(rows),
        "has_more": more,
        "next_cursor": next_cursor,
    }


from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy import Integer


class json_array_size(FunctionElement):
    """Count optional JSON arrays, including legacy SQL/JSON null values."""

    type = Integer()
    inherit_cache = True


@compiles(json_array_size)
def _json_array_size(element, compiler, **kwargs):
    value = compiler.process(list(element.clauses)[0], **kwargs)
    return f"CASE WHEN json_typeof({value}) = 'array' THEN json_array_length({value}) ELSE 0 END"


@compiles(json_array_size, "sqlite")
def _sqlite_json_array_size(element, compiler, **kwargs):
    value = compiler.process(list(element.clauses)[0], **kwargs)
    return f"CASE WHEN json_type({value}) = 'array' THEN json_array_length({value}) ELSE 0 END"
