"""The metadata manager: keeps the files' tags, their place on disk and the
Plex music section in step with one desired description of every album.

    files  <->  desired (MusicBrainz + house rules + your overrides)  <->  Plex

Each album folder ("unit") is checked on its own: identify the release,
build the desired record, compare it with the files and with Plex, and turn
every disagreement into an issue. Issues are applied — automatically once
auto-apply is on, otherwise by hand — through executors that journal every
change so it can be undone.
"""
