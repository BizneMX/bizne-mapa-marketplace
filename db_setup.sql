--
-- PostgreSQL database dump
--

\restrict xc1lF3vZZaDmVeO7sHZ3auXBclQuA148RB2U53G1relzIcqWtZuGND1tZKU2h4X

-- Dumped from database version 16.14 (Homebrew)
-- Dumped by pg_dump version 16.14 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: bono_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bono_config (
    id integer NOT NULL,
    user_id integer NOT NULL,
    esquema character varying(20) DEFAULT 'tiers'::character varying NOT NULL,
    bono_por_bizne numeric(10,2),
    cap_maximo numeric(10,2),
    actualizado_en timestamp with time zone DEFAULT now() NOT NULL,
    actualizado_por character varying(255)
);


--
-- Name: bono_config_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bono_config_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bono_config_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bono_config_id_seq OWNED BY public.bono_config.id;


--
-- Name: bono_tiers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.bono_tiers (
    id integer NOT NULL,
    bono_config_id integer NOT NULL,
    min_fondas integer NOT NULL,
    bono numeric(10,2) NOT NULL,
    orden integer DEFAULT 0 NOT NULL
);


--
-- Name: bono_tiers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.bono_tiers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: bono_tiers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.bono_tiers_id_seq OWNED BY public.bono_tiers.id;


--
-- Name: hunter_conversaciones; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hunter_conversaciones (
    user_id integer NOT NULL,
    ultimo_mensaje text,
    ultimo_mensaje_en timestamp with time zone,
    no_leidos_hunter integer DEFAULT 0 NOT NULL,
    no_leidos_agente integer DEFAULT 0 NOT NULL
);


