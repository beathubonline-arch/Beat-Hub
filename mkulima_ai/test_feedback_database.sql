-- Two independent synthetic database paths. All test rows roll back.
begin;
set local role service_role;
do $$
declare i uuid; k uuid; f uuid; again uuid; n integer;
begin
 insert into public.mkulima_interactions(message_id,actor_ref,question,recommendation,crop,location_context,language,problem,answer_version)
 values('synthetic-foundation-test-1',repeat('a',64),'Synthetic question','Synthetic answer','maize','Synthetic private landmark','en','sale','test') returning id into i;
 f:=public.mkulima_record_feedback('synthetic-feedback-1',i,repeat('a',64),'helpful','Sold after comparing offers');
 again:=public.mkulima_record_feedback('synthetic-feedback-1',i,repeat('a',64),'helpful','Sold after comparing offers');
 if f<>again then raise exception 'Duplicate feedback not idempotent'; end if;
 select count(*) into n from public.mkulima_feedback where interaction_id=i;
 if n<>1 then raise exception 'Duplicate feedback rows'; end if;
 insert into public.mkulima_knowledge(interaction_id,crop,region,language,problem,recommendation,outcome_summary,confidence)
 values(i,'maize','synthetic-region','en','sale','Synthetic reviewed advice','Synthetic outcome',0.8) returning id into k;
 select count(*) into n from public.mkulima_retrieve_knowledge('maize','synthetic-region','en','sale');
 if n<>0 then raise exception 'Unvalidated knowledge leaked'; end if;
 begin
   perform public.mkulima_review_knowledge(k,'','',now()+interval '1 day',true);
   raise exception 'Empty review evidence accepted';
 exception when check_violation then null;
 end;
 perform public.mkulima_review_knowledge(k,'synthetic reviewer','Synthetic evidence only; not agricultural knowledge',now()+interval '1 day',true);
 select count(*) into n from public.mkulima_retrieve_knowledge('MAIZE','synthetic-region','en','sale');
 if n<>1 then raise exception 'Reviewed knowledge not retrievable'; end if;
 select count(*) into n from public.mkulima_retrieve_knowledge('maize','different-region','en','sale');
 if n<>0 then raise exception 'Cross-region knowledge leaked'; end if;
 select count(*) into n from public.mkulima_retrieve_knowledge('beans','synthetic-region','en','sale');
 if n<>0 then raise exception 'Cross-crop knowledge leaked'; end if;
 update public.mkulima_knowledge set status='revoked' where id=k;
 select count(*) into n from public.mkulima_retrieve_knowledge('maize','synthetic-region','en','sale');
 if n<>0 then raise exception 'Revoked knowledge leaked'; end if;
end $$;
rollback;
select 'PASS 1: capture, idempotency, evidence gate, contextual retrieval, revocation' as result;

begin;
set local role service_role;
do $$
declare i uuid; k uuid; n integer;
begin
 insert into public.mkulima_interactions(message_id,actor_ref,question,recommendation,language,answer_version)
 values('synthetic-foundation-test-2',repeat('b',64),'Swali la jaribio','Jibu la jaribio','sw','test') returning id into i;
 begin
   perform public.mkulima_record_feedback('synthetic-cross-actor',i,repeat('x',64),'wrong','bad');
   raise exception 'Cross-actor feedback accepted';
 exception when insufficient_privilege then null;
 end;
 perform public.mkulima_record_feedback('synthetic-thumb-only',i,repeat('b',64),'helpful');
 insert into public.mkulima_knowledge(interaction_id,crop,region,language,problem,recommendation,outcome_summary,confidence)
 values(i,'maize','synthetic-region','sw','sale','Ushauri wa jaribio','Matokeo ya jaribio',0.7) returning id into k;
 begin
   perform public.mkulima_review_knowledge(k,'reviewer','evidence',now()+interval '1 day',true);
   raise exception 'Thumb-only validation accepted';
 exception when raise_exception then
   if sqlerrm<>'actual_outcome_required' then raise; end if;
 end;
 perform public.mkulima_record_feedback('synthetic-outcome',i,repeat('b',64),'helpful','Matokeo ya jaribio');
 perform public.mkulima_review_knowledge(k,'reviewer','Synthetic evidence',now()+interval '1 day',true);
 perform public.mkulima_record_feedback('synthetic-negative',i,repeat('b',64),'still_problem','Bado tatizo');
 select count(*) into n from public.mkulima_retrieve_knowledge('maize','synthetic-region','sw','sale');
 if n<>0 then raise exception 'Contradicted knowledge leaked'; end if;
 if (select status from public.mkulima_knowledge where id=k)<>'needs_review' then raise exception 'Negative feedback failed to pause reuse'; end if;
 -- Later explicit review can resolve the contradiction, but expiry still excludes it.
 perform public.mkulima_review_knowledge(k,'reviewer','Synthetic re-review evidence',now()+interval '1 day',true);
 update public.mkulima_knowledge set reviewed_at=now()-interval '2 days',valid_until=now()-interval '1 day' where id=k;
 select count(*) into n from public.mkulima_retrieve_knowledge('maize','synthetic-region','sw','sale');
 if n<>0 then raise exception 'Expired knowledge leaked'; end if;
end $$;
rollback;
select 'PASS 2: actor ownership, actual outcome gate, negative feedback pause, expiry' as result;
