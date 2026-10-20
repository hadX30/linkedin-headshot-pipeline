-- Original pipeline behaviour restored:
--  * runs can end as "partial" (the CLI's --stage / --analyze-only), as in the original pipeline;
--  * uploads are stored in their original format, so the bucket also accepts WebP and GIF.
-- "rejected" stays allowed so rows written before this change remain valid.

alter table public.runs drop constraint if exists runs_status_check;
alter table public.runs add constraint runs_status_check
    check (status in ('queued', 'running', 'delivered', 'manual_review', 'partial', 'error', 'rejected'));

update storage.buckets
   set allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp', 'image/gif']
 where id = 'malameh-headshots';