--
-- Name: hunter_hex_estados; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hunter_hex_estados (
    id integer NOT NULL,
    user_id integer NOT NULL,
    hex_id character varying(30) NOT NULL,
    estado character varying(30) DEFAULT 'pendiente'::character varying NOT NULL,
    nota text,
    actualizado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hunter_hex_estados_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hunter_hex_estados_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hunter_hex_estados_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hunter_hex_estados_id_seq OWNED BY public.hunter_hex_estados.id;


--
-- Name: hunter_mensajes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hunter_mensajes (
    id character varying(32) NOT NULL,
    user_id integer NOT NULL,
    autor character varying(20) NOT NULL,
    texto text NOT NULL,
    leido boolean DEFAULT false NOT NULL,
    enviado_en timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT hunter_mensajes_autor_check CHECK (((autor)::text = ANY ((ARRAY['hunter'::character varying, 'soporte'::character varying])::text[])))
);


--
-- Name: hunter_visitas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hunter_visitas (
    id character varying(32) NOT NULL,
    user_id integer NOT NULL,
    hex_id character varying(30),
    hex_code character varying(30),
    colonia character varying(255),
    nombre_prospecto character varying(255) NOT NULL,
    categoria_bizne character varying(60) NOT NULL,
    resultado character varying(30) NOT NULL,
    whatsapp character varying(20),
    direccion text,
    lat numeric(10,7),
    lng numeric(10,7),
    comentarios text,
    creado_en timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: hunter_zone_assignments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.hunter_zone_assignments (
    id integer NOT NULL,
    week character varying(10) NOT NULL,
    hex_id character varying(30) NOT NULL,
    hex_code character varying(20) DEFAULT ''::character varying NOT NULL,
    hunter_name character varying(100) NOT NULL,
    route_order integer DEFAULT 1 NOT NULL,
    assigned_by character varying(50) DEFAULT 'mapa'::character varying,
    notes text DEFAULT ''::text,
    days character varying(20) DEFAULT ''::character varying,
    assigned_at timestamp with time zone DEFAULT now(),
    user_id integer
);


--
-- Name: hunter_zone_assignments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.hunter_zone_assignments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: hunter_zone_assignments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.hunter_zone_assignments_id_seq OWNED BY public.hunter_zone_assignments.id;


--
-- Name: usuarios; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.usuarios (
    id integer NOT NULL,
    email character varying(255) NOT NULL,
    nombre character varying(100),
    apellido character varying(100),
    telefono character varying(30),
    roles character varying(100),
    activo boolean DEFAULT true,
    fecha_registro timestamp with time zone,
    ultimo_login timestamp with time zone,
    sincronizado_en timestamp with time zone DEFAULT now(),
    hashed_password text,
    must_change_pw boolean DEFAULT true NOT NULL,
    apps text[] DEFAULT '{}'::text[] NOT NULL,
    auth_role character varying(50),
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT usuarios_auth_role_check CHECK (((auth_role IS NULL) OR ((auth_role)::text = ANY ((ARRAY['superadmin'::character varying, 'operations'::character varying, 'hunters'::character varying])::text[]))))
);


--
-- Name: COLUMN usuarios.auth_role; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.usuarios.auth_role IS 'Rol de acceso a las apps internas de Bizne: superadmin | operations | hunters.';


--
-- Name: usuarios_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.usuarios ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY (
    SEQUENCE NAME public.usuarios_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: bono_config id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bono_config ALTER COLUMN id SET DEFAULT nextval('public.bono_config_id_seq'::regclass);


--
-- Name: bono_tiers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bono_tiers ALTER COLUMN id SET DEFAULT nextval('public.bono_tiers_id_seq'::regclass);


--
-- Name: hunter_hex_estados id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_hex_estados ALTER COLUMN id SET DEFAULT nextval('public.hunter_hex_estados_id_seq'::regclass);


--
-- Name: hunter_zone_assignments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_zone_assignments ALTER COLUMN id SET DEFAULT nextval('public.hunter_zone_assignments_id_seq'::regclass);


--
-- Data for Name: bono_config; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.bono_config (id, user_id, esquema, bono_por_bizne, cap_maximo, actualizado_en, actualizado_por) FROM stdin;
\.


--
-- Data for Name: bono_tiers; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.bono_tiers (id, bono_config_id, min_fondas, bono, orden) FROM stdin;
\.


--
-- Data for Name: hunter_conversaciones; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.hunter_conversaciones (user_id, ultimo_mensaje, ultimo_mensaje_en, no_leidos_hunter, no_leidos_agente) FROM stdin;
\.


--
-- Data for Name: hunter_hex_estados; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.hunter_hex_estados (id, user_id, hex_id, estado, nota, actualizado_en) FROM stdin;
1	152587	8849958e99fffff	pendiente	\N	2026-06-22 22:51:03.35944-06
\.


--
-- Data for Name: hunter_mensajes; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.hunter_mensajes (id, user_id, autor, texto, leido, enviado_en) FROM stdin;
\.


--
-- Data for Name: hunter_visitas; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.hunter_visitas (id, user_id, hex_id, hex_code, colonia, nombre_prospecto, categoria_bizne, resultado, whatsapp, direccion, lat, lng, comentarios, creado_en) FROM stdin;
\.


--
-- Data for Name: hunter_zone_assignments; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.hunter_zone_assignments (id, week, hex_id, hex_code, hunter_name, route_order, assigned_by, notes, days, assigned_at, user_id) FROM stdin;
1	2026-W26	88499516adfffff	HEX-20644	Anel	1	mapa-staging	A Alta prioridad	1,2	2026-06-22 18:53:10.501491-06	108605
2	2026-W26	88499516e7fffff	HEX-20669	Anel	2	mapa-staging	A Alta prioridad	3,4	2026-06-22 18:53:10.501491-06	108605
3	2026-W26	8849958ca1fffff	HEX-23294	Emma	1	mapa-staging	A Alta prioridad	1,2	2026-06-22 18:53:10.501491-06	112894
4	2026-W26	8849958ce3fffff	HEX-23323	Emma	2	mapa-staging	A Alta prioridad	3,4	2026-06-22 18:53:10.501491-06	112894
5	2026-W26	8849958c81fffff	HEX-23280	Emma	3	mapa-staging	A Alta prioridad	5	2026-06-22 18:53:10.501491-06	112894
6	2026-W26	8849951687fffff	HEX-20627	Jose Luis	1	mapa-staging	A Alta prioridad	1,2	2026-06-22 18:53:10.501491-06	108608
7	2026-W26	8849951683fffff	HEX-20625	Jose Luis	2	mapa-staging	A Alta prioridad	3,4	2026-06-22 18:53:10.501491-06	108608
8	2026-W26	88499516b9fffff	HEX-20649	Jose Luis	3	mapa-staging	A Alta prioridad	5	2026-06-22 18:53:10.501491-06	108608
9	2026-W26	884995b9e9fffff	HEX-26492	Leonardo	1	mapa-staging	S Sin señal	1,2	2026-06-22 18:53:10.501491-06	140631
10	2026-W26	884995b917fffff	HEX-26407	Leonardo	2	mapa-staging	C Media	3,4	2026-06-22 18:53:10.501491-06	140631
11	2026-W26	884995b9a9fffff	HEX-26464	Leonardo	3	mapa-staging	C Media	5	2026-06-22 18:53:10.501491-06	140631
12	2026-W26	8849958e99fffff	HEX-23487	Eduardo	1	mapa-staging	D Cubierta	1	2026-06-22 18:53:10.501491-06	152587
13	2026-W26	88499585e5fffff	HEX-22736	Eduardo	2	mapa-staging	C Media	2,3	2026-06-22 18:53:10.501491-06	152587
14	2026-W26	8849958533fffff	HEX-22665	Eduardo	3	mapa-staging	B Media-alta	4,5	2026-06-22 18:53:10.501491-06	152587
\.


--
-- Data for Name: usuarios; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.usuarios (id, email, nombre, apellido, telefono, roles, activo, fecha_registro, ultimo_login, sincronizado_en, hashed_password, must_change_pw, apps, auth_role, updated_at) FROM stdin;
9000000	alonso@bizne.mx	alonso	\N	\N	\N	t	2026-06-22 19:48:04.38378-06	2026-06-23 18:13:58.277047-06	2026-06-22 19:48:04.38378-06	$2b$12$G7CUp6ipkpPQMKMKKD5G1OdqKHQ5ie2ajLTMCaqrSmRLr4bI6vN6u	f	{hunters,mapa,kpis,fraude,perfiles}	superadmin	2026-06-23 07:37:07.842234-06
108608	joseluishunter@gmail.com	Jose Luis	Hunter	555242265	Admin farmer	t	\N	2026-06-23 18:14:32.249722-06	2026-06-22 18:31:25.881923-06	$2b$12$rvXHaVjA77GnNEggxv3E/u28G5w00k2wDGSTxeQN2zTeykMcIS1D.	t	{hunters}	hunters	2026-06-23 18:14:25.03822-06
140631	leo@gmail.co	Leonardo	Hunter	5555555522	Encargado	t	2026-06-08 20:45:59-06	\N	2026-06-22 18:31:25.881923-06	$2b$12$rakRy.uefLV5qrHNOHqPJOzL.tu8PngEUTclt1Eb.2Cd4QGKYz7Pi	t	{hunters}	hunters	2026-06-22 20:08:52.346437-06
9000002	mathis@bizne.mx	Mathis	\N	\N	\N	t	2026-06-22 20:12:36.105114-06	\N	2026-06-22 20:12:36.105114-06	$2b$12$/TU2JabzSKc7r/jCtygH8ORtqGhsuB6qZBRbJe2cTCSQ2N4j2.PEq	t	{hunters,mapa,kpis,perfiles,fraude}	operations	2026-06-22 20:13:00.902077-06
112894	ema@bizne.mx	Emma	Hunter	5555555521	Encargado	t	2026-03-06 14:36:34-06	\N	2026-06-22 18:31:25.881923-06	$2b$12$N99xUkDH3S8jOBwmG/ABp.PIeBKEOU5TQ3KX6.J3DdCx9dtUt.RSy	t	{hunters}	hunters	2026-06-22 21:36:58.119646-06
108605	anelhunter@gmail.com	Anel	Hunter	5555552222	Admin farmer	t	\N	\N	2026-06-22 18:31:25.881923-06	$2b$12$aqWIklbRkcZcYDwvnW1WL.Dr81yzfIiGCf8Dhr457bQlarUB2r1VC	t	{hunters}	hunters	2026-06-22 20:10:41.195321-06
152588	emma@bizne.co	Emma	Hunter	5555550678	Hunter	t	\N	\N	2026-06-22 18:31:25.881923-06	$2b$12$LxRSvVaQQN/3R5MG8A239ONkrSt8Q2Q1F/tDUrRmDD5YQMQUq6nWq	t	{hunters}	hunters	2026-06-22 19:43:17.748358-06
152587	eduardo@bizne.co	Eduardo	Hunter	5555557882	Hunter	t	\N	2026-06-22 22:47:06.69452-06	2026-06-22 18:31:25.881923-06	$2b$12$xE.wyR//HtIH40A3vnp4dOI5RqegsemH4YlaUyd85YY8cMdpBajEq	t	{hunters}	hunters	2026-06-22 19:43:17.748358-06
9000004	oscar@bizne.mx	Oscar 	\N	\N	\N	t	2026-06-22 20:21:11.32609-06	\N	2026-06-22 20:21:11.32609-06	$2b$12$DOc7iHopT1J0nyTuX43E2uN5f574l7DNjKZDRmPx8srIyCbm60h26	t	{kpis,hunters,perfiles,mapa,fraude}	operations	2026-06-22 20:21:29.558036-06
152589	leonardo@bizne.co	Leonardo	Hunter	5555553357	Hunter	t	\N	\N	2026-06-22 18:31:25.881923-06	$2b$12$6WkxCVw4LwiwjnTtx1Syu.CaCz0PvKaslmjhM/CjZgDT0obnYQIt6	t	{hunters}	hunters	2026-06-22 19:43:17.748358-06
9000005	amir@bizne.mx	Amir	\N	\N	\N	t	2026-06-22 20:21:49.706032-06	\N	2026-06-22 20:21:49.706032-06	$2b$12$GpCHYZ3jOhgUfTuQF6wci.2xW1mzrXGEP8xAawWjP6r1UAP917Kou	t	{hunters,kpis,perfiles,mapa,fraude}	operations	2026-06-22 20:21:51.15122-06
116195	teresahernandez1280@gmail.com	Teresita	Activadora	5626768840	Hunter	t	2025-12-24 11:49:24-06	\N	2026-06-22 18:31:25.881923-06	$2b$12$vbWjOGpdmGHrFe5eOyiVNOvahFZOawmReosnzXmpc3HuTnXFfUSaK	t	{hunters}	hunters	2026-06-22 19:43:17.748358-06
9000006	hugo@bizne.mx	Hugo Sanchez	\N	\N	\N	t	2026-06-22 20:22:34.176258-06	\N	2026-06-22 20:22:34.176258-06	$2b$12$UGikyFW5EUo1Zckd7q.4befYp1x3Gtdo5EjEJbSH.w8vgec/A9ESu	t	{hunters,kpis,perfiles,mapa,fraude}	operations	2026-06-22 20:22:34.176258-06
\.


--
-- Name: bono_config_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.bono_config_id_seq', 1, false);


--
-- Name: bono_tiers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.bono_tiers_id_seq', 1, false);


--
-- Name: hunter_hex_estados_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.hunter_hex_estados_id_seq', 3, true);


--
-- Name: hunter_zone_assignments_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.hunter_zone_assignments_id_seq', 14, true);


--
-- Name: usuarios_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.usuarios_id_seq', 9000006, true);


--
-- Name: bono_config bono_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bono_config
    ADD CONSTRAINT bono_config_pkey PRIMARY KEY (id);


--
-- Name: bono_config bono_config_user_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bono_config
    ADD CONSTRAINT bono_config_user_id_key UNIQUE (user_id);


--
-- Name: bono_tiers bono_tiers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bono_tiers
    ADD CONSTRAINT bono_tiers_pkey PRIMARY KEY (id);


--
-- Name: hunter_conversaciones hunter_conversaciones_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_conversaciones
    ADD CONSTRAINT hunter_conversaciones_pkey PRIMARY KEY (user_id);


--
-- Name: hunter_hex_estados hunter_hex_estados_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_hex_estados
    ADD CONSTRAINT hunter_hex_estados_pkey PRIMARY KEY (id);


--
-- Name: hunter_hex_estados hunter_hex_estados_user_id_hex_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_hex_estados
    ADD CONSTRAINT hunter_hex_estados_user_id_hex_id_key UNIQUE (user_id, hex_id);


--
-- Name: hunter_mensajes hunter_mensajes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_mensajes
    ADD CONSTRAINT hunter_mensajes_pkey PRIMARY KEY (id);


--
-- Name: hunter_visitas hunter_visitas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_visitas
    ADD CONSTRAINT hunter_visitas_pkey PRIMARY KEY (id);


--
-- Name: hunter_zone_assignments hunter_zone_assignments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_zone_assignments
    ADD CONSTRAINT hunter_zone_assignments_pkey PRIMARY KEY (id);


--
-- Name: usuarios hunters_usuario_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT hunters_usuario_email_key UNIQUE (email);


--
-- Name: usuarios hunters_usuario_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT hunters_usuario_pkey PRIMARY KEY (id);


--
-- Name: hunter_zone_assignments uq_week_hex_hunter; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_zone_assignments
    ADD CONSTRAINT uq_week_hex_hunter UNIQUE (week, hex_id, hunter_name);


--
-- Name: usuarios usuarios_email_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.usuarios
    ADD CONSTRAINT usuarios_email_unique UNIQUE (email);


--
-- Name: idx_assignments_hunter; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assignments_hunter ON public.hunter_zone_assignments USING btree (hunter_name);


--
-- Name: idx_assignments_week; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assignments_week ON public.hunter_zone_assignments USING btree (week);


--
-- Name: idx_bono_tiers_config; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_bono_tiers_config ON public.bono_tiers USING btree (bono_config_id);


--
-- Name: idx_hex_estados_user; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_hex_estados_user ON public.hunter_hex_estados USING btree (user_id);


--
-- Name: idx_mensajes_no_leidos; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mensajes_no_leidos ON public.hunter_mensajes USING btree (user_id, autor, leido) WHERE (leido = false);


--
-- Name: idx_mensajes_user_enviado; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_mensajes_user_enviado ON public.hunter_mensajes USING btree (user_id, enviado_en);


--
-- Name: idx_usuarios_activo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usuarios_activo ON public.usuarios USING btree (activo);


--
-- Name: idx_usuarios_auth_role; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_usuarios_auth_role ON public.usuarios USING btree (auth_role);


--
-- Name: idx_visitas_user_creado; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_visitas_user_creado ON public.hunter_visitas USING btree (user_id, creado_en DESC);


--
-- Name: bono_config bono_config_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bono_config
    ADD CONSTRAINT bono_config_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.usuarios(id);


--
-- Name: bono_tiers bono_tiers_bono_config_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.bono_tiers
    ADD CONSTRAINT bono_tiers_bono_config_id_fkey FOREIGN KEY (bono_config_id) REFERENCES public.bono_config(id) ON DELETE CASCADE;


--
-- Name: hunter_conversaciones hunter_conversaciones_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_conversaciones
    ADD CONSTRAINT hunter_conversaciones_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.usuarios(id);


--
-- Name: hunter_hex_estados hunter_hex_estados_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_hex_estados
    ADD CONSTRAINT hunter_hex_estados_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.usuarios(id);


--
-- Name: hunter_mensajes hunter_mensajes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_mensajes
    ADD CONSTRAINT hunter_mensajes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.usuarios(id);


--
-- Name: hunter_visitas hunter_visitas_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_visitas
    ADD CONSTRAINT hunter_visitas_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.usuarios(id);


--
-- Name: hunter_zone_assignments hunter_zone_assignments_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.hunter_zone_assignments
    ADD CONSTRAINT hunter_zone_assignments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.usuarios(id);


--
-- PostgreSQL database dump complete
--

\unrestrict xc1lF3vZZaDmVeO7sHZ3auXBclQuA148RB2U53G1relzIcqWtZuGND1tZKU2h4X

