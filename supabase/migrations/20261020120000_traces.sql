-- Malameh headshot pipeline: trace tables, settings, reporting function, private image bucket.
--
-- Access model: Row Level Security is ON for every table with NO policies, so the
-- public anon/publishable key can read or write nothing. Only the server, using the
-- secret (service-role) key, which bypasses RLS, touches these tables.

create table if not exists public.runs (
    id             uuid primary key,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    finished_at    timestamptz,
    mode           text not null check (mode in ('mock', 'live')),
    scenario       text,
    source         text not null,
    status         text not null check (status in ('queued', 'running', 'delivered', 'manual_review', 'rejected', 'error')),
    stage          text check (stage in ('analyze', 'prompt', 'generate', 'validate')),
    attempts       integer not null default 0,
    reason         text,
    error          text,
    input          jsonb,
    analysis       jsonb,
    result         jsonb,
    output_file    text,
    cost_usd       numeric(12, 6) not null default 0,
    cost_estimated boolean not null default false,
    duration_ms    integer,
    models         jsonb
);
create index if not exists runs_created_at_idx on public.runs (created_at desc);
create index if not exists runs_status_idx on public.runs (status);

create table if not exists public.spans (
    id             uuid primary key,
    run_id         uuid not null references public.runs (id) on delete cascade,
    seq            integer not null,
    mode           text not null check (mode in ('mock', 'live')),
    stage          text not null check (stage in ('analyze', 'prompt', 'generate', 'validate')),
    attempt        integer not null default 0,
    provider       text not null,
    model          text not null,
    status         text not null check (status in ('ok', 'error')),
    started_at     timestamptz not null,
    ended_at       timestamptz not null,
    duration_ms    integer not null,
    request        jsonb,
    response       jsonb,
    usage          jsonb,
    cost_usd       numeric(12, 6),
    cost_estimated boolean not null default false,
    error          text
);
create index if not exists spans_run_seq_idx on public.spans (run_id, seq);
create index if not exists spans_started_at_idx on public.spans (started_at);

create table if not exists public.settings (
    key        text primary key,
    value      jsonb not null,
    updated_at timestamptz not null default now()
);

alter table public.runs enable row level security;
alter table public.spans enable row level security;
alter table public.settings enable row level security;

-- Dashboard numbers in one call. Same JSON shape as the SQLite store's stats().
create or replace function public.trace_stats(since timestamptz)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
with r as (
    select * from public.runs where created_at >= since
),
s as (
    select * from public.spans where started_at >= since
),
finished as (
    select * from r where status in ('delivered', 'manual_review', 'rejected', 'error')
)
select jsonb_build_object(
    'since', since,
    'runs', (select count(*) from r),
    'finished', (select count(*) from finished),
    'delivery_rate', (
        select case when count(*) = 0 then null
                    else round(count(*) filter (where status = 'delivered')::numeric / count(*), 4) end
        from finished
    ),
    'avg_duration_ms', (select round(avg(duration_ms)) from finished),
    'avg_attempts', (select round(avg(attempts), 2) from r where attempts > 0),
    'by_status', coalesce((select jsonb_object_agg(status, n) from (select status, count(*) as n from r group by status) x), '{}'::jsonb),
    'by_mode', coalesce((select jsonb_object_agg(mode, n) from (select mode, count(*) as n from r group by mode) x), '{}'::jsonb),
    'cost', jsonb_build_object(
        'live_usd', (select coalesce(sum(cost_usd), 0) from r where mode = 'live'),
        'mock_estimated_usd', (select coalesce(sum(cost_usd), 0) from r where mode = 'mock'),
        'avg_per_delivered_live', (select round(avg(cost_usd), 6) from r where mode = 'live' and status = 'delivered'),
        'avg_per_delivered_mock', (select round(avg(cost_usd), 6) from r where mode = 'mock' and status = 'delivered')
    ),
    'by_stage', coalesce((
        select jsonb_agg(x order by x.stage, x.mode) from (
            select stage, mode, count(*) as calls, coalesce(sum(cost_usd), 0) as cost_usd,
                   avg(duration_ms) as avg_duration_ms, count(*) filter (where status = 'error') as errors
            from s group by stage, mode
        ) x
    ), '[]'::jsonb),
    'by_model', coalesce((
        select jsonb_agg(x order by x.provider, x.model) from (
            select provider, model, mode, count(*) as calls, coalesce(sum(cost_usd), 0) as cost_usd,
                   sum(coalesce((usage ->> 'input_tokens')::bigint, 0)
                       + coalesce((usage ->> 'text_input_tokens')::bigint, 0)
                       + coalesce((usage ->> 'image_input_tokens')::bigint, 0)) as input_tokens,
                   sum(coalesce((usage ->> 'output_tokens')::bigint, 0)) as output_tokens
            from s group by provider, model, mode
        ) x
    ), '[]'::jsonb),
    'daily', coalesce((
        select jsonb_agg(x order by x.day) from (
            select to_char(created_at at time zone 'utc', 'YYYY-MM-DD') as day, count(*) as runs,
                   coalesce(sum(cost_usd) filter (where mode = 'live'), 0) as live_usd,
                   coalesce(sum(cost_usd) filter (where mode = 'mock'), 0) as mock_usd
            from r group by 1
        ) x
    ), '[]'::jsonb)
);
$$;

revoke all on function public.trace_stats(timestamptz) from public, anon, authenticated;
grant execute on function public.trace_stats(timestamptz) to service_role;

-- Private bucket for input photos and generated attempts. No storage policies are
-- created, so only the secret key can read or write objects.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('malameh-headshots', 'malameh-headshots', false, 15728640, array['image/jpeg', 'image/png'])
on conflict (id) do nothing;
