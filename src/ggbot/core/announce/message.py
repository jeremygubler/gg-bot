"""Eine Ankündigung: Text und ein Embed, mit den Grenzen von Discord. Ohne Discord."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ggbot.core.errors import UserError

MAX_CONTENT = 2000
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELDS = 25
MAX_FIELD_NAME = 256
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048
MAX_AUTHOR = 256
MAX_URL = 2000
# Discord zählt Titel, Beschreibung, Felder, Fuss und Autor zusammen.
MAX_TOTAL = 6000
MAX_COLOR = 0xFFFFFF
MAX_NAME = 100


def _blank_to_none(value: object) -> object:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


class EmbedField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=MAX_FIELD_NAME)
    value: str = Field(min_length=1, max_length=MAX_FIELD_VALUE)
    inline: bool = False


class Embed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=MAX_TITLE)
    description: str | None = Field(default=None, max_length=MAX_DESCRIPTION)
    color: int | None = Field(default=None, ge=0, le=MAX_COLOR)
    author: str | None = Field(default=None, max_length=MAX_AUTHOR)
    footer: str | None = Field(default=None, max_length=MAX_FOOTER)
    image_url: str | None = Field(default=None, max_length=MAX_URL)
    thumbnail_url: str | None = Field(default=None, max_length=MAX_URL)
    fields: list[EmbedField] = Field(default_factory=list, max_length=MAX_FIELDS)

    @field_validator("title", "description", "author", "footer", "image_url", "thumbnail_url")
    @classmethod
    def _strip(cls, value: object) -> object:
        return _blank_to_none(value)

    @property
    def is_empty(self) -> bool:
        return not (
            self.title
            or self.description
            or self.author
            or self.footer
            or self.image_url
            or self.thumbnail_url
            or self.fields
        )

    @property
    def size(self) -> int:
        parts = [self.title, self.description, self.author, self.footer]
        total = sum(len(p) for p in parts if p)
        return total + sum(len(f.name) + len(f.value) for f in self.fields)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=MAX_CONTENT)
    embed: Embed | None = None

    @field_validator("content")
    @classmethod
    def _strip(cls, value: object) -> object:
        return _blank_to_none(value)


def _check_url(field: str, value: str | None) -> None:
    """Nur https: andere Schemata lehnt Discord ab und sie gehören in keine Vorschau."""
    if value is None:
        return
    if not value.startswith("https://") or any(ch.isspace() for ch in value) or len(value) < 12:
        raise UserError("error.announce_url", field=field)


def normalize(message: Message) -> Message:
    """Prüft die Regeln über mehrere Felder und liefert die bereinigte Nachricht.

    Ein leeres Embed wird zu `None`, damit „nichts eingetragen“ nie als leere Nachricht rausgeht.
    """
    embed = message.embed
    if embed is not None and embed.is_empty:
        embed = None
    if embed is not None:
        _check_url("image_url", embed.image_url)
        _check_url("thumbnail_url", embed.thumbnail_url)
        if embed.size > MAX_TOTAL:
            raise UserError("error.announce_too_long", limit=MAX_TOTAL)
    if message.content is None and embed is None:
        raise UserError("error.announce_empty")
    return Message(content=message.content, embed=embed)


def embed_dict(embed: Embed) -> dict[str, Any]:
    """Form für `discord.Embed.from_dict`."""
    data: dict[str, Any] = {"type": "rich"}
    if embed.title:
        data["title"] = embed.title
    if embed.description:
        data["description"] = embed.description
    if embed.color is not None:
        data["color"] = embed.color
    if embed.author:
        data["author"] = {"name": embed.author}
    if embed.footer:
        data["footer"] = {"text": embed.footer}
    if embed.image_url:
        data["image"] = {"url": embed.image_url}
    if embed.thumbnail_url:
        data["thumbnail"] = {"url": embed.thumbnail_url}
    if embed.fields:
        data["fields"] = [
            {"name": f.name, "value": f.value, "inline": f.inline} for f in embed.fields
        ]
    return data
