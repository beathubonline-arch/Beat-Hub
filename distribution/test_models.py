from datetime import date
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from distribution.models import DistributionBase, DistributionRelease, DistributionTrack

def test_distribution_records_isolated():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    DistributionBase.metadata.create_all(engine)
    with Session(engine) as session:
        release = DistributionRelease(creator_id="creator-a", title="New Dawn", artist="Artist A",
            release_type="single", artwork_key="private/cover.png", release_date=date(2026,12,1),
            rights_confirmed=True, tracks=[DistributionTrack(position=1,title="Song",audio_key="private/master.wav",writers="Writer A")])
        session.add(release)
        session.commit()
        own = session.scalars(select(DistributionRelease).where(DistributionRelease.creator_id=="creator-a")).all()
        other = session.scalars(select(DistributionRelease).where(DistributionRelease.creator_id=="creator-b")).all()
        assert len(own)==1 and len(own[0].tracks)==1
        assert other==[]
