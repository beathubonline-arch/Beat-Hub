from datetime import date
import pytest
from distribution.release_workflow import Release, Track, Status, submit_for_review

def draft():
    return Release("creator-a", "New Dawn", "Artist A", "single", "private/cover.png",
                   date(2026, 12, 1), True, [Track("Song", "private/master.wav", ["Writer A"])])

def test_owner_can_submit():
    release = draft()
    assert submit_for_review(release, "creator-a").status == Status.IN_REVIEW

@pytest.mark.parametrize("actor", ["creator-b", "creator-c"])
def test_other_creators_blocked_twice(actor):
    with pytest.raises(PermissionError):
        submit_for_review(draft(), actor)

def test_rights_required():
    release = draft()
    release.rights_confirmed = False
    with pytest.raises(ValueError, match="rights"):
        submit_for_review(release, "creator-a")

def test_track_metadata_required():
    release = draft()
    release.tracks[0].writers = []
    with pytest.raises(ValueError, match="writer"):
        submit_for_review(release, "creator-a")

def test_no_live_status():
    assert "LIVE" not in [s.value for s in Status]
