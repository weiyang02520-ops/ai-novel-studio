"""Revision-protected canonical AI draft transactions (never confirmed chapters)."""
from __future__ import annotations
import dataclasses, datetime
from typing import Callable, Any
from .chapter import build_frontmatter, count_words, draft_path, confirmed_path, parse_frontmatter
from .history import prepare_snapshot
from .mutation import file_revision
from .project import Project
from .storage import atomic_write_text
from .locks import chapter_lock

VALID_MODES={"new","rewrite","continue","resume"}; VALID_STATES={"complete","truncated"}
class AIDraftError(Exception):
    def __init__(self,code,message): self.code=code; super().__init__(f"{code}: {message}")
@dataclasses.dataclass(frozen=True)
class AIDraftResult:
    chapter:int; path:str; revision:str; words:int; state:str; mode:str; history_seq:int
def _now(): return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class AIChapterDraftService:
    def __init__(self,project:Project,*,snapshot_factory:Callable[...,Any]=prepare_snapshot,writer:Callable[...,None]=atomic_write_text):
        self.project=project; self.snapshot_factory=snapshot_factory; self.writer=writer
    def finalize(self,*,chapter:int,title:str,body:str,mode:str,generation_state:str,model:str,
                 context_hash:str,task_hash:str,expected_revision:str,characters:list[str]|None=None)->AIDraftResult:
        with chapter_lock(self.project, chapter):
            return self._finalize_locked(chapter=chapter,title=title,body=body,mode=mode,
                generation_state=generation_state,model=model,context_hash=context_hash,
                task_hash=task_hash,expected_revision=expected_revision,characters=characters)
    def _finalize_locked(self,*,chapter:int,title:str,body:str,mode:str,generation_state:str,model:str,
                 context_hash:str,task_hash:str,expected_revision:str,characters:list[str]|None=None)->AIDraftResult:
        if mode not in VALID_MODES or generation_state not in VALID_STATES: raise AIDraftError("INVALID_GENERATION","mode/state")
        if confirmed_path(self.project,chapter).exists(): raise AIDraftError("CONFIRMED_PROTECTED","confirmed exists")
        target=draft_path(self.project,chapter); current=file_revision(target)
        if current!=expected_revision: raise AIDraftError("STALE_DRAFT_REVISION",f"expected={expected_revision} current={current}")
        old_meta={}
        if target.exists():
            old_meta,_=parse_frontmatter(target.read_text(encoding="utf-8"))
            if old_meta.get("origin")!="ai": raise AIDraftError("MANUAL_DRAFT_PROTECTED","manual draft")
            if old_meta.get("status") != "draft": raise AIDraftError("AI_DRAFT_INVALID_STATUS",str(old_meta.get("status")))
            if mode == "new": raise AIDraftError("AI_DRAFT_EXISTS","new cannot overwrite an AI draft")
        elif mode not in ("new","resume"): raise AIDraftError("AI_DRAFT_NOT_FOUND",mode)
        if not body.strip(): raise AIDraftError("EMPTY_WRITER_OUTPUT","empty")
        meta={"chapter":chapter,"volume":int(self.project.metadata.get("current_volume",1)),"title":title,
              "status":"draft","origin":"ai","words":count_words(body),"created_at":old_meta.get("created_at") or _now(),
              "updated_at":_now(),"characters":list(characters or []),"generation_state":generation_state,
              "generation_mode":mode,"generation_model":model,"context_hash":context_hash,"task_hash":task_hash}
        rendered=build_frontmatter(meta)+body; rel=f"drafts/{target.name}"
        op={"new":"ai.draft.create","rewrite":"ai.draft.rewrite","continue":"ai.draft.continue","resume":"ai.draft.resume"}[mode]
        metadata={"agent_id":"writer","content_kind":"ai_draft","old_revision":current,"context_hash":context_hash,
                  "task_hash":task_hash,"generation_mode":mode}
        try: snap=self.snapshot_factory(self.project,op,[rel],metadata=metadata)
        except Exception as e: raise AIDraftError("DRAFT_SNAPSHOT_FAILED","prepare failed") from e
        if file_revision(target)!=current:
            snap.discard(); raise AIDraftError("STALE_DRAFT_REVISION","target changed during snapshot")
        before=target.read_bytes() if target.exists() else b""
        try:
            self.writer(target,rendered)
            if target.read_bytes()!=rendered.encode(): raise AIDraftError("DRAFT_VERIFY_FAILED","verify")
            snap.commit()
        except Exception as e:
            try:
                snap.restore(); restored=target.read_bytes() if target.exists() else b""
                if restored!=before: raise RuntimeError("rollback verify")
                snap.discard()
            except Exception as rb: raise AIDraftError("DRAFT_ROLLBACK_FAILED","rollback incomplete") from rb
            raise AIDraftError(getattr(e,"code","DRAFT_WRITE_FAILED"),"write failed; restored") from e
        return AIDraftResult(chapter,rel,file_revision(target),meta["words"],generation_state,mode,snap.seq)
