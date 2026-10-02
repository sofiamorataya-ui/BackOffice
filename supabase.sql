-- Registro de pre-verificaciones de documentos (BackOffice STT)
create table if not exists public.prechecks (
  id                bigint generated always as identity primary key,
  created_at        timestamptz not null default now(),
  documento         text not null,              -- BCA
  shipment          text not null,              -- S-039981
  driver_assignment text,                       -- D-021956
  carrier           text,
  dot               text,
  mc                text,
  resultado         text not null,              -- AUTORIZADO | NO_ENVIAR | YA_EXISTE
  codigo            text unique,                -- solo cuando es AUTORIZADO
  motivos           jsonb not null default '[]'::jsonb,
  solicitado_por    text
);
create index if not exists prechecks_created_at_idx on public.prechecks (created_at desc);

-- Solo la app (con la service_role key guardada en los secrets) puede leer y escribir.
alter table public.prechecks enable row level security;
