-- Backend-only foundation; does not change marketplace tables or webhook behavior.
create table public.mkulima_interactions (
 id uuid primary key default gen_random_uuid(),
 message_id text not null unique check (length(message_id) between 1 and 512),
 actor_ref text not null check (length(actor_ref) between 32 and 128),
 question text not null check (length(question) between 1 and 8000),
 recommendation text not null check (length(recommendation) between 1 and 8000),
 crop text, location_context text, language text not null,
 problem text, confidence numeric check (confidence between 0 and 1),
 answer_version text not null,
 created_at timestamptz not null default now()
);
create index mkulima_interactions_actor_time on public.mkulima_interactions(actor_ref,created_at desc);
create table public.mkulima_feedback (
 id uuid primary key default gen_random_uuid(),
 message_id text not null unique,
 interaction_id uuid not null references public.mkulima_interactions(id) on delete cascade,
 rating text not null check (rating in ('helpful','wrong','still_problem')),
 outcome text check (length(outcome)<=8000),
 created_at timestamptz not null default now()
);
create index mkulima_feedback_interaction on public.mkulima_feedback(interaction_id,created_at desc);
create table public.mkulima_knowledge (
 id uuid primary key default gen_random_uuid(),
 interaction_id uuid not null references public.mkulima_interactions(id) on delete cascade,
 crop text not null check (length(trim(crop))>0),
 region text not null check (length(trim(region))>0),
 language text not null check (length(trim(language))>0),
 problem text not null check (length(trim(problem))>0),
 recommendation text not null check (length(trim(recommendation))>0),
 outcome_summary text not null check (length(trim(outcome_summary))>0),
 confidence numeric not null check (confidence between 0 and 1),
 status text not null default 'pending' check (status in ('pending','validated','needs_review','rejected','revoked')),
 reviewer text,
 evidence text,
 reviewed_at timestamptz,
 valid_until timestamptz,
 deidentified boolean not null default false,
 created_at timestamptz not null default now(),
 constraint mkulima_validated_evidence check (status <> 'validated' or
   (reviewer is not null and length(trim(reviewer))>0 and evidence is not null and length(trim(evidence))>0
    and reviewed_at is not null and valid_until is not null and valid_until > reviewed_at and deidentified))
);
create index mkulima_knowledge_lookup on public.mkulima_knowledge(crop,region,language,problem) where status='validated';
create index mkulima_knowledge_interaction on public.mkulima_knowledge(interaction_id);
alter table public.mkulima_interactions enable row level security;
alter table public.mkulima_feedback enable row level security;
alter table public.mkulima_knowledge enable row level security;
revoke all on public.mkulima_interactions,public.mkulima_feedback,public.mkulima_knowledge from public,anon,authenticated;
grant select,insert,update,delete on public.mkulima_interactions,public.mkulima_feedback,public.mkulima_knowledge to service_role;

-- A transaction records a vote once and immediately pauses any affected knowledge.
create function public.mkulima_record_feedback(p_message_id text,p_interaction_id uuid,p_actor_ref text,p_rating text,p_outcome text default null)
returns uuid language plpgsql security invoker set search_path='' as $$
declare v_id uuid; v_existing public.mkulima_feedback%rowtype;
begin
 perform 1 from public.mkulima_interactions where id=p_interaction_id and actor_ref=p_actor_ref for update;
 if not found then raise exception 'interaction_not_found' using errcode='42501'; end if;
 select * into v_existing from public.mkulima_feedback where message_id=p_message_id;
 if found then
   if v_existing.interaction_id<>p_interaction_id or v_existing.rating<>p_rating or v_existing.outcome is distinct from nullif(trim(p_outcome),'') then
     raise exception 'feedback_id_conflict' using errcode='22023';
   end if;
   return v_existing.id;
 end if;
 insert into public.mkulima_feedback(message_id,interaction_id,rating,outcome)
 values(p_message_id,p_interaction_id,p_rating,nullif(trim(p_outcome),'')) returning id into v_id;
 if p_rating in ('wrong','still_problem') then
   update public.mkulima_knowledge set status='needs_review' where interaction_id=p_interaction_id and status='validated';
 end if;
 return v_id;
end $$;
revoke all on function public.mkulima_record_feedback(text,uuid,text,text,text) from public,anon,authenticated;
grant execute on function public.mkulima_record_feedback(text,uuid,text,text,text) to service_role;

-- Only current reviewed knowledge can be retrieved. No raw question or actor data leaves this function.
create function public.mkulima_retrieve_knowledge(p_crop text,p_region text,p_language text,p_problem text)
returns table(id uuid,recommendation text,outcome_summary text,confidence numeric,evidence text,reviewed_at timestamptz)
language sql stable security invoker set search_path='' as $$
 select k.id,k.recommendation,k.outcome_summary,k.confidence,k.evidence,k.reviewed_at
 from public.mkulima_knowledge k
 where k.status='validated' and k.valid_until>now() and k.deidentified
 and k.crop=lower(trim(p_crop)) and k.region=lower(trim(p_region))
 and k.language=lower(trim(p_language)) and k.problem=lower(trim(p_problem))
 order by k.reviewed_at desc limit 5
$$;
revoke all on function public.mkulima_retrieve_knowledge(text,text,text,text) from public,anon,authenticated;
grant execute on function public.mkulima_retrieve_knowledge(text,text,text,text) to service_role;

-- Review is a trusted operator action, never called automatically from farmer votes.
create function public.mkulima_review_knowledge(p_id uuid,p_reviewer text,p_evidence text,p_valid_until timestamptz,p_deidentified boolean)
returns void language plpgsql security invoker set search_path='' as $$
declare v_interaction uuid;
begin
 select interaction_id into v_interaction from public.mkulima_knowledge where id=p_id;
 if not found then raise exception 'knowledge_not_found'; end if;
 -- Same lock order as feedback, so a new negative vote cannot race past this review.
 perform 1 from public.mkulima_interactions where id=v_interaction for update;
 if not exists(select 1 from public.mkulima_feedback where interaction_id=v_interaction and length(trim(outcome))>0) then
   raise exception 'actual_outcome_required';
 end if;
 if p_valid_until is null or p_valid_until<=now() then raise exception 'future_review_date_required'; end if;
 update public.mkulima_knowledge set status='validated',reviewer=p_reviewer,evidence=p_evidence,
 reviewed_at=now(),valid_until=p_valid_until,deidentified=p_deidentified where id=p_id;
end $$;
revoke all on function public.mkulima_review_knowledge(uuid,text,text,timestamptz,boolean) from public,anon,authenticated;
grant execute on function public.mkulima_review_knowledge(uuid,text,text,timestamptz,boolean) to service_role;
