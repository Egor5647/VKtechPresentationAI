from __future__ import annotations
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from sqlalchemy import create_engine, String, Text, Float, Integer, select, update, or_, and_, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .settings import data_dir,artifact_path


class Base(DeclarativeBase):pass


class Record(Base):
    __tablename__='records'
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    kind:Mapped[str]=mapped_column(String(20))
    name:Mapped[str]=mapped_column(Text)
    path:Mapped[str]=mapped_column(Text)
    document:Mapped[str]=mapped_column(Text)
    created:Mapped[float]=mapped_column(Float,default=time.time)


class Job(Base):
    __tablename__='jobs'
    __table_args__=(UniqueConstraint('dedup_key'),)
    id:Mapped[str]=mapped_column(String(64),primary_key=True)
    kind:Mapped[str]=mapped_column(String(20),default='generate')
    dedup_key:Mapped[str|None]=mapped_column(String(200),nullable=True)
    state:Mapped[str]=mapped_column(String(30),default='queued',index=True)
    stage:Mapped[str]=mapped_column(String(40),default='queued')
    payload:Mapped[str]=mapped_column(Text)
    result:Mapped[str]=mapped_column(Text,default='{}')
    error:Mapped[str]=mapped_column(Text,default='')
    created:Mapped[float]=mapped_column(Float,default=time.time)
    updated:Mapped[float]=mapped_column(Float,default=time.time)
    lease_owner:Mapped[str|None]=mapped_column(String(64),nullable=True)
    lease_until:Mapped[float]=mapped_column(Float,default=0)
    attempts:Mapped[int]=mapped_column(Integer,default=0)


class Store:
    def __init__(self,url=None):
        url=url or os.environ.get('DATABASE_URL')
        if not url:raise RuntimeError('DATABASE_URL must be configured; PostgreSQL is the production database')
        self.engine=create_engine(url,pool_pre_ping=True,connect_args={'check_same_thread':False} if url.startswith('sqlite') else {})
        self.sessions=sessionmaker(self.engine,expire_on_commit=False)
        if self.engine.dialect.name=='postgresql':
            with self.engine.begin() as connection:
                connection.execute(text('SELECT pg_advisory_xact_lock(73619243)'))
                Base.metadata.create_all(connection)
        else:
            import fcntl
            with (data_dir()/'schema.lock').open('a') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX)
                Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self):
        with self.sessions.begin() as s:yield s

    def save_record(self,kind,name,data,document,extension,version=''):
        digest=hashlib.sha256(data).hexdigest();identity=hashlib.sha256(version.encode()+b'\0'+data).hexdigest() if version else digest;rid=kind+'-'+identity[:24]
        rel=f'blobs/{digest}.{extension}'
        path=artifact_path(rel);path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp');temp.write_bytes(data);temp.replace(path)
        with self.session() as s:
            if s.get(Record,rid) is None:s.add(Record(id=rid,kind=kind,name=name,path=rel,document=json.dumps(document,ensure_ascii=False)))
        return rid

    def record(self,rid,kind=None):
        with self.session() as s:
            r=s.get(Record,rid)
            if r is None or kind and r.kind!=kind:raise KeyError(rid)
            return r

    def records(self,kind):
        with self.session() as s:return list(s.scalars(select(Record).where(Record.kind==kind).order_by(Record.created.desc())))

    def enqueue(self,kind,payload,dedup_key=None):
        with self.session() as s:
            if dedup_key:
                old=s.scalar(select(Job).where(Job.dedup_key==dedup_key))
                if old:
                    if old.payload!=json.dumps(payload,sort_keys=True):raise ValueError('Idempotency key already used with different payload')
                    return old.id
            jid=uuid.uuid4().hex;s.add(Job(id=jid,kind=kind,payload=json.dumps(payload,sort_keys=True),dedup_key=dedup_key));return jid

    def job(self,jid):
        with self.session() as s:
            j=s.get(Job,jid)
            if j is None:raise KeyError(jid)
            return j

    def claim(self):
        now=time.time();token=uuid.uuid4().hex
        with self.session() as s:
            query=select(Job).where(or_(Job.state=='queued',and_(Job.state=='running',Job.lease_until<now))).order_by(Job.created).limit(1)
            if self.engine.dialect.name=='postgresql':query=query.with_for_update(skip_locked=True)
            job=s.scalar(query)
            if not job:return None
            # CAS also protects the SQLite development runner from double claims.
            count=s.execute(update(Job).where(Job.id==job.id,or_(Job.state=='queued',and_(Job.state=='running',Job.lease_until<now))).values(state='running',stage='starting',lease_owner=token,lease_until=now+30,attempts=Job.attempts+1,updated=now)).rowcount
            if not count:return None
            s.refresh(job);return job

    def owned_update(self,jid,owner,**values):
        values['updated']=time.time()
        with self.session() as s:
            count=s.execute(update(Job).where(Job.id==jid,Job.lease_owner==owner,Job.state=='running').values(**values)).rowcount
            if not count:raise RuntimeError('Job cancelled or worker lease lost')

    def cancel(self,jid):
        with self.session() as s:
            j=s.get(Job,jid)
            if j is None:raise KeyError(jid)
            if j.state in {'queued','running','awaiting_input'}:j.state='cancelled';j.updated=time.time()

    def retry(self,jid):
        with self.session() as s:
            j=s.get(Job,jid)
            if j is None:raise KeyError(jid)
            if j.state not in {'failed','awaiting_input'}:raise ValueError('Only failed or awaiting_input jobs can be retried')
            j.state='queued';j.stage='queued';j.error='';j.lease_owner=None;j.lease_until=0;j.updated=time.time()


def job_document(job):
    return {'id':job.id,'kind':job.kind,'state':job.state,'stage':job.stage,'error':job.error,'created':job.created,'updated':job.updated,'attempts':job.attempts,'result':json.loads(job.result)}
