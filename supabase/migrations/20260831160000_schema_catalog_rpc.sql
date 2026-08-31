-- Read-only catalog of the public schema, callable via PostgREST so the
-- schema-snapshot job (sync/schema_snapshot.py -> docs/schema-snapshot.json)
-- can diff docs against the live DB without a raw Postgres connection.

create or replace function public.schema_catalog()
returns jsonb
language sql
stable
security definer
set search_path = public
as $$
  select coalesce(jsonb_object_agg(x.table_name, x.cols order by x.table_name), '{}'::jsonb)
  from (
    select c.table_name,
           jsonb_agg(jsonb_build_object(
             'column', c.column_name,
             'type', c.data_type,
             'nullable', (c.is_nullable = 'YES'),
             'default', c.column_default
           ) order by c.ordinal_position) as cols
    from information_schema.columns c
    join information_schema.tables t
      on t.table_schema = c.table_schema and t.table_name = c.table_name
    where c.table_schema = 'public' and t.table_type = 'BASE TABLE'
    group by c.table_name
  ) x;
$$;

revoke all on function public.schema_catalog() from public, anon;
grant execute on function public.schema_catalog() to authenticated, service_role;
