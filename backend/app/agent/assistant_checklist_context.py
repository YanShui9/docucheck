"""Bounded checklist views; the complete authoritative definition stays server side."""

from typing import Literal

from pydantic import Field, model_validator

from ..criteria.models import Contract, Identifier


class PrepareChangesArgs(Contract):
    criterion_ids: list[Identifier] = Field(default_factory=list, max_length=4)
    item_ids: list[Identifier] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def unique_targets(self):
        if len(set(self.criterion_ids)) != len(self.criterion_ids) or len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("duplicate_edit_target")
        return self


class ReadChecklistArgs(Contract):
    scope: Literal["criteria", "items"] = "criteria"
    query: str = Field(default="", max_length=120)
    offset: int = Field(default=0, ge=0, le=100)
    limit: int = Field(default=8, ge=1, le=8)


def model_definition(definition):
    if definition is None:
        return None
    data = definition.model_dump(mode="json")
    data["criteria"] = [{k: v for k, v in criterion.items() if k != "source"} for criterion in data["criteria"]]
    return data


def catalog(definition, query):
    """Search scoped identifiers/names only. No rule or source is truncated."""
    data = model_definition(definition) or {"items": [], "criteria": []}
    phrase = query.query.strip().casefold()
    items = {i["item_id"]: i for i in data["items"]}
    matched_items = [i["item_id"] for i in data["items"] if not phrase or phrase in (i["item_id"] + " " + i["name"]).casefold()]
    matches = [c for c in data["criteria"] if not phrase or c["item_id"] in matched_items
               or phrase in (c["criterion_id"] + " " + c["name"]).casefold()]
    selected = matches[query.offset:query.offset + query.limit] if query.scope == "criteria" else []
    selected_items = (list(dict.fromkeys(c["item_id"] for c in selected)) or matched_items[:query.limit]
                      if query.scope == "criteria" else matched_items[query.offset:query.offset + query.limit])
    match_count = len(matches) if query.scope == "criteria" else len(matched_items)
    end = query.offset + (len(selected) if query.scope == "criteria" else len(selected_items))
    return {"view": "catalog", "complete": False, "criterion_count": len(data["criteria"]),
            "scope": query.scope, "query": query.query, "limit": query.limit,
            "match_count": match_count, "offset": query.offset,
            "next_offset": end if end < match_count else None,
            "item_count": len(items), "items_complete": len(selected_items) == len(items),
            "items": [{"item_id": key, "name": items[key]["name"]} for key in selected_items],
            "criteria": [{k: c[k] for k in ("criterion_id", "item_id", "name", "required", "enabled")} for c in selected]}


def edit_view(definition, selection):
    data = model_definition(definition) or {"items": [], "criteria": []}
    criteria = {c["criterion_id"]: c for c in data["criteria"]}
    items = {i["item_id"]: i for i in data["items"]}
    if not set(selection.criterion_ids) <= criteria.keys() or not set(selection.item_ids) <= items.keys():
        raise ValueError("checklist_edit_target")
    chosen = [criteria[key] for key in selection.criterion_ids]
    item_ids = list(dict.fromkeys([*selection.item_ids, *(c["item_id"] for c in chosen)]))
    if not item_ids:
        item_ids = list(items)[:8]
    return {"view": "edit_targets", "complete": False, "criterion_count": len(criteria),
            "items": [{"item_id": key, "name": items[key]["name"]} for key in item_ids],
            "criteria": chosen}
