-- Registro de pre-verificaciones de documentos (BackOffice STT)
create table if not exists public.prechecks (
  id                bigint generated always as identity primary key,
  created_at        timestamptz not null default now(),
  documento         text not null,              -- BCA | LC
  shipment          text not null,              -- S-039981
  driver_assignment text,                       -- D-021956
  carrier           text,
  dot               text,
  mc                text,
  resultado         text not null,              -- AUTORIZADO | NO_ENVIAR | YA_EXISTE
  cumplimiento      text,                       -- green | yellow | red
  codigo            text unique,                -- solo cuando es AUTORIZADO
  motivos           jsonb not null default '[]'::jsonb,
  shipment_owner    text,
  dispatcher        text,
  solicitado_por    text,
  politica_version  text,                       -- p. ej. BCA Verification Policy v1.0
  revision_manual   jsonb not null default '[]'::jsonb,  -- 2.6.0: lo que BackOffice revisa a mano
  excepciones       jsonb not null default '[]'::jsonb   -- 2.6.0: excepción del dueño de franquicia
);
create index if not exists prechecks_created_at_idx on public.prechecks (created_at desc);

-- Si la tabla ya existía de la versión 2.0, estas líneas agregan las columnas nuevas (2.2.0).
alter table public.prechecks add column if not exists cumplimiento   text;
alter table public.prechecks add column if not exists shipment_owner text;
alter table public.prechecks add column if not exists dispatcher     text;

-- Solo la app (con la service_role key guardada en los secrets) puede leer y escribir.
alter table public.prechecks enable row level security;

-- 2.3.0: versión de la política aplicada en cada verificación (por ejemplo "BCA Verification Policy v1.0").
alter table public.prechecks add column if not exists politica_version text;

-- 2.6.0: lo que BackOffice revisa a mano y las excepciones del dueño de la franquicia (solo las ve BackOffice).
alter table public.prechecks add column if not exists revision_manual jsonb not null default '[]'::jsonb;
alter table public.prechecks add column if not exists excepciones     jsonb not null default '[]'::jsonb;
