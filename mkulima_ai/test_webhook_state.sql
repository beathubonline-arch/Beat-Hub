begin;
set local role service_role;
do $$
declare j jsonb; retry jsonb; target uuid; n integer;
begin
 j:=public.mkulima_claim_message('synthetic-webhook-1',repeat('d',64),'hash1');
 if j->>'status'<>'claimed' then raise exception 'claim failed'; end if;
 if (public.mkulima_claim_message('synthetic-webhook-1',repeat('d',64),'hash1')->>'status')<>'busy' then raise exception 'concurrent duplicate accepted'; end if;
 perform public.mkulima_stage_message('synthetic-webhook-1',(j->>'token')::uuid,'Synthetic reply','{"language":"en","location":"Synthetic Landmark","bags":10}',
 '{"question":"Synthetic question","recommendation":"Synthetic recommendation","language":"en","answer_version":"test","crop":"maize","location":"Synthetic Landmark","problem":"maize_sale"}');
 -- Failed delivery: release then recover the exact staged reply.
 perform public.mkulima_release_message('synthetic-webhook-1',(j->>'token')::uuid);
 retry:=public.mkulima_claim_message('synthetic-webhook-1',repeat('d',64),'hash1');
 if retry->>'response'<>'Synthetic reply' then raise exception 'staged reply lost'; end if;
 if (public.mkulima_claim_message('synthetic-webhook-next',repeat('d',64),'hash2')->>'status')<>'busy' then raise exception 'unfinished message overtaken'; end if;
 perform public.mkulima_finish_message('synthetic-webhook-1',(retry->>'token')::uuid,'synthetic-outbound-1');
 if (public.mkulima_claim_message('synthetic-webhook-1',repeat('d',64),'hash1')->>'status')<>'sent' then raise exception 'delivered duplicate accepted'; end if;
 j:=public.mkulima_claim_message('synthetic-webhook-next',repeat('d',64),'hash2');
 if j->'state'->>'location'<>'Synthetic Landmark' or j->'state'->>'bags'<>'10' then raise exception 'durable state lost'; end if;
 target:=public.mkulima_feedback_target(repeat('d',64),'synthetic-outbound-1');
 if target is null then raise exception 'reply context not linked'; end if;
 if public.mkulima_feedback_target(repeat('e',64),'synthetic-outbound-1') is not null then raise exception 'cross actor access'; end if;
 perform public.mkulima_record_feedback('synthetic-linked-feedback',target,repeat('d',64),'helpful','Synthetic outcome');
 select count(*) into n from public.mkulima_interactions where message_id='synthetic-webhook-1';
 if n<>1 then raise exception 'duplicate interactions'; end if;
end $$;
rollback;
select 'PASS: durable answer retry, duplicate exclusion, state restore, reply ownership, linked feedback' as result;
