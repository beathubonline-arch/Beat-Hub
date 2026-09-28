-- Follow-on migration: durable state and delivery leases for the opt-in webhook.
create table public.mkulima_conversations (
 actor_ref text primary key,
 state jsonb not null default '{}',
 latest_interaction uuid references public.mkulima_interactions(id) on delete set null
);
create table public.mkulima_message_jobs (
 message_id text primary key,
 actor_ref text not null references public.mkulima_conversations(actor_ref) on delete cascade,
 request_hash text not null,
 lease_token uuid not null,
 lease_until timestamptz not null,
 sent boolean not null default false,
 response text,
 next_state jsonb,
 interaction_id uuid references public.mkulima_interactions(id) on delete set null,
 outbound_id text unique,
 created_at timestamptz not null default now()
);
create index mkulima_message_jobs_actor on public.mkulima_message_jobs(actor_ref);
alter table public.mkulima_conversations enable row level security;
alter table public.mkulima_message_jobs enable row level security;
revoke all on public.mkulima_conversations,public.mkulima_message_jobs from public,anon,authenticated;
grant select,insert,update,delete on public.mkulima_conversations,public.mkulima_message_jobs to service_role;

create function public.mkulima_claim_message(p_id text,p_actor text,p_hash text)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare j public.mkulima_message_jobs%rowtype; c public.mkulima_conversations%rowtype; token uuid:=gen_random_uuid();
begin
 insert into public.mkulima_conversations(actor_ref) values(p_actor) on conflict do nothing;
 select * into c from public.mkulima_conversations where actor_ref=p_actor for update;
 select * into j from public.mkulima_message_jobs where message_id=p_id;
 if found then
   if j.actor_ref<>p_actor or j.request_hash<>p_hash then raise exception 'message_id_conflict'; end if;
   if j.sent then return jsonb_build_object('status','sent'); end if;
   if j.lease_until>now() then return jsonb_build_object('status','busy'); end if;
 else
   if exists(select 1 from public.mkulima_message_jobs where actor_ref=p_actor and not sent) then
     return jsonb_build_object('status','busy');
   end if;
   insert into public.mkulima_message_jobs(message_id,actor_ref,request_hash,lease_token,lease_until)
   values(p_id,p_actor,p_hash,token,now()+interval '5 minutes') returning * into j;
 end if;
 update public.mkulima_message_jobs set lease_token=token,lease_until=now()+interval '5 minutes' where message_id=p_id;
 return jsonb_build_object('status','claimed','token',token,'state',c.state,
   'latest_interaction',c.latest_interaction,'response',j.response);
end $$;

create function public.mkulima_stage_message(p_id text,p_token uuid,p_response text,p_state jsonb,p_interaction jsonb default null)
returns void language plpgsql security invoker set search_path='' as $$
declare j public.mkulima_message_jobs%rowtype; i uuid;
begin
 select * into j from public.mkulima_message_jobs where message_id=p_id and lease_token=p_token and lease_until>now() and not sent for update;
 if not found then raise exception 'lease_lost'; end if;
 if j.response is not null then return; end if;
 if p_interaction is not null then
   insert into public.mkulima_interactions(message_id,actor_ref,question,recommendation,language,answer_version,crop,location_context,problem)
   values(p_id,j.actor_ref,p_interaction->>'question',p_interaction->>'recommendation',p_interaction->>'language',
          p_interaction->>'answer_version',p_interaction->>'crop',p_interaction->>'location',p_interaction->>'problem') returning id into i;
 end if;
 update public.mkulima_message_jobs set response=p_response,next_state=p_state,interaction_id=i where message_id=p_id;
end $$;

create function public.mkulima_finish_message(p_id text,p_token uuid,p_outbound text)
returns void language plpgsql security invoker set search_path='' as $$
declare j public.mkulima_message_jobs%rowtype;
begin
 select * into j from public.mkulima_message_jobs where message_id=p_id;
 perform 1 from public.mkulima_conversations where actor_ref=j.actor_ref for update;
 select * into j from public.mkulima_message_jobs where message_id=p_id and lease_token=p_token and not sent for update;
 if not found or j.response is null or coalesce(p_outbound,'')='' then raise exception 'invalid_delivery'; end if;
 update public.mkulima_conversations set state=j.next_state,
 latest_interaction=coalesce(j.interaction_id,latest_interaction) where actor_ref=j.actor_ref;
 update public.mkulima_message_jobs set sent=true,outbound_id=p_outbound where message_id=p_id;
end $$;

create function public.mkulima_release_message(p_id text,p_token uuid)
returns void language sql security invoker set search_path='' as $$
 update public.mkulima_message_jobs set lease_until=now() where message_id=p_id and lease_token=p_token and not sent
$$;
create function public.mkulima_feedback_target(p_actor text,p_outbound text)
returns uuid language sql stable security invoker set search_path='' as $$
 select interaction_id from public.mkulima_message_jobs where actor_ref=p_actor and outbound_id=p_outbound and sent
$$;
revoke all on function public.mkulima_claim_message(text,text,text),public.mkulima_stage_message(text,uuid,text,jsonb,jsonb),
 public.mkulima_finish_message(text,uuid,text),public.mkulima_release_message(text,uuid),public.mkulima_feedback_target(text,text) from public,anon,authenticated;
grant execute on function public.mkulima_claim_message(text,text,text),public.mkulima_stage_message(text,uuid,text,jsonb,jsonb),
 public.mkulima_finish_message(text,uuid,text),public.mkulima_release_message(text,uuid),public.mkulima_feedback_target(text,text) to service_role;
