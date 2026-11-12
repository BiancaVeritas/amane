"""Assemble PlaybackQuery from repository rows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..db.models import MediaFile, Metadata
from ..plugins.api import PlaybackMediaFile, PlaybackQuery


def playback_query(
    metadata: Metadata,
    files: list[MediaFile],
    *,
    selected_key: str | None = None,
    library_paths: Mapping[int, Sequence[str] | str] | None = None,
) -> PlaybackQuery:
    if metadata.id is None:
        raise ValueError("metadata id is required")
    paths = library_paths or {}
    snapshots: list[PlaybackMediaFile] = []

    def root_for(item: MediaFile) -> str | None:
        configured = paths.get(item.library_id)
        roots = [configured] if isinstance(configured, str) else configured or []
        # Prefer the longest matching root so nested scan roots resolve deterministically.
        matches = [
            root
            for root in roots
            if item.path == root or item.path.startswith(root.rstrip("/\\") + ("\\" if "\\" in root else "/"))
        ]
        return max(matches, key=len) if matches else (roots[0] if len(roots) == 1 else None)

    for item in files:
        if item.id is None:
            continue
        snapshots.append(
            PlaybackMediaFile(
                id=item.id,
                path=item.path,
                size=item.size,
                content_type=metadata.content_type,
                mosaic=item.mosaic,
                has_subtitle=item.has_subtitle,
                definition=item.definition,
                library_id=item.library_id,
                library_path=root_for(item),
            )
        )
    return PlaybackQuery(
        metadata_id=metadata.id,
        number=metadata.number,
        title=metadata.title,
        actors=list(metadata.actors),
        studio=metadata.studio,
        publisher=metadata.publisher,
        release=metadata.release,
        runtime=metadata.runtime,
        tags=list(metadata.tags),
        series=metadata.series,
        series_names=list(metadata.series_names or ([metadata.series] if metadata.series else [])),
        plot=metadata.plot,
        directors=list(metadata.directors),
        source_urls=dict(metadata.source_urls),
        external_ids=dict(metadata.external_ids),
        files=tuple(snapshots),
        selected_key=selected_key,
    )
